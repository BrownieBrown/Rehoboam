"""The measured price of one further move (REH-85).

A "move" costs EUR 6.03m across the whole `manager_transfers` table but
EUR 10.8m in the 2026/27 pre-season. A hardcoded euro figure would be wrong
within one transfer window, which is the same lesson REH-99 learned about the
overbid cap: measure the population, and re-measure it.
"""

from __future__ import annotations

import time

import pytest

from rehoboam.bid_learner import BidLearner


@pytest.fixture
def learner(tmp_path):
    return BidLearner(db_path=tmp_path / "bid_learning.db")


def _iso(days_ago: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - days_ago * 86400))


def _row(price: int, days_ago: float, transfer_type: int = 1, player_id: str = "p") -> dict:
    return {
        "league_id": "L",
        "manager_id": "m1",
        "transfer_dt": _iso(days_ago),
        "player_id": f"{player_id}{price}",
        "player_name": "Test",
        "transfer_type": transfer_type,
        "transfer_price": price,
    }


def test_returns_buy_prices_inside_the_window(learner):
    learner.record_manager_transfers([_row(5_000_000, 10), _row(9_000_000, 20)])
    assert sorted(learner.recent_buy_prices(window_days=90)) == [5_000_000, 9_000_000]


def test_excludes_sells(learner):
    """Type 2 is a sale. Pricing a move off sale proceeds would be wrong."""
    learner.record_manager_transfers([_row(5_000_000, 10), _row(80_000_000, 10, transfer_type=2)])
    assert learner.recent_buy_prices(window_days=90) == [5_000_000]


def test_excludes_rows_outside_the_window(learner):
    learner.record_manager_transfers([_row(5_000_000, 10), _row(9_000_000, 200)])
    assert learner.recent_buy_prices(window_days=90) == [5_000_000]


def test_prices_are_absolute(learner):
    """The feed signs a buy negative in some payloads; a reserve is a magnitude."""
    learner.record_manager_transfers([_row(-7_000_000, 5)])
    assert learner.recent_buy_prices(window_days=90) == [7_000_000]


def test_empty_table_returns_empty_list_not_an_error(learner):
    assert learner.recent_buy_prices(window_days=90) == []
