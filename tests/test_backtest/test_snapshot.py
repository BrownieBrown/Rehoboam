"""Tests for rehoboam.backtest.snapshot — the anti-leakage boundary."""

from __future__ import annotations

from rehoboam.backtest.snapshot import matches_before


def _m(season: str, day: int, points: int = 50) -> dict:
    return {"season": season, "day_number": day, "points": points, "minutes": 90}


def test_returns_only_earlier_days_in_same_season():
    matches = [_m("2025/2026", d) for d in (1, 2, 3, 4, 5)]
    result = matches_before(matches, season="2025/2026", day_number=3)
    assert [m["day_number"] for m in result] == [1, 2]


def test_includes_all_prior_seasons():
    matches = [
        _m("2024/2025", 30),
        _m("2024/2025", 34),
        _m("2025/2026", 1),
        _m("2025/2026", 5),
    ]
    result = matches_before(matches, season="2025/2026", day_number=2)
    assert [(m["season"], m["day_number"]) for m in result] == [
        ("2024/2025", 30),
        ("2024/2025", 34),
        ("2025/2026", 1),
    ]


def test_excludes_future_seasons():
    matches = [_m("2025/2026", 1), _m("2026/2027", 1)]
    result = matches_before(matches, season="2025/2026", day_number=5)
    assert [m["season"] for m in result] == ["2025/2026"]


def test_deliberate_cheat_finds_no_future_data():
    """The leak check. If this ever passes with future data present, every
    backtest number in the project is worthless."""
    matches = [_m("2025/2026", d, points=999) for d in range(1, 35)]

    for cutoff in range(1, 35):
        result = matches_before(matches, season="2025/2026", day_number=cutoff)
        assert all(
            m["day_number"] < cutoff for m in result
        ), f"LEAK at cutoff {cutoff}: {[m['day_number'] for m in result if m['day_number'] >= cutoff]}"


def test_day_zero_returns_nothing_from_current_season():
    matches = [_m("2025/2026", 1)]
    assert matches_before(matches, season="2025/2026", day_number=1) == []


def test_empty_input_returns_empty():
    assert matches_before([], season="2025/2026", day_number=5) == []
