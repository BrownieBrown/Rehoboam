"""The matchday loop: score, sell, buy, field eleven, award real points.

Every decision at matchday N is made with data strictly before N's kickoff.
The engine takes callables rather than a database so that boundary lives in
one place (the driver) and can be tested without I/O.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from rehoboam.config import INSTANT_SELL_PCT
from rehoboam.formation import select_best_eleven
from rehoboam.replay.rules import (
    MAX_SQUAD_SIZE,
    can_buy,
    can_field_eleven,
    empty_slot_penalty,
)
from rehoboam.replay.state import ReplayPlayer, ReplayState

# `INSTANT_SELL_PCT` (imported above) is the FULL market value, 1.00. It is
# re-exported from `config` rather than redeclared here: REH-67 measured it and
# corrected this module, but seven live sites kept 0.95 for a season because the
# two were separate literals (REH-79). One definition, so they cannot drift
# again. Ratios above 1.00 are sales to other managers at a premium, a channel
# this replay deliberately does not model.
# Decisions are made this long before kickoff, mirroring the live bot's
# pre-matchday session rather than pretending to trade at the whistle.
DECISION_LEAD_SECONDS = 3600.0


@dataclass(frozen=True)
class Matchday:
    day_number: int
    kickoff: float
    points: dict[str, float]


@dataclass(frozen=True)
class MatchdayOutcome:
    day_number: int
    points_scored: int
    lineup_ids: list[str]
    penalty: int
    budget_at_kickoff: int
    zeroed: bool
    squad_size: int
    buys: int
    sells: int


@dataclass(frozen=True)
class FlipRecord:
    """One completed round trip: a player the replay bought and later sold.

    NARROWER THAN THE LIVE DEFINITION, AND OPTIMISTIC (REH-71 review). Records
    are appended in exactly one place — ``_flip_sells`` — so a round trip
    counts here only when the *profit-taking* pass is what closed it. The live
    counterpart, ``LearningTracker.record_flip_outcome``, fires on every instant
    sell of a tracked purchase, forced and make-room exits included (see
    ``services/execution.py:instant_sell``).

    So a replay player liquidated by ``_restore_budget`` or sold to make room
    for an EP buy never enters this ledger at all: arm D of the 2x2 shows 35
    buys and 32 sells against only 23 recorded round trips. The excluded exits
    are precisely the forced ones — sales made under solvency pressure or to
    fund a better player — which skew loss-making, so omitting them flatters
    the replay's P&L.

    The reported cash figure is therefore NOT like-for-like with the real 151
    round trips printed beside it. It is a lower bound on flip harm, not a
    measurement of it. Left as measured on purpose: REH-71's numbers were
    recorded once and are not to be re-cut after the fact.
    """

    player_id: str
    buy_price: int
    proceeds: int
    bought_at: float | None
    sold_at: float


@dataclass
class SeasonResult:
    outcomes: list[MatchdayOutcome] = field(default_factory=list)
    total_points: int = 0
    final_budget: int = 0
    flips: list[FlipRecord] = field(default_factory=list)


def _team_value(state: ReplayState, mv_fn: Callable[[str, float], int | None], at: float) -> int:
    return sum(mv_fn(pid, at) or 0 for pid in state.player_ids)


def _proceeds(pid: str, mv_fn: Callable[[str, float], int | None], at: float) -> int:
    """Cash raised by selling ``pid`` instantly back to Kickbase."""
    return int((mv_fn(pid, at) or 0) * INSTANT_SELL_PCT)


def _solvent_after(state: ReplayState, price: int, proceeds: int = 0) -> bool:
    """Whether the balance stays non-negative once this purchase settles.

    Every decision here is taken ``DECISION_LEAD_SECONDS`` before kickoff, and
    a negative balance *at* kickoff zeroes the whole matchday. There is no
    intervening window in which to recover, so the credit line — a mid-week
    facility this engine deliberately does not model — is the wrong constraint
    at this instant. The bot may spend only cash it actually holds, plus what
    the sale it is about to make will raise.

    Without this gate the engine leveraged to the credit floor (measured:
    EUR -167,048,229 against a EUR 80,000,000 starting budget) and then had to
    unwind it in the same session, destroying squad equity every matchday until
    nothing was left. The round-trip cost is the buy-side premium alone — a
    measured mean transaction price of 1.117x market value against an instant
    sell that returns the full market value, so ~11.7%, not the ~15% claimed
    while ``INSTANT_SELL_PCT`` was wrongly 0.95 (see REH-67).

    KEEP THE 1.117x HERE (REH-76). That mean was measured on ``transfer_type = 2``
    rows — manager-to-manager auctions — and this gate is about exactly those:
    contested purchases the engine wins by outbidding a rival. REH-76 corrected
    the figure everywhere it was wrongly generalised to Kickbase-sourced flips,
    where price IS market value by construction. This site is not one of those,
    and narrowing it to match them would understate the real cost of a
    contested buy. ``can_buy``'s
    credit-line check stays as the standing legality rule; this is an
    additional decision-time one.
    """
    return state.budget + int(proceeds) - int(price) >= 0


def _eleven_total(players: list[ReplayPlayer], scores: dict[str, float]) -> float:
    """Expected points of the best *legal* eleven drawn from ``players``."""
    return sum(scores.get(p.id, 0.0) for p in select_best_eleven(players, scores))


def _fieldable_sale_victim(state: ReplayState, scores: dict[str, float]) -> str | None:
    """Cheapest player by EP whose sale still leaves a legal starting eleven.

    Picking the sale victim by score alone strands the squad without a
    goalkeeper: keepers reliably score least, so a naive ``min`` sells the only
    one and silently books an empty slot. Returns ``None`` when no sale keeps
    the eleven legal.
    """
    for pid in sorted(state.player_ids, key=lambda p: scores.get(p, 0.0)):
        remaining = {k: v for k, v in state.squad.items() if k != pid}
        if can_field_eleven(ReplayState(budget=state.budget, squad=remaining)):
            return pid
    return None


def _restore_budget(
    state: ReplayState,
    scores: dict[str, float],
    mv_fn: Callable[[str, float], int | None],
    at: float,
) -> int:
    """Sell down until the balance is non-negative at kickoff; returns sells.

    A negative balance at kickoff zeroes the *entire* matchday, which is by far
    the most expensive outcome in Kickbase — worth several hundred points,
    against the -100 per empty slot charged for a short squad. So this sells
    below eleven when that is what it takes, accepting the penalty.

    It prefers victims whose sale keeps the eleven legal, and it refuses to
    sell at all when liquidating the whole squad still could not clear the
    debt: a zero with the squad intact beats a zero with no squad, because the
    squad carries into the following matchday.
    """
    if state.budget >= 0:
        return 0
    realisable = sum(_proceeds(pid, mv_fn, at) for pid in state.player_ids)
    if state.budget + realisable < 0:
        return 0

    sells = 0
    while state.budget < 0 and state.squad:
        victim = _fieldable_sale_victim(state, scores)
        if victim is None:
            # Nothing can be sold without breaking the eleven, but a zero is
            # worse than the penalty — sell the cheapest player regardless.
            victim = min(state.player_ids, key=lambda p: scores.get(p, 0.0))
        state.sell(victim, _proceeds(victim, mv_fn, at), at=at)
        scores.pop(victim, None)
        sells += 1
    return sells


def _flip_sells(
    state: ReplayState,
    scores: dict[str, float],
    mv_fn: Callable[[str, float], int | None],
    at: float,
    *,
    profit_take_pct: float | None,
    loss_cut_pct: float | None,
    ledger: list[FlipRecord],
) -> int:
    """Take profit and cut losses on squad players; returns sells (REH-68).

    Ports the live trade-side thresholds (``min_sell_profit_pct`` 15.0,
    ``max_loss_pct`` -15.0). Until now the replay sold only to make room or to
    restore solvency, so an entire revenue behaviour of the real bot was
    missing from every result.

    Fieldability is checked before every sale for the same reason it is on the
    make-room path: an empty slot costs -100, which dwarfs any plausible
    trading gain. A squad of exactly eleven therefore holds a winner rather
    than banking it.

    Best-eleven starters are protected, mirroring the live sell logic
    ("Non-displaced best-11 starters are protected and cannot be sold",
    decision.py). Without that, the replay banks a 15% gain by selling the very
    player it needs on Saturday — converting points into cash it then spends
    worse. Re-entry through a contested auction pays a measured 1.117x market
    value (``transfer_type = 2`` rows); re-entry through a Kickbase listing pays
    market value exactly, so the loss there is the forgone points and the spread
    on whatever the cash buys instead, not a fixed premium (REH-76).

    Players with no cost basis are skipped rather than assumed free.

    Every sale here also gets a shot at ``ledger``: a completed round trip is
    recorded, but only for players whose ``acquired == "bought"``. The opening
    squad was *assigned*, not bought (REH-68), so counting its disposals would
    inflate the round-trip count and make ``ledger`` incomparable to the real
    151 round trips it exists to be compared against (REH-71).

    THIS IS ALSO THE ONLY PLACE THE LEDGER IS WRITTEN, which makes the replay's
    round-trip definition narrower than the live one and its cash figure
    optimistic. Sales by ``_restore_budget`` and by the EP make-room path are
    invisible to it, while ``LearningTracker.record_flip_outcome`` counts them.
    See ``FlipRecord`` for the full statement of the gap; it is documented
    rather than closed, because closing it would change numbers that were
    recorded once by design.
    """
    if profit_take_pct is None and loss_cut_pct is None:
        return 0

    protected = {p.id for p in select_best_eleven(state.players, scores)}

    sells = 0
    for pid in list(state.player_ids):
        if pid in protected:
            continue
        player = state.squad.get(pid)
        if player is None or not player.buy_price:
            continue
        current = mv_fn(pid, at)
        if not current:
            continue
        change_pct = (current - player.buy_price) / player.buy_price * 100.0
        take = profit_take_pct is not None and change_pct >= profit_take_pct
        cut = loss_cut_pct is not None and change_pct <= loss_cut_pct
        if not (take or cut):
            continue
        remaining = {k: v for k, v in state.squad.items() if k != pid}
        if not can_field_eleven(ReplayState(budget=state.budget, squad=remaining)):
            continue
        if player.acquired == "bought":
            ledger.append(
                FlipRecord(
                    player_id=pid,
                    buy_price=int(player.buy_price),
                    proceeds=_proceeds(pid, mv_fn, at),
                    bought_at=player.bought_at,
                    sold_at=at,
                )
            )
        state.sell(pid, _proceeds(pid, mv_fn, at), at=at)
        scores.pop(pid, None)
        sells += 1
    return sells


def _flip_buys(
    state: ReplayState,
    scores: dict[str, float],
    listings: list,
    at: float,
    *,
    flip_buy_fn: Callable[[list, float, int, int], list],
    score_fn: Callable[[str, float], float],
    mv_fn: Callable[[str, float], int | None],
    position_fn: Callable[[str], str | None],
    team_fn: Callable[[str], str | None],
    with_competition: bool,
    wash_trade_block_seconds: float | None = None,
) -> int:
    """Buy for expected appreciation with the slots the EP pass left (REH-71).

    Mirrors the live ordering: EP candidates execute first and flips take
    "remaining slots" (auto_trader.py:533). A flip never displaces a squad
    member, so this stops at MAX_SQUAD_SIZE rather than calling
    ``_fieldable_sale_victim``.

    Candidates carry their own ``max_bid`` from ``flip_buys.flip_bid_ceiling``
    rather than going through ``bid_fn``: a flip's marginal EP gain is ~0 by
    construction, so the EP bidder would put every flip in its bottom tier and
    lose every contested listing, reporting a bidder artifact as a fact about
    flipping.
    """
    from rehoboam.scoring.decision import _would_create_dead_weight

    by_id = {listing.player_id: listing for listing in listings}
    team_value = _team_value(state, mv_fn, at)
    buys = 0

    for candidate in flip_buy_fn(listings, at, state.budget, team_value):
        if state.squad_size >= MAX_SQUAD_SIZE:
            break
        pid = candidate.player_id
        listing = by_id.get(pid)
        position = position_fn(pid)
        if listing is None or not position or pid in state.squad:
            continue

        # Live wash-trade guard (auto_trader.py:374, applying
        # Settings.wash_trade_block_hours, default 168h/7d): refuse to re-buy a
        # player sold within the block window. Applied here only, not in the EP
        # loop above -- the EP loop already gates on gain >= min_ep_gain, so
        # re-buying a just-sold player there requires them to be a large
        # upgrade, whereas the flip pass has no such gate and is where a
        # same-matchday wash trade actually appeared (REH-71). Extending the
        # guard to the EP loop would also change that experiment's baseline
        # arms, which is not this task's call to make.
        if wash_trade_block_seconds is not None:
            sold = state.sold_at.get(pid)
            if sold is not None and at - sold < wash_trade_block_seconds:
                continue

        # Under competition we must outbid what the real buyer paid, and we then
        # pay our own bid. Without it the listing is ours at its asking price --
        # but never above the ceiling, which is an economic limit either way.
        if with_competition:
            if candidate.max_bid <= listing.price:
                continue
            cost = int(candidate.max_bid)
        else:
            if listing.price > candidate.max_bid:
                continue
            cost = int(listing.price)

        player = ReplayPlayer(id=pid, position=position, team_id=team_fn(pid))
        # Deliberate duck typing, sanctioned by the REH-71 plan: the shipped
        # guard is annotated against `MarketPlayer` but reads only `.position`,
        # which `ReplayPlayer` has. Calling the real rule beats reimplementing
        # it; the alternative -- widening the shipped signature to a Protocol --
        # is a refactor of live scoring code that this replay-only change has no
        # business making. Scoped to this call, not silenced module-wide.
        if _would_create_dead_weight(player, state.players):  # type: ignore[arg-type]
            continue
        allowed, _reason = can_buy(state, player, cost, team_value=team_value)
        if not allowed or not _solvent_after(state, cost):
            continue

        state.buy(player, cost, at=at)
        scores[pid] = score_fn(pid, at)
        team_value = _team_value(state, mv_fn, at)
        buys += 1

    return buys


def shipped_min_ep_gain() -> float:
    """The marginal-gain floor the live bot actually ships with (REH-66).

    Read from the field default rather than by instantiating ``Settings``,
    which requires KICKBASE credentials — the replay has to stay runnable
    offline and deterministic against the DBs alone. Reading it dynamically
    means a re-tune in production cannot silently leave the harness behind.
    """
    from rehoboam.config import Settings

    return float(Settings.model_fields["min_ep_upgrade_threshold"].default)


def run_season(
    *,
    state: ReplayState,
    market,
    matchdays: list[Matchday],
    score_fn: Callable[[str, float], float],
    mv_fn: Callable[[str, float], int | None],
    position_fn: Callable[[str], str | None],
    team_fn: Callable[[str], str | None],
    # NOTE: this default is the *engine's* neutral value, not the bot's. Any
    # caller reporting a headline number must pass shipped_min_ep_gain() —
    # REH-51's result was produced by silently accepting 5.0 while production
    # gated at 40.0. `driver.run_replay` does this; see REH-66.
    min_ep_gain: float = 5.0,
    # REH-67 buy-side control. When buy_rank_fn is given, candidates are ranked
    # by it instead of by EP and the marginal-gain gate is bypassed, with
    # buy_quota[day_number] holding the trading tempo fixed to a reference run.
    # Lineup and sell decisions keep using score_fn, so a difference in the
    # result is attributable to the buy side alone. Leave both None for the
    # shipped behaviour.
    buy_rank_fn: Callable[[str, float], float] | None = None,
    buy_quota: dict[int, int] | None = None,
    # REH-68 bid competition. Given (player_id, real_price, at, marginal_gain,
    # budget, squad_size), returns our maximum bid. Gain and budget are passed
    # because a real bid is a function of both — SmartBidding sizes its overbid
    # from the marginal-gain tier and caps it against what we can afford — and
    # a hook without them could only express a flat willingness to pay, which
    # is not what the bot does. squad_size (REH-85) lets the hook derive how
    # many slots remain, which is what capital pacing reserves against.
    # We win only if the bid EXCEEDS what the real buyer actually paid,
    # and we then pay our own bid rather than theirs — outbidding a rival costs
    # more than the rival paid, and charging their price would hand us the
    # upside of competition with none of its cost. None keeps the shipped
    # behaviour, in which every wanted player is won.
    bid_fn: Callable[[str, int, float, float, int, int], int] | None = None,
    # REH-68 profit flipping. The live trade-side thresholds
    # (min_sell_profit_pct 15.0 / max_loss_pct -15.0). Both None disables the
    # pass entirely, preserving the shipped replay behaviour in which the bot
    # sells only to make room or to restore solvency.
    profit_take_pct: float | None = None,
    loss_cut_pct: float | None = None,
    # REH-71 flip buys. Given (listings, at, budget, team_value), returns
    # candidates carrying their own economic max_bid. None keeps the shipped
    # behaviour, in which every buy is justified by marginal expected points.
    flip_buy_fn: Callable[[list, float, int, int], list] | None = None,
    # REH-71 wash-trade guard, ported from the live bot's
    # `AutoTrader._is_wash_trade` (auto_trader.py:667) and
    # `Settings.wash_trade_block_hours` (config.py, default 168h/7d). Applied
    # to flip candidates only (see `_flip_buys`). None keeps the shipped
    # replay behaviour, in which nothing blocks a re-buy.
    wash_trade_block_seconds: float | None = None,
) -> SeasonResult:
    """Replay every matchday in order, mutating ``state`` as the bot would."""
    result = SeasonResult()

    for md in matchdays:
        decide_at = md.kickoff - DECISION_LEAD_SECONDS
        scores = {pid: score_fn(pid, decide_at) for pid in state.player_ids}

        buys = sells = 0
        # Trade before shopping: proceeds fund the same matchday's buys.
        sells += _flip_sells(
            state,
            scores,
            mv_fn,
            decide_at,
            profit_take_pct=profit_take_pct,
            loss_cut_pct=loss_cut_pct,
            ledger=result.flips,
        )
        rank_fn = buy_rank_fn or score_fn
        quota = None if buy_quota is None else buy_quota.get(md.day_number, 0)
        listings = sorted(
            market.available_before(decide_at),
            key=lambda x: rank_fn(x.player_id, decide_at),
            reverse=True,
        )
        for listing in listings:
            if listing.player_id in state.squad:
                continue
            cand_ep = score_fn(listing.player_id, decide_at)
            position = position_fn(listing.player_id)
            if not position:
                continue

            candidate = ReplayPlayer(
                id=listing.player_id,
                position=position,
                team_id=team_fn(listing.player_id),
            )

            # Marginal gain: how much this player improves the best legal
            # eleven. Measuring against the weakest *squad* member instead
            # waves through a twelfth midfielder who outscores the bench but
            # cannot displace a starter — a true gain of zero.
            gain = _eleven_total(
                [*state.players, candidate], {**scores, candidate.id: cand_ep}
            ) - _eleven_total(state.players, scores)
            if buy_rank_fn is None:
                if gain < min_ep_gain and state.squad_size >= 11:
                    continue
            elif quota is not None and buys >= quota:
                # Tempo matched to the reference run — the control may not buy
                # its way to a better season simply by trading more.
                break

            # What this signing costs us. Without a competition model that is
            # the price the real buyer paid; with one it is our own winning bid.
            cost = listing.price
            if bid_fn is not None:
                our_bid = bid_fn(
                    listing.player_id,
                    listing.price,
                    decide_at,
                    gain,
                    state.budget,
                    state.squad_size,
                )
                if our_bid <= listing.price:
                    continue  # outbid by the manager who really signed him
                cost = our_bid

            # Check every constraint that a sale would NOT relieve before
            # selling anyone — otherwise a blocked buy leaves us a player down.
            allowed, reason = can_buy(
                state, candidate, cost, team_value=_team_value(state, mv_fn, decide_at)
            )
            if not allowed and "squad full" not in reason:
                continue

            if state.squad_size >= MAX_SQUAD_SIZE:
                sold_id = _fieldable_sale_victim(state, scores)
                if sold_id is None:
                    continue  # no sale keeps the eleven legal — skip this buy
                sale_proceeds = _proceeds(sold_id, mv_fn, decide_at)
                # Solvency is tested *before* the sale so that an unaffordable
                # candidate never costs us a player we then cannot replace.
                if not _solvent_after(state, cost, sale_proceeds):
                    continue
                state.sell(sold_id, sale_proceeds, at=decide_at)
                scores.pop(sold_id, None)
                sells += 1
                # The credit floor is 70% of *current* team value, so it has to
                # be re-derived after the sale rather than reused from before.
                allowed, reason = can_buy(
                    state,
                    candidate,
                    cost,
                    team_value=_team_value(state, mv_fn, decide_at),
                )
                if not allowed:
                    continue
            elif not _solvent_after(state, cost):
                continue

            state.buy(candidate, cost, at=decide_at)
            scores[candidate.id] = cand_ep
            buys += 1

        if flip_buy_fn is not None:
            buys += _flip_buys(
                state,
                scores,
                listings,
                decide_at,
                flip_buy_fn=flip_buy_fn,
                score_fn=score_fn,
                mv_fn=mv_fn,
                position_fn=position_fn,
                team_fn=team_fn,
                with_competition=bid_fn is not None,
                wash_trade_block_seconds=wash_trade_block_seconds,
            )

        # Budget must be non-negative at kickoff or the matchday scores zero.
        sells += _restore_budget(state, scores, mv_fn, decide_at)

        eleven = select_best_eleven(state.players, scores)
        lineup_ids = [p.id for p in eleven]
        penalty = empty_slot_penalty(len(lineup_ids))
        zeroed = state.budget < 0
        raw = sum(md.points.get(pid, 0.0) for pid in lineup_ids)
        scored = 0 if zeroed else int(raw + penalty)

        result.outcomes.append(
            MatchdayOutcome(
                day_number=md.day_number,
                points_scored=scored,
                lineup_ids=lineup_ids,
                penalty=0 if zeroed else penalty,
                budget_at_kickoff=state.budget,
                zeroed=zeroed,
                squad_size=state.squad_size,
                buys=buys,
                sells=sells,
            )
        )
        result.total_points += scored

    result.final_budget = state.budget
    return result
