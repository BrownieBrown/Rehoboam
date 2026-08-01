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
    empty_slot_penalty,
)
from rehoboam.replay.state import ReplayPlayer, ReplayState

# Selling instantly to Kickbase returns 95% of market value.
INSTANT_SELL_PCT = 0.95
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


def run_season(
    *,
    state: ReplayState,
    market,
    matchdays: list[Matchday],
    score_fn: Callable[[str, float], float],
    mv_fn: Callable[[str, float], int | None],
    position_fn: Callable[[str], str | None],
    team_fn: Callable[[str], str | None],
    min_ep_gain: float = 5.0,
) -> SeasonResult:
    """Replay every matchday in order, mutating ``state`` as the bot would."""
    result = SeasonResult()

    for md in matchdays:
        decide_at = md.kickoff - DECISION_LEAD_SECONDS
        scores = {pid: score_fn(pid, decide_at) for pid in state.player_ids}

        buys = sells = 0
        listings = sorted(
            market.available_before(decide_at),
            key=lambda x: score_fn(x.player_id, decide_at),
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
            team_value = _team_value(state, mv_fn, decide_at)

            # Marginal gain: how much this player improves the weakest slot.
            weakest = min(scores.values()) if scores else 0.0
            if cand_ep - weakest < min_ep_gain and state.squad_size >= 11:
                continue

            # Check every constraint that a sale would NOT relieve before
            # selling anyone — otherwise a blocked buy leaves us a player down.
            allowed, _reason = can_buy(state, candidate, listing.price, team_value=team_value)
            if not allowed and "squad full" not in _reason:
                continue

            sold_id: str | None = None
            if state.squad_size >= MAX_SQUAD_SIZE:
                sold_id = min(scores, key=lambda p: scores[p])
                proceeds = int((mv_fn(sold_id, decide_at) or 0) * INSTANT_SELL_PCT)
                state.sell(sold_id, proceeds)
                del scores[sold_id]
                sells += 1
                allowed, _reason = can_buy(state, candidate, listing.price, team_value=team_value)
                if not allowed:
                    continue

            state.buy(candidate, listing.price)
            scores[candidate.id] = cand_ep
            buys += 1

        # Budget must be non-negative at kickoff — sell the weakest until it is.
        while state.budget < 0 and state.squad_size > 11:
            worst_id = min(scores, key=lambda p: scores[p])
            proceeds = int((mv_fn(worst_id, decide_at) or 0) * INSTANT_SELL_PCT)
            state.sell(worst_id, proceeds)
            del scores[worst_id]
            sells += 1

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
