"""REH-82: the locked-phase -100 guard must ask whether an eleven is FIELDABLE.

The old trigger was `11 - len(squad)`, a headcount. An empty lineup slot costs
-100 points, and the case that produces one is not always a short squad: eleven
players with no goalkeeper cannot fill any legal formation, but a headcount
reports zero missing and the emergency fill never runs.
"""

from __future__ import annotations

from rehoboam.auto_trader import _emergency_slots_short
from rehoboam.kickbase_client import Player


def _p(pid: str, position: str) -> Player:
    return Player(
        id=pid,
        first_name="P",
        last_name=pid,
        position=position,
        team_id="1",
        team_name="T",
        market_value=1_000_000,
        points=0,
        average_points=50.0,
    )


def _fieldable_eleven() -> list[Player]:
    return (
        [_p("gk", "Goalkeeper")]
        + [_p(f"d{i}", "Defender") for i in range(4)]
        + [_p(f"m{i}", "Midfielder") for i in range(4)]
        + [_p(f"f{i}", "Forward") for i in range(2)]
    )


def test_a_fieldable_eleven_is_not_an_emergency():
    assert _emergency_slots_short(_fieldable_eleven()) == 0


def test_eleven_players_with_no_goalkeeper_is_an_emergency():
    """The case the headcount trigger missed entirely: enough bodies, no legal
    formation. `11 - 11 == 0` said "fine" and the lineup went out a slot short."""
    squad = [p for p in _fieldable_eleven() if p.position != "Goalkeeper"]
    squad.append(_p("extra", "Midfielder"))
    assert len(squad) == 11
    assert _emergency_slots_short(squad) == 1


def test_a_short_squad_still_reports_the_headcount_gap():
    """The old behaviour has to survive: fieldability subsumes it, not replaces it."""
    squad = _fieldable_eleven()[:9]
    assert _emergency_slots_short(squad) == 2


def test_a_fieldable_larger_squad_is_not_an_emergency():
    squad = _fieldable_eleven() + [_p("b1", "Defender"), _p("b2", "Midfielder")]
    assert _emergency_slots_short(squad) == 0


def test_an_empty_squad_is_an_emergency_for_the_full_eleven():
    assert _emergency_slots_short([]) == 11
