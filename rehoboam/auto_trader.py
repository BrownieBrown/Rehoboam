"""Automated trading - Execute trades without manual intervention"""

import time
from dataclasses import dataclass
from datetime import datetime

from rich.console import Console

from .services import AutoTradeResult, ExecutionService

console = Console()


@dataclass
class AutoTradeSession:
    """Summary of an automated trading session"""

    start_time: float
    end_time: float
    profit_trades: list[AutoTradeResult]
    lineup_trades: list[AutoTradeResult]
    errors: list[str]
    total_spent: int
    total_earned: int
    net_change: int


@dataclass
class MatchdayPhase:
    """Trading aggressiveness based on how close the next match is."""

    days_until_match: int | None  # None = unknown
    phase: str  # "aggressive" | "moderate" | "locked"
    max_trades: int
    allow_flips: bool  # Profit flips only make sense with enough time to sell
    reason: str


def _max_flip_hold_days(days_until_match: int | None) -> int | None:
    """Cap on a profit-flip's hold_days to fit before the next matchday.

    A flip we can't sell before kickoff risks both an unsellable position and
    the value drop that often follows uncertain matchday outcomes. Returns
    ``None`` when the schedule is unknown (no constraint applied — the
    matchday-phase logic already defaults to no-flips in that case).

    The "−1 day" buffer leaves a safety margin for the sell to actually clear
    given Kickbase's auction mechanics.
    """
    if days_until_match is None:
        return None
    return max(1, days_until_match - 1)


def _compute_flip_budget(
    phase: str, current_budget: int, pending_bid_total: int, max_debt: int
) -> int:
    """Free budget for flip trading, by matchday phase.

    Shared by session-context build and the trade-phase refresh so both
    call sites agree on the formula after sells/bid-cancels mutate the
    inputs mid-session.
    """
    if phase == "locked":
        return 0
    if phase == "moderate":
        return current_budget - pending_bid_total
    return current_budget + max_debt - pending_bid_total


@dataclass
class EPSessionContext:
    """Single-fetch context for the entire auto session."""

    ep_result: dict
    matchday_phase: MatchdayPhase
    my_bids: list
    my_bid_amounts: dict  # {player_id: bid_amount}
    squad: list
    current_budget: int
    team_value: int
    flip_budget: int
    executed_trade_count: int = 0


