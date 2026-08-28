"""Persisting and backfilling flip entry context (REH-104).

The pure reconstruction lives in `learning/entry_context.py`; this covers the
storage seam around it — the three new `flip_outcomes` columns, the MV reader
the reconstruction consumes, and the backfill that makes the 151 rows of
existing history measurable.
"""

import sqlite3

import pytest

from rehoboam.bid_learner import BidLearner, FlipOutcome

DAY = 86400.0
BUY = 1_700_000_000.0


@pytest.fixture
def learner(tmp_path):
    return BidLearner(db_path=tmp_path / "bids.db")


def _flip(learner, pid="p1", **over):
    fields = {
        "player_id": pid,
        "player_name": "Tester",
        "buy_price": 10_000_000,
        "sell_price": 9_000_000,
        "profit": -1_000_000,
        "profit_pct": -10.0,
        "hold_days": 6,
        "buy_date": BUY,
        "sell_date": BUY + 6 * DAY,
    }
    fields.update(over)
    learner.record_flip(FlipOutcome(**fields))


def _mv(learner, pid, *pairs):
    """Seed player_mv_history: (days_before_buy, market_value)."""
    learner.record_player_mv_snapshot(
        [{"player_id": pid, "market_value": mv, "snapshot_at": BUY - d * DAY} for d, mv in pairs]
    )


def _rows(learner):
    """Read flip_outcomes straight from the file — a persistence test should
    assert on what actually landed on disk, not through another accessor."""
    with sqlite3.connect(learner.db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM flip_outcomes")]


class TestTheColumnsRoundTrip:
    def test_entry_context_is_stored_and_read_back(self, learner):
        _flip(
            learner,
            trend_at_buy="rising",
            trend_pct_at_buy=13.2,
            mv_at_buy=9_400_000,
            pct_below_peak_30d_at_buy=-2.5,
        )
        (row,) = _rows(learner)
        assert row["trend_at_buy"] == "rising"
        assert row["trend_pct_at_buy"] == pytest.approx(13.2)
        assert row["mv_at_buy"] == 9_400_000
        assert row["pct_below_peak_30d_at_buy"] == pytest.approx(-2.5)

    def test_a_flip_recorded_without_context_stores_nulls_not_zeros(self, learner):
        """A zero would be a real reading — 'bought exactly at the peak'. An
        unknown entry must stay distinguishable from that or the aggregates lie."""
        _flip(learner)
        (row,) = _rows(learner)
        assert row["trend_pct_at_buy"] is None
        assert row["pct_below_peak_30d_at_buy"] is None


class TestTheMarketValueReader:
    def test_it_returns_the_players_snapshots_as_epoch_value_pairs(self, learner):
        _mv(learner, "p1", (0, 5_000_000), (14, 4_400_000))
        rows = learner.mv_history_for("p1")
        assert sorted(rows) == [(BUY - 14 * DAY, 4_400_000), (BUY, 5_000_000)]

    def test_an_unknown_player_yields_nothing_rather_than_raising(self, learner):
        assert learner.mv_history_for("nobody") == []


class TestTheBackfill:
    def test_it_populates_a_row_that_has_no_context(self, learner):
        _flip(learner, pid="p1")
        _mv(learner, "p1", (0, 9_400_000), (14, 8_300_000), (10, 10_000_000))
        assert learner.backfill_flip_entry_context() == 1
        (row,) = _rows(learner)
        assert row["trend_at_buy"] == "rising"
        assert row["mv_at_buy"] == 9_400_000
        # peak in the 30d window is 10,000,000 → we bought 6% under it
        assert row["pct_below_peak_30d_at_buy"] == pytest.approx(-6.0)

    def test_it_leaves_a_row_that_already_has_context_alone(self, learner):
        """Idempotent, so a rerun cannot overwrite a value captured live."""
        _flip(learner, pid="p1", trend_at_buy="falling", mv_at_buy=1)
        _mv(learner, "p1", (0, 9_400_000), (14, 8_300_000))
        assert learner.backfill_flip_entry_context() == 0
        (row,) = _rows(learner)
        assert row["trend_at_buy"] == "falling"

    def test_a_flip_with_no_market_value_history_is_skipped_not_faked(self, learner):
        _flip(learner, pid="ghost")
        assert learner.backfill_flip_entry_context() == 0
        (row,) = _rows(learner)
        assert row["mv_at_buy"] is None

    def test_it_is_idempotent_across_reruns(self, learner):
        _flip(learner, pid="p1")
        _mv(learner, "p1", (0, 9_400_000), (14, 8_300_000))
        assert learner.backfill_flip_entry_context() == 1
        assert learner.backfill_flip_entry_context() == 0


class TestTheLiveSellPathRecordsContext:
    """A flip closed by the bot should land with context already attached, so
    the backfill is only ever needed for history."""

    @staticmethod
    def _player(pid="p1"):
        from types import SimpleNamespace

        return SimpleNamespace(
            id=pid,
            first_name="Test",
            last_name="Player",
            average_points=42.0,
            position="Midfielder",
            status=0,
        )

    def _tracker(self, learner):
        from rehoboam.learning.tracker import LearningTracker

        return LearningTracker(learner)

    def test_a_closed_flip_carries_its_entry_context(self, learner):
        _mv(learner, "p1", (0, 9_400_000), (14, 8_300_000), (10, 10_000_000))
        learner.add_tracked_purchase(
            player_id="p1",
            player_name="Test Player",
            buy_price=10_000_000,
            buy_date=BUY,
            source="real",
        )
        self._tracker(learner).record_flip_outcome(self._player(), sell_price=9_000_000)

        (row,) = _rows(learner)
        assert row["trend_at_buy"] == "rising"
        assert row["mv_at_buy"] == 9_400_000
        assert row["pct_below_peak_30d_at_buy"] == pytest.approx(-6.0)

    def test_missing_market_value_history_still_records_the_flip(self, learner):
        """Context is a learning nicety; losing the P&L row would be a real loss."""
        learner.add_tracked_purchase(
            player_id="ghost",
            player_name="Ghost",
            buy_price=10_000_000,
            buy_date=BUY,
            source="real",
        )
        self._tracker(learner).record_flip_outcome(self._player("ghost"), sell_price=9_000_000)

        (row,) = _rows(learner)
        assert row["profit"] == -1_000_000
        assert row["mv_at_buy"] is None
