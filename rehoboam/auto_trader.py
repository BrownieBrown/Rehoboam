"""Automated trading - Execute trades without manual intervention"""

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from rich.console import Console

from .config import INSTANT_SELL_PCT
from .kickoff import NextKickoff
from .learning.tracker import FlipIntent
from .notify.telegram import send_message
from .services import AutoTradeResult, ExecutionService
from .services.emergency_window import emergency_fill_due
from .services.execution import LOCKOUT_DAYS
from .services.integrity import check_integrity, i3_budget_covered
from .services.pacing import SQUAD_CAP as pacing_squad_cap
from .services.pacing import available_squad_slots
from .services.safety_gate import BuyGate, club_counts
from .services.session_facts import IntegrityFailure, SessionFacts
from .store.calibration_store import CalibrationStore
from .store.league_store import LeagueStore
from .store.session_store import SessionStore

console = Console()
logger = logging.getLogger(__name__)

#: PR E: a league prediction needs a live status younger than this; older rows
#: are skipped and counted rather than scored as if they were fresh.
PREDICTION_STATUS_MAX_AGE_S = 48 * 3600


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
    lineup: list[tuple[str, float, str | None]] = field(default_factory=list)
    session_id: str = ""
    integrity_failures: list = field(default_factory=list)
    #: Offers the session placed itself, and candidates the gate or Kickbase
    #: refused (spec §1). `trades` counts executions; these count decisions.
    offers_placed: int = 0
    offers_refused: int = 0


@dataclass
class MatchdayPhase:
    """Trading aggressiveness based on how close the next match is."""

    days_until_match: int | None  # None = unknown
    phase: str  # "aggressive" | "moderate" | "locked"
    max_trades: int
    allow_flips: bool  # Profit flips only make sense with enough time to sell
    reason: str


def _total_worth(ctx) -> int | None:
    """Team value plus budget — the base for Kickbase's 33% rule (REH-118).

    Returns None when team value is unknown. Falling back to the budget alone
    would shrink the cap to a third of the wallet and refuse every large buy,
    so an unreadable input disables the check instead.
    """
    team_value = int(getattr(ctx, "team_value", 0) or 0)
    if team_value <= 0:
        return None
    return team_value + int(getattr(ctx, "current_budget", 0) or 0)


def _build_buy_gate(
    *,
    settings,
    ctx,
    player,
    spendable_budget: int,
    free_slots: int,
    marginal_ep_gain: float | None,
    released_player_id: str | None = None,
    session_refusal: str | None = None,
) -> BuyGate:
    """The safety gate for one autonomous candidate (REH-100).

    Every autonomous buy is built here rather than at each call site, so a new
    buy path cannot quietly acquire a weaker gate than the existing ones.

    ``marginal_ep_gain`` of ``None`` means "no squad-improvement measurement",
    which is the profit-flip case. It resolves to `FALLBACK_TIER` — the
    tightest ceiling — and that is the correct answer rather than a shrug: a
    flip is precisely the round trip REH-64 measured the 12.2% toll on, so it
    is the one buy that genuinely should not chase a contested price.

    ``released_player_id`` is the trade-pair sell. That player is about to
    leave the squad, so counting him toward the club limit would block the
    legal case of selling a club-mate to buy a better one from the same club.

    ``session_refusal`` is PR D's I3 (deficit not covered), set once for the
    whole session on `ctx` rather than measured per candidate. Callers pass
    it through except the emergency fill, which never does — an empty
    lineup slot is -100 points, worse than the deficit I3 guards against.
    """
    from .services.bid_ceiling import tier_for_marginal_gain

    tier = None
    if marginal_ep_gain is not None:
        tier = tier_for_marginal_gain(
            float(marginal_ep_gain),
            must_have=settings.bid_tier_must_have,
            strong=settings.bid_tier_strong_upgrade,
            solid=settings.bid_tier_solid_upgrade,
        )

    holdings = [
        held
        for held in list(getattr(ctx, "squad", []) or []) + list(getattr(ctx, "my_bids", []) or [])
        if released_player_id is None or str(getattr(held, "id", "")) != str(released_player_id)
    ]

    return BuyGate(
        market_value=int(player.market_value),
        spendable_budget=int(spendable_budget),
        known_player_ids=tuple(ctx.ep_result.get("market_players", {}) or {}),
        free_slots=free_slots,
        tier=tier,
        ceiling_policy=settings.bid_ceiling_policy(),
        club_id=str(getattr(player, "team_id", "") or "") or None,
        squad_club_counts=club_counts(holdings),
        # REH-118: Kickbase refuses a purchase above ~33% of total worth with
        # `err 5050 ThirtyThreePercentRuleExceeded`. Worth is team value plus
        # budget, so the raw balance is used here rather than the phase's
        # spendable allowance — the cap is about what we own, not what this
        # path may commit.
        total_worth=_total_worth(ctx),
        max_single_buy_pct=settings.max_single_buy_pct_of_worth,
        session_refusal=session_refusal,
    )


def _max_flip_hold_days(
    days_until_match: int | None, *, respect_matchday: bool = False
) -> int | None:
    """Cap on a profit-flip's hold_days. ``None`` means unconstrained.

    REH-109: this used to return ``days_until_match - 1`` on the premise that a
    flip we cannot sell before kickoff risks an unsellable position. That
    premise is false. Kickbase locks the fielded eleven at kickoff and every
    squad change lands on the FOLLOWING matchday, so holding a flip through a
    match costs nothing in points. The constraint that genuinely survives is
    budget-at-kickoff (REH-11), which is about cash and is enforced elsewhere.

    The cap was also actively expensive. Within rising-trend entries — the only
    class `flip_buys_require_rising_trend` now admits, and the only profitable
    one — outcomes by hold length were:

        0-2d    23 flips   -EUR  9.67m   13.0% win
        3-7d    22 flips   -EUR  4.67m   50.0% win
        8-21d   25 flips   -EUR  9.34m   44.0% win
        22d+    12 flips   +EUR 26.85m   66.7% win

    Deriving the cap from the matchday forced a 1-3 day exit in the moderate
    phase, which is precisely the 13%-win bucket.

    ``respect_matchday`` restores the old behaviour, so the change is
    revertible from .env without a deploy like every other flip knob.
    """
    if not respect_matchday or days_until_match is None:
        return None
    return max(1, days_until_match - 1)


def _is_too_falling_to_buy(trend_7d_pct: float | None, settings) -> bool:
    """True when a market value is sliding too steeply to buy (REH-117).

    Absence is not evidence: most market candidates have little or no MV
    history, and `None` must not block them.
    """
    if trend_7d_pct is None:
        return False
    return float(trend_7d_pct) < float(settings.max_falling_trend_pct_to_buy)


def _club_name(player, score=None) -> str:
    """The player's club, or a bare team id rather than a guess.

    The market payload carries `tid` and never `tn`, so `player.team_name` is
    empty for every market listing. `PlayerScore.club` is populated from the
    pipeline's team profile (REH-117). Falling back to "club 7" is deliberate:
    it is at least lookupable, where "unknown club" told Marco nothing and a
    guessed name would be worse than either.
    """
    club = (getattr(score, "club", "") or "").strip()
    if club:
        return club
    club = (getattr(player, "team_name", "") or "").strip()
    if club:
        return club
    tid = str(getattr(player, "team_id", "") or "").strip()
    return f"club {tid}" if tid else "unknown club"


def _offer_line(
    offer_id: str,
    rec,
    bid: int,
    trend: float | None,
    risks: list[str],
    *,
    outcome: str,
    detail: str,
):
    """Everything the board shows, from data the pipeline already has.

    `PlayerScore` carries position, lineup probability, minutes trend, average
    points and next opponent; `BuyRecommendation` carries the roster impact.
    The old per-player message discarded all of it and printed "unknown club"
    with no position, which is how a defender was proposed to a squad whose
    actual hole was at striker (REH-117).
    """
    from .config import POSITION_MINIMUMS
    from .notify.overview import OfferLine

    player = rec.player
    score = getattr(rec, "score", None)
    position = getattr(score, "position", "") or getattr(player, "position", "") or ""
    impact = getattr(rec, "roster_impact", "") or ""
    return OfferLine(
        offer_id=offer_id,
        name=f"{player.first_name} {player.last_name}".strip() or player.last_name,
        bid=int(bid),
        ep=float(getattr(score, "expected_points", 0.0) or 0.0),
        marginal_gain=float(getattr(rec, "marginal_ep_gain", 0.0) or 0.0),
        outcome=outcome,
        detail=detail,
        position=position,
        club=_club_name(player, score),
        market_value=int(getattr(player, "market_value", 0) or 0),
        fills_gap=impact == "fills_gap",
        trend_7d_pct=trend,
        season_avg=(
            float(score.average_points)
            if score is not None and getattr(score, "average_points", None) is not None
            else None
        ),
        lineup_probability=getattr(score, "lineup_probability", None),
        minutes_trend=getattr(score, "minutes_trend", None),
        next_opponent=getattr(score, "next_opponent", None),
        is_dgw=bool(getattr(score, "is_dgw", False)),
        position_minimum=POSITION_MINIMUMS.get(position),
        risks=tuple(risks),
    )


def _compute_flip_budget(
    phase: str,
    current_budget: int,
    pending_bid_total: int,
    max_debt: int,
    *,
    schedule_known: bool = True,
) -> int:
    """Free budget for flip trading, by matchday phase.

    Shared by session-context build and the trade-phase refresh so both
    call sites agree on the formula after sells/bid-cancels mutate the
    inputs mid-session.

    Moderate (2-4 days out) used to allow no new debt, on the premise that
    nothing would recover it in time. Since 2026-09-22 the locked window
    sells the wallet back to zero itself (`_run_debt_recovery`), so the
    allowance is the same in every trading phase: Marco's rule is "it can go
    into minus until gameday, always".

    ``schedule_known`` is False when neither the schedule nor `/myeleven`
    gave a kickoff. Then the phase is a fallback, not a measurement, and the
    recovery — gated on the day count — could never fire before a kickoff
    the bot cannot see; so no NEW debt, exactly as the old moderate rule.
    """
    if phase == "locked":
        return 0
    if not schedule_known:
        return current_budget - pending_bid_total
    return current_budget + max_debt - pending_bid_total


# One definition of the squad cap, in `services/pacing`. Two copies of one
# safety-relevant number in different modules is how REH-99's 8%/20% split
# happened; the name is kept here because callers and tests already use it.
SQUAD_CAP = pacing_squad_cap


def _flip_worsens_fieldability(squad: list, player) -> bool:
    """Would buying this flip leave the squad less able to field eleven?

    The guard used to demand that squad-plus-flip *can* field eleven, which a
    nine-man squad can never satisfy — so below ten players every flip was
    "unfieldable" and the profit side was dead by construction (2026-09-24,
    two rising-trend flips dropped at 9/15). Adding a player never removes a
    body, so the honest question is relative: only a squad that can field
    eleven today and could not afterwards is made worse. Marco's rule is that
    the bot may trade below eleven when it makes sense.
    """
    from .formation import validate_formation

    before = validate_formation(list(squad))["can_field_eleven"]
    after = validate_formation(list(squad) + [player])["can_field_eleven"]
    return bool(before and not after)


def _available_squad_slots(squad_size: int, open_bid_count: int, cap: int = SQUAD_CAP) -> int:
    """Slots left under Kickbase's squad cap, counting open bids as committed.

    Kickbase counts pending offers toward the 15-player cap before they even
    resolve — a squad at 13 with 2 open bids has zero room for a further
    bid, not two. Positive means room for another bid; zero or negative
    means none.
    """
    return available_squad_slots(squad_size, open_bid_count, cap)


def _starter_swap_has_recovery_time(days_until_match: int | None, min_days: int) -> bool:
    """May a trade pair break up the best eleven for a bid that might not land?

    A pair sells before it bids, and that ordering is forced rather than
    careless: Kickbase counts open bids toward the 15-player cap, so at 15/15
    the sell is what frees the slot the bid needs. The plain-buy path can defer
    its sell until the auction resolves (`sell_plan_player_ids`); the pair path
    has nothing to defer into.

    So the sell is certain and the buy is only a bid. Lose the auction and the
    squad is simply one player lighter until a later session replaces him --
    which costs nothing on the pitch for a bench player, and real points every
    matchday for a member of the best eleven.

    The bot runs twice a day, so that risk is only material when there is no
    time left to refill before kickoff. An unknown date counts as no time: this
    guard's whole point is the case we cannot see, and `get_days_until_match`
    returning None is exactly how the matchday phase silently degraded for
    months (PR #66).
    """
    if days_until_match is None:
        return False
    return days_until_match >= min_days


def _emergency_slots_short(squad: list) -> int:
    """How many players must be bought to make a legal eleven fieldable.

    Zero when the squad can already field one. Answered by
    :func:`rehoboam.formation.fieldability`, which knows the legal formations:
    eleven bodies with six defenders can field ten, and this returns 1 for
    them, at a position the fill path reads from the same answer.

    The squad is passed unfiltered. Excluding injured and suspended players
    would be better still, but it makes emergencies fire more often in the
    locked phase and is a behaviour change worth measuring on its own.
    """
    from .formation import fieldability

    return fieldability(squad).purchases


def _target_availability(buy_recs: list, competitor_ids: set, bar: float) -> dict:
    """How many targets exist, and where they are.

    A target is a player whose ABSOLUTE expected points clear the bar —
    "is he worth a squad slot at all" — as distinct from marginal gain, which
    answers "is he worth today's price and who does he displace".

    Split by where they sit, because the two states call for different
    behaviour: a target that is listed can be bid on now, while one sitting in
    an opponent's squad is a reason to keep a slot free rather than to act.
    """
    listed = 0
    owned = 0
    for rec in buy_recs:
        if rec.score.expected_points < bar:
            continue
        if rec.player.id in competitor_ids:
            owned += 1
        else:
            listed += 1
    return {"listed": listed, "owned_by_opponents": owned, "bar": bar}


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
    #: PR D's integrity rule I3 (budget minus open offers not covered): set
    #: once per session (Task 5), then reported by every non-emergency buy
    #: gate for the rest of the session. None means no session-wide refusal.
    session_refusal: str | None = None
    offers_placed: int = 0
    offers_refused: int = 0


