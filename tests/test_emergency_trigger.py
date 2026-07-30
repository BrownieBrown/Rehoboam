"""Regression tests for the emergency-buy trigger (Task 12 fix round 1).

Background: raising ``min_squad_size`` 10->13 (to fix the squad-floor bug)
also widened the emergency-buy trigger from ``squad_size <= 9`` to
``squad_size <= 12`` — against real 2025/26 session data, squad size was 11
in 41% of sessions and 12 in 52%, so the naive headcount comparison would
have fired the "buy almost anything, no upgrade gate" emergency path on 93%
of sessions instead of 0%.

The actual historical failure (matchdays 6, 17, 21: 11-12 players fielding
only 10) was a position/availability-shape problem, not a headcount problem.
``_determine_emergency`` fixes this by asking "can these players field a
legal eleven?" (via ``can_fill_starting_eleven``) instead of comparing raw
headcount to a floor.
"""

from __future__ import annotations

from rehoboam.kickbase_client import Player
from rehoboam.trader import _determine_emergency


def _player(pid: str, position: str) -> Player:
    return Player(
        id=pid,
        first_name="F",
        last_name=f"P{pid}",
        position=position,
        team_id="1",
        team_name="T",
        market_value=1_000_000,
        points=0,
        average_points=0.0,
    )


def test_healthy_twelve_player_squad_is_not_emergency():
    """A normal 11-12 player squad (the everyday range last season) with a
    legal position mix must NOT trip the emergency "buy almost anything"
    path. Under the pre-fix code (squad_size < min_squad_size=13), this
    exact squad would have wrongly been flagged as an emergency."""
    squad = (
        [_player("1", "Goalkeeper")]
        + [_player(str(i), "Defender") for i in range(2, 6)]
        + [_player(str(i), "Midfielder") for i in range(6, 10)]
        + [_player(str(i), "Forward") for i in range(10, 13)]
    )
    assert len(squad) == 12

    is_emergency, reason = _determine_emergency(squad)

    assert is_emergency is False
    assert reason == ""


def test_squad_that_cannot_field_eleven_is_emergency():
    """Pinning test: 11 raw bodies (headcount alone looks fine) but only 2
    defenders — one short of the formation minimum. This is the actual
    shape of the 2025/26 bug (11-12 players, still fielded 10), and only a
    position-aware check catches it; a headcount-only check at any
    threshold <= 11 would have missed it."""
    squad = (
        [_player("1", "Goalkeeper")]
        + [_player("2", "Defender"), _player("3", "Defender")]
        + [_player(str(i), "Midfielder") for i in range(4, 9)]
        + [_player(str(i), "Forward") for i in range(9, 12)]
    )
    assert len(squad) == 11

    is_emergency, reason = _determine_emergency(squad)

    assert is_emergency is True
    assert "Defender" in reason
