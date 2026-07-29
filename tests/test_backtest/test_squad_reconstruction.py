"""Tests for rehoboam.backtest.squad_reconstruction."""

from __future__ import annotations

from rehoboam.backtest.squad_reconstruction import squad_on_matchday

DAY = 86400.0
T0 = 1_700_000_000.0


def _flip(pid: str, buy_offset_days: float, sell_offset_days: float) -> dict:
    return {
        "player_id": pid,
        "buy_date": T0 + buy_offset_days * DAY,
        "sell_date": T0 + sell_offset_days * DAY,
    }


def test_player_inside_hold_window_is_in_squad():
    flips = [_flip("1", 0, 10)]
    assert "1" in squad_on_matchday(flips, [], T0 + 5 * DAY)


def test_player_outside_hold_window_is_not_in_squad():
    flips = [_flip("1", 0, 10)]
    assert squad_on_matchday(flips, [], T0 + 20 * DAY) == set()
    assert squad_on_matchday(flips, [], T0 - 5 * DAY) == set()


def test_boundaries_are_inclusive():
    flips = [_flip("1", 0, 10)]
    assert "1" in squad_on_matchday(flips, [], T0)
    assert "1" in squad_on_matchday(flips, [], T0 + 10 * DAY)


def test_fielded_players_are_always_included():
    """Players bought and never sold have no flip row, so the fielded 11 is
    unioned in — otherwise long-term holds vanish from the reconstruction."""
    assert squad_on_matchday([], ["7", "8"], T0) == {"7", "8"}


def test_union_of_both_sources():
    flips = [_flip("1", 0, 10)]
    assert squad_on_matchday(flips, ["7"], T0 + 5 * DAY) == {"1", "7"}


def test_multiple_holds_of_same_player():
    flips = [_flip("1", 0, 5), _flip("1", 20, 30)]
    assert "1" in squad_on_matchday(flips, [], T0 + 3 * DAY)
    assert squad_on_matchday(flips, [], T0 + 10 * DAY) == set()
    assert "1" in squad_on_matchday(flips, [], T0 + 25 * DAY)
