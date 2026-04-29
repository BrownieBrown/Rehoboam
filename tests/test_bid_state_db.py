"""Tests for the SQLite-backed bid/purchase state replacing legacy JSON files.

The bot used to keep `pending_bids.json` and `tracked_purchases.json` in
the working directory. Azure wiped those between runs because only
`bid_learning.db` was synced to blob storage. These tests cover the new
in-DB equivalents living alongside the existing learning tables in
`bid_learning.db`.
"""

import time

import pytest

from rehoboam.bid_learner import BidLearner


@pytest.fixture
def learner(tmp_path):
    return BidLearner(db_path=tmp_path / "bid_learning.db")


# ---------------------------------------------------------------------------
# pending_bids
# ---------------------------------------------------------------------------


class TestPendingBids:
    def test_get_pending_bids_empty_by_default(self, learner):
        assert learner.get_pending_bids() == []

    def test_add_and_get_simple_bid(self, learner):
        ts = time.time()
        learner.add_pending_bid(
            player_id="123",
            player_name="Test Player",
            our_bid=10_000_000,
            asking_price=8_000_000,
            our_overbid_pct=25.0,
            timestamp=ts,
            market_value=8_500_000,
            player_value_score=72.5,
        )

        bids = learner.get_pending_bids()
        assert len(bids) == 1
        bid = bids[0]
        assert bid["player_id"] == "123"
        assert bid["player_name"] == "Test Player"
        assert bid["our_bid"] == 10_000_000
        assert bid["asking_price"] == 8_000_000
        assert bid["our_overbid_pct"] == 25.0
        assert bid["timestamp"] == ts
        assert bid["market_value"] == 8_500_000
        assert bid["player_value_score"] == 72.5
        # Bids without sell plans should expose an empty list, never None,
        # so resolve_auctions can iterate without an isinstance check.
        assert bid["sell_plan_player_ids"] == []

    def test_add_bid_with_sell_plan_persists_join_rows(self, learner):
        # Sell plans are stored in a normalized join table so future
        # analytics can ask "which bids freed which slot" without parsing
        # JSON blobs.
        learner.add_pending_bid(
            player_id="555",
            player_name="Star Striker",
            our_bid=20_000_000,
            asking_price=18_000_000,
            our_overbid_pct=11.1,
            timestamp=time.time(),
            sell_plan_player_ids=["111", "222"],
        )

        bids = learner.get_pending_bids()
        assert len(bids) == 1
        # Order doesn't matter — caller only iterates the list.
        assert sorted(bids[0]["sell_plan_player_ids"]) == ["111", "222"]

    def test_get_pending_bids_returns_sorted_by_timestamp(self, learner):
        learner.add_pending_bid(
            player_id="A",
            player_name="A",
            our_bid=1,
            asking_price=1,
            our_overbid_pct=0.0,
            timestamp=200.0,
        )
        learner.add_pending_bid(
            player_id="B",
            player_name="B",
            our_bid=1,
            asking_price=1,
            our_overbid_pct=0.0,
            timestamp=100.0,
        )

        bids = learner.get_pending_bids()
        # Oldest first matches resolve_auctions semantics — earliest bids
        # tend to be the ones whose auctions resolve first.
        assert [b["player_id"] for b in bids] == ["B", "A"]

    def test_delete_pending_bid_removes_row(self, learner):
        learner.add_pending_bid(
            player_id="123",
            player_name="x",
            our_bid=1,
            asking_price=1,
            our_overbid_pct=0.0,
            timestamp=time.time(),
        )
        learner.delete_pending_bid("123")
        assert learner.get_pending_bids() == []

    def test_delete_pending_bid_also_removes_sell_plan_rows(self, learner):
        # A dangling sell-plan row would be a silent FK leak — every
        # delete must clear both tables.
        learner.add_pending_bid(
            player_id="123",
            player_name="x",
            our_bid=1,
            asking_price=1,
            our_overbid_pct=0.0,
            timestamp=time.time(),
            sell_plan_player_ids=["A", "B"],
        )
        learner.delete_pending_bid("123")

        # Re-adding the same player_id with no sell plan must not see
        # ghosts from the previous insert.
        learner.add_pending_bid(
            player_id="123",
            player_name="x",
            our_bid=1,
            asking_price=1,
            our_overbid_pct=0.0,
            timestamp=time.time(),
        )
        bids = learner.get_pending_bids()
        assert len(bids) == 1
        assert bids[0]["sell_plan_player_ids"] == []

    def test_delete_unknown_pending_bid_is_noop(self, learner):
        # resolve_auctions can race with squad fetches — being asked to
        # delete a bid that's already gone must not raise.
        learner.delete_pending_bid("nonexistent")  # no exception

    def test_player_id_is_unique(self, learner):
        # Re-placing a bid on the same player should overwrite, not
        # duplicate.  Two pending rows for the same player would make
        # resolve_auctions ambiguous.
        learner.add_pending_bid(
            player_id="123",
            player_name="x",
            our_bid=1_000_000,
            asking_price=1_000_000,
            our_overbid_pct=0.0,
            timestamp=time.time(),
        )
        learner.add_pending_bid(
            player_id="123",
            player_name="x",
            our_bid=2_000_000,
            asking_price=1_500_000,
            our_overbid_pct=33.3,
            timestamp=time.time(),
        )

        bids = learner.get_pending_bids()
        assert len(bids) == 1
        assert bids[0]["our_bid"] == 2_000_000


# ---------------------------------------------------------------------------
# tracked_purchases
# ---------------------------------------------------------------------------


class TestTrackedPurchases:
    def test_get_unknown_purchase_returns_none(self, learner):
        assert learner.get_tracked_purchase("missing") is None

    def test_add_and_get_purchase(self, learner):
        ts = time.time()
        learner.add_tracked_purchase(
            player_id="42",
            player_name="Test Player",
            buy_price=5_000_000,
            buy_date=ts,
            source="real",
        )

        p = learner.get_tracked_purchase("42")
        assert p is not None
        assert p["player_id"] == "42"
        assert p["player_name"] == "Test Player"
        assert p["buy_price"] == 5_000_000
        assert p["buy_date"] == ts
        assert p["source"] == "real"

    def test_source_defaults_to_none_when_not_provided(self, learner):
        # Real bids we won have source='real'; squad-snapshot detections
        # have source='detected'; an explicit None is fine for callers
        # that don't track provenance.
        learner.add_tracked_purchase(
            player_id="99",
            player_name="x",
            buy_price=1,
            buy_date=1.0,
        )
        p = learner.get_tracked_purchase("99")
        assert p is not None
        assert p["source"] is None

    def test_delete_tracked_purchase(self, learner):
        learner.add_tracked_purchase(
            player_id="42",
            player_name="x",
            buy_price=1,
            buy_date=1.0,
        )
        learner.delete_tracked_purchase("42")
        assert learner.get_tracked_purchase("42") is None

    def test_delete_unknown_purchase_is_noop(self, learner):
        learner.delete_tracked_purchase("missing")  # no exception

    def test_player_id_is_unique(self, learner):
        # If we somehow buy the same player twice without a sell in
        # between, the latest cost basis wins — flip P&L should reflect
        # the most recent purchase.
        learner.add_tracked_purchase(
            player_id="42",
            player_name="x",
            buy_price=1_000_000,
            buy_date=1.0,
        )
        learner.add_tracked_purchase(
            player_id="42",
            player_name="x",
            buy_price=2_000_000,
            buy_date=2.0,
        )

        p = learner.get_tracked_purchase("42")
        assert p["buy_price"] == 2_000_000
        assert p["buy_date"] == 2.0
