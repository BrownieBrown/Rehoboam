"""Tests for rehoboam.backtest.metrics."""

from __future__ import annotations

import pytest

from rehoboam.backtest.metrics import lineup_regret, spearman
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


def test_spearman_perfect_positive():
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)


def test_spearman_perfect_negative():
    assert spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)


def test_spearman_is_rank_based_not_value_based():
    # monotonic but wildly non-linear -> still a perfect rank correlation
    assert spearman([1, 2, 3, 4], [1, 10, 1000, 100000]) == pytest.approx(1.0)


def test_spearman_handles_ties_with_average_ranks():
    result = spearman([1, 1, 2, 2], [5, 5, 9, 9])
    assert result == pytest.approx(1.0)


def test_spearman_zero_variance_returns_zero():
    assert spearman([1, 1, 1], [1, 2, 3]) == 0.0


def test_spearman_too_few_points_returns_zero():
    assert spearman([1], [2]) == 0.0


def test_lineup_regret_is_zero_for_optimal_choice():
    squad = (
        [_player("1", "Goalkeeper")]
        + [_player(str(i), "Defender") for i in range(2, 7)]
        + [_player(str(i), "Midfielder") for i in range(7, 12)]
        + [_player(str(i), "Forward") for i in range(12, 15)]
    )
    actual = {p.id: 100.0 for p in squad}

    chosen = [p.id for p in squad[:11]]
    assert lineup_regret(squad, chosen, actual) == pytest.approx(0.0)


def test_lineup_regret_penalises_benching_the_best_player():
    squad = (
        [_player("1", "Goalkeeper")]
        + [_player(str(i), "Defender") for i in range(2, 7)]
        + [_player(str(i), "Midfielder") for i in range(7, 12)]
        + [_player(str(i), "Forward") for i in range(12, 15)]
    )
    actual = {p.id: 50.0 for p in squad}
    actual["14"] = 300.0  # a forward who exploded

    # deliberately field the two weaker forwards and bench "14"
    chosen = ["1"] + [str(i) for i in range(2, 7)] + [str(i) for i in range(7, 11)] + ["12"]
    regret = lineup_regret(squad, chosen, actual)
    assert regret > 0.0


def test_lineup_regret_missing_player_scores_zero():
    squad = [_player("1", "Goalkeeper")]
    assert lineup_regret(squad, ["missing"], {"1": 10.0}) >= 0.0