class AutoTrader:
    """Executes trades automatically based on bot recommendations"""

    def __init__(
        self,
        api,
        settings,
        max_trades_per_session: int = 5,  # Increased from 3 for more competitiveness
        max_daily_spend: int = 50_000_000,  # 50M max per day
        dry_run: bool = False,
        app_name: str = "cli",
        session_store: SessionStore | None = None,
        calibration_store: CalibrationStore | None = None,
        league_store: LeagueStore | None = None,
    ):
        """
        Args:
            api: KickbaseAPI instance
            settings: Bot settings
            max_trades_per_session: Max trades per run (safety limit)
            max_daily_spend: Max money to spend per day (safety limit)
            dry_run: If True, simulate but don't execute
            app_name: Who is running this session ("cli" | "function") — stamped
                onto every session-facts row so I7 (ingest freshness) can tell
                this app's own runs apart from the ingest app's.
            session_store: Where session facts land. Defaults to a lazily
                connected `SessionStore()` so callers that never pass one still
                work; tests inject one pinned to a throwaway database.
            calibration_store: Where league-wide predictions land (PR E). Defaults
                to a lazily connected `CalibrationStore()`, same reasoning.
            league_store: Where the market, the managers and every squad the
                session fetched land (PR G1). Defaults to a lazily connected
                `LeagueStore()`, same reasoning.
        """
        self.api = api
        self.settings = settings
        self.max_trades_per_session = max_trades_per_session
        self.max_daily_spend = max_daily_spend
        self.dry_run = dry_run
        self.app_name = app_name
        self._session_store = session_store or SessionStore()
        self._calibration_store = calibration_store or CalibrationStore()
        self._league_store = league_store or LeagueStore()
        self._next_kickoff: NextKickoff | None = None

        # Daily tracking
        self.daily_spend = 0
        self.last_reset = datetime.now().date()

        # Learning system — file-based outcome tracking + adaptive bidding
        from .activity_feed_learner import ActivityFeedLearner
        from .bid_learner import BidLearner
        from .learning import LearningTracker

        self.learner = BidLearner()
        # One message per session, not one per offer (REH-117). Every attempted
        # buy appends an `OfferLine` here; `_send_session_board` sends it once.
        self._session_board: list = []
        # Snapshotted once per session, right after the context builds, so the
        # board's header reflects the wallet BEFORE anything moved rather than
        # a derivation that only accounts for plain offers.
        self._session_budget_before: int | None = None
        # What the debt recovery sold for this session; the board's wallet
        # arithmetic adds it back, since it otherwise only knows offers.
        self._session_recovered: int = 0
        # Open offers already on the wallet before this session touched
        # anything — Kickbase counts them toward the squad cap but does not
        # deduct them from the budget it reports, so the board's header shows
        # them separately rather than silently folding them into "after".
        self._session_open_offers_before: int | None = None
        # Every player id this session has offered on, plain buy or emergency
        # fill alike — so a later phase in the same session (or its refresh
        # from the live API, which cannot see an offer this session just
        # placed) does not bid on the same player twice.
        self._session_offer_ids: set[str] = set()
        self._session_batch_id: str = ""
        # None until a session starts (`run_full_session`) or a caller drives
        # `_build_session_context` directly (tests) — every write site guards
        # with `getattr(self, "_facts", None)` so a facts-less caller never
        # crashes on what is, deliberately, a best-effort diagnostic layer.
        self._facts: SessionFacts | None = None
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

    def _get_matchday_phase(
        self, days_until_match: int | None, *, matchday_in_progress: bool = False
    ) -> MatchdayPhase:
        """Determine trading aggressiveness based on days to next match.

        REH-110: `matchday_in_progress` overrides the day count entirely. Once
        the round's first fixture has kicked off, Kickbase has locked our
        eleven and every squad change lands on the FOLLOWING matchday, so there
        is no lineup left to protect. `days_until_match` still reads 0 all
        weekend — it is `min()` of the fixtures still to come — which locked the
        bot out of the Saturday and Sunday of every round for no benefit.

        The budget-at-kickoff guard (REH-11) is deliberately untouched and keeps
        reading `days_until_match`: that constraint is about cash, not slots,
        and its conservative "earliest remaining fixture" meaning is correct.
        """
        # Applied ONLY where the day count would otherwise lock us out. Between
        # rounds `days_until_match` can be large while a fixture is still inside
        # the recent-past window, and blanket-overriding there would DEMOTE an
        # aggressive phase to a moderate one — more information making the bot
        # more restrictive, which the invariant test forbids.
        if matchday_in_progress and days_until_match is not None and days_until_match <= 1:
            return MatchdayPhase(
                days_until_match=days_until_match,
                phase="matchday_in_progress",
                # The moderate allowance rather than the full one: the round in
                # progress hides the NEXT round's kickoff, so we cannot see how
                # much runway a purchase actually has.
                max_trades=max(self.max_trades_per_session // 2, 2),
                allow_flips=True,
                reason="Matchday under way — lineup locked, trading + flips open",
            )
        if days_until_match is not None and days_until_match <= 1:
            return MatchdayPhase(
                days_until_match=days_until_match,
                phase="locked",
                max_trades=0,
                allow_flips=False,
                reason=f"Match in {days_until_match}d — lineup only, no trading",
            )
        elif days_until_match is not None and days_until_match <= 4:
            # REH-109: flips ARE allowed here. `allow_flips` used to be True
            # only at >= 5 days, and measured against the real calendar that
            # window was five hours wide — MD2's last fixture 2026-08-30T13:30Z,
            # MD3's first 2026-09-04T18:30Z — which the 08:00/20:00 timer missed
            # entirely. Zero flips since 2026-05-15 was the code path never
            # executing, not a selection failure.
            #
            # Safe because the premise for excluding them was wrong: a trade
            # near a matchday cannot disturb the eleven, which Kickbase locks at
            # kickoff. `max_trades` stays halved — this reopens flipping, not
            # full-rate squad churn.
            return MatchdayPhase(
                days_until_match=days_until_match,
                phase="moderate",
                max_trades=max(self.max_trades_per_session // 2, 2),
                allow_flips=True,
                reason=f"Match in {days_until_match}d — lineup improvements + flips",
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

        # Fetch matchday timing via `next_kickoff` directly (Task 5), not
        # `get_days_until_match` -- that method just calls `next_kickoff`
        # itself, and calling it here too would be a second /myeleven fetch
        # for the same answer. The `NextKickoff` this produces also feeds
        # `self._facts` below, so the phase decision and the facts row are
        # guaranteed to agree on what "next kickoff" meant for this session.
        now = datetime.now(tz=timezone.utc)
        nk = trader.next_kickoff(league, now=now)
        self._next_kickoff = nk
        days = max((nk.at - now).days, 0) if nk.at is not None else None
        phase = self._get_matchday_phase(days, matchday_in_progress=nk.matchday_in_progress)

        console.print(f"[cyan]📅 {phase.reason}[/cyan]")

        # Single EP pipeline call with trend data. The day count goes along
        # so the pipeline's emergency decision reads the same kickoff as the
        # phase above (services/emergency_window.py), and not a second lookup.
        ep_result = trader.get_ep_recommendations_with_trends(league, days_until_match=days)

        # Fetch bids and squad
        my_bids = self.api.get_my_bids(league)
        squad = self.api.get_squad(league)
        team_info = self.api.get_team_info(league)
        current_budget = team_info.get("budget", 0)
        team_value = team_info.get("team_value", 0)

        # Calculate flip budget based on matchday phase
        max_debt = int(team_value * (self.settings.max_debt_pct_of_team_value / 100))
        pending_bid_total = sum(p.user_offer_price for p in my_bids)
        flip_budget = _compute_flip_budget(
            phase.phase,
            current_budget,
            pending_bid_total,
            max_debt,
            schedule_known=phase.days_until_match is not None,
        )

        # Kickbase counts open bids toward the 15-player cap, so the
        # committed headcount is squad + pending bids, not squad alone.
        committed = len(squad) + len(my_bids)
        logger.info(
            "session-context phase=%s days_to_match=%s squad=%d/15 "
            "budget=%d team_value=%d flip_budget=%d pending_bids=%d",
            phase.phase,
            phase.days_until_match,
            committed,
            int(current_budget),
            int(team_value),
            flip_budget,
            len(my_bids),
        )

        ctx = EPSessionContext(
            ep_result=ep_result,
            matchday_phase=phase,
            my_bids=my_bids,
            my_bid_amounts={p.id: p.user_offer_price for p in my_bids},
            squad=squad,
            current_budget=current_budget,
            team_value=team_value,
            flip_budget=flip_budget,
        )

        # A caller that drives this method directly (tests, mainly) without
        # going through `run_full_session` first still gets a facts object to
        # fill in below -- `run_full_session` itself already created the real
        # one at Step 0, so this only ever fires for that direct-call case.
        if self._facts is None:
            self._facts = SessionFacts(
                session_id=self._session_batch_id,
                app=self.app_name,
                mode=self.settings.trading_mode,
                dry_run=self.dry_run,
                started_at=time.time(),
            )
        try:
            self._facts_from_context(ctx, nk)
        except Exception:
            logger.warning("facts: could not fill session facts from context", exc_info=True)

        return ctx

    def _facts_from_context(self, ctx: EPSessionContext, nk) -> None:
        """Fill `self._facts` from a built context and its `NextKickoff`.

        Split out from `_build_session_context` so a test that patches that
        method wholesale can still populate facts the same way the real
        builder does, by calling this directly with its own context and
        kickoff answer (Task 5).

        `open_offers_manual` exists because a bid the bot itself placed is
        already accounted for in the phase/gate logic that placed it --
        what integrity needs to see is money committed *outside* that,
        i.e. bids Marco placed by hand that `get_pending_bids` (the bot's
        own bid ledger) has no row for. If that read fails, every open bid
        counts as manual (fail toward reporting exposure, not hiding it) --
        no integrity rule reads the field today, so nothing gates on it.
        """
        from .formation import get_position_counts

        counts = get_position_counts(ctx.squad)
        self._facts.phase = ctx.matchday_phase.phase
        self._facts.next_kickoff = nk.at.timestamp() if nk.at is not None else None
        self._facts.next_kickoff_source = nk.source
        self._facts.squad_gk = counts["Goalkeeper"]
        self._facts.squad_def = counts["Defender"]
        self._facts.squad_mid = counts["Midfielder"]
        self._facts.squad_fw = counts["Forward"]
        self._facts.budget = int(ctx.current_budget)
        # Upper bound: this sums full market value, but a real instant sell
        # pays out INSTANT_SELL_PCT of it (`config.py`) -- 1.0 today, so the
        # sum is exact for now. Scale by it here if that constant ever drops.
        self._facts.sellable_value = sum(int(p.market_value) for p in ctx.squad)
        self._facts.open_offers_total = sum(int(b.user_offer_price or 0) for b in ctx.my_bids)

        bot_placed_ids: set[str] = set()
        try:
            bot_placed_ids = {str(row["player_id"]) for row in self.learner.get_pending_bids()}
        except Exception:
            logger.warning("facts: could not read pending-bid provenance", exc_info=True)
        self._facts.open_offers_manual = sum(
            int(b.user_offer_price or 0) for b in ctx.my_bids if str(b.id) not in bot_placed_ids
        )

        # I3 as a session-wide buy refusal (consumed by the safety gate) is
        # `full`-mode only -- `lineup_only` never bids on anything but the
        # emergency fill, which is exempt, so refusing new offers there would
        # refuse nothing real. The end-of-session I3 integrity rule is
        # unconditional; it just reports, it never gates.
        if self.settings.trading_mode == "full":
            ok, detail = i3_budget_covered(
                budget=self._facts.budget,
                open_offers_total=self._facts.open_offers_total,
                sellable_value=self._facts.sellable_value,
            )
            if not ok:
                ctx.session_refusal = f"I3: {detail}"
                logger.warning("session-refusal I3 %s", detail)

    def _write_league_predictions(self, ctx: EPSessionContext, nk: NextKickoff | None) -> int:
        """PR E §1: score every live player from store rows and write `predictions`.

        Returns the number of rows written; 0 with a logged reason when the
        matchday is unknown, so rule I5 fails for the right cause. Everything
        here reads the store and the already-built context — no API call.
        """
        from .formation import select_best_eleven
        from .scoring.store_scorer import score_stored
        from .scoring.v2.coefficients import load_coefficients

        if nk is None or nk.at is None or nk.day_number is None:
            logger.info(
                "predictions: skipped, matchday unknown (source=%s)",
                getattr(nk, "source", "none"),
            )
            return 0
        now = datetime.now(tz=timezone.utc)
        store = self._calibration_store
        season = store.current_season()
        if season is None:
            logger.info("predictions: skipped, corpus has no season")
            return 0
        availability, rate, _meta = load_coefficients()
        since_iso = (now - timedelta(days=400)).strftime("%Y-%m-%dT%H:%M:%SZ")
        players = store.stored_players(since_iso=since_iso, status_day=now.date())
        fresh_after = now.timestamp() - PREDICTION_STATUS_MAX_AGE_S

        owned = {p.id for p in ctx.squad}
        listed = set((ctx.ep_result.get("market_players") or {}).keys())
        lineup_map = ctx.ep_result.get("lineup_map") or {}
        best_11 = {p.id for p in select_best_eleven(ctx.squad, lineup_map)} if lineup_map else set()
        live_ep = {
            s.player_id: float(s.expected_points) for s in ctx.ep_result.get("squad_scores") or []
        }
        live_ep.update(
            {
                pid: float(s.expected_points)
                for pid, s in (ctx.ep_result.get("market_scores") or {}).items()
            }
        )

        rows: list[dict] = []
        skipped_stale = 0
        for p in players:
            if p.status_fetched_at is None or p.status_fetched_at < fresh_after:
                skipped_stale += 1
                continue
            pred = score_stored(
                p,
                now=now,
                max_status_age_days=self.settings.max_status_age_days,
                availability=availability,
                rate=rate,
            )
            rows.append(
                {
                    "session_id": self._session_batch_id,
                    "player_id": p.player_id,
                    "season": season,
                    "day_number": nk.day_number,
                    "kickoff": nk.at.timestamp(),
                    "predicted_at": now.timestamp(),
                    "predicted_ep": pred.predicted_ep,
                    "p_status": pred.p_status,
                    "rate": pred.rate,
                    "prev_status": pred.prev_status,
                    "live_status": p.live_status,
                    "position": p.position,
                    "team_id": p.team_id,
                    "owned": p.player_id in owned,
                    "listed": p.player_id in listed,
                    "in_best_11": p.player_id in best_11,
                    "live_ep": live_ep.get(p.player_id),
                    "data_grade": pred.data_grade,
                    "app": self.app_name,
                    "dry_run": bool(self.dry_run),
                    "backfill": False,
                }
            )
        written = store.write_predictions(rows)
        logger.info(
            "predictions written=%d skipped_stale=%d matchday=%s",
            written,
            skipped_stale,
            nk.day_number,
        )
        return written

    def _write_league_state(self, ctx: EPSessionContext, league) -> dict[str, int]:
        """G1: persist the market, the managers and every squad the session already fetched.

        No API call here: `Trader` keeps the raw market payload, the ranking and
        the competitor squads on `ep_result`; our own squad is `ctx.squad`.
        """
        from .enrichment.rows import (
            manager_rows,
            manager_squad_rows,
            market_listing_rows,
            own_squad_rows,
        )

        snapshot_at = time.time()
        my_id = str(getattr(getattr(self.api, "user", None), "id", "") or "")
        store = self._league_store
        counts = {"listings": 0, "managers": 0, "squads": 0}

        ranking = ctx.ep_result.get("ranking_payload")
        if ranking:
            counts["managers"] = store.upsert_managers(
                manager_rows(
                    ranking,
                    league_id=str(league.id),
                    our_user_id=my_id,
                    updated_at=snapshot_at,
                )
            )
        market = ctx.ep_result.get("market_payload")
        if market:
            counts["listings"] = store.write_listings(
                market_listing_rows(
                    market, snapshot_at=snapshot_at, our_user_id=my_id, source="session"
                )
            )
        squad_rows: list[dict] = []
        for mgr_id, items in (ctx.ep_result.get("competitor_squads") or {}).items():
            squad_rows += manager_squad_rows(
                str(mgr_id), items, snapshot_at=snapshot_at, source="session"
            )
        if my_id and ctx.squad:
            squad_rows += own_squad_rows(
                my_id, ctx.squad, snapshot_at=snapshot_at, source="session"
            )
        if squad_rows:
            counts["squads"] = store.write_squads(squad_rows)
        logger.info(
            "league-state listings=%d managers=%d squads=%d",
            counts["listings"],
            counts["managers"],
            counts["squads"],
        )
        return counts

    def _finish_facts(self, errors: list[str], start_time: float, phase: str, league) -> list:
        """Close out session facts at any exit: record, check, alert, board.

        Called from all three of `run_full_session`'s exits -- the normal
        return, `_finish_lineup_only`, and the pipeline-failure `except` --
        so a run that dies mid-pipeline gets the same treatment as one that
        finishes cleanly. That symmetry is the point of writing the early
        row at Step 0: a session that never reaches here still left evidence,
        and one that does reach here always gets exactly one row update, one
        integrity check, and at most one Telegram message.

        `phase` is the caller's own answer for what phase this session ended
        in -- not read back off `self._facts`, because the pipeline-failure
        exit has no context to read it from and needs to stamp `"unknown"`
        instead. Never raises: each side effect (store write, the refresh,
        the check itself, the alert) is its own try/except, so one failing --
        Postgres unreachable, Telegram down -- cannot take out the others or
        the session that called this.
        """
        facts = getattr(self, "_facts", None)
        if facts is None:
            return []

        facts.duration_s = time.time() - start_time
        facts.errors = len(errors)
        facts.error_text = "; ".join(errors)[:2000]
        facts.phase = phase

        # The pre-flight I3 refusal in `_facts_from_context` reads budget and
        # open offers as they stood before the session spent anything -- that
        # is deliberate, it is a pre-flight. The end-of-session I3 rule below
        # is a report, not a gate, and must reflect what the session actually
        # left behind, so refresh both from the live API one more time.
        try:
            facts.budget = int(self.api.get_team_info(league)["budget"])
            facts.open_offers_total = int(
                sum(p.user_offer_price for p in self.api.get_my_bids(league))
            )
        except Exception:
            logger.warning("facts: could not refresh budget/open_offers for I3", exc_info=True)

        try:
            self._session_store.record(facts)
        except Exception:
            logger.warning("session-facts: could not record final row", exc_info=True)

        # `last_ingest_completed_at` gets its own try/except, separate from
        # `check_integrity` below: the old code read it inside that same try,
        # so a store outage skipped the whole check and printed "all seven
        # rules pass" -- a false green precisely when the store is the thing
        # that's down. Falling back to None instead lets I7 fail naturally
        # ("ingestion last completed never"); the "(store unreachable)" tag
        # is stitched onto that failure below so the detail says why.
        store_unreachable = False
        last_ingest_completed_at = None
        try:
            last_ingest_completed_at = self._session_store.last_ingest_completed_at()
        except Exception:
            store_unreachable = True
            logger.warning("integrity: could not read last_ingest_completed_at", exc_info=True)

        failures: list[IntegrityFailure] = []
        try:
            failures = check_integrity(
                facts,
                now=time.time(),
                last_ingest_completed_at=last_ingest_completed_at,
            )
        except Exception:
            logger.warning("integrity: check_integrity failed", exc_info=True)

        if store_unreachable:
            failures = [
                IntegrityFailure(f.rule, f"{f.detail} (store unreachable)") if f.rule == "I7" else f
                for f in failures
            ]

        if failures:
            try:
                self._session_store.record_failures(facts.session_id, failures)
            except Exception:
                logger.warning("integrity: could not record failures", exc_info=True)

        try:
            if failures:
                for failure in failures:
                    console.print(f"[red]Integrity: {failure.rule} {failure.detail}[/red]")
            else:
                console.print("[green]Integrity: all seven rules pass[/green]")
        except Exception:
            logger.warning("integrity: could not print the board", exc_info=True)

        # Dry runs mirror `_send_session_board`: preview locally, never
        # page. Recording the row and the failures above still happens --
        # only the outbound alert is gated on `dry_run`.
        if (
            failures
            and not self.dry_run
            and self.settings.telegram_bot_token
            and self.settings.telegram_chat_id
        ):
            try:
                lines = "\n".join(f"• {f.rule} {f.detail}" for f in failures)
                text = (
                    f"Rehoboam integrity — {self.app_name} {self.settings.trading_mode} "
                    f"session {facts.session_id}\n{lines}"
                )
                if facts.errors > 0:
                    text += f"\nErrors: {facts.error_text[:300]}"
                send_message(self.settings.telegram_bot_token, self.settings.telegram_chat_id, text)
            except Exception:
                logger.warning("integrity: telegram alert failed", exc_info=True)

        if failures:
            logger.warning(
                "integrity-failures n=%d rules=%s",
                len(failures),
                ",".join(f.rule for f in failures),
            )

        return failures

    def _record_decline(self, player, reason: str, *, ep_gain=None, ceiling=None) -> None:
        """Best-effort record of a candidate the bot evaluated and did not bid on.

        REH-86. Declines were invisible: `run_unified_trade_phase` drops any
        candidate whose `recommended_bid` is <= 0 without a trace, so a session
        that bought nothing looked identical whether it found nothing worth
        buying or wanted a player it could not afford. Wrapped in try/except
        like every other learning write -- instrumentation must never be able
        to stop a trade.
        """
        if self.learner is None:
            return
        try:
            self.learner.record_buy_decision(
                player_id=getattr(player, "id", ""),
                player_name=getattr(player, "last_name", None),
                decision="declined",
                reason=reason,
                marginal_ep_gain=ep_gain,
                asking_price=getattr(player, "price", None)
                or getattr(player, "market_value", None),
                market_value=getattr(player, "market_value", None),
                budget_ceiling=ceiling,
            )
        except Exception as e:  # pragma: no cover - defensive
            logger.debug("record_buy_decision failed: %s", e)

    def _evaluate_open_bids(self, league, *, player_trends: dict) -> None:
        """Re-read every live offer and withdraw only the ones on unfieldable players.

        `get_my_bids` is `get_market` filtered by "do we hold an offer", so it
        returns offers Marco placed by hand alongside the bot's — and it cannot
        say which is which. Provenance comes from `pending_bids`: a recorded
        tier (REH-111) or plain membership (REH-115). A bid the bot placed is
        a commitment — price never cancels it (spec §2); only a player who can
        no longer be fielded does.

        Both reads are best-effort: a learning-side failure must leave the
        phase working, not silently resume cancelling Marco's bids, so the
        provenance set falls back to the tiers rather than to None.
        """
        from .bid_evaluator import BidEvaluator

        bid_tiers: dict[str, str] = {}
        bot_placed_ids: set[str] = set()
        if self.learner is not None:
            try:
                pending = self.learner.get_pending_bids()
                bot_placed_ids = {str(row["player_id"]) for row in pending}
                bid_tiers = {
                    str(row["player_id"]): row["tier"] for row in pending if row.get("tier")
                }
            except Exception:
                logger.warning("could not read open-bid provenance", exc_info=True)
                bot_placed_ids = set(bid_tiers)

        evaluator = BidEvaluator(self.api, self.settings)
        evaluations = evaluator.evaluate_active_bids(
            league,
            player_trends=player_trends,
            bid_tiers=bid_tiers,
            bot_placed_ids=bot_placed_ids,
        )
        if not evaluations:
            return

        evaluator.display_bid_evaluations(evaluations)
        logger.info(
            "bid-eval keep=%d cancel=%d",
            sum(1 for e in evaluations if e.recommendation == "KEEP"),
            sum(1 for e in evaluations if e.recommendation == "CANCEL"),
        )
        canceled = evaluator.cancel_bad_bids(league, evaluations, dry_run=self.dry_run)
        if canceled:
            console.print(f"[yellow]Canceled {canceled} bid(s) that no longer make sense[/yellow]")

    def _execute_buy(self, league, rec, ctx, *, free_slots: int) -> AutoTradeResult | None:
        """Place the offer for a plain squad-improvement buy, and keep the case.

        Spec §1: one execution path. The trend floor still applies, the case is
        still rendered (`render_proposal` — now the record of what was done and
        why), and then `ExecutionService.buy` runs the safety gate and places
        the offer. The `trade_proposals` row is written AFTER the fact as
        'executed' / 'refused' / 'failed', so the board and the daily summary
        report what happened rather than what was asked.

        Returns None when the trend floor skipped the player (nothing was
        attempted), else the execution result — the caller appends it to the
        session's results so `trades=`, `total_spent` and the board see it.

        ``free_slots`` is the caller's count of open squad slots (open bids
        already deducted); the gate refuses a buy into a full squad.
        """
        import uuid

        from .notify.render import render_proposal
        from .services.bid_ceiling import tier_for_marginal_gain
        from .services.execution import BudgetSafetyError

        offer_id = uuid.uuid4().hex[:12]
        player = rec.player
        bid_amount = int(rec.recommended_bid)
        trend = None
        try:
            from .trader import Trader

            trend = (
                Trader(self.api, self.settings)
                .trend_service.get_trend(player.id, player.market_value, league.id)
                .trend_7d_pct
            )
        except Exception:
            logger.debug("buy: no trend for %s", player.id, exc_info=True)

        if _is_too_falling_to_buy(trend, self.settings):
            console.print(
                f"[dim]Skip {player.last_name} — market value falling {trend:.1f}%/7d[/dim]"
            )
            logger.info(
                "buy-skip player=%s trend=%.1f%% below %.1f%% floor",
                player.id,
                trend,
                float(self.settings.max_falling_trend_pct_to_buy),
            )
            return None

        # The emergency fill used to waive the floor above (2026-09-15: Baack at
        # -40%/7d was the only affordable body and was skipped). It no longer
        # proposes at all — it buys behind the gate, which has no trend floor —
        # so an empty slot still outranks a sliding market value.
        risks: list[str] = []
        if getattr(rec.score, "data_quality", None) and rec.score.data_quality.grade != "A":
            risks.append(
                f"Data quality {rec.score.data_quality.grade} — no fitted history, "
                "scored on the position prior."
            )

        case = render_proposal(
            player_name=f"{player.first_name} {player.last_name}".strip(),
            club=getattr(player, "team_name", "") or "unknown club",
            bid=bid_amount,
            market_value=int(player.market_value),
            ep=float(rec.score.expected_points),
            displaced_name=getattr(rec, "replaces_player_name", None) or "the weakest starter",
            displaced_ep=float(getattr(rec, "replaces_player_ep", 0.0) or 0.0),
            marginal_gain=float(rec.marginal_ep_gain),
            budget_before=int(ctx.current_budget),
            trend_7d_pct=trend,
            risks=risks,
            fills_empty_slot=bool(getattr(rec, "fills_empty_slot", False)),
            position=str(getattr(player, "position", "") or ""),
        )

        # A buy that only works by selling someone first carries its sell plan
        # on the bid; `resolve_auctions` runs those sells if we win. Buy first,
        # sell after — never sell before securing the player.
        sell_plan = getattr(rec, "sell_plan", None)
        sp_ids = (
            [entry.player_id for entry in sell_plan.players_to_sell]
            if sell_plan and getattr(sell_plan, "players_to_sell", None)
            else None
        )

        try:
            result = self.execution.buy(
                league,
                player,
                bid_amount,
                getattr(rec, "reason", "") or "EP upgrade",
                sell_plan_player_ids=sp_ids,
                current_budget=ctx.current_budget,
                days_until_match=ctx.matchday_phase.days_until_match,
                gate=_build_buy_gate(
                    settings=self.settings,
                    ctx=ctx,
                    player=player,
                    # The phase's allowance, not the wallet — see `BuyGate`.
                    spendable_budget=int(ctx.flip_budget),
                    free_slots=free_slots,
                    marginal_ep_gain=rec.marginal_ep_gain,
                ),
            )
        except BudgetSafetyError as exc:
            # Live mode raises here; one unaffordable candidate must cost
            # itself, not every offer this loop already placed (they live in
            # `results`, not in this call).
            result = AutoTradeResult(
                success=False,
                player_name=f"{player.first_name} {player.last_name}".strip(),
                action="BUY",
                price=bid_amount,
                reason=getattr(rec, "reason", "") or "EP upgrade",
                timestamp=time.time(),
                error=str(exc),
            )

        gate_prefix = "safety gate refused: "
        if result.success:
            outcome, status, detail = "placed", "executed", ""
            ctx.offers_placed += 1
            self._session_offer_ids.add(str(player.id))
        elif (result.error or "").startswith(gate_prefix):
            outcome, status = "refused", "refused"
            detail = (result.error or "")[len(gate_prefix) :]
            ctx.offers_refused += 1
        else:
            outcome, status = "failed", "failed"
            detail = result.error or "unknown error"
            ctx.offers_refused += 1

        # Collected before the dry-run exit so `status` shows the board Marco
        # would receive rather than a line saying one exists.
        self._session_board.append(
            _offer_line(offer_id, rec, bid_amount, trend, risks, outcome=outcome, detail=detail)
        )
        if self.dry_run:
            # ExecutionService already printed DRY RUN; nothing is recorded.
            return result

        tier = tier_for_marginal_gain(
            float(rec.marginal_ep_gain),
            must_have=self.settings.bid_tier_must_have,
            strong=self.settings.bid_tier_strong_upgrade,
            solid=self.settings.bid_tier_solid_upgrade,
        )
        message = case if not detail else f"{case}\n\n{outcome.upper()}\n  {detail}"
        try:
            self.learner.record_proposal(
                proposal_id=offer_id,
                player_id=player.id,
                player_name=player.last_name,
                bid=bid_amount,
                market_value=int(player.market_value),
                message=message,
                tier=tier.value,
                batch_id=self._session_batch_id,
                status=status,
            )
        except Exception:
            logger.exception("buy: could not record %s", offer_id)

        logger.info(
            "offer %s id=%s player=%s bid=%d batch=%s%s",
            status,
            offer_id,
            player.id,
            bid_amount,
            self._session_batch_id,
            f" — {detail}" if detail else "",
        )
        return result

    def _send_session_board(self, league, ctx) -> None:
        """Send what this session did with the wallet, as one message (spec §1).

        Once, at the end, after every offer has been placed or refused. Nothing
        here asks for a decision — there is no button — it is the record.
        Best-effort: a delivery failure must not fail the session; the rows in
        `trade_proposals` are the durable record and the daily summary reads
        them.
        """
        if not self._session_board:
            return

        from .notify.overview import render_session_board
        from .notify.telegram import send_message

        placed = [line for line in self._session_board if line.outcome == "placed"]
        refused = [line for line in self._session_board if line.outcome != "placed"]
        spend = sum(line.bid for line in placed)
        # The opening budget is snapshotted once per session; every session
        # move (plain offers, pairs, flips) has decremented ctx.current_budget
        # since, so `budget_after` is derived from the snapshot and this
        # session's own offers rather than read back off ctx — the fallback
        # only serves direct callers that never built a session.
        budget_before = (
            int(self._session_budget_before)
            if self._session_budget_before is not None
            else int(getattr(ctx, "current_budget", 0) or 0) + spend
        )
        open_offers_before = int(self._session_open_offers_before or 0)
        text = render_session_board(
            squad_size=len(getattr(ctx, "squad", []) or []),
            squad_cap=SQUAD_CAP,
            budget_before=budget_before,
            budget_after=budget_before + self._session_recovered - spend,
            recovered=self._session_recovered,
            open_offers_before=open_offers_before,
            placed=placed,
            refused=refused,
        )
        console.print(text)
        if self.dry_run:
            console.print("[yellow]DRY RUN - board not sent[/yellow]")
            return

        delivered = send_message(
            self.settings.telegram_bot_token, self.settings.telegram_chat_id, text
        )
        logger.info(
            "session-board batch=%s placed=%d refused=%d spend=%d budget_after=%d "
            "open_offers_before=%d delivered=%s",
            self._session_batch_id,
            len(placed),
            len(refused),
            spend,
            budget_before + self._session_recovered - spend,
            open_offers_before,
            delivered,
        )
        if not delivered:
            logger.warning(
                "session board %s not delivered; the trade_proposals rows are the record",
                self._session_batch_id,
            )

    def _settle_top5_obligation(self, league, ctx) -> None:
        """Discharge the league's Top-5 forced sale for the last finished matchday.

        The pool is chosen by what players scored THAT matchday; which of them
        to give up is chosen by expected points from here on. A one-off big
        score is the cheapest thing to lose, and first place has no choice at
        all — see `rehoboam.top5`.
        """
        from . import top5

        last_finished = self._last_finished_matchday(league)
        if last_finished is None:
            return

        forward_ep = {
            str(s.player_id): float(s.expected_points)
            for s in (ctx.ep_result.get("squad_scores") or [])
        }
        sale = top5.settle(
            api=self.api,
            league=league,
            learner=self.learner,
            squad=list(ctx.squad or []),
            forward_ep=forward_ep,
            matchday=last_finished,
            dry_run=self.dry_run,
        )
        if sale is None:
            return
        verb = "would sell" if self.dry_run else "sold"
        console.print(
            f"[yellow]Top-5 rule: finished {sale.place} on matchday "
            f"{last_finished} — {verb} {sale.chosen_name} ({sale.reason})[/yellow]"
        )

    def _last_finished_matchday(self, league) -> int | None:
        """The most recent matchday whose window has closed, or None.

        Read from the H2H fixture list, which carries each matchday's end time.
        """
        from datetime import datetime, timezone

        from .h2h import _parse_iso

        try:
            payload = self.api.client.session.get(
                f"{self.api.client.BASE_URL}/v4/leagues/{league.id}/matchups"
            ).json()
        except Exception:
            logger.warning("top5: could not read the fixture list", exc_info=True)
            return None

        now = datetime.now(timezone.utc)
        finished = [
            int(md.get("day") or 0)
            for md in payload.get("mds") or []
            if (ends := _parse_iso(md.get("ed"))) is not None and ends < now
        ]
        return max(finished) if finished else None

    def run_unified_trade_phase(self, league, ctx: EPSessionContext) -> list[AutoTradeResult]:
        """Execute all qualifying trades from a single ranked candidate list.

        Trade pairs and plain buys compete head-to-head by EP gain.
        This replaces the old separate profit + lineup sessions.
        """
        results: list[AutoTradeResult] = []
        buy_recs = ctx.ep_result.get("buy_recs", [])
        trade_pairs = ctx.ep_result.get("trade_pairs", [])

        target_state = _target_availability(
            buy_recs,
            ctx.ep_result.get("competitor_player_ids") or set(),
            self.settings.target_ep_bar,
        )
        logger.info(
            "target-availability listed=%d owned_by_opponents=%d bar=%.1f",
            target_state["listed"],
            target_state["owned_by_opponents"],
            target_state["bar"],
        )

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
            else:
                # REH-86: the bot looked at this player and did not bid. That
                # is a decision, and it was previously unrecorded.
                self._record_decline(
                    rec.player,
                    rec.reason or "no_bid",
                    ep_gain=rec.marginal_ep_gain,
                    ceiling=int(ctx.current_budget),
                )
        for pair in trade_pairs:
            if pair.recommended_bid and pair.recommended_bid > 0:
                candidates.append(("pair", pair.ep_gain, pair))

        # Wash-trade guard: refuse to re-bid on a player we sold within the
        # configured window. Without this, the same player can be sold and
        # re-bought within hours — paying the bid spread on both legs for
        # zero EP gain.
        wash_skipped = 0
        filtered: list = []
        for kind, ep_val, obj in candidates:
            target_id = obj.player.id if kind == "buy" else obj.buy_player.id
            target_name = obj.player.last_name if kind == "buy" else obj.buy_player.last_name
            if self._is_wash_trade(target_id):
                wash_skipped += 1
                self._record_decline(
                    obj.player if kind == "buy" else obj.buy_player,
                    "wash_trade_block",
                    ep_gain=ep_val,
                    ceiling=int(ctx.current_budget),
                )
                console.print(f"[dim]Skip {target_name} — wash-trade block (sold recently)[/dim]")
                logger.info(
                    "guard-wash-trade: skipped %s (id=%s) — sold within block window",
                    target_name,
                    target_id,
                )
                continue
            filtered.append((kind, ep_val, obj))
        if wash_skipped:
            console.print(f"[yellow]Wash-trade guard: skipped {wash_skipped} candidate(s)[/yellow]")
            logger.info("guard-wash-trade total_skipped=%d", wash_skipped)
        candidates = filtered

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
        available_slots = _available_squad_slots(current_squad_size, active_bid_count)
        ctx.current_budget = fresh_team_info.get("budget", ctx.current_budget)
        ctx.team_value = fresh_team_info.get("team_value", ctx.team_value)
        pending_bid_total = sum(p.user_offer_price for p in fresh_bids)
        max_debt = int(ctx.team_value * (self.settings.max_debt_pct_of_team_value / 100))
        ctx.flip_budget = _compute_flip_budget(
            ctx.matchday_phase.phase,
            ctx.current_budget,
            pending_bid_total,
            max_debt,
            schedule_known=ctx.matchday_phase.days_until_match is not None,
        )
        ctx.my_bid_amounts = {p.id: p.user_offer_price for p in fresh_bids}
        # An offer this session already placed (plain buy or emergency fill)
        # may not be visible on `fresh_bids` yet — in dry-run the API never
        # saw it at all — so the rebuild above can silently drop it. Keep it
        # present (amount is a placeholder; only presence matters here) so
        # the "already have active bid" skip below still fires.
        for pid in self._session_offer_ids:
            ctx.my_bid_amounts.setdefault(pid, 1)
        # `_build_buy_gate`'s club-limit count reads ctx.squad + ctx.my_bids;
        # without this refresh it stays the pre-session snapshot forever, so a
        # second offer on the same session's club can push past the limit
        # while the gate still sees room.
        ctx.my_bids = list(fresh_bids)

        console.print(
            f"[cyan]📋 Squad: {current_squad_size} + {active_bid_count} bids = "
            f"{current_squad_size + active_bid_count}/15 "
            f"({available_slots} slot(s) open)[/cyan]"
        )

        # Also add profit flip candidates if phase allows and there are open slots
        profit_flip_candidates = []
        if (
            self.settings.enable_flip_buys
            and ctx.matchday_phase.allow_flips
            and available_slots > 0
        ):
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
                max_hold_days = _max_flip_hold_days(
                    ctx.matchday_phase.days_until_match,
                    respect_matchday=self.settings.flip_hold_respects_matchday,
                )

                from .scoring.decision import _would_create_dead_weight

                skipped_long_hold = 0
                skipped_unfieldable = 0
                skipped_wash = 0
                for opp in profit_opps:
                    if opp.player.id in ep_player_ids:
                        continue
                    if self._is_wash_trade(opp.player.id):
                        skipped_wash += 1
                        continue
                    if max_hold_days is not None and opp.hold_days > max_hold_days:
                        skipped_long_hold += 1
                        continue
                    # Fieldability guard, relative: refuse only a flip that
                    # leaves the squad less able to field eleven than today.
                    if _flip_worsens_fieldability(fresh_squad, opp.player):
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
                if skipped_wash > 0:
                    console.print(f"[dim]Skipped {skipped_wash} flip(s) — wash-trade block[/dim]")
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
                if str(obj.player.id) in self._session_offer_ids:
                    console.print(
                        f"[dim]Skip {obj.player.last_name} — offered on this session already[/dim]"
                    )
                    continue
                if obj.recommended_bid > ctx.flip_budget:
                    console.print(
                        f"[yellow]Cannot afford {obj.player.last_name} "
                        f"(€{obj.recommended_bid:,} > €{ctx.flip_budget:,})[/yellow]"
                    )
                    continue

                result = self._execute_buy(league, obj, ctx, free_slots=available_slots)
                if result is None:
                    continue  # trend floor — nothing attempted
                results.append(result)
                if result.success:
                    ctx.executed_trade_count += 1
                    self.daily_spend += obj.recommended_bid
                    ctx.flip_budget -= obj.recommended_bid
                    ctx.current_budget -= obj.recommended_bid
                    # Kickbase counts an open offer toward the squad cap.
                    available_slots -= 1
                    # This offer is now held for the rest of THIS session too —
                    # the club-limit gate and the "already have active bid"
                    # skip must both see it on the very next candidate, not
                    # only after the next session's refresh.
                    ctx.my_bids = list(ctx.my_bids) + [obj.player]
                    ctx.my_bid_amounts[obj.player.id] = obj.recommended_bid
                continue

            elif kind == "pair":
                # Don't sell a player unnecessarily while there is an open slot —
                # the same target appears as a plain buy candidate instead. An
                # offer this session placed has taken its slot (Kickbase counts
                # open offers toward the cap), so pairs run once the squad is
                # full. PR 3's squad plan tightens this to "floor met and full".
                if available_slots > 0:
                    continue
                if ctx.my_bid_amounts.get(obj.buy_player.id, 0) > 0:
                    console.print(
                        f"[dim]Skip pair {obj.buy_player.last_name} — already have active bid[/dim]"
                    )
                    continue
                net_cost = obj.recommended_bid - int(
                    obj.sell_player.market_value * INSTANT_SELL_PCT
                )
                if net_cost > ctx.flip_budget:
                    console.print(
                        f"[yellow]Cannot afford trade pair "
                        f"{obj.sell_player.last_name}→{obj.buy_player.last_name} "
                        f"(net €{net_cost:,} > €{ctx.flip_budget:,})[/yellow]"
                    )
                    continue

                # The sell below is irreversible while the buy is only a bid,
                # so refuse to open a hole in the starting eleven that we may
                # not have time to close again. Bench sells pass freely.
                if obj.sell_is_starter and not _starter_swap_has_recovery_time(
                    ctx.matchday_phase.days_until_match,
                    self.settings.min_days_to_match_for_starter_swap,
                ):
                    console.print(
                        f"[yellow]Skip pair {obj.sell_player.last_name}→"
                        f"{obj.buy_player.last_name} — would sell a starter with "
                        f"{ctx.matchday_phase.days_until_match} day(s) to kickoff "
                        f"(need {self.settings.min_days_to_match_for_starter_swap}+); "
                        f"a lost auction would leave the eleven short[/yellow]"
                    )
                    logger.info(
                        "trade-pair skip starter-swap sell=%s buy=%s days_to_match=%s min=%d",
                        obj.sell_player.id,
                        obj.buy_player.id,
                        ctx.matchday_phase.days_until_match,
                        self.settings.min_days_to_match_for_starter_swap,
                    )
                    continue

                # REH-100: check the buy leg BEFORE the irreversible sell leg.
                refusal = self._trade_pair_preflight(obj, ctx)
                if refusal:
                    console.print(
                        f"[yellow]Skip pair {obj.sell_player.last_name}→"
                        f"{obj.buy_player.last_name} — gate would refuse the buy: "
                        f"{refusal}[/yellow]"
                    )
                    logger.error(
                        "trade-pair preflight refused sell=%s buy=%s: %s",
                        obj.sell_player.id,
                        obj.buy_player.id,
                        refusal,
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
                    current_budget=ctx.current_budget,
                    days_until_match=ctx.matchday_phase.days_until_match,
                    gate=_build_buy_gate(
                        settings=self.settings,
                        ctx=ctx,
                        player=obj.buy_player,
                        # The sell has landed, so its actual proceeds — not the
                        # estimate the pre-flight used — are what we may commit.
                        spendable_budget=int(ctx.flip_budget) + int(sell_result.price),
                        free_slots=1,
                        marginal_ep_gain=obj.ep_gain,
                        released_player_id=obj.sell_player.id,
                        session_refusal=getattr(ctx, "session_refusal", None),
                    ),
                )
                results.append(buy_result)
                if buy_result.success:
                    ctx.executed_trade_count += 1
                    self.daily_spend += obj.recommended_bid
                    # Use the actual sell proceeds (from sell_result.price) rather
                    # than the estimated market value, to avoid budget drift.
                    actual_net_cost = obj.recommended_bid - sell_result.price
                    ctx.flip_budget -= actual_net_cost
                    ctx.current_budget -= actual_net_cost
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
                        available_slots = _available_squad_slots(
                            current_squad_size, active_bid_count
                        )
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
                # REH-85 Finding 2: a flip is discretionary spend, and design
                # §3 says pacing applies to it same as a plain buy -- capital
                # parked in a flip is capital the reserve exists to protect.
                # `pacing` is None when pacing is off entirely, which must
                # not skip anything.
                pacing_ctx = ctx.ep_result.get("pacing")
                if pacing_ctx is not None:
                    pace_cap = pacing_ctx.max_bid(ctx.flip_budget, ctx.current_budget)
                    if opp.buy_price > pace_cap:
                        console.print(
                            f"[yellow]Cannot afford flip {opp.player.last_name} — "
                            f"pacing reserve (€{opp.buy_price:,} > €{pace_cap:,})[/yellow]"
                        )
                        continue

                result = self.execution.buy(
                    league,
                    opp.player,
                    opp.buy_price,
                    f"Flip: +{opp.expected_appreciation:.0f}% in {opp.hold_days}d",
                    current_budget=ctx.current_budget,
                    days_until_match=ctx.matchday_phase.days_until_match,
                    # The mark that makes this a flip from bid to sale: the
                    # sell loop reads it off the purchase record and trades
                    # the player on flip rules however the squad looks.
                    flip=FlipIntent(
                        target_pct=float(opp.expected_appreciation),
                        max_hold_days=int(opp.hold_days),
                    ),
                    # No marginal EP gain to band: a flip is bought to resell,
                    # not to improve the eleven. That resolves to the tightest
                    # ceiling, which is the right answer — the round-trip toll
                    # REH-64 measured is exactly what a flip pays.
                    gate=_build_buy_gate(
                        settings=self.settings,
                        ctx=ctx,
                        player=opp.player,
                        spendable_budget=int(ctx.flip_budget),
                        free_slots=available_slots,
                        marginal_ep_gain=None,
                        session_refusal=getattr(ctx, "session_refusal", None),
                    ),
                )
                results.append(result)
                if result.success:
                    ctx.executed_trade_count += 1
                    self.daily_spend += opp.buy_price
                    ctx.flip_budget -= opp.buy_price
                    ctx.current_budget -= opp.buy_price
                    available_slots -= 1

        console.print(
            f"\n[green]✓ Executed {ctx.executed_trade_count} trade(s) this session[/green]"
        )
        return results

    def _trade_pair_preflight(self, pair, ctx) -> str | None:
        """Would the gate refuse this pair's buy? Returns the reasons, or None.

        A pair sells before it bids — forced, because Kickbase counts open bids
        toward the 15-player cap, so at 15/15 the sell is what frees the slot.
        That makes the sell irreversible while the buy is still only a bid, and
        a gate consulted inside `ExecutionService.buy` would therefore fire
        *after* the squad was already a player lighter.

        So the same gate runs first, against the world as it will be once the
        sell lands: one free slot, and the sale proceeds added to what the
        phase allows us to commit. The gate inside `buy` still runs afterwards
        against the actual proceeds — this is a pre-flight, not a replacement.
        """
        proceeds = int(pair.sell_player.market_value * INSTANT_SELL_PCT)
        gate = _build_buy_gate(
            settings=self.settings,
            ctx=ctx,
            player=pair.buy_player,
            spendable_budget=int(ctx.flip_budget) + proceeds,
            free_slots=1,
            marginal_ep_gain=pair.ep_gain,
            released_player_id=pair.sell_player.id,
            session_refusal=getattr(ctx, "session_refusal", None),
        )
        verdict = gate.check(player_id=pair.buy_player.id, bid=int(pair.recommended_bid))
        return None if verdict.ok else "; ".join(verdict.reasons)

    def _run_debt_recovery(self, league, ctx: EPSessionContext) -> list[AutoTradeResult]:
        """Sell until the wallet, net of open offers, is back at zero.

        Runs only in the locked window (the last two days before kickoff),
        because a negative budget at kickoff is zero points for the whole
        matchday while a negative budget between rounds is how the bot buys
        players it cannot yet afford. Open offers count as spent: one that is
        won after this session and before kickoff drains the wallet too (rule
        I3's definition of covered).

        Who goes is `services/debt_recovery.plan_debt_recovery`'s call —
        profits first, slumping starters last, position minimums as the last resort (the fill refills).
        """
        from .formation import get_position_counts, select_best_eleven
        from .services.debt_recovery import DebtCandidate, plan_debt_recovery
        from .trader import Trader

        open_offers = sum(int(v or 0) for v in (ctx.my_bid_amounts or {}).values())
        shortfall = open_offers - int(ctx.current_budget)
        if shortfall <= 0:
            return []

        console.print(
            f"\n[bold red]💳 Debt recovery — wallet EUR {int(ctx.current_budget):,} "
            f"with EUR {open_offers:,} in open offers: EUR {shortfall:,} short of "
            f"zero at kickoff[/bold red]"
        )
        squad = self.api.get_squad(league)
        squad_scores = ctx.ep_result.get("squad_scores") or []
        score_map = {s.player_id: float(s.expected_points) for s in squad_scores}
        best_ids = {p.id for p in select_best_eleven(squad, score_map)}
        trader = Trader(
            self.api,
            self.settings,
            bid_learner=self.learner,
            activity_feed_learner=self.activity_feed_learner,
        )

        by_id = {p.id: p for p in squad}
        candidates: list[DebtCandidate] = []
        for p in squad:
            try:
                trend = trader.trend_service.get_trend(p.id, p.market_value, league.id).trend_7d_pct
            except Exception:
                trend = None
            buy_price = int(getattr(p, "buy_price", 0) or 0)
            candidates.append(
                DebtCandidate(
                    player_id=p.id,
                    name=f"{p.first_name} {p.last_name}".strip(),
                    position=p.position,
                    market_value=int(p.market_value),
                    buy_price=buy_price if buy_price > 0 else None,
                    expected_points=score_map.get(p.id, 0.0),
                    in_best_eleven=p.id in best_ids,
                    trend_7d_pct=trend,
                )
            )

        plan = plan_debt_recovery(
            candidates, shortfall=shortfall, position_counts=get_position_counts(squad)
        )
        logger.warning(
            "debt-recovery budget=%d open_offers=%d shortfall=%d sells=%d recovered=%d "
            "remaining=%d | %s",
            int(ctx.current_budget),
            open_offers,
            shortfall,
            len(plan.sells),
            plan.recovered,
            plan.remaining,
            ", ".join(
                f"{c.name}@{c.sell_value:,}"
                f"({'+' if (c.profit_pct or 0) >= 0 else ''}{(c.profit_pct or 0):.0f}%"
                f"{', starter' if c.in_best_eleven else ''})"
                for c in plan.sells
            ),
        )
        if plan.below_minimum:
            names = ", ".join(f"{c.name} ({c.position})" for c in plan.below_minimum)
            msg = (
                f"Debt recovery sells below a position minimum: {names} — "
                "the emergency fill buys the slot(s) back this session"
            )
            console.print(f"[bold yellow]{msg}[/bold yellow]")
            logger.warning("debt-recovery %s", msg)
        if not plan.covered:
            msg = (
                f"Debt recovery cannot cover EUR {plan.remaining:,} even selling the whole "
                "squad — the wallet stays negative"
            )
            console.print(f"[bold red]{msg}[/bold red]")
            logger.error("debt-recovery %s", msg)

        results: list[AutoTradeResult] = []
        for cand in plan.sells:
            player = by_id[cand.player_id]
            pct = cand.profit_pct
            why = (
                f"Debt recovery before kickoff — {'+' if (pct or 0) >= 0 else ''}"
                f"{(pct or 0):.1f}% vs cost basis"
                if pct is not None
                else "Debt recovery before kickoff — no cost basis"
            )
            result = self.execution.instant_sell(league, player, why)
            results.append(result)
            if result.success:
                ctx.current_budget += cand.sell_value
                ctx.squad = [p for p in ctx.squad if p.id != cand.player_id]
                self._session_recovered += cand.sell_value
        console.print(
            f"[green]✓ Debt recovery: sold {len([r for r in results if r.success])}/"
            f"{len(plan.sells)}, wallet now EUR {int(ctx.current_budget):,}[/green]"
        )
        return results

    def _run_emergency_squad_fill(
        self,
        league,
        ctx: EPSessionContext,
        fresh_squad: list,
        slots_short: int,
    ) -> list[AutoTradeResult]:
        """Buy enough players to reach 11, even when phase is "locked".

        Locked phase normally blocks all buys to keep budget liquid at kickoff,
        but an empty lineup slot is -100 pts per match — a far worse failure
        mode than a few hours of leftover debt. This path:

        - Buys only plain in-budget candidates (no sell plans, no flips, no
          trade pairs — those all add complexity right before kickoff).
        - Buys only positions that actually close a slot: a position a legal
          formation still needs, re-checked as proposals land.
        - Honors wash-trade and active-bid guards.
        - Caps spend at ``slots_short`` purchases, and at the free squad
          slots — a full 15/15 squad needs a swap, which this path refuses to
          make.
        """
        results: list[AutoTradeResult] = []

        # A full squad has nowhere to put a sixteenth player, so an
        # unfieldable 15/15 is not a buying problem at all — it is a seventh
        # defender where a forward should be, and only a swap fixes it. Say so
        # once and stop, rather than proposing a purchase that cannot land.
        if len(fresh_squad) >= SQUAD_CAP:
            msg = (
                f"emergency-fill: squad full ({len(fresh_squad)}/{SQUAD_CAP}) "
                "and unfieldable — needs a swap, not a buy"
            )
            console.print(f"[red]{msg}[/red]")
            logger.warning(msg)
            return results

        # `_build_buy_gate`'s club-limit count reads `ctx.squad`; the caller
        # re-fetched the squad into `fresh_squad` but never wrote it back.
        ctx.squad = list(fresh_squad)
        buy_recs = ctx.ep_result.get("buy_recs", [])
        if not buy_recs:
            console.print("[red]No buy candidates available — cannot fill emergency slots[/red]")
            return results

        from .formation import fieldability_from_counts, get_position_counts

        counts = get_position_counts(fresh_squad)
        need = fieldability_from_counts(counts)
        gap_positions = set(need.positions)

        def _gap_after(positions) -> int:
            after = dict(counts)
            for pos in positions:
                after[pos] = after.get(pos, 0) + 1
            return fieldability_from_counts(after).purchases

        active_bid_ids = set(ctx.my_bid_amounts.keys())
        # Open offers are money already committed — a bid that resolves
        # after the fill has spent the wallet is the negative-budget-at-
        # kickoff path, which zeroes the ENTIRE matchday's points. That is
        # strictly worse than the -100 an unfilled slot costs, so the fill
        # must net open offers out of what it treats as spendable. PR 3's
        # squad plan replaces this with a pending-bid-aware allowance.
        open_offers = sum(int(v or 0) for v in ctx.my_bid_amounts.values())
        budget_remaining = int(ctx.current_budget) - open_offers
        if open_offers > 0:
            logger.warning(
                "emergency-fill budget netted for open offers: wallet=%d open_offers=%d spendable=%d",
                int(ctx.current_budget),
                open_offers,
                budget_remaining,
            )

        # REH-113: choose the BASKET that scores the most points, not the
        # best-ranked player affordable right now. An empty slot is -100 per
        # slot regardless of who fills it, so `select_emergency_basket`
        # maximises `total_ep + 100 x count` on ASK price and spends the
        # leftover as overbid afterwards. The old greedy walk ignored the
        # penalty entirely and bought 3 of 4 on 2026-08-31, missing the fourth
        # by 1,168,502 of overbid it had already committed elsewhere.
        from .config import MAX_PLAYERS_PER_CLUB
        from .services.emergency_basket import EmergencyCandidate, select_emergency_basket

        # League rule: three per club, open bids included. A candidate the gate
        # would refuse must not take the basket's pick — on 2026-09-15 Henrichs
        # did, with three Leipzig players already held, and the slot stayed empty.
        club_held = club_counts(list(fresh_squad or []) + list(ctx.my_bids or []))

        by_id: dict[str, Any] = {}
        candidates: list[EmergencyCandidate] = []
        for rec in buy_recs:
            # REH-85 pacing can legitimately size recommended_bid to 0 (its
            # reserve rule consumed the whole spendable budget). An empty
            # lineup slot costs -100 pts at kickoff, which outranks the
            # pacing reserve, so this path is deliberately exempt from it:
            # fall back to the asking price, which is what the plan
            # anticipated paying, whenever the paced bid is zero or missing.
            bid = (
                rec.recommended_bid
                if rec.recommended_bid and rec.recommended_bid > 0
                else rec.player.price
            )
            if not bid or bid <= 0:
                continue
            if rec.player.id in active_bid_ids:
                continue
            if self._is_wash_trade(rec.player.id):
                console.print(f"[dim]Skip {rec.player.last_name} — wash-trade block[/dim]")
                continue
            club = str(getattr(rec.player, "team_id", "") or "")
            if club and club_held.get(club, 0) >= MAX_PLAYERS_PER_CLUB:
                console.print(
                    f"[dim]Skip {rec.player.last_name} — already hold "
                    f"{club_held[club]} from club {club}[/dim]"
                )
                logger.info(
                    "emergency-skip player=%s club=%s held=%d limit=%d",
                    rec.player.id,
                    club,
                    club_held[club],
                    MAX_PLAYERS_PER_CLUB,
                )
                continue
            # Only plain in-budget candidates — sell plans add execution risk
            # at kickoff that the emergency path explicitly avoids. The
            # affordability check still applies to the fallback bid — the
            # exemption is from pacing, not from the budget guard.
            # The floor is the asking price — a bid below it cannot win — and
            # the ceiling is the paced bid the gate will accept. Selection
            # happens between the two.
            ask = int(rec.player.price) or int(bid)
            by_id[rec.player.id] = rec
            candidates.append(
                EmergencyCandidate(
                    id=rec.player.id,
                    name=rec.player.last_name,
                    ask=ask,
                    max_bid=max(int(bid), ask),
                    ep=float(rec.marginal_ep_gain or 0.0),
                    fills_gap=rec.player.position in gap_positions,
                    position=rec.player.position,
                )
            )

        picks = select_emergency_basket(
            candidates, slots_short, budget_remaining, gap_after=_gap_after
        )

        if not picks:
            console.print(
                "[red]No affordable wash-trade-clean candidates " "to fill empty slot(s)[/red]"
            )
            return results

        logger.info(
            "emergency-basket slots_short=%d budget=%d picked=%d spend=%d | %s",
            slots_short,
            budget_remaining,
            len(picks),
            sum(p.bid for p in picks),
            ", ".join(f"{p.candidate.name}@{p.bid:,}" for p in picks),
        )

        # The basket is the plan; the rest of the board is the reserve behind
        # it. A gate refusal means "try the next candidate", not "field
        # nobody", so an unchosen candidate must still be reachable when a
        # pick is refused — otherwise the slot stays empty at -100.
        chosen_ids = {p.candidate.id for p in picks}
        attempts: list[tuple[Any, int]] = [(by_id[p.candidate.id], p.bid) for p in picks]
        attempts += [
            (by_id[c.id], c.max_bid)
            for c in sorted(candidates, key=lambda c: -c.ep)
            if c.id not in chosen_ids and c.position in gap_positions
        ]

        # An empty slot is -100 at kickoff whether or not anyone is watching,
        # so the fill spends (spec §1). REH-114 made it propose with a 24h
        # auto-approve; checked on a 12h timer that fired 24-36h later, and
        # El-Faouzi was gone by then. The gate is the only thing between a
        # pick and the money, and a refusal means "try the next candidate",
        # not "field nobody". What the loop must NOT do is relax the budget
        # rule: a negative budget at kickoff is zero points for the entire
        # matchday, far worse than -100.
        bought = 0
        bought_positions: list[str] = []
        for rec, bid in attempts:
            if bought >= slots_short:
                break
            if bid > budget_remaining:
                continue

            # The basket refuses a buy that closes no slot; the reserves walk
            # behind it has to honour the same invariant, because the gap
            # moves as purchases land. With two slots open at 6 DEF / 3 MID /
            # 0 FW the first midfielder closes one and the second closes
            # none — only a forward closes what is left — and buying him
            # anyway is the seventh defender again, one position over.
            if _gap_after(bought_positions + [rec.player.position]) >= _gap_after(bought_positions):
                console.print(
                    f"[dim]Skip {rec.player.last_name} — "
                    f"{rec.player.position} closes no remaining lineup slot[/dim]"
                )
                continue

            result = self.execution.buy(
                league,
                rec.player,
                bid,
                f"Emergency lineup fill (squad short by {slots_short})",
                current_budget=budget_remaining,
                days_until_match=ctx.matchday_phase.days_until_match,
                gate=_build_buy_gate(
                    settings=self.settings,
                    ctx=ctx,
                    player=rec.player,
                    spendable_budget=budget_remaining,
                    # Two different limits, and the gate needs the binding one:
                    # how many more players this emergency wants, and how many
                    # the squad can still hold. `slots_short` alone is a claim
                    # about the lineup, and a squad at 14/15 two slots short
                    # would have used it to buy with no room. Each purchase
                    # this session takes one of both.
                    free_slots=min(slots_short, SQUAD_CAP - len(fresh_squad)) - bought,
                    marginal_ep_gain=rec.marginal_ep_gain,
                    # No `session_refusal`: this is the emergency fill, and an
                    # empty lineup slot is -100 points — worse than the deficit
                    # PR D's I3 guards against, so this path is exempt from it.
                ),
            )
            results.append(result)
            # The fill's offers reach the board and the session counters too
            # — before this, an emergency pick never showed up on the board
            # or in `offers_placed`/`offers_refused`, mirroring `_execute_buy`.
            if result.success:
                outcome, detail = "placed", ""
                ctx.offers_placed += 1
                self._session_offer_ids.add(str(rec.player.id))
            elif (result.error or "").startswith("safety gate refused: "):
                outcome, detail = "refused", (result.error or "")[len("safety gate refused: ") :]
                ctx.offers_refused += 1
            else:
                outcome, detail = "failed", (result.error or "unknown error")
                ctx.offers_refused += 1
            self._session_board.append(
                _offer_line(
                    uuid.uuid4().hex[:12], rec, bid, None, [], outcome=outcome, detail=detail
                )
            )
            if not result.success:
                continue
            bought += 1
            bought_positions.append(rec.player.position)
            # The next pick sees what this one spent, so a basket cannot
            # assume the whole wallet twice.
            budget_remaining -= bid
            self.daily_spend += bid
            gap_positions.discard(rec.player.position)
            # Mirror the plain-buy branch: this offer is held for the rest of
            # THIS session too, so the club-limit gate sees it on the very
            # next candidate rather than only after the next refresh.
            ctx.my_bids = list(ctx.my_bids) + [rec.player]
            ctx.my_bid_amounts[rec.player.id] = bid

        console.print(f"[green]✓ Emergency fill: bought {bought}/{slots_short} player(s)[/green]")
        logger.info(
            "emergency-fill slots_short=%d bought=%d spend=%d",
            slots_short,
            bought,
            sum(r.price for r in results if r.success),
        )
        return results

    def _wash_trade_block_seconds(self) -> float:
        return float(getattr(self.settings, "wash_trade_block_hours", 168.0)) * 3600.0

    def _min_hold_seconds(self) -> float:
        return float(getattr(self.settings, "min_hold_hours_before_sell", 48.0)) * 3600.0

    def _is_wash_trade(self, player_id: str) -> bool:
        """True if we sold this player within the wash-trade block window."""
        try:
            return self.learner.was_recently_sold(player_id, self._wash_trade_block_seconds())
        except Exception:
            return False  # Guard never blocks trading on infrastructure errors

    def _was_recently_bought(self, player_id: str) -> tuple[bool, float | None]:
        """Return (held_too_briefly, hours_held). hours_held is None if untracked."""
        try:
            purchase = self.learner.get_tracked_purchase(player_id)
        except Exception:
            return (False, None)
        if not purchase:
            return (False, None)
        buy_date = purchase.get("buy_date")
        if not buy_date:
            return (False, None)
        held_seconds = time.time() - float(buy_date)
        if held_seconds < self._min_hold_seconds():
            return (True, held_seconds / 3600.0)
        return (False, held_seconds / 3600.0)

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
        """Trend-aware sell monitoring: profit/loss exits AND dead-weight release.

        Uses formation-aware best-11 to protect true starters, and trend data
        to dynamically adjust sell thresholds. Only sells best-11 members when
        a replacement is lined up in the EP pipeline.

        Two different behaviours live in this one method, and only one of them
        is flipping (REH-71):

        * **Profit-taking / loss-cutting** against the cost basis. This is
          trading for cash, and it is what ``Settings.enable_profit_sells``
          exists to switch off.
        * **Dead-weight release** — dumping a position-saturated bench player
          (a 5th goalkeeper, a 6th defender) who can never enter any starting
          eleven under any formation, so that the squad slot is free for a
          points upgrade. The slot is the asset; the branch deliberately
          accepts a small market-value loss to obtain it.

        The dead-weight branch serves POINTS, not profit. It was never part of
        the flip question REH-71 asked, the season replay never modelled it,
        and the 2x2 factorial measured nothing about it — so
        ``enable_profit_sells=False`` must leave it running. Gating it too
        would be an unmeasured live regression, not a decision anyone made.
        Hence the switch guards the candidate loop below rather than the whole
        method: both branches share the squad refresh, the best-11
        computation, the position counts and the trend lookups.
        """
        from .formation import select_best_eleven
        from .trader import Trader

        profit_sells_enabled = self.settings.enable_profit_sells

        results = []
        console.print("\n[bold cyan]📈 Sell Monitoring (trend-aware)[/bold cyan]")
        if not profit_sells_enabled:
            console.print(
                "[dim]Profit selling disabled (REH-71) — dead-weight release still runs[/dim]"
            )

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

        # Marked flips first (2026-09-24). A player bought to resell is traded
        # on flip rules however the squad looks around him — in particular he
        # is NOT protected by the best-eleven gate below, which at nine
        # players covers the whole squad and used to make a flip unsellable
        # for as long as the squad was short. The mark comes from the
        # purchase record (`FlipIntent` → `tracked_purchases.intent`).
        # Rules, per Marco: sell at the trend-adjusted target or at the
        # stop-loss (no replacement required — a flip is not a starter by
        # intent); the hold limit is reported, never a trigger.
        flip_rows: dict[str, dict] = {}
        if profit_sells_enabled:
            try:
                found = self.learner.get_tracked_purchases(intent="flip")
            except Exception:
                logger.warning("marked flips unavailable — selling on squad rules", exc_info=True)
                found = None
            # Best-effort like every learner read: anything but a real mapping
            # (a failed lookup, a stub) means "no marked flips", never a crash.
            flip_rows = dict(found) if isinstance(found, dict) else {}
        for player in squad:
            row = flip_rows.get(player.id)
            if row is None:
                continue

            held_too_briefly, hours_held = self._was_recently_bought(player.id)
            if held_too_briefly:
                console.print(
                    f"[dim]Hold-period guard {player.last_name} (flip) — held "
                    f"{hours_held:.1f}h (< {self._min_hold_seconds() / 3600:.0f}h min)[/dim]"
                )
                continue

            pos_min = POSITION_MINIMUMS.get(player.position, 0)
            if position_counts.get(player.position, 0) <= pos_min:
                console.print(
                    f"[dim]Protected {player.last_name} (flip, {player.position}) — "
                    f"at position minimum ({pos_min})[/dim]"
                )
                continue

            buy_price = int(player.buy_price or 0) or int(row.get("buy_price") or 0)
            if buy_price <= 0:
                continue
            profit = player.market_value - buy_price
            profit_pct = (profit / buy_price) * 100

            try:
                trend_7d = trader.trend_service.get_trend(
                    player.id, player.market_value, league.id
                ).trend_7d_pct
            except Exception:
                trend_7d = None
            trend_7d_by_id[player.id] = trend_7d

            target = self._sell_threshold_for_trend(trend_7d)
            days_held = (
                (time.time() - float(row["buy_date"])) / 86_400 if row.get("buy_date") else None
            )
            max_hold = row.get("max_hold_days")
            overdue = days_held is not None and max_hold is not None and days_held > max_hold
            trend_info = f", trend {trend_7d:+.1f}%/wk" if trend_7d is not None else ""
            logger.info(
                "flip-held player=%s profit_pct=%.1f target=%.1f days_held=%s max_hold=%s",
                player.id,
                profit_pct,
                target,
                f"{days_held:.1f}" if days_held is not None else None,
                max_hold,
            )
            if profit_pct >= target:
                sell_candidates.append(
                    (
                        player,
                        profit_pct,
                        f"Flip target ({target:.0f}%) hit: +{profit_pct:.1f}% "
                        f"(€{profit:,}{trend_info})",
                    )
                )
            elif profit_pct <= self.settings.max_loss_pct and self._can_loss_sell_with_replacement(
                trend_7d
            ):
                sell_candidates.append(
                    (
                        player,
                        profit_pct,
                        f"Flip stop-loss ({self.settings.max_loss_pct:.0f}%): "
                        f"{profit_pct:.1f}% (€{profit:,}{trend_info})",
                    )
                )
            elif overdue:
                console.print(
                    f"[dim]Flip {player.last_name} past its {max_hold}d hold at "
                    f"{profit_pct:+.1f}% — holding for the target or the stop-loss[/dim]"
                )

        # Profit-taking and loss-cutting: the flip behaviour, and the only
        # part of this method REH-71's switch governs.
        if profit_sells_enabled:
            for player in squad:
                if player.id in best_11_ids or player.id in flip_rows:
                    continue

                if not player.buy_price or player.buy_price <= 0:
                    continue

                # Min-hold guard: refuse to sell a player we just bought. Same-
                # session reversals (bid → win → instant-sell at -71% within 7h
                # for Niang in production) cost both the bid spread and a
                # market-value loss, with no signal change to justify them.
                held_too_briefly, hours_held = self._was_recently_bought(player.id)
                if held_too_briefly:
                    console.print(
                        f"[dim]Hold-period guard {player.last_name} — held "
                        f"{hours_held:.1f}h (< {self._min_hold_seconds() / 3600:.0f}h min)[/dim]"
                    )
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
                    trend = trader.trend_service.get_trend(
                        player.id, player.market_value, league.id
                    )
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
                            f"Profit target ({sell_threshold:.0f}%) hit: "
                            f"+{profit_pct:.1f}% (€{profit:,}{trend_info})",
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
        #
        # DELIBERATELY NOT gated on `enable_profit_sells` (REH-71): this is a
        # points move, not a flip. See the method docstring.
        from .formation import _POSITION_MAX_STARTERS

        already_selling = {p.id for p, _, _ in sell_candidates}
        for player in squad:
            if player.id in best_11_ids or player.id in already_selling or player.id in flip_rows:
                continue
            if not player.buy_price or player.buy_price <= 0:
                continue

            held_too_briefly, _ = self._was_recently_bought(player.id)
            if held_too_briefly:
                continue  # Already logged in the loop above; skip silently here.

            # Saturation implies the position is comfortably above its
            # formation minimum (max starters >= minimum for every position),
            # so loop 1's explicit POSITION_MINIMUMS guard is redundant here —
            # which is why this branch stays correct when loop 1 is skipped.
            max_starters = _POSITION_MAX_STARTERS.get(player.position, 3)
            if position_counts.get(player.position, 0) <= max_starters:
                continue  # Position not saturated — not dead weight

            profit = player.market_value - player.buy_price
            profit_pct = (profit / player.buy_price) * 100

            # Reuse the trend cached in the first loop when there is one. With
            # profit selling enabled, every player reaching this point passed
            # the same best_11 + buy_price gates in loop 1 and either landed in
            # `already_selling` (filtered above) or had its trend cached, so a
            # missing key would mean an upstream filter changed. With profit
            # selling disabled the cache is empty by construction because loop
            # 1 never ran. Both cases fall through to the same lookup.
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

        The flow, with the step numbers the console and the logs use:

        0. Sync the activity feed (competitive intelligence).
        1. Resolve pending bids into won/lost, reconcile squad cost basis, and
           execute the sell plans deferred behind auctions we won — that last
           part only in ``full`` mode; ``lineup_only`` skips and logs them.
        2. Build the session context (one EP pipeline call + trends +
           matchday timing).
        2a. Learning: reconcile finished matchdays, settle the league's Top-5
           forced-sale obligation, snapshot predictions and team value.
        3. Emergency squad fill when no legal eleven is fieldable. Runs in
           EVERY phase, locked included — an empty slot is -100 a matchday.
        Then stop after the lineup if the match is imminent (phase ``locked``)
        or ``trading_mode=lineup_only``; steps 4 to 7 do not run.
        4. Trend-aware profit selling.
        5. Squad optimisation (budget/size safety).
        6. Bid compliance + open-bid quality check.
        7. Unified trade phase (trade pairs compete with plain buys by EP).
        8. Set the optimal lineup.
        """
        start_time = time.time()

        console.print(f"\n{'=' * 70}")
        console.print(
            f"[bold]Automated Trading Session - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/bold]"
        )
        if self.dry_run:
            console.print("[yellow]DRY RUN MODE - No trades will be executed[/yellow]")
        console.print(f"{'=' * 70}")

        # REH-117: one batch per session, so "Approve all" can take the set
        # that was chosen to fit the budget together.
        self._session_board = []
        self._session_budget_before = None
        self._session_recovered = 0
        self._session_open_offers_before = None
        self._session_offer_ids = set()
        self._session_batch_id = uuid.uuid4().hex[:12]

        # Task 5: an early row, written before anything that could fail. A
        # session that dies later still leaves this behind -- the whole
        # reason `record` upserts rather than inserts once at the end.
        self._facts = SessionFacts(
            session_id=self._session_batch_id,
            app=self.app_name,
            mode=self.settings.trading_mode,
            dry_run=self.dry_run,
            started_at=start_time,
        )
        try:
            self._session_store.record(self._facts)
        except Exception:
            logger.warning("session-facts: could not record start row", exc_info=True)

        logger.info(
            "session-start league=%s mode=%s dry_run=%s max_trades=%d max_spend=%d",
            getattr(league, "name", league.id),
            self.settings.trading_mode,
            self.dry_run,
            self.max_trades_per_session,
            self.max_daily_spend,
        )
        if self.settings.trading_mode == "lineup_only":
            console.print(
                "[yellow]MODE lineup_only — no sells, no buys, except the emergency fill "
                "and the league's Top-5 forced sale[/yellow]"
            )

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
            # REH-86: fill winning_bid/winner_user_id on auctions we lost,
            # from the transfer feed earlier sessions already ingested. Runs
            # before resolution so this session's newly-resolved losses are
            # picked up on the next pass, once their transfer has landed.
            try:
                if self.learner is not None:
                    self.learner.resolve_auction_winners()
            except Exception as e:  # pragma: no cover - defensive
                logger.debug("resolve_auction_winners failed: %s", e)

            deferred_sell_ids = self.tracker.resolve_auctions(
                squad_ids={p.id for p in squad},
                active_bid_ids={p.id for p in bids},
            )

            # REH-103: resolve_auctions can only record a purchase it can match
            # to a pending bid, so a player who joined the squad any other way
            # is tracked nowhere — and with no cost basis, `enable_profit_sells`
            # can never evaluate them. Raum (EUR 40.7m) arrived that way and
            # left 10 of 12 squad players unsellable-on-profit, silently.
            # Reconciling against squad membership closes the gap regardless of
            # how the player arrived. Never raises; reports what it could not
            # recover rather than inventing a price.
            try:
                my_id = getattr(getattr(self.api, "user", None), "id", None)
                if my_id:
                    rec = self.tracker.reconcile_squad_cost_basis(squad, manager_id=str(my_id))
                    self._facts.cost_basis_missing = len(rec.still_missing)
            except Exception:  # pragma: no cover - defensive
                logger.exception("cost-basis reconciliation failed (non-fatal)")
            # Execute any deferred sell plans from bids we won (buy-first-sell-after).
            if deferred_sell_ids and self.settings.trading_mode != "full":
                console.print(
                    f"[yellow]Mode lineup_only — {len(deferred_sell_ids)} deferred sell "
                    f"plan(s) skipped[/yellow]"
                )
                logger.info(
                    "trading-mode lineup_only: deferred sell plans skipped n=%d",
                    len(deferred_sell_ids),
                )
            elif deferred_sell_ids:
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
            logger.exception("Auction resolution failed")

        # Step 2: Build session context (single EP pipeline + trends + matchday phase)
        try:
            ctx = self._build_session_context(league)
            # Snapshot the wallet BEFORE anything this session moves — the
            # board's header reads this rather than re-deriving it, since a
            # derivation from `placed` alone misses trade pairs and flips.
            self._session_budget_before = int(ctx.current_budget)
            self._session_open_offers_before = sum(int(v or 0) for v in ctx.my_bid_amounts.values())
        except Exception as e:
            error_msg = f"EP pipeline failed: {e!s}"
            console.print(f"[red]{error_msg}[/red]")
            errors.append(error_msg)
            logger.exception("EP pipeline failed — falling back to lineup-only")
            # Fall back to just setting lineup
            lineup = self._set_optimal_lineup(league, errors) or []
            # "unknown" because this exit never learned a phase -- the EP
            # pipeline call that would have told us is exactly what failed.
            # Today this exit logs no session-end at all, which is why prod
            # telemetry has no trace of a pipeline failure ever happening.
            integrity_failures = self._finish_facts(errors, start_time, "unknown", league)
            logger.info(
                "session-end duration=%.1fs mode=%s phase=unknown errors=%d",
                time.time() - start_time,
                self.settings.trading_mode,
                len(errors),
            )
            return AutoTradeSession(
                start_time=start_time,
                end_time=time.time(),
                profit_trades=[],
                lineup_trades=[],
                errors=errors,
                total_spent=0,
                total_earned=0,
                net_change=0,
                lineup=lineup,
                session_id=self._session_batch_id,
                integrity_failures=integrity_failures,
            )

        # Step 2a: Matchday self-calibration (REH-20).
        #
        # Reconcile finished matchdays FIRST using snapshots from prior
        # sessions, THEN snapshot the current session. Order matters:
        # snapshotting first would make the current run's prediction the
        # "latest snapshot before kickoff" for any matchday whose `md`
        # lies between this snapshot and the next reconcile — corrupting
        # the actual-vs-predicted pairing.
        #
        # Both calls swallow exceptions internally so a learning-side
        # failure never blocks the trading loop.
        try:
            squad_perf = ctx.ep_result.get("squad_performance") or {}
            self.tracker.reconcile_finished_matchdays(ctx.squad, squad_perf)
        except Exception:
            logger.exception("reconcile_finished_matchdays failed (non-fatal)")

        # League Top-5 rule: finishing in the top five of a matchday obliges us
        # to give up one of our best performers from it. Settled here, right
        # after the matchday is reconciled, because the obligation only exists
        # once a matchday has actually finished — and settled at most once per
        # matchday, which `record_forced_sale` enforces on the database rather
        # than on this call site.
        try:
            self._settle_top5_obligation(league, ctx)
        except Exception:
            logger.exception("top5 settlement failed (non-fatal)")
        try:
            squad_scores = ctx.ep_result.get("squad_scores") or []
            lineup_map = ctx.ep_result.get("lineup_map") or {}
            # Best-11 = top 11 player_ids by EP. lineup_map is {pid: ep}.
            best_11 = {
                pid
                for pid, _ep in sorted(lineup_map.items(), key=lambda kv: kv[1], reverse=True)[:11]
            }
            self.tracker.snapshot_predictions(
                league_id=league.id,
                squad_scores=squad_scores,
                best_11_ids=best_11,
            )
        except Exception:
            logger.exception("snapshot_predictions failed (non-fatal)")

        # PR E §1: score every live player from store rows and write
        # `rehoboam.predictions`. Best-effort and store-only -- no API call --
        # so a store outage or a missing matchday never stops the session; it
        # only leaves I5 (no predictions written) to report why.
        try:
            league_written = self._write_league_predictions(ctx, self._next_kickoff)
        except Exception:
            logger.exception("league predictions failed (non-fatal)")
            league_written = 0
        self._facts.predictions_written = int(league_written)

        # G1: the market, the managers and every squad the session already
        # fetched -- no new API call, best-effort like everything else here.
        try:
            counts = self._write_league_state(ctx, league)
            extra = dict(self._facts.extra or {})
            extra["league_state"] = counts
            self._facts.extra = extra
        except Exception:
            logger.exception("league state write failed (non-fatal)")
        try:
            # REH-23: persist the team_value/budget snapshot the bot already
            # fetched in _build_session_context. Provides the longitudinal
            # series for goal 3 (team value growth) and feeds REH-37.
            self.learner.record_team_value_snapshot(
                league_id=league.id,
                team_value=ctx.team_value,
                budget=ctx.current_budget,
                squad_size=len(ctx.squad),
            )
        except Exception:
            logger.exception("record_team_value_snapshot failed (non-fatal)")

        # Step 2b: in the locked window a negative wallet is zero points for
        # the entire matchday — worse than anything the fill below can fix.
        # Sell back to zero FIRST, so the fill can refill any slot this frees
        # with money that is actually there. Every phase before this one may
        # run a debt (`_compute_flip_budget`); this is where it is repaid.
        # Gated on the DAY COUNT like `ExecutionService.buy`'s lockout guard,
        # not on the phase label: `matchday_in_progress` can also sit within a
        # day of the next kickoff, and an unknown schedule (None) must not
        # pretend to be either.
        days_left = ctx.matchday_phase.days_until_match
        if days_left is not None and days_left <= LOCKOUT_DAYS:
            try:
                sell_results.extend(self._run_debt_recovery(league, ctx))
            except Exception as e:
                error_msg = f"Debt recovery failed: {e!s}"
                console.print(f"[red]{error_msg}[/red]")
                errors.append(error_msg)
                logger.exception("debt recovery failed")

        # Step 3: A squad that cannot field a legal eleven is an emergency in
        # EVERY phase (REH-112). This used to sit inside the `locked` branch
        # below, so it could only run when the phase detector had found an
        # imminent fixture. On 2026-08-31 `/myeleven` reported no upcoming
        # fixture between MD2 and MD3, `_get_matchday_phase` took its `else`
        # branch to "moderate", and a squad of 7 sat four slots short — a
        # standing -400 — with the fill unreachable. That `else` is
        # conservative about *spending*, which is right; the -100 is not
        # spending, and the fail-safe has to fail toward fielding an eleven.
        #
        # Every phase, but not every DAY (2026-09-22). The fill is the "buy
        # almost anything" path -- relaxed filters, the leftover spent as
        # overbid -- and that is the price of the last day before kickoff,
        # when the locked phase has stood every other buy path down. On
        # 2026-09-22 it ran seventeen days out, in the aggressive phase, and
        # bought a falling non-starter at +overbid while the ordinary trade
        # phase below could have closed the slot properly. So the shortfall
        # is answered by `emergency_fill_due`: the last `emergency_fill_days`
        # days, or an unknown schedule (the 2026-08-31 state above).
        fresh_squad = self.api.get_squad(league)
        slots_short = _emergency_slots_short(fresh_squad)
        fill_due = emergency_fill_due(
            ctx.matchday_phase.days_until_match,
            window_days=self.settings.emergency_fill_days,
        )
        if slots_short > 0 and not fill_due:
            console.print(
                f"[yellow]Squad short by {slots_short} (squad {len(fresh_squad)}), kickoff in "
                f"{ctx.matchday_phase.days_until_match}d — the trading phases fill it; "
                f"the emergency fill waits for the last "
                f"{self.settings.emergency_fill_days}d.[/yellow]"
            )
            logger.info(
                "squad short by %d (squad=%d) days_to_match=%s phase=%s — "
                "emergency fill waits for the last %dd; the trading phases fill it",
                slots_short,
                len(fresh_squad),
                ctx.matchday_phase.days_until_match,
                ctx.matchday_phase.phase,
                self.settings.emergency_fill_days,
            )
        if slots_short > 0 and fill_due:
            from .formation import can_fill_starting_eleven

            reason = can_fill_starting_eleven(fresh_squad)["reason"]
            console.print(
                f"[bold red]⚠ LINEUP EMERGENCY — {reason} "
                f"(squad {len(fresh_squad)}, buying {slots_short}). "
                f"Phase '{ctx.matchday_phase.phase}' overridden.[/bold red]"
            )
            logger.warning(
                "lineup emergency: squad=%d slots_short=%d phase=%s — %s",
                len(fresh_squad),
                slots_short,
                ctx.matchday_phase.phase,
                reason,
            )
            try:
                emergency_results = self._run_emergency_squad_fill(
                    league, ctx, fresh_squad, slots_short
                )
                trade_results.extend(emergency_results)
            except Exception as e:
                error_msg = f"Emergency squad fill failed: {e!s}"
                console.print(f"[red]{error_msg}[/red]")
                errors.append(error_msg)

        # Reuses `fresh_squad` rather than re-fetching: the fill (if it ran)
        # placed bids, not squad changes -- a won auction only lands on the
        # squad once the bid resolves, next session. Runs whether or not
        # there was an emergency: I2 needs to know the squad ended up
        # fieldable either way.
        try:
            from .formation import fieldability

            fb = fieldability(fresh_squad)
            self._facts.fieldable_count = 11 if fb.ok else 11 - fb.purchases
        except Exception:
            logger.warning("facts: could not compute fieldable_count", exc_info=True)

        # Two reasons to stop after the lineup: the match is imminent, or the
        # bot is in lineup_only mode (spec 2026-09-11 §5). The emergency fill
        # above has already had its chance to make an eleven fieldable.
        stop_reason: str | None = None
        if ctx.matchday_phase.phase == "locked":
            stop_reason = f"Match imminent ({ctx.matchday_phase.days_until_match}d)"
        elif self.settings.trading_mode == "lineup_only":
            stop_reason = "Mode lineup_only"
        if stop_reason is not None:
            return self._finish_lineup_only(
                league, ctx, trade_results, sell_results, errors, start_time, stop_reason
            )

        # Step 4: Trend-aware profit selling
        try:
            sell_results.extend(self.run_profit_sell_phase(league, ctx))
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

                compliance_checker = LeagueComplianceChecker(
                    self.api, self.settings, learner=self.learner
                )
                adjusted, canceled = compliance_checker.run_bid_compliance_check(
                    league, player_trends=player_trends, auto_resolve=True, dry_run=self.dry_run
                )
                if adjusted > 0 or canceled > 0:
                    console.print(
                        f"[cyan]Bid compliance: {adjusted} adjusted, {canceled} canceled[/cyan]"
                    )

                self._evaluate_open_bids(league, player_trends=player_trends)
            except Exception as e:
                console.print(f"[yellow]Bid compliance check failed: {e}[/yellow]")

        # Step 7: Unified trade phase (EP buys + trade pairs + profit flips)
        try:
            trade_results.extend(self.run_unified_trade_phase(league, ctx))
        except Exception as e:
            error_msg = f"Trading error: {e!s}"
            console.print(f"[red]{error_msg}[/red]")
            errors.append(error_msg)
            logger.exception("Unified trade phase failed")

        # Step 8: Set optimal lineup using EP pipeline scores from the session.
        # Players acquired mid-session (if any) are scored by the v2 fallback
        # inside _set_optimal_lineup.
        self._send_session_board(league, ctx)

        lineup = (
            self._set_optimal_lineup(league, errors, squad_scores=ctx.ep_result.get("squad_scores"))
            or []
        )

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
        console.print(f"Offers: {ctx.offers_placed} placed, {ctx.offers_refused} refused")
        console.print(f"Total spent: €{total_spent:,}")
        console.print(f"Total earned: €{total_earned:,}")
        net_color = "green" if net_change >= 0 else "red"
        console.print(f"Net change: [{net_color}]€{net_change:,}[/{net_color}]")

        if errors:
            console.print(f"\n[red]Errors: {len(errors)}[/red]")
            for err in errors:
                console.print(f"[red]  • {err}[/red]")

        logger.info(
            "session-end duration=%.1fs mode=%s phase=%s sells=%d trades=%d/%d "
            "offers=%d refused=%d "
            "spent=%d earned=%d net=%d errors=%d",
            end_time - start_time,
            self.settings.trading_mode,
            ctx.matchday_phase.phase,
            len([r for r in sell_results if r.success and r.action == "SELL"]),
            len([r for r in trade_results if r.success]),
            len(trade_results),
            ctx.offers_placed,
            ctx.offers_refused,
            total_spent,
            total_earned,
            net_change,
            len(errors),
        )

        integrity_failures = self._finish_facts(
            errors, start_time, ctx.matchday_phase.phase, league
        )

        return AutoTradeSession(
            start_time=start_time,
            end_time=end_time,
            profit_trades=trade_results,
            lineup_trades=sell_results,
            errors=errors,
            total_spent=total_spent,
            total_earned=total_earned,
            net_change=net_change,
            lineup=lineup,
            session_id=self._session_batch_id,
            integrity_failures=integrity_failures,
            offers_placed=ctx.offers_placed,
            offers_refused=ctx.offers_refused,
        )

    def _finish_lineup_only(
        self,
        league,
        ctx: EPSessionContext,
        trade_results: list[AutoTradeResult],
        sell_results: list[AutoTradeResult],
        errors: list[str],
        start_time: float,
        reason: str,
    ) -> AutoTradeSession:
        """Set the lineup and end the session without trading.

        Shared by the locked phase and by ``trading_mode=lineup_only`` so the
        two exits cannot drift apart, and so both log ``session-end`` — the
        locked branch used to return without one, which is why prod telemetry
        shows no session-end for 2026-09-10 20:00 or 2026-09-11 08:00.

        ``sell_results`` is not always empty on this path: step 1 executes the
        sell plans deferred behind auctions this session won, and in ``full``
        mode it does so before either exit condition is tested. The line used
        to hard-code ``sells=0``, so a sale that really happened was reported
        as none — the one number a telemetry reader would use to conclude the
        locked path spends nothing.
        """
        console.print(f"[yellow]{reason} — setting lineup only, no trading[/yellow]")
        self._send_session_board(league, ctx)
        lineup = (
            self._set_optimal_lineup(league, errors, squad_scores=ctx.ep_result.get("squad_scores"))
            or []
        )
        all_results = sell_results + trade_results
        total_spent = sum(r.price for r in all_results if r.action == "BUY" and r.success)
        total_earned = sum(r.price for r in all_results if r.action == "SELL" and r.success)
        end_time = time.time()
        logger.info(
            "session-end duration=%.1fs mode=%s phase=%s sells=%d trades=%d/%d "
            "offers=%d refused=%d spent=%d earned=%d net=%d errors=%d | %s",
            end_time - start_time,
            self.settings.trading_mode,
            ctx.matchday_phase.phase,
            len([r for r in sell_results if r.success and r.action == "SELL"]),
            len([r for r in trade_results if r.success]),
            len(trade_results),
            ctx.offers_placed,
            ctx.offers_refused,
            total_spent,
            total_earned,
            total_earned - total_spent,
            len(errors),
            reason,
        )
        integrity_failures = self._finish_facts(
            errors, start_time, ctx.matchday_phase.phase, league
        )
        return AutoTradeSession(
            start_time=start_time,
            end_time=end_time,
            profit_trades=trade_results,
            lineup_trades=sell_results,
            errors=errors,
            total_spent=total_spent,
            total_earned=total_earned,
            net_change=total_earned - total_spent,
            lineup=lineup,
            session_id=self._session_batch_id,
            integrity_failures=integrity_failures,
            offers_placed=ctx.offers_placed,
            offers_refused=ctx.offers_refused,
        )

    def _set_optimal_lineup(
        self,
        league,
        errors: list[str],
        squad_scores: list | None = None,
    ) -> list[tuple[str, float, str | None]]:
        """Calculate and set the optimal starting 11 via API.

        Prefers the new EP scoring pipeline (via *squad_scores* when the caller
        already computed them) so the lineup benefits from DGW multipliers,
        injury penalties, 5-fixture SOS, and position-weighted scoring. Falls
        back to a per-player v2 score only when scores are missing (e.g. EP
        pipeline failed, or a player was just bought mid-session).

        Returns the (name, ep, flag) triples for the eleven it selected — or
        an empty list on any early-exit or failure path, so callers never see
        ``None``.
        """
        from .formation import (
            get_formation_string,
            get_position_counts,
            is_legal_formation,
            order_for_lineup,
            select_best_eleven,
        )

        console.print("\n[bold cyan]📋 Setting Optimal Lineup[/bold cyan]")

        # None for a caller that never started a session through
        # `run_full_session` or `_build_session_context` -- several tests
        # drive this method directly. `facts` stays None in that case and
        # every write below is a no-op.
        facts = getattr(self, "_facts", None)

        try:
            squad = self.api.get_squad(league)
            if not squad or len(squad) < 11:
                # A squad under eleven is the -100-per-slot case, not a quiet
                # skip: on 2026-08-31 seven players sat unfielded and this
                # branch left no trace at all, so M1 (lineup regret) had
                # nothing to attribute the loss to. Same shape as the
                # illegal-eleven refusal below, and read by the same grep.
                msg = f"lineup not legal: squad has {len(squad or [])} players, need 11"
                console.print(f"[yellow]{msg} — not submitted[/yellow]")
                logger.error("lineup-illegal %s", msg)
                errors.append(msg)
                if facts is not None:
                    facts.lineup_result = "illegal"
                return []

            # Build ep_scores from the pipeline when available; fall back to a
            # per-player v2 score only for uncovered squad members or when the
            # caller didn't provide scores. Both sides are real points AND
            # obey the same availability recency bound (REH-85), so they are
            # safe to rank against each other below -- a mid-session signing
            # landing in `missing` must not get a different availability rule
            # than everyone the pipeline already scored.
            ep_scores: dict[str, float] = {}
            if squad_scores:
                ep_scores = {s.player_id: s.expected_points for s in squad_scores}

            missing = [p for p in squad if p.id not in ep_scores]
            if missing:
                for player in missing:
                    ep_scores[player.id] = self._fallback_expected_points(league, player)

            # Select best 11, order by position for API (GK→DEF→MID→FWD)
            best_eleven = select_best_eleven(squad, ep_scores)
            if not is_legal_formation(best_eleven):
                counts = get_position_counts(squad)
                msg = (
                    f"lineup not legal: {len(best_eleven)} players as "
                    f"{get_formation_string(best_eleven)} — squad "
                    f"GK {counts['Goalkeeper']} DEF {counts['Defender']} "
                    f"MID {counts['Midfielder']} FW {counts['Forward']}; not submitted"
                )
                console.print(f"[red]{msg}[/red]")
                logger.error("lineup-illegal %s", msg)
                errors.append(msg)
                if facts is not None:
                    facts.lineup_result = "illegal"
                return []
            ordered = order_for_lineup(best_eleven)
            formation = get_formation_string(ordered)
            if facts is not None:
                facts.legal_formation = formation
            player_ids = [p.id for p in ordered]

            names = [
                f"{p.first_name[0]}. {p.last_name}" if p.first_name else p.last_name
                for p in ordered
            ]
            console.print(f"[dim]Formation: {formation} | {', '.join(names)}[/dim]")

            lineup_summary: list[tuple[str, float, str | None]] = [
                (
                    f"{p.first_name[0]}. {p.last_name}" if p.first_name else p.last_name,
                    float(ep_scores.get(p.id, 0.0)),
                    None,
                )
                for p in ordered
            ]

            if self.dry_run:
                console.print("[yellow]DRY RUN - Lineup not applied[/yellow]")
                if facts is not None:
                    facts.lineup_result = "dry_run"
                return lineup_summary

            self.api.set_lineup(league, formation, player_ids)
            console.print("[green]✓ Lineup set successfully[/green]")
            if facts is not None:
                facts.lineup_result = "set"
            return lineup_summary

        except Exception as e:
            error_msg = f"Set lineup error: {e!s}"
            console.print(f"[red]{error_msg}[/red]")
            errors.append(error_msg)
            if facts is not None:
                facts.lineup_result = "failed"
            return []

    def _fallback_expected_points(self, league, player) -> float:
        """Fallback per-player EP for a squad member the pipeline didn't score.

        Only fires for a mid-session purchase or an upstream pipeline failure.
        Returns REAL Kickbase points (REH-55), which matters more than it looks:
        the caller merges this into the same ``ep_scores`` dict as the pipeline's
        own scores and ranks them together, so a value on a different scale would
        silently reorder the starting eleven.

        Degrades in two stages rather than one. If the performance fetch fails we
        still score the player cold — ``compose_ep`` with ``prev_status=None``
        falls back to the availability model's marginal prior, which is a usable
        number. Only a failure of the fitted models themselves returns 0.0. That
        ordering is deliberate: a 0.0 sorts a player to the bottom of
        ``select_best_eleven``, and benching someone we simply failed to fetch is
        how an avoidable empty slot turns into -100.

        Applies the same ``max_status_age_days`` recency bound (REH-85) the
        pipeline's own ``score_player_v2`` calls use, via ``last_played_status``.
        Without it, this path -- which only fires for the highest-stakes case,
        a player just bought mid-session -- would keep anchoring on a stale
        end-of-last-season status forever, even after the pipeline everywhere
        else was fixed.
        """
        from .scoring.v2.adapter import compose_ep, last_played_status
        from .scoring.v2.coefficients import load_coefficients
        from .value_history import ValueHistoryCache

        perf_data = None
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
        except Exception:
            logger.debug("fallback-ep: performance fetch failed for %s", player.id)

        try:
            availability, rate, _meta = load_coefficients()
            return compose_ep(
                str(player.id),
                last_played_status(perf_data, max_age_days=self.settings.max_status_age_days),
                player.position,
                availability,
                rate,
            )
        except Exception:
            logger.debug("fallback-ep: v2 scoring failed for %s", player.id)
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

        # NOTE: hardcoded 11 here vs. self.settings.min_squad_size (13) in
        # trader.py:753 — two different values for the same setting, on the
        # same SquadOptimizer.min_squad_size that nothing currently reads
        # (see config.py's min_squad_size docstring). Harmless today because
        # it's inert either way; week 4 (wiring an actual sell-floor guard)
        # is where this needs to be reconciled, not rewired here.
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
