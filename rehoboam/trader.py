"""Trader — EP-first orchestration layer for the auto trading pipeline.

This module exposes the methods the `auto` command (and Azure function) need:

- `get_ep_recommendations(league)` — run the full EP scoring pipeline and
  return structured buy/sell/trade-pair recommendations.
- `get_ep_recommendations_with_trends(league)` — wraps the above and wires
  market-value trend data into EP bid calculations (fixes the "permanent 40%
  overbid penalty" when trend_change_pct is None).
- `get_days_until_match(league)` — matchday phase detection.
- `find_profit_opportunities(league)` — short-hold profit flips.
- `optimize_squad_for_gameday(league)` — squad size / negative budget safety.

Everything else from the old trader.py (legacy analyze/trade/display logic)
has been removed.
"""

from datetime import datetime, timezone

from rich.console import Console

from .api import KickbaseAPI
from .bidding_strategy import SmartBidding
from .config import Settings
from .kickbase_client import League
from .matchup_analyzer import MatchupAnalyzer
from .services.trend_service import TrendService
from .value_history import ValueHistoryCache

console = Console()


class Trader:
    """EP-first orchestrator for the auto trading pipeline."""

    def __init__(
        self,
        api: KickbaseAPI,
        settings: Settings,
        verbose: bool = False,
        bid_learner=None,
        activity_feed_learner=None,
    ):
        self.api = api
        self.settings = settings
        self.verbose = verbose
        self.bid_learner = bid_learner
        self.activity_feed_learner = activity_feed_learner
        self.history_cache = ValueHistoryCache()
        self.trend_service = TrendService(self.api.client, self.history_cache)
        self.matchup_analyzer = MatchupAnalyzer()
        self.bidding = SmartBidding(
            bid_learner=bid_learner,
            activity_feed_learner=activity_feed_learner,
        )

    # ------------------------------------------------------------------
    # Matchday timing
    # ------------------------------------------------------------------

    def get_days_until_match(self, league) -> int | None:
        """Return days until the next match, or None if unknown.

        Uses timezone-aware datetime comparison to avoid the naive/aware
        TypeError that silently broke matchday-phase detection in early
        revisions of this code.
        """
        try:
            starting_eleven = self.api.get_starting_eleven(league)
            next_match = starting_eleven.get("nm") or starting_eleven.get("nextMatch")
            if not next_match:
                return None
            if isinstance(next_match, int | float):
                next_match_date = datetime.fromtimestamp(next_match, tz=timezone.utc)
            elif isinstance(next_match, str):
                next_match_date = datetime.fromisoformat(next_match.replace("Z", "+00:00"))
            else:
                return None
            now = datetime.now(tz=timezone.utc)
            days = (next_match_date - now).days
            return max(days, 0)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # EP pipeline
    # ------------------------------------------------------------------

    def get_ep_recommendations(self, league: League) -> dict:
        """Run the EP scoring pipeline and return structured recommendations.

        Returns a dict with keys:
            buy_recs, trade_pairs, sell_recs, squad_scores, lineup_map,
            budget, squad_size, squad_players, market_players,
            competitor_player_ids
        """
        from .config import POSITION_MINIMUMS
        from .scoring.collector import DataCollector
        from .scoring.decision import DecisionEngine
        from .scoring.scorer import score_player

        # --- 1. Fetch squad and market ---
        squad = self.api.get_squad(league)
        squad_size = len(squad)

        # Emergency mode — triggered when squad size is too low OR formation
        # is broken (e.g., 0 forwards = -100 pts every matchday from empty slot).
        position_counts: dict[str, int] = {}
        for p in squad:
            position_counts[p.position] = position_counts.get(p.position, 0) + 1
        formation_broken = any(
            position_counts.get(pos, 0) < minimum for pos, minimum in POSITION_MINIMUMS.items()
        )
        is_emergency = squad_size < self.settings.min_squad_size or formation_broken
        if formation_broken:
            missing = [
                f"{pos} ({position_counts.get(pos, 0)}/{minimum})"
                for pos, minimum in POSITION_MINIMUMS.items()
                if position_counts.get(pos, 0) < minimum
            ]
            console.print(
                f"[bold red]⚠ FORMATION EMERGENCY — missing: {', '.join(missing)}[/bold red]"
            )

        market_players_list = self.api.get_market(league)
        kickbase_market = [p for p in market_players_list if p.is_kickbase_seller()]

        team_info = self.api.get_team_info(league)
        current_budget = team_info.get("budget", 0)

        # --- 2. Collect performance + details + team profiles ---
        team_profiles: dict[str, dict] = {}

        def _get_team_profile_cached(team_id: str) -> dict | None:
            if not team_id:
                return None
            if team_id not in team_profiles:
                try:
                    profile = self.api.client.get_team_profile(league.id, team_id)
                    team_profiles[team_id] = profile
                except Exception:
                    team_profiles[team_id] = {}
            return team_profiles[team_id] or None

        def _fetch_player_data(player) -> tuple[dict | None, dict | None]:
            perf_data = None
            try:
                perf_data = self.history_cache.get_cached_performance(
                    player_id=player.id, league_id=league.id, max_age_hours=6
                )
                if not perf_data:
                    perf_data = self.api.client.get_player_performance(league.id, player.id)
                    if perf_data:
                        self.history_cache.cache_performance(player.id, league.id, perf_data)
            except Exception:
                perf_data = None

            player_details = None
            try:
                player_details = self.api.client.get_player_details(league.id, player.id)
            except Exception:
                pass

            if player_details:
                tid = player_details.get("tid", "")
                _get_team_profile_cached(tid)
                next_matchups = self.matchup_analyzer.get_next_matchups(player_details, n=3)
                for m in next_matchups:
                    if m.opponent_id:
                        _get_team_profile_cached(m.opponent_id)
            else:
                _get_team_profile_cached(getattr(player, "team_id", ""))

            return perf_data, player_details

        # --- 2b. Competitor scouting (best-effort) ---
        competitor_player_ids: set[str] = set()
        try:
            ranking = self.api.get_league_ranking(league)
            managers = ranking.get("it", ranking.get("us", []))
            my_id = self.api.user.id
            for mgr in managers:
                mgr_id = mgr.get("i", mgr.get("id", ""))
                if mgr_id == my_id:
                    continue
                try:
                    mgr_squad = self.api.get_manager_squad(league, mgr_id)
                    for p in mgr_squad.get("it", []):
                        pid = p.get("i", p.get("id", ""))
                        if pid:
                            competitor_player_ids.add(pid)
                except Exception:
                    pass
        except Exception:
            pass

        # --- 2c. Load competition matchday schedule for DGW detection ---
        try:
            matchdays = self.api.get_competition_matchdays()
            self.matchup_analyzer.load_dgw_from_matchdays(matchdays)
        except Exception:
            pass  # DGW detection is best-effort

        # --- 3. Score all players ---
        collector = DataCollector(matchup_analyzer=self.matchup_analyzer)

        # Pre-compute position calibration multipliers once per session
        # (one SQL query per position vs once per player). Stays at 1.0
        # without a bid_learner or with insufficient historical data.
        position_calibrations: dict[str, float] = {}
        if self.bid_learner is not None:
            for pos in ("Goalkeeper", "Defender", "Midfielder", "Forward"):
                try:
                    position_calibrations[pos] = (
                        self.bid_learner.get_position_calibration_multiplier(pos)
                    )
                except Exception:
                    position_calibrations[pos] = 1.0

        def _calibration_for(player) -> float:
            return position_calibrations.get(player.position, 1.0)

        market_scores: list = []
        market_player_map: dict = {}
        for player in kickbase_market:
            try:
                perf, details = _fetch_player_data(player)
                data = collector.collect(
                    player=player,
                    performance=perf,
                    player_details=details,
                    team_profiles=team_profiles,
                )
                market_scores.append(
                    score_player(data, calibration_multiplier=_calibration_for(player))
                )
                market_player_map[player.id] = player
            except Exception as e:
                if self.verbose:
                    console.print(f"[dim]EP: scoring failed for {player.last_name}: {e}[/dim]")

        squad_scores: list = []
        squad_player_map: dict = {}
        squad_performance: dict[str, dict] = {}
        for player in squad:
            try:
                perf, details = _fetch_player_data(player)
                if perf is not None:
                    squad_performance[player.id] = perf
                data = collector.collect(
                    player=player,
                    performance=perf,
                    player_details=details,
                    team_profiles=team_profiles,
                )
                squad_scores.append(
                    score_player(data, calibration_multiplier=_calibration_for(player))
                )
                squad_player_map[player.id] = player
            except Exception as e:
                if self.verbose:
                    console.print(f"[dim]EP: scoring failed for {player.last_name}: {e}[/dim]")

        # --- 4. Make decisions ---
        # roster_context is legacy — DecisionEngine accepts it but doesn't
        # use it; position counting happens directly on squad_scores.
        roster_context: dict = {}

        engine = DecisionEngine(
            min_ep_to_buy=getattr(self.settings, "min_expected_points_to_buy", 30.0),
            min_ep_upgrade=getattr(self.settings, "min_ep_upgrade_threshold", 5.0),
        )

        # Always compute both buy recs and trade pairs so the unified trade
        # phase can rank them against each other regardless of squad size.
        buy_recs = engine.recommend_buys(
            market_scores=market_scores,
            squad_scores=squad_scores,
            roster_context=roster_context,
            budget=current_budget,
            market_players=market_player_map,
            is_emergency=is_emergency,
            top_n=8 if is_emergency else 10,
            squad_players=squad_player_map,
        )
        trade_pairs = engine.build_trade_pairs(
            market_scores=market_scores,
            squad_scores=squad_scores,
            roster_context=roster_context,
            budget=current_budget,
            market_players=market_player_map,
            squad_players=squad_player_map,
            top_n=10,
        )
        sell_recs = engine.recommend_sells(
            squad_scores=squad_scores,
            roster_context=roster_context,
            squad_players=squad_player_map,
        )

        # --- 5. Compute EP-based bid amounts ---
        for rec in buy_recs:
            try:
                bid_rec = self.bidding.calculate_ep_bid(
                    asking_price=rec.player.price,
                    market_value=rec.player.market_value,
                    expected_points=rec.score.expected_points,
                    marginal_ep_gain=rec.marginal_ep_gain,
                    confidence=0.7,
                    current_budget=int(current_budget),
                    sell_plan=rec.sell_plan,
                    player_id=rec.player.id,
                    is_dgw=rec.score.is_dgw,
                )
                rec.recommended_bid = bid_rec.recommended_bid
            except Exception:
                rec.recommended_bid = rec.player.price

        for pair in trade_pairs:
            try:
                # Trade pairs get a synthetic sell plan so calculate_ep_bid
                # factors in the sell recovery when computing budget_ceiling.
                # Without this, budget_ceiling = current_budget + 0 which
                # caps the bid below asking price → recommended_bid=0 and
                # perfectly affordable trade pairs get silently dropped.
                from .scoring.models import SellPlan

                sell_recovery = int(pair.sell_player.market_value * 0.95)
                synthetic_sell_plan = SellPlan(
                    players_to_sell=[],
                    total_recovery=sell_recovery,
                    net_budget_after=int(current_budget) + sell_recovery - pair.buy_player.price,
                    is_viable=True,
                    ep_impact=0.0,
                    reasoning="Trade pair sell recovery",
                )
                bid_rec = self.bidding.calculate_ep_bid(
                    asking_price=pair.buy_player.price,
                    market_value=pair.buy_player.market_value,
                    expected_points=pair.buy_score.expected_points,
                    marginal_ep_gain=pair.ep_gain,
                    confidence=0.7,
                    current_budget=int(current_budget),
                    sell_plan=synthetic_sell_plan,
                    player_id=pair.buy_player.id,
                    is_dgw=pair.buy_score.is_dgw,
                )
                pair.recommended_bid = bid_rec.recommended_bid
            except Exception:
                pair.recommended_bid = pair.buy_player.price

        lineup_map = engine.select_lineup(squad_scores)

        # Mark uncontested buy recommendations (no competitor owns them)
        for rec in buy_recs:
            rec.metadata = getattr(rec, "metadata", {}) or {}
            rec.metadata["uncontested"] = rec.player.id not in competitor_player_ids
        for pair in trade_pairs:
            pair.metadata = getattr(pair, "metadata", {}) or {}
            pair.metadata["uncontested"] = pair.buy_player.id not in competitor_player_ids

        return {
            "buy_recs": buy_recs,
            "trade_pairs": trade_pairs,
            "sell_recs": sell_recs,
            "squad_scores": squad_scores,
            "lineup_map": lineup_map,
            "budget": current_budget,
            "squad_size": squad_size,
            "squad_players": squad_player_map,
            "market_players": market_player_map,
            "competitor_player_ids": competitor_player_ids,
            # Surfaced for matchday reconciliation (REH-20). Reconciliation
            # needs raw performance dicts to read past actual points (`p`,
            # `mdst`, `md`); piggybacking on the EP pipeline's existing
            # fetch avoids a second round-trip.
            "squad_performance": squad_performance,
        }

    def get_ep_recommendations_with_trends(self, league) -> dict:
        """get_ep_recommendations + trend-aware bid calculation.

        Fetches market-value trend for each buy rec / trade pair and recomputes
        the EP bid with trend_change_pct populated. Without this, every EP bid
        gets the `*= 0.6` conservative penalty from `calculate_ep_bid`.
        """
        result = self.get_ep_recommendations(league)
        current_budget = int(result.get("budget", 0))

        # League-level competitor context: checked once per session so all bids
        # in this run share the same "is the league aggressive today?" signal.
        has_whales = False
        if self.activity_feed_learner is not None:
            try:
                has_whales = self.activity_feed_learner.has_aggressive_competitors()
            except Exception:
                has_whales = False

        for rec in result.get("buy_recs", []):
            try:
                trend = self.trend_service.get_trend(
                    rec.player.id, rec.player.market_value, league.id
                )
                rec.metadata = rec.metadata or {}
                rec.metadata["trend_7d_pct"] = trend.trend_7d_pct
                rec.metadata["trend_14d_pct"] = trend.trend_14d_pct
                rec.metadata["momentum"] = trend.momentum
                rec.metadata["offer_count"] = rec.player.offer_count

                # Floor confidence at 0.7 to match the non-trend path so a
                # player with few recorded games doesn't produce a *lower*
                # bid here than in the plain EP pipeline. Data-quality gaps
                # already penalize the EP score upstream (grade F halving).
                bid_rec = self.bidding.calculate_ep_bid(
                    asking_price=rec.player.price,
                    market_value=rec.player.market_value,
                    expected_points=rec.score.expected_points,
                    marginal_ep_gain=rec.marginal_ep_gain,
                    confidence=max(0.7, min(1.0, rec.score.data_quality.games_played / 10.0)),
                    current_budget=current_budget,
                    sell_plan=rec.sell_plan,
                    player_id=rec.player.id,
                    trend_change_pct=trend.trend_7d_pct,
                    offer_count=rec.player.offer_count,
                    has_aggressive_competitors=has_whales,
                    is_dgw=rec.score.is_dgw,
                )
                rec.recommended_bid = bid_rec.recommended_bid
            except Exception:
                pass  # Keep original bid if trend fetch fails

        for pair in result.get("trade_pairs", []):
            try:
                trend = self.trend_service.get_trend(
                    pair.buy_player.id, pair.buy_player.market_value, league.id
                )
                pair.metadata = pair.metadata or {}
                pair.metadata["trend_7d_pct"] = trend.trend_7d_pct
                pair.metadata["offer_count"] = pair.buy_player.offer_count

                from .scoring.models import SellPlan

                sell_recovery = int(pair.sell_player.market_value * 0.95)
                synthetic_sell_plan = SellPlan(
                    players_to_sell=[],
                    total_recovery=sell_recovery,
                    net_budget_after=current_budget + sell_recovery - pair.buy_player.price,
                    is_viable=True,
                    ep_impact=0.0,
                    reasoning="Trade pair sell recovery",
                )
                bid_rec = self.bidding.calculate_ep_bid(
                    asking_price=pair.buy_player.price,
                    market_value=pair.buy_player.market_value,
                    expected_points=pair.buy_score.expected_points,
                    marginal_ep_gain=pair.ep_gain,
                    confidence=0.7,
                    current_budget=current_budget,
                    sell_plan=synthetic_sell_plan,
                    player_id=pair.buy_player.id,
                    trend_change_pct=trend.trend_7d_pct,
                    offer_count=pair.buy_player.offer_count,
                    has_aggressive_competitors=has_whales,
                    is_dgw=pair.buy_score.is_dgw,
                )
                pair.recommended_bid = bid_rec.recommended_bid
            except Exception:
                pass

        return result

    # ------------------------------------------------------------------
    # Profit flip discovery (buy low, sell high short-hold)
    # ------------------------------------------------------------------

    def find_profit_opportunities(self, league: League) -> list:
        """Find short-hold profit flip candidates (buy low, sell high).

        Uses matchday timing to scale down debt capacity when the match is near
        (need liquid budget at kickoff to avoid the zero-points penalty).
        """
        from .profit_trader import ProfitTrader

        market = self.api.get_market(league)
        kickbase_market = [p for p in market if p.is_kickbase_seller()]

        team_info = self.api.get_team_info(league)
        current_budget = team_info.get("budget", 0)
        team_value = team_info.get("team_value", 0)

        if team_value == 0:
            squad = self.api.get_squad(league)
            team_value = sum(player.market_value for player in squad)

        max_debt = int(team_value * (self.settings.max_debt_pct_of_team_value / 100))
        total_buying_power = current_budget + max_debt

        days_until_match = self.get_days_until_match(league)

        # Scale flip budget by matchday proximity — we need to be liquid at kickoff
        if days_until_match is None:
            flip_budget = current_budget + int(max_debt * 0.75)
        elif days_until_match <= 2:
            flip_budget = max(0, current_budget)  # No debt close to match
        elif days_until_match <= 4:
            flip_budget = current_budget + int(max_debt * 0.5)
        else:
            flip_budget = total_buying_power  # Full capacity when match is far

        player_trends = {
            p.id: self.trend_service.get_trend(p.id, p.market_value, league.id).to_dict()
            for p in kickbase_market[:50]
        }

        profit_trader = ProfitTrader(
            min_profit_pct=8.0,
            max_hold_days=7,
            max_risk_score=60.0,
        )

        max_opps = 5 if flip_budget < current_budget else 10
        return profit_trader.find_profit_opportunities(
            market_players=kickbase_market,
            current_budget=flip_budget,
            player_trends=player_trends,
            max_opportunities=max_opps,
            team_value=team_value,
            max_debt_pct=self.settings.max_debt_pct_of_team_value,
        )

    # ------------------------------------------------------------------
    # Squad optimization (budget + size safety before gameday)
    # ------------------------------------------------------------------

    def optimize_squad_for_gameday(self, league: League):
        """Squad-size + negative-budget safety check before gameday.

        Uses EP scores (avg points) directly as the value signal — this is
        simpler and tighter than the legacy PlayerValue+matchup pipeline and
        avoids a second round of per-player API calls.
        """
        from .squad_optimizer import SquadOptimizer

        squad = self.api.get_squad(league)
        team_info = self.api.get_team_info(league)
        current_budget = team_info.get("budget", 0)
        days_until_gameday = self.get_days_until_match(league)

        # Use avg_points as the ranking signal. This is good enough for
        # size/budget safety — we're picking who to drop from a bench, not
        # computing a precise best-11.
        player_values = {p.id: float(p.average_points or 0) for p in squad}

        optimizer = SquadOptimizer(
            min_squad_size=self.settings.min_squad_size,
            max_squad_size=15,
        )
        return optimizer.optimize_squad(
            squad=squad,
            player_values=player_values,
            current_budget=current_budget,
            days_until_gameday=days_until_gameday,
        )
