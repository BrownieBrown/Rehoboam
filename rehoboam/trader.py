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

import logging
from datetime import datetime, timedelta, timezone
from typing import cast

from rich.console import Console

from .api import KickbaseAPI
from .bidding_strategy import (
    BID_FULL_COMMIT_GAIN,
    TIER_MUST_HAVE,
    TIER_SOLID_UPGRADE,
    TIER_STRONG_UPGRADE,
    SmartBidding,
)
from .config import INSTANT_SELL_PCT, Settings
from .formation import can_fill_starting_eleven
from .kickbase_client import League
from .kickoff import NextKickoff, fixtures_from_myeleven, next_fixture_from_matchdays
from .matchup_analyzer import MatchupAnalyzer
from .services.emergency_window import emergency_fill_due
from .services.trend_service import TrendService
from .value_history import ValueHistoryCache

logger = logging.getLogger(__name__)

console = Console()


#: "The caller did not say" for `days_until_match` -- distinct from None,
#: which is a real answer (the schedule is unknown) and fails toward the
#: emergency. A missing argument asks the schedule instead.
_UNSET: object = object()


def _determine_emergency(squad: list) -> tuple[bool, str]:
    """Is the squad in a lineup emergency? Position-aware, not headcount-based.

    Emergency means "cannot field a legal starting 11 from the available
    squad" — too few players overall, or too few at some position (e.g. 0
    forwards = an empty slot = -100 pts). This is deliberately NOT a
    comparison against ``Settings.min_squad_size``: that value is a sell
    floor (never sell below 13), and a raw-headcount emergency trigger
    ("squad_size < floor") fires on any squad below the floor regardless of
    whether it can actually field eleven — which, against last season's real
    session data, meant 93% of sessions (squad at 11 or 12, the normal
    range) would have tripped the "buy almost anything" emergency path.
    ``can_fill_starting_eleven`` asks the real question directly.

    Availability caveat: ``squad`` here is whatever the caller passes in —
    typically straight from ``api.get_squad()``, whose ``Player`` objects
    carry no injury/availability status field (only ``MarketPlayer`` has
    ``status``, populated from a different endpoint). So this only catches
    headcount and position-shape emergencies (too few total, or too few at a
    position) — not "12 well-shaped players, one of them injured". Real
    availability filtering needs per-player status/lineup data that isn't
    fetched this early in the pipeline; that arrives with the week-2 scorer.

    Returns:
        ``(is_emergency, reason)`` — reason is empty when not an emergency.
    """
    fieldability = can_fill_starting_eleven(squad)
    if fieldability["ok"]:
        return False, ""
    return True, fieldability["reason"]


#: A Bundesliga round spans at most Friday to Monday. A fixture older than this
#: belongs to a PREVIOUS round, so it must not be read as one in progress —
#: otherwise a single un-rolled date would unlock trading permanently.
MATCHDAY_ROUND_SPAN_DAYS = 4


