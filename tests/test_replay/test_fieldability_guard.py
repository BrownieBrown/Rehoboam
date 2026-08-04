"""REH-68: the fieldability guard must agree with the thing that fields.

Symptom: the full-fidelity run booked -300 in empty-slot penalties on matchdays
where the squad held 12-13 players. Minimum squad size all season was 12, so
this was never "too few players".

Root cause: two different definitions of "can field eleven".

  can_field_eleven  -> checks POSITION_MINIMUMS only (GK 1, DEF 3, MID 2, FW 1)
  select_best_eleven-> ALSO enforces _POSITION_MAX_STARTERS ceilings
                       (GK 1, DEF 5, MID 5, FW 3), because a 2nd keeper can
                       never start in any formation

So a squad can satisfy every minimum, pass the guard, and still not fill the
eleven. The guard was a weaker re-implementation of a rule that already had an
authority. Profit flipping did not introduce this bug; it skewed squad shapes
enough to expose it.
"""

from __future__ import annotations

from rehoboam.formation import select_best_eleven
from rehoboam.replay.rules import can_field_eleven
from rehoboam.replay.state import ReplayPlayer, ReplayState


def _skewed_squad():
    """13 players meeting every minimum, but 7 of them forwards.

    Ceilings cap selection at 1 GK + 3 DEF + 2 MID + 3 FW = 9, so two starting
    slots cannot be filled however the eleven is chosen.
    """
    squad = {}
    for i, pos in enumerate(
        ["Goalkeeper"] + ["Defender"] * 3 + ["Midfielder"] * 2 + ["Forward"] * 7
    ):
        squad[str(i)] = ReplayPlayer(id=str(i), position=pos, team_id=str(i))
    return ReplayState(budget=0, squad=squad)


def test_the_skewed_squad_really_cannot_fill_eleven():
    """Establishes the ground truth the guard has to match."""
    state = _skewed_squad()

    chosen = select_best_eleven(state.players, {p.id: 1.0 for p in state.players})

    assert len(chosen) < 11, "fixture is wrong if the eleven can actually be filled"


def test_the_guard_rejects_a_squad_that_cannot_fill_eleven():
    """The bug: minimums are satisfied, so the old guard said yes and the
    engine then sold into an unfieldable shape and ate -100 per empty slot."""
    assert can_field_eleven(_skewed_squad()) is False


def test_the_guard_still_accepts_a_normal_squad():
    squad = {}
    for i, pos in enumerate(
        ["Goalkeeper"] + ["Defender"] * 4 + ["Midfielder"] * 4 + ["Forward"] * 2
    ):
        squad[str(i)] = ReplayPlayer(id=str(i), position=pos, team_id=str(i))

    assert can_field_eleven(ReplayState(budget=0, squad=squad)) is True
