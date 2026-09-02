"""Automated trading - Execute trades without manual intervention"""

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from rich.console import Console

from .config import INSTANT_SELL_PCT
from .services import AutoTradeResult, ExecutionService
from .services.pacing import SQUAD_CAP as pacing_squad_cap
from .services.pacing import available_squad_slots
from .services.safety_gate import BuyGate, club_counts

console = Console()
logger = logging.getLogger(__name__)


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


def _is_too_falling_to_propose(trend_7d_pct: float | None, settings) -> bool:
    """True when a market value is sliding too steeply to ask about (REH-117).

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


def _proposal_line(
    proposal_id: str, rec, bid: int, trend: float | None, risks: list[str], auto_approve_at
):
    """Everything the overview shows, from data the pipeline already has.

    `PlayerScore` carries position, lineup probability, minutes trend, average
    points and next opponent; `BuyRecommendation` carries the roster impact.
    The old per-player message discarded all of it and printed "unknown club"
    with no position, which is how a defender was proposed to a squad whose
    actual hole was at striker (REH-117).
    """
    from .config import POSITION_MINIMUMS
    from .notify.overview import ProposalLine

    player = rec.player
    score = getattr(rec, "score", None)
    position = getattr(score, "position", "") or getattr(player, "position", "") or ""
    impact = getattr(rec, "roster_impact", "") or ""
    return ProposalLine(
        proposal_id=proposal_id,
        name=f"{player.first_name} {player.last_name}".strip() or player.last_name,
        bid=int(bid),
        ep=float(getattr(score, "expected_points", 0.0) or 0.0),
        marginal_gain=float(getattr(rec, "marginal_ep_gain", 0.0) or 0.0),
        position=position,
        club=_club_name(player, score),
        market_value=int(getattr(player, "market_value", 0) or 0),
        is_emergency=auto_approve_at is not None,
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


# One definition of the squad cap, in `services/pacing`. Two copies of one
# safety-relevant number in different modules is how REH-99's 8%/20% split
# happened; the name is kept here because callers and tests already use it.
SQUAD_CAP = pacing_squad_cap


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

    Zero when the squad can already field one. REH-82: this used to be
    ``11 - len(squad)``, which is a headcount and therefore blind to the case
    that actually costs -100 -- eleven players whose POSITIONS cannot fill any
    legal formation (no goalkeeper, say) yields zero and triggers nothing,
    while the emergency fill sitting behind the gate would have handled it
    correctly, since it already prioritises positions below their minimum.

    ``can_fill_starting_eleven`` subsumes the headcount test -- it reports
    "Only N available players, need 11" as well as a broken position mix -- so
    this is strictly more coverage, not a trade.

    The squad is passed unfiltered, exactly as the headcount version did.
    ``can_fill_starting_eleven`` documents that injured and suspended players
    should be excluded, which would be better still, but that makes emergencies
    fire more often in the locked phase and is a behaviour change worth
    measuring on its own.
    """
    from .formation import FormationRequirements, can_fill_starting_eleven

    if can_fill_starting_eleven(squad)["ok"]:
        return 0
    # Unfieldable despite enough bodies: buy at least one, and let the fill
    # path's `gap_positions` choose which position it must be.
    return max(FormationRequirements().starting_eleven_size - len(squad), 1)


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
        # REH-117: one message per session, not one per player. Collected here
        # and sent once by `_send_proposal_overview`.
        self._session_proposals: list = []
        self._session_batch_id: str = ""
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

        # Fetch matchday timing
        days = trader.get_days_until_match(league)
        # Set as a side effect of the call above, so the flag costs no second
        # /myeleven fetch. Absent (None) whenever that fetch failed.
        in_progress = bool(getattr(trader, "_last_matchday_in_progress", False))
        phase = self._get_matchday_phase(days, matchday_in_progress=in_progress)

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
        """Re-read every live offer and withdraw the bot's own bad ones.

        Two facts about the input shape drive everything here. `get_my_bids` is
        `get_market` filtered by "do we hold an offer", so it returns offers
        Marco placed by hand alongside the bot's — and it cannot say which is
        which. And a bid the bot placed carries the tier it was priced under,
        which is both the ceiling to judge it by (REH-111) and the proof of
        provenance (REH-115).

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
            for_profit=True,
            bid_tiers=bid_tiers,
            bot_placed_ids=bot_placed_ids,
        )
        if not evaluations:
            return

        evaluator.display_bid_evaluations(evaluations)
        canceled = evaluator.cancel_bad_bids(league, evaluations, dry_run=self.dry_run)
        if canceled:
            console.print(f"[yellow]Canceled {canceled} bid(s) that no longer make sense[/yellow]")

    def _process_due_auto_approvals(self, league) -> None:
        """Execute emergency proposals whose approval deadline has passed.

        Only the emergency fill stamps a deadline (REH-114). An ordinary
        upgrade has `auto_approve_at` NULL and waits for a human forever, which
        is the point — Marco approves squad trades. What cannot wait forever is
        an empty lineup slot, because that is -100 every matchday whether or
        not anyone taps.

        Best-effort like the rest of the learning layer: a failure here must
        not stop the session, and each proposal is claimed out of 'pending'
        before execution so a retry cannot buy the same player twice.
        """
        if self.learner is None:
            return
        try:
            due = self.learner.due_auto_approvals(now=time.time())
        except Exception:
            logger.warning("could not read auto-approval deadlines", exc_info=True)
            return
        if not due:
            return

        from .notify.approval import execute_proposal

        for proposal in due:
            pid = proposal["proposal_id"]
            if self.dry_run:
                console.print(
                    f"[yellow]DRY RUN - would auto-approve {proposal['player_name']} "
                    f"for EUR {int(proposal['bid']):,} (deadline passed)[/yellow]"
                )
                continue
            # The claim is the replay guard, exactly as in the webhook.
            if not self.learner.mark_proposal(pid, "approved"):
                continue
            try:
                reply = execute_proposal(
                    proposal,
                    settings=self.settings,
                    learner=self.learner,
                    api=self.api,
                    league=league,
                )
            except Exception:
                logger.exception("auto-approval failed for %s", pid)
                continue
            console.print(f"[yellow]⏰ Auto-approved (deadline): {reply}[/yellow]")
            logger.warning(
                "auto-approval fired proposal=%s player=%s bid=%d — %s",
                pid,
                proposal["player_name"],
                int(proposal["bid"]),
                reply,
            )

    def _propose_buy(
        self, league, rec, ctx, *, bid: int | None = None, auto_approve_at: float | None = None
    ) -> bool:
        """Record and send a proposal instead of buying. True if recorded.

        The proposal is recorded FIRST and sent second, so a Telegram outage
        loses the notification but not the decision — it still surfaces in the
        daily email.

        ``bid`` overrides `rec.recommended_bid` for callers that price the buy
        themselves — the emergency basket lowers some picks toward the asking
        price so one more slot fits (REH-113), and the proposal must show the
        number that will actually be offered.

        ``auto_approve_at`` stamps a deadline after which the proposal executes
        unapproved. Only the emergency fill sets one: there the alternative to
        spending is -100 per empty slot every matchday, so silence cannot mean
        "do nothing" (REH-114). An ordinary upgrade waits indefinitely.
        """
        import uuid

        from .notify.render import render_proposal
        from .services.bid_ceiling import tier_for_marginal_gain

        proposal_id = uuid.uuid4().hex[:12]
        player = rec.player
        trend = None
        try:
            from .trader import Trader

            trend = (
                Trader(self.api, self.settings)
                .trend_service.get_trend(player.id, player.market_value, league.id)
                .trend_7d_pct
            )
        except Exception:
            logger.debug("proposal: no trend for %s", player.id, exc_info=True)

        if _is_too_falling_to_propose(trend, self.settings):
            console.print(
                f"[dim]Skip {player.last_name} — market value falling " f"{trend:.1f}%/7d[/dim]"
            )
            logger.info(
                "proposal-skip player=%s trend=%.1f%% below %.1f%% floor",
                player.id,
                trend,
                float(self.settings.max_falling_trend_pct_to_buy),
            )
            return False

        risks: list[str] = []
        if getattr(rec.score, "data_quality", None) and rec.score.data_quality.grade != "A":
            risks.append(
                f"Data quality {rec.score.data_quality.grade} — no fitted history, "
                "scored on the position prior."
            )

        message = render_proposal(
            player_name=f"{player.first_name} {player.last_name}".strip(),
            club=getattr(player, "team_name", "") or "unknown club",
            bid=int(bid if bid is not None else rec.recommended_bid),
            market_value=int(player.market_value),
            ep=float(rec.score.expected_points),
            displaced_name=getattr(rec, "replaces_player_name", None) or "the weakest starter",
            displaced_ep=float(getattr(rec, "replaces_player_ep", 0.0) or 0.0),
            marginal_gain=float(rec.marginal_ep_gain),
            budget_before=int(ctx.current_budget),
            trend_7d_pct=trend,
            risks=risks,
        )

        bid_amount = int(bid if bid is not None else rec.recommended_bid)

        # Collected before the dry-run exit so `status` renders the message
        # Marco would actually receive, rather than a line saying one exists.
        self._session_proposals.append(
            _proposal_line(proposal_id, rec, bid_amount, trend, risks, auto_approve_at)
        )

        if self.dry_run:
            console.print(
                f"[yellow]DRY RUN - would propose {player.last_name} "
                f"for EUR {bid_amount:,}[/yellow]"
            )
            return True

        # REH-99: record the tier the bid was sized under. Approval recomputes
        # the ceiling from it against the *live* market value, so a proposal is
        # re-checked as the world is at approval time rather than waved through
        # on the stale number it was priced at.
        tier = tier_for_marginal_gain(
            float(rec.marginal_ep_gain),
            must_have=self.settings.bid_tier_must_have,
            strong=self.settings.bid_tier_strong_upgrade,
            solid=self.settings.bid_tier_solid_upgrade,
        )

        try:
            self.learner.record_proposal(
                proposal_id=proposal_id,
                player_id=player.id,
                player_name=player.last_name,
                bid=bid_amount,
                market_value=int(player.market_value),
                message=message,
                tier=tier.value,
                auto_approve_at=auto_approve_at,
                batch_id=self._session_batch_id,
            )
        except Exception:
            logger.exception("proposal: could not record %s", proposal_id)
            return False

        # REH-117: no send here. Six separate messages could not show that the
        # proposals compete for one wallet, and on 2026-08-31 approving two of
        # them stranded the other four on "budget would go negative". The
        # session collects the board and sends it once, in `_send_proposal_overview`.
        logger.info(
            "proposal recorded id=%s player=%s bid=%d batch=%s",
            proposal_id,
            player.id,
            bid_amount,
            self._session_batch_id,
        )
        return True

    def _send_proposal_overview(self, league, ctx) -> None:
        """Send the session's whole proposal board as one message (REH-117).

        Called once, at the end, so the message can show what the proposals do
        to each other. Six separate messages could not: on 2026-08-31 approving
        two of them consumed the wallet and the other four died on "budget
        would go negative".

        Best-effort — a delivery failure must not fail the session. The
        proposals are already recorded, so the daily summary's keyboard remains
        a way to act on them (REH-106).
        """
        if not self._session_proposals:
            return

        from .notify.overview import render_proposal_overview, split_by_budget
        from .notify.telegram import send_overview

        budget = int(getattr(ctx, "current_budget", 0) or 0)
        recommended, alternatives = split_by_budget(self._session_proposals, budget)
        text = render_proposal_overview(
            squad_size=len(getattr(ctx, "squad", []) or []),
            squad_cap=SQUAD_CAP,
            budget=budget,
            recommended=recommended,
            alternatives=alternatives,
        )
        console.print(text)
        if self.dry_run:
            console.print("[yellow]DRY RUN - overview not sent[/yellow]")
            return

        delivered = send_overview(
            self.settings.telegram_bot_token,
            self.settings.telegram_chat_id,
            text,
            batch_id=self._session_batch_id,
            recommended_count=len(recommended),
            alternatives=[(line.proposal_id, line.name) for line in alternatives],
        )
        logger.info(
            "proposal-overview batch=%s proposals=%d recommended=%d "
            "spend=%d budget=%d delivered=%s",
            self._session_batch_id,
            len(self._session_proposals),
            len(recommended),
            sum(line.bid for line in recommended),
            budget,
            delivered,
        )
        if not delivered:
            logger.warning(
                "proposal overview %s recorded but NOT delivered; the proposals "
                "are actionable only from the daily summary",
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

    def _has_pending_proposal(
        self,
        player_id: str,
        *,
        max_age_days: float = 3.0,
        rejected_age_days: float = 14.0,
    ) -> bool:
        """True if this player was recently proposed and should not be re-sent.

        The bot runs twice a day; without this guard it would re-send the same
        proposal every run until it was actioned. Two windows, because the two
        states mean different things:

        - ``pending`` — Marco has not answered. Suppress for ``max_age_days``,
          then let it through again: proposal expiry is not implemented, so an
          unbounded guard would let one ignored proposal block a player forever.
        - ``rejected`` — Marco said no. That is an answer, and re-asking twelve
          hours later is exactly the daily-nagging this whole branch exists to
          stop. Suppress for much longer, but still not forever, because the
          price and the player's form both move.

        Any other status (``approved``/``executed``/``failed``) does not
        suppress: the buy either happened or definitively did not, and a fresh
        proposal is the right response to a fresh situation.
        """
        now = time.time()
        pending_cutoff = now - max_age_days * 86400.0
        rejected_cutoff = now - rejected_age_days * 86400.0
        try:
            for p in self.learner.proposals_for_player(str(player_id)):
                created = float(p.get("created_at") or 0.0)
                status = p.get("status")
                if status == "pending" and created >= pending_cutoff:
                    return True
                if status == "rejected" and created >= rejected_cutoff:
                    return True
            return False
        except Exception:
            logger.warning("proposal: could not read proposals", exc_info=True)
            return False

    @staticmethod
    def _needs_sell_plan(obj) -> bool:
        """True if this buy only works by selling someone first.

        Proposals carry no sell plan, so such a buy would be refused by the safety
        gate after approval. Skip it rather than send a proposal that cannot be
        honoured.
        """
        sell_plan = getattr(obj, "sell_plan", None)
        return bool(sell_plan and getattr(sell_plan, "players_to_sell", None))

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
        proposed_slots = 0  # slots reserved by proposals nobody has approved yet
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

                from .formation import validate_formation
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
                if obj.recommended_bid > ctx.flip_budget:
                    console.print(
                        f"[yellow]Cannot afford {obj.player.last_name} "
                        f"(€{obj.recommended_bid:,} > €{ctx.flip_budget:,})[/yellow]"
                    )
                    continue

                if self._has_pending_proposal(obj.player.id):
                    console.print(
                        f"[dim]Skip {obj.player.last_name} — proposal already awaiting approval[/dim]"
                    )
                    continue

                if self._needs_sell_plan(obj):
                    console.print(
                        f"[dim]Skip {obj.player.last_name} — needs a sell plan, "
                        f"cannot be proposed[/dim]"
                    )
                    logger.info("proposal-skip: %s needs a sell plan", obj.player.last_name)
                    continue

                if self._propose_buy(league, obj, ctx):
                    console.print(
                        f"[cyan]Proposed {obj.player.last_name} — awaiting approval[/cyan]"
                    )
                    available_slots -= 1
                    proposed_slots += 1
                    ctx.flip_budget -= obj.recommended_bid
                continue

            elif kind == "pair":
                # Don't sell a player unnecessarily if there are open slots —
                # the same target should appear as a plain buy candidate instead.
                #
                # `proposed_slots` is added back deliberately. A proposal is not
                # a commitment: nobody has approved it and no money has moved.
                # Counting it as a filled slot would mean that merely PROPOSING
                # a buy is what switches on the autonomous sell-then-buy pair
                # path — the bot would start selling squad players off the back
                # of a decision Marco has not made yet.
                if available_slots + proposed_slots > 0:
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
        )
        verdict = gate.check(player_id=pair.buy_player.id, bid=int(pair.recommended_bid))
        return None if verdict.ok else "; ".join(verdict.reasons)

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
        - Prioritizes positions below the formation minimum.
        - Honors wash-trade and active-bid guards.
        - Caps spend at ``slots_short`` purchases.
        """
        from .config import POSITION_MINIMUMS

        results: list[AutoTradeResult] = []
        buy_recs = ctx.ep_result.get("buy_recs", [])
        if not buy_recs:
            console.print("[red]No buy candidates available — cannot fill emergency slots[/red]")
            return results

        position_counts: dict[str, int] = {}
        for p in fresh_squad:
            position_counts[p.position] = position_counts.get(p.position, 0) + 1
        gap_positions = {
            pos
            for pos, minimum in POSITION_MINIMUMS.items()
            if position_counts.get(pos, 0) < minimum
        }

        active_bid_ids = set(ctx.my_bid_amounts.keys())
        budget_remaining = int(ctx.current_budget)

        # REH-113: choose the BASKET that scores the most points, not the
        # best-ranked player affordable right now. An empty slot is -100 per
        # slot regardless of who fills it, so `select_emergency_basket`
        # maximises `total_ep + 100 x count` on ASK price and spends the
        # leftover as overbid afterwards. The old greedy walk ignored the
        # penalty entirely and bought 3 of 4 on 2026-08-31, missing the fourth
        # by 1,168,502 of overbid it had already committed elsewhere.
        from .services.emergency_basket import EmergencyCandidate, select_emergency_basket

        by_id: dict[str, object] = {}
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

        picks = select_emergency_basket(candidates, slots_short, budget_remaining)

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
        attempts: list[tuple[object, int]] = [(by_id[p.candidate.id], p.bid) for p in picks]
        attempts += [
            (by_id[c.id], c.max_bid)
            for c in sorted(candidates, key=lambda c: -c.ep)
            if c.id not in chosen_ids
        ]

        # REH-114: propose rather than spend. Marco approves squad trades, and
        # on the 2026-08-31 board this path would have committed EUR 55,485,928
        # across four players unattended. Every pick carries an auto-approve
        # deadline, because a proposal nobody taps protects nothing and the
        # slot is still -100 every matchday. Only the reserves behind the
        # basket are dropped — proposing the whole board would bury the ask.
        deadline = time.time() + float(self.settings.emergency_auto_approve_hours) * 3600.0
        proposed = 0
        for rec, bid in attempts:
            if proposed >= slots_short:
                break
            if bid > budget_remaining:
                continue

            # Pre-flight the same gate approval will apply. Without this the
            # bot can ask Marco to approve a bid the gate then refuses — the
            # exact broken Approve button REH-99 existed to fix — and burn one
            # of the slots asking. A refusal means "try the next candidate",
            # not "field nobody", so the walk continues down the reserves.
            gate = _build_buy_gate(
                settings=self.settings,
                ctx=ctx,
                player=rec.player,
                spendable_budget=budget_remaining,
                free_slots=slots_short - proposed,
                marginal_ep_gain=rec.marginal_ep_gain,
            )
            verdict = gate.check(player_id=rec.player.id, bid=bid)
            if not verdict.ok:
                console.print(
                    f"[dim]Skip {rec.player.last_name} — " f"{'; '.join(verdict.reasons)}[/dim]"
                )
                continue

            if self._propose_buy(league, rec, ctx, bid=bid, auto_approve_at=deadline):
                proposed += 1
                # Reserve the money against the rest of this basket, so four
                # proposals cannot each assume the whole wallet.
                budget_remaining -= bid
                gap_positions.discard(rec.player.position)
                results.append(
                    AutoTradeResult(
                        success=True,
                        player_name=f"{rec.player.first_name} {rec.player.last_name}".strip(),
                        # Not "BUY": no money has moved, and the session
                        # summary sums BUY prices into `total_spent`.
                        action="PROPOSE",
                        price=bid,
                        reason=f"Emergency lineup fill (squad short by {slots_short})",
                        timestamp=time.time(),
                    )
                )

        console.print(
            f"[green]✓ Emergency fill: proposed {proposed}/{slots_short} player(s) "
            f"— auto-approving in {self.settings.emergency_auto_approve_hours:.0f}h "
            f"if not actioned[/green]"
        )
        logger.info(
            "emergency-proposals slots_short=%d proposed=%d auto_approve_in_h=%.1f",
            slots_short,
            proposed,
            float(self.settings.emergency_auto_approve_hours),
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
        # Profit-taking and loss-cutting: the flip behaviour, and the only
        # part of this method REH-71's switch governs.
        if profit_sells_enabled:
            for player in squad:
                if player.id in best_11_ids:
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
            if player.id in best_11_ids or player.id in already_selling:
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

        # REH-117: one batch per session, so "Approve all" can take the set
        # that was chosen to fit the budget together.
        self._session_proposals = []
        self._session_batch_id = uuid.uuid4().hex[:12]

        logger.info(
            "session-start league=%s dry_run=%s max_trades=%d max_spend=%d",
            getattr(league, "name", league.id),
            self.dry_run,
            self.max_trades_per_session,
            self.max_daily_spend,
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

        # REH-114: an emergency proposal that nobody actioned becomes a buy once
        # its deadline passes. Runs before the emergency check below so a slot
        # already paid for is not proposed a second time.
        self._process_due_auto_approvals(league)

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
                    self.tracker.reconcile_squad_cost_basis(squad, manager_id=str(my_id))
            except Exception:  # pragma: no cover - defensive
                logger.exception("cost-basis reconciliation failed (non-fatal)")
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
            logger.exception("Auction resolution failed")

        # Step 2: Build session context (single EP pipeline + trends + matchday phase)
        try:
            ctx = self._build_session_context(league)
        except Exception as e:
            error_msg = f"EP pipeline failed: {e!s}"
            console.print(f"[red]{error_msg}[/red]")
            errors.append(error_msg)
            logger.exception("EP pipeline failed — falling back to lineup-only")
            # Fall back to just setting lineup
            lineup = self._set_optimal_lineup(league, errors) or []
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

        # Step 3: A squad that cannot field a legal eleven is an emergency in
        # EVERY phase (REH-112). This used to sit inside the `locked` branch
        # below, so it could only run when the phase detector had found an
        # imminent fixture. On 2026-08-31 `/myeleven` reported no upcoming
        # fixture between MD2 and MD3, `_get_matchday_phase` took its `else`
        # branch to "moderate", and a squad of 7 sat four slots short — a
        # standing -400 — with the fill unreachable. That `else` is
        # conservative about *spending*, which is right; the -100 is not
        # spending, and the fail-safe has to fail toward fielding an eleven.
        fresh_squad = self.api.get_squad(league)
        slots_short = _emergency_slots_short(fresh_squad)
        if slots_short > 0:
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

        # If locked (match imminent), set the lineup and exit — the emergency
        # above has already had its chance to make an eleven fieldable.
        if ctx.matchday_phase.phase == "locked":
            if slots_short == 0:
                console.print(
                    f"[yellow]Match imminent ({ctx.matchday_phase.days_until_match}d) "
                    f"— setting lineup only, no trading[/yellow]"
                )

            self._send_proposal_overview(league, ctx)
            lineup = (
                self._set_optimal_lineup(
                    league, errors, squad_scores=ctx.ep_result.get("squad_scores")
                )
                or []
            )
            total_spent = sum(r.price for r in trade_results if r.action == "BUY" and r.success)
            total_earned = sum(r.price for r in trade_results if r.action == "SELL" and r.success)
            return AutoTradeSession(
                start_time=start_time,
                end_time=time.time(),
                profit_trades=trade_results,
                lineup_trades=[],
                errors=errors,
                total_spent=total_spent,
                total_earned=total_earned,
                net_change=total_earned - total_spent,
                lineup=lineup,
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

                self._evaluate_open_bids(league, player_trends=player_trends)
            except Exception as e:
                console.print(f"[yellow]Bid compliance check failed: {e}[/yellow]")

        # Step 7: Unified trade phase (EP buys + trade pairs + profit flips)
        try:
            trade_results = self.run_unified_trade_phase(league, ctx)
        except Exception as e:
            error_msg = f"Trading error: {e!s}"
            console.print(f"[red]{error_msg}[/red]")
            errors.append(error_msg)
            logger.exception("Unified trade phase failed")

        # Step 8: Set optimal lineup using EP pipeline scores from the session.
        # Players acquired mid-session (if any) are scored by the v2 fallback
        # inside _set_optimal_lineup.
        self._send_proposal_overview(league, ctx)

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
        console.print(f"Total spent: €{total_spent:,}")
        console.print(f"Total earned: €{total_earned:,}")
        net_color = "green" if net_change >= 0 else "red"
        console.print(f"Net change: [{net_color}]€{net_change:,}[/{net_color}]")

        if errors:
            console.print(f"\n[red]Errors: {len(errors)}[/red]")
            for err in errors:
                console.print(f"[red]  • {err}[/red]")

        logger.info(
            "session-end duration=%.1fs phase=%s sells=%d trades=%d/%d "
            "spent=%d earned=%d net=%d errors=%d",
            end_time - start_time,
            ctx.matchday_phase.phase,
            len([r for r in sell_results if r.success and r.action == "SELL"]),
            len([r for r in trade_results if r.success]),
            len(trade_results),
            total_spent,
            total_earned,
            net_change,
            len(errors),
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
        from .formation import get_formation_string, order_for_lineup, select_best_eleven

        console.print("\n[bold cyan]📋 Setting Optimal Lineup[/bold cyan]")

        try:
            squad = self.api.get_squad(league)
            if not squad or len(squad) < 11:
                console.print("[yellow]Not enough players to set lineup[/yellow]")
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
            ordered = order_for_lineup(best_eleven)
            formation = get_formation_string(ordered)
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
                return lineup_summary

            self.api.set_lineup(league, formation, player_ids)
            console.print("[green]✓ Lineup set successfully[/green]")
            return lineup_summary

        except Exception as e:
            error_msg = f"Set lineup error: {e!s}"
            console.print(f"[red]{error_msg}[/red]")
            errors.append(error_msg)
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