def matchday_in_progress(
    fixtures: "list[datetime]",
    now: "datetime",
    *,
    span_days: int = MATCHDAY_ROUND_SPAN_DAYS,
) -> bool:
    """Has the current matchday already kicked off? (REH-110)

    True when any of our players' fixtures for this round is in the recent
    past. Kickbase locks the fielded eleven at the round's first kickoff and
    every squad change lands on the FOLLOWING matchday, so once that instant
    passes there is no lineup left to protect and trading is free.

    `get_days_until_match` cannot answer this: it takes `min()` of the fixtures
    still to come, so on the Saturday of a round that began Friday evening it
    reports 0 days and the bot locks itself out. Measured live 2026-08-29,
    that cost the Saturday and Sunday of every matchday — two tradeable days a
    week.

    All fixtures in the past also returns True: that is the gap between rounds,
    the safest moment to trade rather than the least.

    Pure, so the round-boundary reasoning is testable without an API.
    """
    cutoff = now - timedelta(days=span_days)
    return any(cutoff <= f < now for f in fixtures)


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
            # Real-points marginal-gain bands, overridable from `.env` so they
            # can be re-tuned mid-season once the live market gives evidence.
            tier_must_have=getattr(settings, "bid_tier_must_have", TIER_MUST_HAVE),
            tier_strong_upgrade=getattr(settings, "bid_tier_strong_upgrade", TIER_STRONG_UPGRADE),
            tier_solid_upgrade=getattr(settings, "bid_tier_solid_upgrade", TIER_SOLID_UPGRADE),
            full_commit_gain=getattr(settings, "bid_full_commit_gain", BID_FULL_COMMIT_GAIN),
            # REH-99: the same ceiling `safety_gate.check_buy` enforces. Passing
            # it here is the fix for the bug where the bidder used its own 20%/30%
            # caps and Telegram offered buys the gate would then refuse.
            ceiling_policy=settings.bid_ceiling_policy(),
        )

    # ------------------------------------------------------------------
    # Matchday timing
    # ------------------------------------------------------------------

    def next_kickoff(self, league, *, now: datetime | None = None) -> NextKickoff:
        """The next kickoff, schedule-first with `/myeleven` as the cross-check.

        The competition schedule (`get_competition_matchdays`) lists the
        whole season's fixtures and never blanks out between matchdays, so
        it is the primary source. `/myeleven` only carries fixtures for
        players currently on our squad -- it can go empty (new season, squad
        rebuilt after a position was sold off) even though the schedule
        knows perfectly well when the next round kicks off -- so it is kept
        as a fallback when the schedule is unreachable, and always as a
        cross-check either way.

        Each source is fetched in its own try/except: a failure there means
        that source contributes nothing, it does not take the other source
        down with it. `matchday_in_progress` is read from `/myeleven`'s
        fixtures exactly as before this method existed -- the schedule lists
        every fixture in the competition, not just ours, so it cannot answer
        "has OUR round started" the way `/myeleven` can.
        """
        now = now or datetime.now(tz=timezone.utc)

        schedule_at: datetime | None = None
        schedule_fixture = None
        try:
            schedule_payload = self.api.get_competition_matchdays()
            schedule_fixture = next_fixture_from_matchdays(schedule_payload, now)
            schedule_at = schedule_fixture.at if schedule_fixture else None
        except Exception:
            logger.warning("next-kickoff: schedule fetch/parse failed", exc_info=True)

        myeleven_fixtures: list[datetime] = []
        myeleven_fetched = False
        try:
            starting_eleven = self.api.get_starting_eleven(league)
            if not isinstance(starting_eleven, dict):
                logger.warning(
                    "next-match: unexpected /myeleven payload type=%s",
                    type(starting_eleven).__name__,
                )
            else:
                myeleven_fetched = True
                myeleven_fixtures = fixtures_from_myeleven(starting_eleven)
        except Exception:
            logger.warning("next-match: /myeleven fetch failed", exc_info=True)

        upcoming_myeleven = [d for d in myeleven_fixtures if d >= now]
        myeleven_at = min(upcoming_myeleven) if upcoming_myeleven else None
        if myeleven_fetched and myeleven_at is None:
            logger.warning(
                "next-match: no upcoming fixture found in /myeleven "
                "(parsed=%d date(s)) - the cross-check has nothing to offer",
                len(myeleven_fixtures),
            )

        if schedule_at is not None:
            at, source, cross_check = schedule_at, "schedule", myeleven_at
        elif myeleven_at is not None:
            at, source, cross_check = myeleven_at, "myeleven", None
        else:
            at, source, cross_check = None, "none", None

        in_progress = matchday_in_progress(myeleven_fixtures, now)

        logger.info("next-kickoff at=%s source=%s cross_check=%s", at, source, cross_check)
        if schedule_at is not None and myeleven_at is not None:
            disagreement_hours = abs((schedule_at - myeleven_at).total_seconds()) / 3600
            if disagreement_hours > 24:
                logger.warning("next-kickoff sources disagree by %.0f h", disagreement_hours)

        return NextKickoff(
            at=at,
            source=source,
            cross_check=cross_check,
            matchday_in_progress=in_progress,
            day_number=schedule_fixture.day_number if source == "schedule" else None,
        )

    def get_days_until_match(self, league, *, now: datetime | None = None) -> int | None:
        """Return days until the next match, or None if genuinely unknown.

        Uses timezone-aware datetime comparison to avoid the naive/aware
        TypeError that silently broke matchday-phase detection in early
        revisions of this code.

        Delegates to `next_kickoff`, which reads the competition schedule
        first and falls back to `/myeleven` -- see that method for why. This
        method keeps its own return contract (days, floored at 0, or None
        when neither source has an answer) and its side effect
        (`self._last_matchday_in_progress`) so every existing caller stays
        unchanged.
        """
        now = now or datetime.now(tz=timezone.utc)
        nk = self.next_kickoff(league, now=now)
        self._last_matchday_in_progress = nk.matchday_in_progress
        if nk.at is None:
            return None
        return max((nk.at - now).days, 0)

    # ------------------------------------------------------------------
    # EP pipeline
    # ------------------------------------------------------------------

    def _build_pacing_context(self, squad_size: int, my_bids: list, current_budget: int):
        """The REH-85 reserve for this session, or None when pacing is off.

        Built once per run rather than per candidate: the median move price is
        a league-level measurement, and recomputing it inside the candidate
        loop would hit the DB once per listing for an identical answer.

        Returns None — pacing disabled — rather than raising when the learning
        DB cannot be read. Pacing is a spending restraint, and the established
        rule in this codebase is that a learning-side failure never blocks the
        EP pipeline. A restraint that cannot be measured is not applied.
        """
        from .services.pacing import (
            PacingContext,
            available_squad_slots,
            capital_reserve,
            median_move_price,
        )

        if not getattr(self.settings, "pacing_enabled", True):
            return None
        if self.bid_learner is None:
            return None
        try:
            prices = self.bid_learner.recent_buy_prices(
                window_days=int(self.settings.pacing_window_days)
            )
        except Exception:
            logger.exception("pacing: could not read recent buy prices — pacing disabled")
            return None

        median_move = median_move_price(
            prices, floor_eur=int(self.settings.pacing_median_floor_eur)
        )
        open_offers = sum(int(getattr(b, "user_offer_price", 0) or 0) for b in my_bids)
        slots_to_fill = available_squad_slots(squad_size, len(my_bids))
        max_reserve_fraction = float(getattr(self.settings, "pacing_max_reserve_fraction", 0.5))
        min_spendable_moves = float(getattr(self.settings, "pacing_min_spendable_moves", 1.0))
        reserve = capital_reserve(
            slots_to_fill=slots_to_fill,
            in_season_min_moves=int(self.settings.pacing_in_season_min_moves),
            median_move=median_move,
            budget=int(current_budget),
            max_reserve_fraction=max_reserve_fraction,
            min_spendable_moves=min_spendable_moves,
        )
        logger.info(
            "pacing session median_move=%d slots_to_fill=%d reserve=%d open_offers=%d "
            "n_prices=%d budget=%d max_fraction=%.2f min_spendable_moves=%.2f",
            median_move,
            slots_to_fill,
            reserve,
            open_offers,
            len(prices),
            int(current_budget),
            max_reserve_fraction,
            min_spendable_moves,
        )
        return PacingContext(reserve=reserve, open_offers=open_offers)

    def get_ep_recommendations(
        self, league: League, *, days_until_match: int | None | object = _UNSET
    ) -> dict:
        """Run the EP scoring pipeline and return structured recommendations.

        Returns a dict with keys:
            buy_recs, trade_pairs, sell_recs, squad_scores, lineup_map,
            budget, squad_size, squad_players, market_players,
            competitor_player_ids
        """
        from .scoring.collector import DataCollector
        from .scoring.decision import DecisionEngine
        from .scoring.v2.adapter import score_player_v2

        # --- 1. Fetch squad and market ---
        squad = self.api.get_squad(league)
        squad_size = len(squad)

        # Emergency mode — see _determine_emergency for why this is a
        # position-aware fieldability check, not a headcount comparison.
        #
        # And a day-count check on top (2026-09-22): the emergency relaxes
        # every buy filter below (EP floor 10, no target bar, no upgrade
        # threshold, no pacing, top 8), which is the price of the LAST day
        # before kickoff, not of a short squad seventeen days out. The session
        # normally hands the day count in, so the phase decision and this one
        # read the same kickoff; a caller without one asks the schedule.
        days_to_match: int | None = (
            self.get_days_until_match(league)
            if days_until_match is _UNSET
            else cast("int | None", days_until_match)
        )
        squad_short, shortfall_reason = _determine_emergency(squad)
        is_emergency = squad_short and emergency_fill_due(
            days_to_match, window_days=self.settings.emergency_fill_days
        )
        if is_emergency:
            console.print(f"[bold red]⚠ FORMATION EMERGENCY — {shortfall_reason}[/bold red]")
        elif squad_short:
            console.print(
                f"[yellow]Squad short — {shortfall_reason}; kickoff in "
                f"{days_to_match}d, so the ordinary buy filters apply and the "
                f"emergency fill waits for the last "
                f"{self.settings.emergency_fill_days}d[/yellow]"
            )

        market_players_list = self.api.get_market(league)
        market_payload = getattr(self.api, "last_market_payload", None)
        kickbase_market = [p for p in market_players_list if p.is_kickbase_seller()]

        team_info = self.api.get_team_info(league)
        current_budget = team_info.get("budget", 0)

        # Open bids are needed twice over for REH-85: they are euros already
        # committed, and Kickbase counts them toward the 15-player cap.
        #
        # The whole block — the API call AND _build_pacing_context — shares
        # one try/except. _build_pacing_context's docstring promises it fails
        # open (returns None instead of raising), but that promise only
        # covers its own body; the call to it was outside any try, so a
        # malformed bid payload (e.g. a non-numeric user_offer_price) could
        # abort the whole EP pipeline instead of merely disabling pacing.
        # Best-effort learning: a learning-side failure must never block it.
        try:
            my_bids = self.api.get_my_bids(league)
            # An empty lineup slot is -100 pts at kickoff, which outranks
            # the pacing reserve — never cap spending during a formation
            # emergency (see _determine_emergency above).
            pacing = (
                None
                if is_emergency
                else self._build_pacing_context(squad_size, my_bids, int(current_budget))
            )
        except Exception:
            logger.exception("pacing: could not read open bids — pacing disabled this run")
            my_bids = None
            pacing = None

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

        # --- 2b. Competitor scouting + rank snapshot (best-effort) ---
        # /ranking returns the full league standings — rank, points, team
        # value per manager. Historically this loop only extracted manager
        # IDs to drive squad scouting; REH-24 also persists the rank/points/
        # team_value fields so we can measure goals 3, 4, 5 over time.
        competitor_player_ids: set[str] = set()
        ranking_payload: dict | None = None
        competitor_squads: dict[str, list] = {}
        try:
            ranking = self.api.get_league_ranking(league)
            ranking_payload = ranking
            managers = ranking.get("it", ranking.get("us", []))
            # `day` may legitimately be None (pre-season) or absent. int(None)
            # would raise inside the outer try/except and lose the
            # competitor_player_ids set that was already built — None-guard
            # explicitly so the surrounding scouting always succeeds.
            _day_raw = ranking.get("day")
            day_number = int(_day_raw) if _day_raw is not None else 0
            my_id = self.api.user.id

            rank_rows: list[dict] = []
            # REH-38: per-manager dashboard (`prft`, `mdw`) + transfer
            # history. Only collected when a learner is attached — skipping
            # the extra HTTP calls when nothing would be persisted.
            profile_rows: list[dict] = []
            transfer_rows: list[dict] = []
            snapshot_at = datetime.now(tz=timezone.utc).timestamp()
            for mgr in managers:
                mgr_id = mgr.get("i", mgr.get("id", ""))
                if not mgr_id:
                    continue
                rank_rows.append(
                    {
                        "snapshot_at": snapshot_at,
                        "league_id": league.id,
                        "manager_id": str(mgr_id),
                        "day_number": day_number,
                        "rank_overall": mgr.get("spl"),
                        "rank_matchday": mgr.get("mdpl"),
                        "total_points": mgr.get("sp"),
                        "matchday_points": mgr.get("mdp"),
                        "team_value": mgr.get("tv"),
                        "is_self": mgr_id == my_id,
                    }
                )
                if mgr_id != my_id:
                    try:
                        mgr_squad = self.api.get_manager_squad(league, mgr_id)
                        competitor_squads[str(mgr_id)] = list(mgr_squad.get("it", []) or [])
                        for p in mgr_squad.get("it", []):
                            pid = p.get("i", p.get("id", ""))
                            if pid:
                                competitor_player_ids.add(pid)
                    except Exception:
                        pass

                # REH-38: dashboard pull for prft + mdw. Done for every
                # manager (including self — `prft` is not exposed in
                # /ranking, so we cannot get our own P&L from the loop's
                # ranking response). Best-effort per-manager.
                if self.bid_learner is not None:
                    try:
                        dash = self.api.get_manager_dashboard(league, str(mgr_id))
                        prft = dash.get("prft")
                        if prft is not None:
                            profile_rows.append(
                                {
                                    "snapshot_at": snapshot_at,
                                    "league_id": league.id,
                                    "manager_id": str(mgr_id),
                                    "transfer_pnl": int(prft),
                                    "matchday_wins": dash.get("mdw"),
                                    "is_self": mgr_id == my_id,
                                }
                            )
                    except Exception:
                        pass

                    # REH-38: transfer history page 0 (latest 25 trades).
                    # We run 2x/day; 25 trades in a 12h window is highly
                    # improbable for a single manager, so page 0 catches
                    # every new transfer in steady state. Older history
                    # (start=25, 50, ...) is a backfill concern handled
                    # outside the session loop.
                    try:
                        th = self.api.get_manager_transfer_history(league, str(mgr_id))
                        for t in th.get("it", []):
                            pid = t.get("pi", "")
                            tdt = t.get("dt", "")
                            if not pid or not tdt:
                                continue
                            transfer_rows.append(
                                {
                                    "league_id": league.id,
                                    "manager_id": str(mgr_id),
                                    "transfer_dt": tdt,
                                    "player_id": str(pid),
                                    "player_name": t.get("pn", ""),
                                    "transfer_type": t.get("tty"),
                                    "transfer_price": t.get("trp"),
                                }
                            )
                    except Exception:
                        pass

            if self.bid_learner is not None and rank_rows:
                try:
                    self.bid_learner.record_league_rank_snapshot(rank_rows)
                except Exception:
                    # Learning side effects must never block the EP pipeline.
                    pass
            if self.bid_learner is not None and profile_rows:
                try:
                    self.bid_learner.record_manager_profile_snapshot(profile_rows)
                except Exception:
                    pass
            if self.bid_learner is not None and transfer_rows:
                try:
                    self.bid_learner.record_manager_transfers(transfer_rows)
                except Exception:
                    pass

            # REH-25: capture the actual fielded lineup + total points for
            # the most recently completed matchday, once. Skipped if the row
            # already exists or if the matchday isn't fully finished yet.
            if (
                self.bid_learner is not None
                and day_number > 0
                and not self.bid_learner.has_matchday_lineup_result(league.id, day_number)
            ):
                try:
                    tc = self.api.get_user_teamcenter(league, day_number=day_number)
                    lp = tc.get("lp") or []
                    if lp and all(item.get("mst") == 2 for item in lp):
                        total_points = sum(int(item.get("p", 0)) for item in lp)
                        first_md = lp[0].get("md", "")
                        player_ids = [str(item.get("i", "")) for item in lp]
                        lineup_count = int(tc.get("clpc", len(lp)))
                        self.bid_learner.record_matchday_lineup_result(
                            league_id=league.id,
                            day_number=day_number,
                            matchday_date=first_md,
                            total_points=total_points,
                            lineup_player_ids=player_ids,
                            lineup_count=lineup_count,
                        )
                except Exception:
                    # Best-effort — never block the EP pipeline.
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

        # REH-55: scoring runs through the fitted v2 models, which return real
        # Kickbase matchday points rather than the old 0-100 index. REH-20's
        # per-position calibration multiplier is deliberately NOT applied — it
        # was fitted against that index, so on real points it would correct a
        # bias that no longer exists.

        # REH-26 + REH-40: collect daily MV rows for both squad AND market
        # players in a single mv_rows list, persisted after both loops via
        # one bulk INSERT. trend_service.get_history uses the 24h-cached
        # data already fetched for trend analysis — no extra HTTP traffic.
        # Market coverage (REH-40) gives REH-32 / REH-33 calibrations a
        # populated trajectory for any future flip without further backfill.
        mv_rows: list[dict] = []
        snapshot_at = datetime.now(tz=timezone.utc).timestamp()

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
                    score_player_v2(
                        data,
                        max_status_age_days=self.settings.max_status_age_days,
                        uncertain_start_multiplier=getattr(
                            self.settings, "uncertain_start_multiplier", 0.5
                        ),
                        stale_shrinkage_k=getattr(
                            self.settings, "stale_availability_shrinkage_k", 20.0
                        ),
                    )
                )
                market_player_map[player.id] = player

                try:
                    mvh = self.trend_service.get_history(player.id, league.id)
                    recent = mvh.points[-30:] if mvh.points else []
                    peak_30d = max((p.value for p in recent), default=None)
                    trough_30d = min((p.value for p in recent), default=None)
                    mv_rows.append(
                        {
                            "player_id": player.id,
                            "snapshot_at": snapshot_at,
                            "market_value": player.market_value,
                            "peak_mv_30d": peak_30d,
                            "trough_mv_30d": trough_30d,
                        }
                    )
                except Exception:
                    pass
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
                    score_player_v2(
                        data,
                        max_status_age_days=self.settings.max_status_age_days,
                        uncertain_start_multiplier=getattr(
                            self.settings, "uncertain_start_multiplier", 0.5
                        ),
                        stale_shrinkage_k=getattr(
                            self.settings, "stale_availability_shrinkage_k", 20.0
                        ),
                    )
                )
                squad_player_map[player.id] = player

                try:
                    mvh = self.trend_service.get_history(player.id, league.id)
                    recent = mvh.points[-30:] if mvh.points else []
                    peak_30d = max((p.value for p in recent), default=None)
                    trough_30d = min((p.value for p in recent), default=None)
                    mv_rows.append(
                        {
                            "player_id": player.id,
                            "snapshot_at": snapshot_at,
                            "market_value": player.market_value,
                            "peak_mv_30d": peak_30d,
                            "trough_mv_30d": trough_30d,
                        }
                    )
                except Exception:
                    # MV-history fetch is best-effort; keep scoring this
                    # player even if persistence fails.
                    pass
            except Exception as e:
                if self.verbose:
                    console.print(f"[dim]EP: scoring failed for {player.last_name}: {e}[/dim]")

        if self.bid_learner is not None and mv_rows:
            try:
                self.bid_learner.record_player_mv_snapshot(mv_rows)
            except Exception:
                # Learning side effects must never block the EP pipeline.
                pass

        # --- 4. Make decisions ---
        # roster_context is legacy — DecisionEngine accepts it but doesn't
        # use it; position counting happens directly on squad_scores.
        roster_context: dict = {}

        engine = DecisionEngine(
            # Fallbacks are real-points values (REH-55). The old 30.0 / 5.0 were
            # 0-100 index thresholds — leaving them here would silently disable
            # both gates for any caller whose Settings lacks the fields.
            min_ep_to_buy=getattr(self.settings, "min_expected_points_to_buy", 35.0),
            min_ep_upgrade=getattr(self.settings, "min_ep_upgrade_threshold", 40.0),
            target_ep_bar=getattr(self.settings, "target_ep_bar", 0.0),
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
                    pacing=pacing,
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

                sell_recovery = int(pair.sell_player.market_value * INSTANT_SELL_PCT)
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
                    pacing=pacing,
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
            # Whether the buy recs above were built with the relaxed emergency
            # filters -- true only for a short squad inside the emergency
            # window (services/emergency_window.py).
            "emergency": is_emergency,
            "squad_players": squad_player_map,
            "market_players": market_player_map,
            "market_scores": {s.player_id: s for s in market_scores},
            "competitor_player_ids": competitor_player_ids,
            # REH-85: surfaced so get_ep_recommendations_with_trends can reuse
            # this session's context instead of rebuilding it — a rebuild
            # would mean a second get_my_bids call and could yield a
            # different reserve within one session.
            "pacing": pacing,
            # Surfaced for matchday reconciliation (REH-20). Reconciliation
            # needs raw performance dicts to read past actual points (`p`,
            # `mdst`, `md`); piggybacking on the EP pipeline's existing
            # fetch avoids a second round-trip.
            "squad_performance": squad_performance,
            # G1 Task 4: raw payloads the session already fetched, so the
            # store can persist them without a second API call.
            "market_payload": market_payload,
            "ranking_payload": ranking_payload,
            "competitor_squads": competitor_squads,
        }

    def get_ep_recommendations_with_trends(
        self, league, *, days_until_match: int | None | object = _UNSET
    ) -> dict:
        """get_ep_recommendations + trend-aware bid calculation.

        Fetches market-value trend for each buy rec / trade pair and recomputes
        the EP bid with trend_change_pct populated. Without this, every EP bid
        gets the `*= 0.6` conservative penalty from `calculate_ep_bid`.

        ``days_until_match`` is passed through to `get_ep_recommendations`,
        which decides from it whether a short squad is an emergency today.
        """
        result = self.get_ep_recommendations(league, days_until_match=days_until_match)
        current_budget = int(result.get("budget", 0))
        # REH-85: reuse the context built inside get_ep_recommendations rather
        # than rebuilding it here — a rebuild would be a second get_my_bids
        # call and could produce a different reserve within one session.
        pacing = result.get("pacing")

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
                    pacing=pacing,
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

                sell_recovery = int(pair.sell_player.market_value * INSTANT_SELL_PCT)
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
                    pacing=pacing,
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
            max_overpay_pct=self.settings.max_flip_overpay_pct,
            require_rising_trend=self.settings.flip_buys_require_rising_trend,
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
