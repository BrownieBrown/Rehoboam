"""Tests for the wash-trade guard in BidLearner + LearningTracker.

A wash trade is selling a player and re-bidding on them shortly after,
paying the bid spread on both legs for no EP gain. The guard records
every sell into ``recently_sold`` and lets the buy path query the table
to refuse repeat bids inside the configured window.
"""

import time
from types import SimpleNamespace

import pytest

from rehoboam.bid_learner import BidLearner
from rehoboam.learning.tracker import LearningTracker


@pytest.fixture
def learner(tmp_path):
    return BidLearner(db_path=tmp_path / "bid_learning.db")


@pytest.fixture
def tracker(learner):
    return LearningTracker(learner)


def _player(pid: str = "12333", name: str = "Niang"):
    return SimpleNamespace(
        id=pid,
        first_name="M.",
        last_name=name,
        average_points=12.0,
        position="Forward",
        status=0,
    )


class TestRecentlySoldTable:
    def test_empty_by_default(self, learner):
        assert learner.was_recently_sold("12333", within_seconds=86400) is False
        assert learner.get_recent_sell("12333") is None

    def test_record_then_query_within_window(self, learner):
        learner.record_recent_sell(
            player_id="12333",
            player_name="Niang",
            sold_price=572_468,
            sold_at=time.time(),
            reason="dead-weight surplus",
        )
        assert learner.was_recently_sold("12333", within_seconds=86400) is True
        row = learner.get_recent_sell("12333")
        assert row["player_name"] == "Niang"
        assert row["sold_price"] == 572_468
        assert row["reason"] == "dead-weight surplus"

    def test_record_then_query_outside_window(self, learner):
        # Sold 8 days ago; default block window is 7 days
        learner.record_recent_sell(
            player_id="12333",
            player_name="Niang",
            sold_price=572_468,
            sold_at=time.time() - 8 * 86400,
        )
        assert learner.was_recently_sold("12333", within_seconds=7 * 86400) is False

    def test_repeat_sell_overwrites_previous_row(self, learner):
        learner.record_recent_sell(
            player_id="12333",
            player_name="Niang",
            sold_price=2_000_000,
            sold_at=time.time() - 86400,
        )
        learner.record_recent_sell(
            player_id="12333",
            player_name="Niang",
            sold_price=572_468,
            sold_at=time.time(),
        )
        row = learner.get_recent_sell("12333")
        # Latest sell wins — wash-trade window restarts from the most
        # recent sell, which is the conservative behaviour for the guard.
        assert row["sold_price"] == 572_468

    def test_prune_drops_old_rows_only(self, learner):
        learner.record_recent_sell(
            player_id="old",
            player_name="Old",
            sold_price=1,
            sold_at=time.time() - 30 * 86400,
        )
        learner.record_recent_sell(
            player_id="new",
            player_name="New",
            sold_price=1,
            sold_at=time.time(),
        )
        deleted = learner.prune_recent_sells(older_than_seconds=14 * 86400)
        assert deleted == 1
        assert learner.get_recent_sell("old") is None
        assert learner.get_recent_sell("new") is not None


class TestLearningTrackerHook:
    def test_record_flip_outcome_writes_recently_sold(self, tracker, learner):
        # Buy first so the flip outcome has a cost basis to use.
        learner.add_tracked_purchase(
            player_id="12333",
            player_name="M. Niang",
            buy_price=2_000_000,
            buy_date=time.time() - 7 * 3600,  # bought 7h ago — same-day flip
        )

        tracker.record_flip_outcome(_player(), sell_price=572_468, reason="stop-loss")

        # Wash-trade guard sees the sell
        assert learner.was_recently_sold("12333", within_seconds=86400) is True
        row = learner.get_recent_sell("12333")
        assert row["sold_price"] == 572_468
        assert row["reason"] == "stop-loss"
        # Tracked purchase is consumed after the flip outcome
        assert learner.get_tracked_purchase("12333") is None

    def test_record_flip_outcome_records_even_without_purchase(self, tracker, learner):
        # No tracked_purchase row — e.g. the player was bought before the
        # tracker existed (Azure wiped JSON state). The flip outcome can't
        # be computed but the wash-trade guard still has to fire.
        tracker.record_flip_outcome(_player("99999", "Pieper"), sell_price=3_034_839)

        assert learner.was_recently_sold("99999", within_seconds=86400) is True
