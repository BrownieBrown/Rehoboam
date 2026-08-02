"""Kickbase legality constraints the replay must respect.

Budget may go negative between kickoffs — the binding constraint is a credit
line of 70% of current team value, and a non-negative balance at kickoff.
A negative balance at kickoff zeroes the entire matchday.
"""

from __future__ import annotations

from rehoboam.config import POSITION_MINIMUMS
from rehoboam.replay.state import ReplayPlayer, ReplayState

MAX_SQUAD_SIZE = 15
MAX_PER_TEAM = 3
CREDIT_LINE_PCT = 0.70
EMPTY_SLOT_PENALTY = -100
STARTING_ELEVEN = 11


def can_buy(
    state: ReplayState, player: ReplayPlayer, price: int, *, team_value: int
) -> tuple[bool, str]:
    """Whether this purchase is legal. Returns ``(allowed, reason)``."""
    if state.squad_size >= MAX_SQUAD_SIZE:
        return False, f"squad full ({MAX_SQUAD_SIZE})"
    if player.team_id and state.team_counts().get(player.team_id, 0) >= MAX_PER_TEAM:
        return False, f"team limit ({MAX_PER_TEAM} from team {player.team_id})"
    floor = -int(team_value * CREDIT_LINE_PCT)
    if state.budget - int(price) < floor:
        return False, f"credit line (floor EUR {floor:,})"
    return True, ""


def can_field_eleven(state: ReplayState) -> bool:
    """Whether the squad can legally fill all 11 starting slots."""
    if state.squad_size < STARTING_ELEVEN:
        return False
    counts: dict[str, int] = {}
    for p in state.players:
        counts[p.position] = counts.get(p.position, 0) + 1
    return all(counts.get(pos, 0) >= need for pos, need in POSITION_MINIMUMS.items())


def empty_slot_penalty(chosen_count: int) -> int:
    """Penalty points for unfilled starting slots."""
    return max(0, STARTING_ELEVEN - chosen_count) * EMPTY_SLOT_PENALTY
