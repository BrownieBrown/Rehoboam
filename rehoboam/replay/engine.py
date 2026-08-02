"""The matchday loop: score, sell, buy, field eleven, award real points.

Every decision at matchday N is made with data strictly before N's kickoff.
The engine takes callables rather than a database so that boundary lives in
one place (the driver) and can be tested without I/O.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from rehoboam.formation import select_best_eleven
from rehoboam.replay.rules import (
    MAX_SQUAD_SIZE,
    can_buy,
    can_field_eleven,
    empty_slot_penalty,
)
from rehoboam.replay.state import ReplayPlayer, ReplayState

# Selling instantly to Kickbase returns the FULL market value.
#
# REH-51 asserted 0.95 in its plan and nothing ever checked it. Measured across
# all 151 real flips in `flip_outcomes`, joined to `player_mv_history` within a
# day of the sale: the sell/MV ratio has a hard mode of 41 rows at exactly 1.00
# and ZERO rows at 0.95 (2 anywhere in 0.94-0.96). A 5% haircut would leave a
# cluster at 0.95; there is none. Ratios above 1.00 are sales to other managers
# at a premium, a channel this replay deliberately does not model.
# `api.sell_player_instant` documents the same: "sell instantly to Kickbase at
# market value".
#
# This is not cosmetic: proceeds feed `_solvent_after`, so a 5% understatement
# suppressed buys the bot could actually afford.
INSTANT_SELL_PCT = 1.0
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


@dataclass
class SeasonResult:
    outcomes: list[MatchdayOutcome] = field(default_factory=list)
    total_points: int = 0
    final_budget: int = 0


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
    while ``INSTANT_SELL_PCT`` was wrongly 0.95 (see REH-67). ``can_buy``'s
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
        state.sell(victim, _proceeds(victim, mv_fn, at))
        scores.pop(victim, None)
        sells += 1
    return sells


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
    # REH-68 bid competition. Given (player_id, real_price, at), returns our
    # maximum bid. We win only if it EXCEEDS what the real buyer actually paid,
    # and we then pay our own bid rather than theirs — outbidding a rival costs
    # more than the rival paid, and charging their price would hand us the
    # upside of competition with none of its cost. None keeps the shipped
    # behaviour, in which every wanted player is won.
    bid_fn: Callable[[str, int, float], int] | None = None,
) -> SeasonResult:
    """Replay every matchday in order, mutating ``state`` as the bot would."""
    result = SeasonResult()

    for md in matchdays:
        decide_at = md.kickoff - DECISION_LEAD_SECONDS
        scores = {pid: score_fn(pid, decide_at) for pid in state.player_ids}

        buys = sells = 0
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

            # What this signing costs us. Without a competition model that is
            # the price the real buyer paid; with one it is our own winning bid.
            cost = listing.price
            if bid_fn is not None:
                our_bid = bid_fn(listing.player_id, listing.price, decide_at)
                if our_bid <= listing.price:
                    continue  # outbid by the manager who really signed him
                cost = our_bid

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
                state.sell(sold_id, sale_proceeds)
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

            state.buy(candidate, cost)
            scores[candidate.id] = cand_ep
            buys += 1

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
