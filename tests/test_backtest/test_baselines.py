"""Tests for rehoboam.backtest.baselines."""

from __future__ import annotations

import pytest

from rehoboam.backtest.baselines import season_average_baseline


def _m(day: int, points: int, minutes: int = 90) -> dict:
    return {
        "season": "2025/2026",
        "day_number": day,
        "points": points,
        "minutes": minutes,
    }


def test_average_over_played_matches():
    assert season_average_baseline([_m(1, 60), _m(2, 80), _m(3, 100)]) == pytest.approx(80.0)


def test_did_not_play_matches_are_excluded():
    """A 0-point, 0-minute row is an absence, not a bad performance. Counting
    it would drag every rotation player's estimate toward zero."""
    assert season_average_baseline([_m(1, 90), _m(2, 0, minutes=0)]) == pytest.approx(90.0)


def test_played_but_scored_zero_counts():
    result = season_average_baseline([_m(1, 90), _m(2, 0, minutes=45)])
    assert result == pytest.approx(45.0)


def test_negative_points_count():
    assert season_average_baseline([_m(1, -4), _m(2, 4)]) == pytest.approx(0.0)


def test_empty_history_returns_zero():
    assert season_average_baseline([]) == 0.0


def test_all_absences_returns_zero():
    assert season_average_baseline([_m(1, 0, minutes=0)]) == 0.0