class AutoTrader:
    """Executes trades automatically based on bot recommendations"""

    def __init__(
        self,
        api,
        settings,
        max_trades_per_session: int = 5,  # Increased from 3 for more competitiveness
        max_daily_spend: int = 50_000_000,  # 50M max per day
        dry_run: bool = False,
    ):
        """
        Args:
            api: KickbaseAPI instance
            settings: Bot settings
            max_trades_per_session: Max trades per run (safety limit)
            max_daily_spend: Max money to spend per day (safety limit)
            dry_run: If True, simulate but don't execute
        """
        self.api = api
        self.settings = settings
        self.max_trades_per_session = max_trades_per_session
        self.max_daily_spend = max_daily_spend
        self.dry_run = dry_run

        # Daily tracking
        self.daily_spend = 0
        self.last_reset = datetime.now().date()

        # Learning system — file-based outcome tracking + adaptive bidding
        from .activity_feed_learner import ActivityFeedLearner
        from .bid_learner import BidLearner
        from .learning import LearningTracker

        self.learner = BidLearner()
        self.activity_feed_learner = ActivityFeedLearner()
        self.tracker = LearningTracker(self.learner)

        # Execution service — owns dry-run/try-except/AutoTradeResult plumbing
        self.execution = ExecutionService(api=api, tracker=self.tracker, dry_run=dry_run)

    def _reset_daily_limits_if_needed(self):
        """Reset daily limits at midnight"""
        today = datetime.now().date()
        if today > self.last_reset:
            self.daily_spend = 0
            self.last_reset = today
            console.print("[cyan]Daily limits reset[/cyan]")

    def _get_matchday_phase(self, days_until_match: int | None) -> MatchdayPhase:
        """Determine trading aggressiveness based on days to next match."""
        if days_until_match is not None and days_until_match <= 1:
            return MatchdayPhase(
                days_until_match=days_until_match,
                phase="locked",
                max_trades=0,
                allow_flips=False,
                reason=f"Match in {days_until_match}d — lineup only, no trading",
            )
        elif days_until_match is not None and days_until_match <= 4:
            return MatchdayPhase(
                days_until_match=days_until_match,
                phase="moderate",
                max_trades=max(self.max_trades_per_session // 2, 2),
                allow_flips=False,
                reason=f"Match in {days_until_match}d — lineup improvements only",
            )
        elif days_until_match is not None:
            return MatchdayPhase(
                days_until_match=days_until_match,
                phase="aggressive",
                max_trades=self.max_trades_per_session,
                allow_flips=True,
                reason=f"Match in {days_until_match}d — full trading",
            )
        else:
            # Unknown schedule — default to moderate (not aggressive) to avoid
            # accidentally going into debt right before a matchday we can't see.
            return MatchdayPhase(
                days_until_match=None,
                phase="moderate",
                max_trades=max(self.max_trades_per_session // 2, 2),
                allow_flips=False,
                reason="Unknown schedule — moderate trading (no flips)",
            )

    def _build_session_context(self, league) -> EPSessionContext:
        """Build the single-fetch context for the entire session."""
        from .trader import Trader

        trader = Trader(
            self.api,
            self.settings,
            bid_learner=self.learner,
            activity_feed_learner=self.activity_feed_learner,
        )

        # Fetch matchday timing
        days = trader.get_days_until_match(league)
        phase = self._get_matchday_phase(days)

        console.print(f"[cyan]📅 {phase.reason}[/cyan]")

        # Single EP pipeline call with trend data
        ep_result = trader.get_ep_recommendations_with_trends(league)

        # Fetch bids and squad
        my_bids = self.api.get_my_bids(league)
        squad = self.api.get_squad(league)
        team_info = self.api.get_team_info(league)
        current_budget = team_info.get("budget", 0)
        team_value = team_info.get("team_value", 0)

        # Calculate flip budget based on matchday phase
        max_debt = int(team_value * (self.settings.max_debt_pct_of_team_value / 100))
        pending_bid_total = sum(p.user_offer_price for p in my_bids)
        flip_budget = _compute_flip_budget(phase.phase, current_budget, pending_bid_total, max_debt)

        return EPSessionContext(
            ep_result=ep_result,
            matchday_phase=phase,
            my_bids=my_bids,
            my_bid_amounts={p.id: p.user_offer_price for p in my_bids},
            squad=squad,
            current_budget=current_budget,
            team_value=team_value,
            flip_budget=flip_budget,
        )

    def run_unified_trade_phase(self, league, ctx: EPSessionContext) -> list[AutoTradeResult]:
        """Execute all qualifying trades from a single ranked candidate list.

        Trade pairs and plain buys compete head-to-head by EP gain.
        This replaces the old separate profit + lineup sessions.
        """
        results: list[AutoTradeResult] = []
        buy_recs = ctx.ep_result.get("buy_recs", [])
        trade_pairs = ctx.ep_result.get("trade_pairs", [])

        effective_limit = min(
            self.max_trades_per_session,
            ctx.matchday_phase.max_trades,
        )

        console.print(
            f"\n[bold cyan]🤖 Unified Trade Phase "
            f"(limit {effective_limit}, phase: {ctx.matchday_phase.phase})[/bold cyan]"
        )

        # Build unified candidate list: (kind, ep_value, object)
        candidates = []
        for rec in buy_recs:
            if rec.recommended_bid and rec.recommended_bid > 0:
                candidates.append(("buy", rec.marginal_ep_gain, rec))
        for pair in trade_pairs:
            if pair.recommended_bid and pair.recommended_bid > 0:
                candidates.append(("pair", pair.ep_gain, pair))

        # Sort by EP gain descending — trade pairs compete directly with plain buys
        candidates.sort(key=lambda x: x[1], reverse=True)

        if not candidates:
            console.print("[dim]No actionable opportunities[/dim]")
            return results

        console.print(
            f"[cyan]📋 {len(candidates)} candidates "
            f"({sum(1 for c in candidates if c[0] == 'buy')} buys, "
            f"{sum(1 for c in candidates if c[0] == 'pair')} trade pairs)[/cyan]"
        )

        # Refresh squad, bids, and budget — sell monitoring, squad optimization,
        # and bid compliance/evaluation can all mutate these between ctx build
        # and the trade phase. Without re-running the flip-budget math against
        # fresh numbers, we skip affordable candidates after a mid-session sell
        # or bid cancel.
        fresh_squad = self.api.get_squad(league)
        fresh_bids = self.api.get_my_bids(league)
        fresh_team_info = self.api.get_team_info(league)
        current_squad_size = len(fresh_squad)
        active_bid_count = len(fresh_bids)
        available_slots = 15 - current_squad_size - active_bid_count
        ctx.current_budget = fresh_team_info.get("budget", ctx.current_budget)
        ctx.team_value = fresh_team_info.get("team_value", ctx.team_value)
        pending_bid_total = sum(p.user_offer_price for p in fresh_bids)
        max_debt = int(ctx.team_value * (self.settings.max_debt_pct_of_team_value / 100))
        ctx.flip_budget = _compute_flip_budget(
            ctx.matchday_phase.phase, ctx.current_budget, pending_bid_total, max_debt
        )
        ctx.my_bid_amounts = {p.id: p.user_offer_price for p in fresh_bids}

        console.print(
            f"[cyan]📋 Squad: {current_squad_size} + {active_bid_count} bids = "
            f"{current_squad_size + active_bid_count}/15 "
            f"({available_slots} slot(s) open)[/cyan]"
        )

        # Also add profit flip candidates if phase allows and there are open slots
        profit_flip_candidates = []
        if ctx.matchday_phase.allow_flips and available_slots > 0:
            try:
                from .trader import Trader

                trader = Trader(
                    self.api,
                    self.settings,
                    bid_learner=self.learner,
                    activity_feed_learner=self.activity_feed_learner,
                )
                profit_opps = trader.find_profit_opportunities(league)
                # Filter out players already in EP candidates
                ep_player_ids = {
                    rec.player.id for _, _, rec in candidates if hasattr(rec, "player")
                } | {pair.buy_player.id for _, _, pair in candidates if hasattr(pair, "buy_player")}

                # Cap flip hold time so we don't enter a position we can't exit
                # before the next matchday — being caught at kickoff with a
                # half-finished flip risks the lineup penalty AND market drop.
                max_hold_days = _max_flip_hold_days(ctx.matchday_phase.days_until_match)

                from .formation import validate_formation
                from .scoring.decision import _would_create_dead_weight

                skipped_long_hold = 0
                skipped_unfieldable = 0
                for opp in profit_opps:
                    if opp.player.id in ep_player_ids:
                        continue
                    if max_hold_days is not None and opp.hold_days > max_hold_days:
                        skipped_long_hold += 1
                        continue
                    # Fieldability guard: don't buy a flip that would make the
                    # squad unable to field a valid starting 11.
                    hypothetical = list(fresh_squad) + [opp.player]
                    fieldability = validate_formation(hypothetical)
                    if not fieldability["can_field_eleven"]:
                        skipped_unfieldable += 1
                        continue
                    # Dead-weight guard: don't flip-buy a player whose position
                    # is already saturated (e.g. 2nd GK, 6th DEF).
                    if _would_create_dead_weight(opp.player, fresh_squad):
                        skipped_unfieldable += 1
                        continue
                    profit_flip_candidates.append(opp)

                if profit_flip_candidates:
                    console.print(
                        f"[cyan]💰 + {len(profit_flip_candidates)} profit flip candidate(s)[/cyan]"
                    )
                if skipped_long_hold > 0:
                    console.print(
                        f"[dim]Skipped {skipped_long_hold} flip(s) — "
                        f"hold time would exceed matchday window[/dim]"
                    )
                if skipped_unfieldable > 0:
                    console.print(
                        f"[dim]Skipped {skipped_unfieldable} flip(s) — "
                        f"would make squad unfieldable[/dim]"
                    )
            except Exception as e:
                console.print(f"[yellow]Profit flip search failed: {e}[/yellow]")

        for kind, _ep_val, obj in candidates:
            if ctx.executed_trade_count >= effective_limit:
                console.print(f"[yellow]Trade limit reached ({effective_limit})[/yellow]")
                break
            if self.daily_spend >= self.max_daily_spend:
                console.print("[yellow]Daily spend limit reached[/yellow]")
                break

            if kind == "buy":
                if available_slots <= 0:
                    continue  # No slot for a plain buy
                if ctx.my_bid_amounts.get(obj.player.id, 0) > 0:
                    console.print(
                        f"[dim]Skip {obj.player.last_name} — already have active bid[/dim]"
                    )
                    continue
                if obj.recommended_bid > ctx.flip_budget:
                    console.print(
                        f"[yellow]Cannot afford {obj.player.last_name} "
                        f"(€{obj.recommended_bid:,} > €{ctx.flip_budget:,})[/yellow]"
                    )
                    continue

                # If buy exceeds current budget but has a sell plan, persist
                # the sell plan IDs alongside the bid. Once the auction resolves
                # (we won), the sells will be executed to recover the budget.
                # This is buy-first-sell-after: never sell before securing the player.
                sp_ids = None
                if hasattr(obj, "sell_plan") and obj.sell_plan and obj.sell_plan.players_to_sell:
                    sp_ids = [e.player_id for e in obj.sell_plan.players_to_sell]

                result = self.execution.buy(
                    league,
                    obj.player,
                    obj.recommended_bid,
                    obj.reason,
                    sell_plan_player_ids=sp_ids,
                )
                results.append(result)
                if result.success:
                    ctx.executed_trade_count += 1
                    self.daily_spend += obj.recommended_bid
                    ctx.flip_budget -= obj.recommended_bid
                    available_slots -= 1

            elif kind == "pair":
                # Don't sell a player unnecessarily if there are open slots —
                # the same target should appear as a plain buy candidate instead.
                if available_slots > 0:
                    continue
                if ctx.my_bid_amounts.get(obj.buy_player.id, 0) > 0:
                    console.print(
                        f"[dim]Skip pair {obj.buy_player.last_name} — already have active bid[/dim]"
                    )
                    continue
                net_cost = obj.recommended_bid - int(obj.sell_player.market_value * 0.95)
                if net_cost > ctx.flip_budget:
                    console.print(
                        f"[yellow]Cannot afford trade pair "
                        f"{obj.sell_player.last_name}→{obj.buy_player.last_name} "
                        f"(net €{net_cost:,} > €{ctx.flip_budget:,})[/yellow]"
                    )
                    continue

                console.print(
                    f"\n[cyan]Trade: sell {obj.sell_player.first_name} {obj.sell_player.last_name}"
                    f" → buy {obj.buy_player.first_name} {obj.buy_player.last_name}"
                    f" (EP +{obj.ep_gain:.1f})[/cyan]"
                )

                sell_result = self.execution.instant_sell(
                    league,
                    obj.sell_player,
                    f"Trade pair: making room for {obj.buy_player.last_name} (EP +{obj.ep_gain:.1f})",
                )
                results.append(sell_result)
                if not sell_result.success:
                    console.print("[red]Sell failed, skipping this trade pair[/red]")
                    continue

                buy_result = self.execution.buy(
                    league,
                    obj.buy_player,
                    obj.recommended_bid,
                    f"Trade pair: EP +{obj.ep_gain:.1f}",
                )
                results.append(buy_result)
                if buy_result.success:
                    ctx.executed_trade_count += 1
                    self.daily_spend += obj.recommended_bid
                    # Use the actual sell proceeds (from sell_result.price) rather
                    # than the estimated market value, to avoid budget drift.
                    actual_net_cost = obj.recommended_bid - sell_result.price
                    ctx.flip_budget -= actual_net_cost
                    # Trade pair: slot freed by sell, consumed by buy = net zero
                else:
                    console.print(
                        f"[bold red]⚠ WARNING: Sold {obj.sell_player.last_name} but failed to buy "
                        f"{obj.buy_player.last_name}[/bold red]"
                    )
                    # Sell freed a slot but buy failed — re-fetch actual state
                    # to avoid the counter drifting from reality.
                    try:
                        fresh = self.api.get_squad(league)
                        fresh_bids = self.api.get_my_bids(league)
                        current_squad_size = len(fresh)
                        active_bid_count = len(fresh_bids)
                        available_slots = 15 - current_squad_size - active_bid_count
                    except Exception:
                        available_slots += 1  # Fallback: optimistic increment

        # Execute profit flips with remaining slots
        if profit_flip_candidates and available_slots > 0:
            console.print(
                f"\n[bold cyan]💰 Profit Flips ({len(profit_flip_candidates)} candidates)[/bold cyan]"
            )
            for opp in profit_flip_candidates:
                if ctx.executed_trade_count >= effective_limit:
                    break
                if self.daily_spend >= self.max_daily_spend:
                    break
                if available_slots <= 0:
                    break
                if ctx.my_bid_amounts.get(opp.player.id, 0) > 0:
                    continue
                if opp.buy_price > ctx.flip_budget:
                    continue

                result = self.execution.buy(
                    league,
                    opp.player,
                    opp.buy_price,
                    f"Flip: +{opp.expected_appreciation:.0f}% in {opp.hold_days}d",
                )
                results.append(result)
                if result.success:
                    ctx.executed_trade_count += 1
                    self.daily_spend += opp.buy_price
                    ctx.flip_budget -= opp.buy_price
                    available_slots -= 1

        console.print(
            f"\n[green]✓ Executed {ctx.executed_trade_count} trade(s) this session[/green]"
        )
        return results

    @staticmethod
    def _sell_threshold_for_trend(trend_7d_pct: float | None) -> float:
        """Profit% threshold required before selling, based on price momentum.

        Rising players are held longer; falling players are sold earlier.
        """
        if trend_7d_pct is None:
            return 10.0
        if trend_7d_pct >= 5.0:
            return 15.0  # Rising fast — let it ride
        elif trend_7d_pct >= 2.0:
            return 12.0  # Rising — hold a bit longer
        elif trend_7d_pct >= -2.0:
            return 10.0  # Stable — default
        elif trend_7d_pct >= -5.0:
            return 7.0  # Slight decline — take profit sooner
        else:
            return 5.0  # Falling fast — take any profit

    @staticmethod
    def _has_position_replacement(
        position: str,
        buy_recs: list,
        trade_pairs: list,
        min_ep_gain: float,
    ) -> bool:
        """True if a queued buy or trade pair would actually replace this position.

        Loss-sells lock in a market-value loss, so they should only fire when
        the EP pipeline has a same-position upgrade big enough to justify the
        cost. The old global flag (``len(buy_recs) > 0 or len(trade_pairs) > 0``)
        triggered a defender's loss-sell when only forward buys were queued —
        the slot was freed but never filled, leaving cash idle.

        Field names differ between the two collections: ``BuyRecommendation``
        exposes ``player.position`` + ``marginal_ep_gain``; ``TradePair`` uses
        ``buy_player.position`` + ``ep_gain``.
        """
        for rec in buy_recs:
            if rec.player.position == position and rec.marginal_ep_gain >= min_ep_gain:
                return True
        for pair in trade_pairs:
            if pair.buy_player.position == position and pair.ep_gain >= min_ep_gain:
                return True
        return False

    @staticmethod
    def _can_loss_sell_with_replacement(trend_7d_pct: float | None) -> bool:
        """Loss-sell guard: don't realize a loss while the price is rebounding.

        The stop-loss and dead-weight branches realize a market-value loss
        when a buy candidate is available. That makes sense for a player
        whose price keeps sliding, but not for one already bouncing back —
        selling there just locks in a loss the recovery would erase.
        Returns False to defer the sell when the 7-day trend is a real
        rebound (≥+1%/wk); falls back to legacy behavior otherwise.
        """
        if trend_7d_pct is None:
            return True
        return trend_7d_pct < 1.0

    def run_profit_sell_phase(self, league, ctx: EPSessionContext) -> list[AutoTradeResult]:
        """Trend-aware profit sell monitoring.

        Uses formation-aware best-11 to protect true starters, and trend data
        to dynamically adjust sell thresholds. Only sells best-11 members when
        a replacement is lined up in the EP pipeline.
        """
        from .formation import select_best_eleven
        from .trader import Trader

        results = []
        console.print("\n[bold cyan]📈 Profit Sell Monitoring (trend-aware)[/bold cyan]")

        # Refresh squad — earlier phases (auction resolution, deferred sells)
        # may have changed the squad since ctx was built.
        squad = self.api.get_squad(league)
        if not squad:
            console.print("[dim]No squad loaded[/dim]")
            return results

        squad_scores = ctx.ep_result.get("squad_scores", [])
        if not squad_scores:
            console.print("[yellow]Could not score squad — skipping sell monitoring[/yellow]")
            return results

        # Formation-aware best-11: respects position minimums (1 GK, 3 DEF, 2 MID, 1 FW)
        # This matches what actually plays on matchday. A simple top-N-by-raw-EP sort
        # can wrongly "protect" a 2nd GK and leave a starting midfielder exposed.
        score_map = {s.player_id: s.expected_points for s in squad_scores}
        best_11 = select_best_eleven(squad, score_map)
        best_11_ids = {p.id for p in best_11}

        # Position-minimum protection: never sell if it would break formation.
        # A squad with 0 forwards loses -100 pts every matchday from empty slots.
        from .config import POSITION_MINIMUMS

        position_counts: dict[str, int] = {}
        for p in squad:
            position_counts[p.position] = position_counts.get(p.position, 0) + 1

        # Per-player same-position replacement check is applied below;
        # see `_has_position_replacement`. We pull the EP pipeline output
        # once here and pass it into each loss-sell decision.
        buy_recs = ctx.ep_result.get("buy_recs", [])
        trade_pairs = ctx.ep_result.get("trade_pairs", [])
        # Stop-loss locks in real cash loss against an EP gain that only
        # accumulates if the replacement auction is won; require 2x the
        # normal upgrade threshold to justify it. Mirrors the
        # `min_ep_upgrade * 2` heuristic used for starter swaps in
        # `decision.build_trade_pairs`.
        stop_loss_min_ep_gain = self.settings.min_ep_upgrade_threshold * 2
        any_buy_queued = bool(buy_recs) or bool(trade_pairs)

        # Build a Trader instance for trend lookups (uses cached data)
        trader = Trader(
            self.api,
            self.settings,
            bid_learner=self.learner,
            activity_feed_learner=self.activity_feed_learner,
        )

        # Cache the 7d trend per player — used in both the stop-loss branch
        # and the dead-weight loop below to keep loss-sells from firing while
        # a player's price is rebounding.
        trend_7d_by_id: dict[str, float | None] = {}

        sell_candidates = []
        for player in squad:
            if player.id in best_11_ids:
                continue

            if not player.buy_price or player.buy_price <= 0:
                continue

            # Hard block: never sell if it would drop a position below its
            # formation minimum. A squad with 0 FW loses -100 pts every matchday.
            pos_min = POSITION_MINIMUMS.get(player.position, 0)
            if position_counts.get(player.position, 0) <= pos_min:
                console.print(
                    f"[dim]Protected {player.last_name} ({player.position}) — "
                    f"at position minimum ({pos_min})[/dim]"
                )
                continue

            profit = player.market_value - player.buy_price
            profit_pct = (profit / player.buy_price) * 100

            # Get trend to determine dynamic threshold
            try:
                trend = trader.trend_service.get_trend(player.id, player.market_value, league.id)
                trend_7d = trend.trend_7d_pct
            except Exception:
                trend_7d = None
            trend_7d_by_id[player.id] = trend_7d

            sell_threshold = self._sell_threshold_for_trend(trend_7d)

            # Profit target hit (trend-adjusted)
            if profit_pct >= sell_threshold:
                trend_info = f", trend {trend_7d:+.1f}%/wk" if trend_7d is not None else ""
                sell_candidates.append(
                    (
                        player,
                        profit_pct,
                        f"Profit target ({sell_threshold:.0f}%) hit: +{profit_pct:.1f}% (€{profit:,}{trend_info})",
                    )
                )
            # Stop-loss: only if a same-position upgrade is queued AND the
            # price isn't already rebounding (locking in a loss while the
            # recovery is in progress is the worst possible exit).
            elif (
                profit_pct <= -5.0
                and self._has_position_replacement(
                    player.position, buy_recs, trade_pairs, stop_loss_min_ep_gain
                )
                and self._can_loss_sell_with_replacement(trend_7d)
            ):
                sell_candidates.append(
                    (
                        player,
                        profit_pct,
                        f"Stop-loss ({player.position} upgrade queued): "
                        f"{profit_pct:.1f}% (€{profit:,})",
                    )
                )

        # Dead-weight sell: surplus-position bench players (e.g. 2nd/3rd GK)
        # that block the squad from buying useful players.  Even at a small
        # loss, freeing the slot is worth it when the EP pipeline has buy
        # candidates waiting — the matchday points gained over a season
        # vastly outweigh a one-time market value loss.
        from .formation import _POSITION_MAX_STARTERS

        already_selling = {p.id for p, _, _ in sell_candidates}
        for player in squad:
            if player.id in best_11_ids or player.id in already_selling:
                continue
            if not player.buy_price or player.buy_price <= 0:
                continue

            max_starters = _POSITION_MAX_STARTERS.get(player.position, 3)
            if position_counts.get(player.position, 0) <= max_starters:
                continue  # Position not saturated — not dead weight

            profit = player.market_value - player.buy_price
            profit_pct = (profit / player.buy_price) * 100

            # Reuse the trend cached in the first loop. Every player that
            # reaches this point passed the same best_11 + buy_price gates
            # in loop 1 and either landed in `already_selling` (filtered
            # above) or had its trend cached, so a missing key would mean
            # an upstream filter changed — fetch defensively in that case.
            if player.id in trend_7d_by_id:
                trend_7d = trend_7d_by_id[player.id]
            else:
                try:
                    trend_7d = trader.trend_service.get_trend(
                        player.id, player.market_value, league.id
                    ).trend_7d_pct
                except Exception:
                    trend_7d = None

            # Always sell dead weight at a profit. At a loss, sell when
            # *any* buy is queued and the price isn't rebounding. Unlike
            # the stop-loss branch above we deliberately don't require a
            # same-position match here: a position-saturated player can
            # never enter best-11 in any formation, so the slot itself
            # is the asset — freeing it for a buy of any position is a
            # net win, even at a small loss. (Without this release valve
            # a 5th GK at -3% with no GK on the market sits forever.)
            if profit_pct >= 0 or (
                any_buy_queued and self._can_loss_sell_with_replacement(trend_7d)
            ):
                sell_candidates.append(
                    (
                        player,
                        profit_pct,
                        f"Dead weight ({player.position} surplus): "
                        f"{profit_pct:+.1f}% (€{profit:,}), freeing slot",
                    )
                )

        if not sell_candidates:
            console.print("[dim]No players meet sell criteria[/dim]")
            return results

        sell_candidates.sort(key=lambda x: x[1], reverse=True)
        console.print(f"[green]Found {len(sell_candidates)} player(s) to sell[/green]")

        for player, profit_pct, reason in sell_candidates:
            full_reason = (
                f"{reason} (bought €{player.buy_price:,}, "
                f"now €{player.market_value:,}, {profit_pct:+.1f}%)"
            )
            results.append(self.execution.instant_sell(league, player, full_reason))

        return results

    def run_full_session(self, league) -> AutoTradeSession:
        """Run a complete automated trading session.

        New unified flow:
        1. Sync activity feed (competitive intelligence)
        2. Build session context (single EP pipeline call + trends + matchday timing)
        3. If locked (0-1 days to match) → set lineup only
        4. Trend-aware profit selling
        5. Squad optimization (budget/size safety)
        6. Unified trade phase (trade pairs compete with plain buys, ranked by EP)
        7. Set optimal lineup
        """
        start_time = time.time()

        console.print(f"\n{'=' * 70}")
        console.print(
            f"[bold]Automated Trading Session - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/bold]"
        )
        if self.dry_run:
            console.print("[yellow]DRY RUN MODE - No trades will be executed[/yellow]")
        console.print(f"{'=' * 70}")

        # Step 0: Sync activity feed for competitive intelligence
        try:
            console.print("\n[dim]Syncing league activity feed...[/dim]")
            activities = self.api.client.get_activities_feed(league.id, start=0)
            stats = self.activity_feed_learner.process_activity_feed(
                activities, api_client=self.api.client
            )
            if stats["transfers_new"] > 0 or stats["market_values_new"] > 0:
                console.print(
                    f"[dim]✓ Synced: {stats['transfers_new']} new transfers, "
                    f"{stats['market_values_new']} new market values[/dim]"
                )
        except Exception as e:
            console.print(f"[yellow]Warning: Could not sync activity feed: {e}[/yellow]")

        sell_results: list[AutoTradeResult] = []
        trade_results: list[AutoTradeResult] = []
        errors: list[str] = []

        # Step 1: Reconcile pending bids (won/lost) + execute deferred sell plans
        try:
            squad = self.api.get_squad(league)
            bids = self.api.get_my_bids(league)
            deferred_sell_ids = self.tracker.resolve_auctions(
                squad_ids={p.id for p in squad},
                active_bid_ids={p.id for p in bids},
            )
            # Execute any deferred sell plans from bids we won (buy-first-sell-after).
            if deferred_sell_ids:
                console.print(
                    f"[cyan]Executing deferred sell plan for {len(deferred_sell_ids)} player(s)[/cyan]"
                )
                for sell_id in deferred_sell_ids:
                    sell_player = next((p for p in squad if p.id == sell_id), None)
                    if sell_player:
                        result = self.execution.instant_sell(
                            league,
                            sell_player,
                            "Deferred sell plan — recovering budget after winning auction",
                        )
                        sell_results.append(result)
                    else:
                        console.print(
                            f"[yellow]Deferred sell target {sell_id} not in squad (already sold?)[/yellow]"
                        )
        except Exception as e:
            console.print(f"[yellow]Auction resolution failed: {e}[/yellow]")

        # Step 2: Build session context (single EP pipeline + trends + matchday phase)
        try:
            ctx = self._build_session_context(league)
        except Exception as e:
            error_msg = f"EP pipeline failed: {e!s}"
            console.print(f"[red]{error_msg}[/red]")
            errors.append(error_msg)
            # Fall back to just setting lineup
            self._set_optimal_lineup(league, errors)
            return AutoTradeSession(
                start_time=start_time,
                end_time=time.time(),
                profit_trades=[],
                lineup_trades=[],
                errors=errors,
                total_spent=0,
                total_earned=0,
                net_change=0,
            )

        # Step 3: If locked (match imminent), just set lineup and exit
        if ctx.matchday_phase.phase == "locked":
            console.print(
                f"[yellow]Match imminent ({ctx.matchday_phase.days_until_match}d) "
                f"— setting lineup only, no trading[/yellow]"
            )
            self._set_optimal_lineup(league, errors, squad_scores=ctx.ep_result.get("squad_scores"))
            return AutoTradeSession(
                start_time=start_time,
                end_time=time.time(),
                profit_trades=[],
                lineup_trades=[],
                errors=errors,
                total_spent=0,
                total_earned=0,
                net_change=0,
            )

        # Step 4: Trend-aware profit selling
        try:
            sell_results = self.run_profit_sell_phase(league, ctx)
        except Exception as e:
            error_msg = f"Sell monitoring error: {e!s}"
            console.print(f"[red]{error_msg}[/red]")
            errors.append(error_msg)

        # Step 5: Squad Optimization (budget/size safety)
        console.print("\n[bold cyan]🎯 Squad Optimization[/bold cyan]")
        try:
            optimization_sells = self.optimize_and_execute_squad(league)
            sell_results.extend(optimization_sells)
        except Exception as e:
            error_msg = f"Squad optimization error: {e!s}"
            console.print(f"[red]{error_msg}[/red]")
            errors.append(error_msg)

        # Step 6: Bid compliance + quality check
        self._reset_daily_limits_if_needed()
        if self.daily_spend < self.max_daily_spend:
            try:
                from .bid_evaluator import BidEvaluator
                from .league_compliance import LeagueComplianceChecker
                from .trader import Trader

                trader = Trader(
                    self.api,
                    self.settings,
                    bid_learner=self.learner,
                    activity_feed_learner=self.activity_feed_learner,
                )

                market = self.api.get_market(league)
                kickbase_market = [p for p in market if p.is_kickbase_seller()]
                player_trends = {
                    p.id: trader.trend_service.get_trend(p.id, p.market_value, league.id).to_dict()
                    for p in kickbase_market[:50]
                }

                compliance_checker = LeagueComplianceChecker(self.api, self.settings)
                adjusted, canceled = compliance_checker.run_bid_compliance_check(
                    league, player_trends=player_trends, auto_resolve=True, dry_run=self.dry_run
                )
                if adjusted > 0 or canceled > 0:
                    console.print(
                        f"[cyan]Bid compliance: {adjusted} adjusted, {canceled} canceled[/cyan]"
                    )

                bid_evaluator = BidEvaluator(self.api, self.settings)
                bid_evaluations = bid_evaluator.evaluate_active_bids(
                    league, player_trends=player_trends, for_profit=True
                )
                if bid_evaluations:
                    bid_evaluator.display_bid_evaluations(bid_evaluations)
                    canceled_count = bid_evaluator.cancel_bad_bids(
                        league, bid_evaluations, dry_run=self.dry_run
                    )
                    if canceled_count > 0:
                        console.print(
                            f"[yellow]Canceled {canceled_count} bid(s) that no longer make sense[/yellow]"
                        )
            except Exception as e:
                console.print(f"[yellow]Bid compliance check failed: {e}[/yellow]")

        # Step 7: Unified trade phase (EP buys + trade pairs + profit flips)
        try:
            trade_results = self.run_unified_trade_phase(league, ctx)
        except Exception as e:
            error_msg = f"Trading error: {e!s}"
            console.print(f"[red]{error_msg}[/red]")
            errors.append(error_msg)

        # Step 8: Set optimal lineup using EP pipeline scores from the session.
        # Players acquired mid-session (if any) fall back to the legacy
        # calculator inside _set_optimal_lineup.
        self._set_optimal_lineup(league, errors, squad_scores=ctx.ep_result.get("squad_scores"))

        # Calculate totals
        all_results = sell_results + trade_results
        total_spent = sum(r.price for r in all_results if r.action == "BUY" and r.success)
        total_earned = sum(r.price for r in all_results if r.action == "SELL" and r.success)
        net_change = total_earned - total_spent

        end_time = time.time()

        # Print summary
        console.print(f"\n{'=' * 70}")
        console.print("[bold]Session Summary[/bold]")
        console.print(f"{'=' * 70}")
        console.print(f"Duration: {end_time - start_time:.1f}s")
        console.print(f"Phase: {ctx.matchday_phase.phase} ({ctx.matchday_phase.reason})")
        console.print(
            f"Sells: {len([r for r in sell_results if r.success and r.action == 'SELL'])}"
        )
        console.print(
            f"Trades: {len([r for r in trade_results if r.success])}/{len(trade_results)}"
        )
        console.print(f"Total spent: €{total_spent:,}")
        console.print(f"Total earned: €{total_earned:,}")
        net_color = "green" if net_change >= 0 else "red"
        console.print(f"Net change: [{net_color}]€{net_change:,}[/{net_color}]")

        if errors:
            console.print(f"\n[red]Errors: {len(errors)}[/red]")
            for err in errors:
                console.print(f"[red]  • {err}[/red]")

        return AutoTradeSession(
            start_time=start_time,
            end_time=end_time,
            profit_trades=trade_results,
            lineup_trades=sell_results,
            errors=errors,
            total_spent=total_spent,
            total_earned=total_earned,
            net_change=net_change,
        )

    def _set_optimal_lineup(
        self,
        league,
        errors: list[str],
        squad_scores: list | None = None,
    ):
        """Calculate and set the optimal starting 11 via API.

        Prefers the new EP scoring pipeline (via *squad_scores* when the caller
        already computed them) so the lineup benefits from DGW multipliers,
        injury penalties, 5-fixture SOS, and position-weighted scoring. Falls
        back to the legacy calculator per-player only when scores are missing
        (e.g. EP pipeline failed, or a player was just bought mid-session).
        """
        from .formation import get_formation_string, order_for_lineup, select_best_eleven

        console.print("\n[bold cyan]📋 Setting Optimal Lineup[/bold cyan]")

        try:
            squad = self.api.get_squad(league)
            if not squad or len(squad) < 11:
                console.print("[yellow]Not enough players to set lineup[/yellow]")
                return

            # Build ep_scores from the new pipeline when available; fall back
            # to the legacy per-player calculator only for uncovered squad
            # members or when the caller didn't provide scores.
            ep_scores: dict[str, float] = {}
            if squad_scores:
                ep_scores = {s.player_id: s.expected_points for s in squad_scores}

            missing = [p for p in squad if p.id not in ep_scores]
            if missing:
                for player in missing:
                    ep_scores[player.id] = self._legacy_expected_points(league, player)

            # Select best 11, order by position for API (GK→DEF→MID→FWD)
            best_eleven = select_best_eleven(squad, ep_scores)
            ordered = order_for_lineup(best_eleven)
            formation = get_formation_string(ordered)
            player_ids = [p.id for p in ordered]

            names = [
                f"{p.first_name[0]}. {p.last_name}" if p.first_name else p.last_name
                for p in ordered
            ]
            console.print(f"[dim]Formation: {formation} | {', '.join(names)}[/dim]")

            if self.dry_run:
                console.print("[yellow]DRY RUN - Lineup not applied[/yellow]")
                return

            self.api.set_lineup(league, formation, player_ids)
            console.print("[green]✓ Lineup set successfully[/green]")

        except Exception as e:
            error_msg = f"Set lineup error: {e!s}"
            console.print(f"[red]{error_msg}[/red]")
            errors.append(error_msg)

    def _legacy_expected_points(self, league, player) -> float:
        """Fallback per-player EP using the legacy calculator.

        Only used when the new EP pipeline didn't score this player — e.g. a
        mid-session purchase, or the pipeline failed upstream. Kept narrow so
        the legacy path doesn't drift back into the main lineup flow.
        """
        from .expected_points import calculate_expected_points
        from .value_history import ValueHistoryCache

        try:
            history_cache = ValueHistoryCache()
            perf_data = history_cache.get_cached_performance(
                player_id=player.id, league_id=league.id, max_age_hours=24
            )
            if not perf_data:
                perf_data = self.api.client.get_player_performance(league.id, player.id)
                if perf_data:
                    history_cache.cache_performance(
                        player_id=player.id, league_id=league.id, data=perf_data
                    )
            ep = calculate_expected_points(player=player, performance_data=perf_data)
            return ep.expected_points
        except Exception:
            return 0.0

    def optimize_and_execute_squad(self, league) -> list[AutoTradeResult]:
        """Run squad optimization and execute any forced sales.

        Returns a list of AutoTradeResult for actual sells executed (not
        hypothetical). An empty list means no sells were needed.
        """
        from .squad_optimizer import SquadOptimizer
        from .trader import Trader

        results: list[AutoTradeResult] = []

        trader = Trader(
            self.api,
            self.settings,
            bid_learner=self.learner,
            activity_feed_learner=self.activity_feed_learner,
        )
        optimization = trader.optimize_squad_for_gameday(league)

        if not optimization:
            return results

        squad = self.api.get_squad(league)
        player_values = {p.id: float(p.average_points or 0) for p in squad}

        optimizer = SquadOptimizer(min_squad_size=11, max_squad_size=15)
        optimizer.display_optimization(optimization, player_values=player_values)

        if optimization.players_to_sell and not optimization.is_gameday_ready:
            console.print(
                f"\n[yellow]⚠️  Budget negative, selling "
                f"{len(optimization.players_to_sell)} player(s)...[/yellow]"
            )
            # Execute via our ExecutionService so dry_run, tracking, and real
            # success/failure results all flow through the same path.
            for player in optimization.players_to_sell:
                result = self.execution.instant_sell(
                    league,
                    player,
                    "Squad optimization — forced sell to recover budget",
                )
                results.append(result)

        return results
