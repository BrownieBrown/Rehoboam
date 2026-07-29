"""Guardrail regression tests for the 2025/26 10-man lineup failures.

Matchdays 6, 17 and 21 fielded only 10 players, costing -100 points each.
Root cause: min_squad_size defaulted to 10, which cannot fill 11 slots.
"""

from __future__ import annotations

from rehoboam.config import Settings
from rehoboam.formation import can_fill_starting_eleven
from rehoboam.kickbase_client import Player


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


def test_squad_floor_can_fill_eleven_slots():
    """The 2025/26 bug in one assertion: the floor must exceed the 11 a
    lineup needs, with room for injury cover."""
    assert Settings().min_squad_size >= 13


def test_can_fill_with_a_legal_squad():
    available = (
        [_player("1", "Goalkeeper")]
        + [_player(str(i), "Defender") for i in range(2, 7)]
        + [_player(str(i), "Midfielder") for i in range(7, 12)]
        + [_player(str(i), "Forward") for i in range(12, 15)]
    )
    result = can_fill_starting_eleven(available)
    assert result["ok"] is True


def test_cannot_fill_with_only_ten_available():
    available = (
        [_player("1", "Goalkeeper")]
        + [_player(str(i), "Defender") for i in range(2, 5)]
        + [_player(str(i), "Midfielder") for i in range(5, 10)]
        + [_player("10", "Forward")]
    )
    result = can_fill_starting_eleven(available)
    assert result["ok"] is False
    assert "10" in result["reason"] or "11" in result["reason"]


def test_cannot_fill_without_a_goalkeeper():
    """13 outfielders and no keeper is a -100 penalty waiting to happen."""
    available = (
        [_player(str(i), "Defender") for i in range(2, 8)]
        + [_player(str(i), "Midfielder") for i in range(8, 13)]
        + [_player(str(i), "Forward") for i in range(13, 16)]
    )
    result = can_fill_starting_eleven(available)
    assert result["ok"] is False
    assert "Goalkeeper" in result["reason"]


def test_cannot_fill_with_too_few_defenders():
    available = (
        [_player("1", "Goalkeeper")]
        + [_player("2", "Defender"), _player("3", "Defender")]
        + [_player(str(i), "Midfielder") for i in range(4, 12)]
        + [_player("12", "Forward")]
    )
    result = can_fill_starting_eleven(available)
    assert result["ok"] is False
    assert "Defender" in result["reason"]
