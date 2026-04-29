"""Tests for the one-time JSON → SQLite migration of pending bids and
tracked purchases.

The migration runs on `LearningTracker` construction and is idempotent —
once the JSON files have been imported and renamed to `.bak`, subsequent
boots are no-ops.
"""

import json
from pathlib import Path

import pytest

from rehoboam.bid_learner import BidLearner
from rehoboam.learning.migration import migrate_json_state_if_needed


@pytest.fixture
def learner(tmp_path):
    return BidLearner(db_path=tmp_path / "bid_learning.db")


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


class TestMigratePendingBids:
    def test_imports_existing_pending_bids_json(self, tmp_path, learner):
        pending_path = tmp_path / "pending_bids.json"
        _write_json(
            pending_path,
            [
                {
                    "player_id": "10771",
                    "player_name": "Yan Diomande",
                    "our_bid": 14_097_338,
                    "asking_price": 12_477_338,
                    "our_overbid_pct": 12.98,
                    "timestamp": 1_762_686_174.95,
                    "market_value": 1_821_396,
                }
            ],
        )

        migrate_json_state_if_needed(
            learner,
            pending_bids_path=pending_path,
            tracked_purchases_path=tmp_path / "missing.json",
        )

        bids = learner.get_pending_bids()
        assert len(bids) == 1
        assert bids[0]["player_id"] == "10771"
        assert bids[0]["our_bid"] == 14_097_338
        # Successful import renames the JSON to .bak so the next boot
        # doesn't re-import (and we keep a recovery copy for one cycle).
        assert not pending_path.exists()
        assert pending_path.with_suffix(".json.bak").exists()

    def test_imports_pending_bid_sell_plans(self, tmp_path, learner):
        pending_path = tmp_path / "pending_bids.json"
        _write_json(
            pending_path,
            [
                {
                    "player_id": "555",
                    "player_name": "Star",
                    "our_bid": 1,
                    "asking_price": 1,
                    "our_overbid_pct": 0.0,
                    "timestamp": 1.0,
                    "sell_plan_player_ids": ["111", "222"],
                }
            ],
        )

        migrate_json_state_if_needed(
            learner,
            pending_bids_path=pending_path,
            tracked_purchases_path=tmp_path / "missing.json",
        )

        bids = learner.get_pending_bids()
        assert sorted(bids[0]["sell_plan_player_ids"]) == ["111", "222"]

    def test_no_pending_bids_file_is_noop(self, tmp_path, learner):
        # Fresh deploys without legacy state must not crash.
        migrate_json_state_if_needed(
            learner,
            pending_bids_path=tmp_path / "missing.json",
            tracked_purchases_path=tmp_path / "missing.json",
        )
        assert learner.get_pending_bids() == []

    def test_migration_is_idempotent_via_bak_rename(self, tmp_path, learner):
        pending_path = tmp_path / "pending_bids.json"
        _write_json(
            pending_path,
            [
                {
                    "player_id": "X",
                    "player_name": "x",
                    "our_bid": 1,
                    "asking_price": 1,
                    "our_overbid_pct": 0.0,
                    "timestamp": 1.0,
                }
            ],
        )

        # First call imports.
        migrate_json_state_if_needed(
            learner,
            pending_bids_path=pending_path,
            tracked_purchases_path=tmp_path / "missing.json",
        )
        # Second call must not double-insert (file is gone, .bak remains).
        migrate_json_state_if_needed(
            learner,
            pending_bids_path=pending_path,
            tracked_purchases_path=tmp_path / "missing.json",
        )

        assert len(learner.get_pending_bids()) == 1


class TestMigrateTrackedPurchases:
    def test_imports_existing_tracked_purchases_json(self, tmp_path, learner):
        purchases_path = tmp_path / "tracked_purchases.json"
        _write_json(
            purchases_path,
            {
                "10115": {
                    "player_name": "Svensson",
                    "buy_price": 25_066_414,
                    "buy_date": 1_776_501_427.5,
                    "source": "detected",
                },
                "2855": {
                    "player_name": "Ragnar Ache",
                    "buy_price": 10_224_738,
                    "buy_date": 1_763_030_992.5,
                    # Missing 'source' — older entries didn't have it; default to None.
                },
            },
        )

        migrate_json_state_if_needed(
            learner,
            pending_bids_path=tmp_path / "missing.json",
            tracked_purchases_path=purchases_path,
        )

        svensson = learner.get_tracked_purchase("10115")
        assert svensson is not None
        assert svensson["buy_price"] == 25_066_414
        assert svensson["source"] == "detected"

        ache = learner.get_tracked_purchase("2855")
        assert ache is not None
        assert ache["source"] is None

        assert not purchases_path.exists()
        assert purchases_path.with_suffix(".json.bak").exists()

    def test_no_tracked_purchases_file_is_noop(self, tmp_path, learner):
        migrate_json_state_if_needed(
            learner,
            pending_bids_path=tmp_path / "missing.json",
            tracked_purchases_path=tmp_path / "missing.json",
        )
        assert learner.get_tracked_purchase("anything") is None


class TestMigrationDoesNotOverwriteExistingDbRows:
    def test_skips_import_when_table_already_populated(self, tmp_path, learner):
        # If the DB already has rows (e.g. Azure restored a backup that
        # included the new tables) we must NOT re-import an old JSON
        # left lying around — that would resurrect stale state.
        learner.add_tracked_purchase(
            player_id="10115",
            player_name="Svensson (DB)",
            buy_price=22_000_000,
            buy_date=999.0,
            source="real",
        )

        purchases_path = tmp_path / "tracked_purchases.json"
        _write_json(
            purchases_path,
            {
                "10115": {
                    "player_name": "Svensson (JSON)",
                    "buy_price": 25_066_414,
                    "buy_date": 1.0,
                    "source": "detected",
                }
            },
        )

        migrate_json_state_if_needed(
            learner,
            pending_bids_path=tmp_path / "missing.json",
            tracked_purchases_path=purchases_path,
        )

        # DB row wins; JSON is still renamed (to prevent re-import next boot).
        p = learner.get_tracked_purchase("10115")
        assert p["player_name"] == "Svensson (DB)"
        assert p["buy_price"] == 22_000_000
        assert not purchases_path.exists()
        assert purchases_path.with_suffix(".json.bak").exists()
