"""Tests for rehoboam.mv_backfill — REH-40 player_mv_history backfill."""

from __future__ import annotations

import sqlite3
from typing import Any
from unittest.mock import MagicMock

from rehoboam.bid_learner import BidLearner, FlipOutcome
from rehoboam.mv_backfill import (
    MvBackfillStats,
    _history_to_rows,
    run_mv_backfill,
)


def _learner_with_flips(tmp_path, player_ids: list[str]) -> BidLearner:
    """BidLearner pre-seeded with one minimal flip per player_id so the
    backfill has a non-empty distinct-id list to walk."""
    learner = BidLearner(db_path=tmp_path / "bid_learning.db")
    for i, pid in enumerate(player_ids):
        # buy_date varies so the UNIQUE(player_id, buy_date) constraint
        # never collides on rerun.
        learner.record_flip(
            FlipOutcome(
                player_id=pid,
                player_name=f"Player{pid}",
                buy_price=1_000_000,
                sell_price=1_100_000,
                profit=100_000,
                profit_pct=10.0,
                hold_days=5,
                buy_date=1_700_000_000.0 + i,
                sell_date=1_700_500_000.0 + i,
            )
        )
    return learner


def _mv_history(points: list[tuple[int, int]]) -> dict[str, Any]:
    """Build a fake v2 MV history response. Each tuple is (days_since_epoch, mv)."""
    return {"it": [{"dt": dt, "mv": mv} for dt, mv in points]}


def _client_with_history(per_player: dict[str, dict[str, Any]]) -> MagicMock:
    c = MagicMock()

    def get_history(player_id: str, timeframe: int = 365):
        if isinstance(per_player.get(player_id), Exception):
            raise per_player[player_id]
        return per_player.get(player_id, {"it": []})

    c.get_player_market_value_history_v2.side_effect = get_history
    return c


# --- _history_to_rows -----------------------------------------------------


def test_history_to_rows_converts_dt_to_unix_epoch():
    rows = _history_to_rows("p1", _mv_history([(20000, 5_000_000)]))
    assert len(rows) == 1
    assert rows[0]["snapshot_at"] == 20000 * 86400.0
    assert rows[0]["market_value"] == 5_000_000
    assert rows[0]["player_id"] == "p1"
    assert rows[0]["peak_mv_30d"] is None
    assert rows[0]["trough_mv_30d"] is None


def test_history_to_rows_filters_zero_and_missing_mv():
    rows = _history_to_rows(
        "p1",
        {
            "it": [
                {"dt": 20000, "mv": 5_000_000},
                {"dt": 20001, "mv": 0},
                {"dt": 20002, "mv": None},
                {"dt": 20003, "mv": 4_900_000},
            ]
        },
    )
    assert len(rows) == 2
    assert {r["snapshot_at"] for r in rows} == {20000 * 86400.0, 20003 * 86400.0}


def test_history_to_rows_handles_empty_response():
    assert _history_to_rows("p1", {}) == []
    assert _history_to_rows("p1", {"it": []}) == []


# --- run_mv_backfill ------------------------------------------------------


def test_backfill_writes_one_row_per_dt(tmp_path):
    learner = _learner_with_flips(tmp_path, ["p1", "p2", "p3"])
    client = _client_with_history(
        {
            "p1": _mv_history([(20000, 1_000_000), (20001, 1_100_000)]),
            "p2": _mv_history([(20000, 2_000_000)]),
            "p3": _mv_history([(20000, 3_000_000), (20001, 3_100_000), (20002, 3_200_000)]),
        }
    )

    stats = run_mv_backfill(client, learner, dry_run=False)

    assert stats.players_processed == 3
    assert stats.rows_attempted == 6  # 2 + 1 + 3
    assert stats.players_failed == 0

    with sqlite3.connect(learner.db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM player_mv_history").fetchone()[0]
    assert count == 6


def test_backfill_is_idempotent(tmp_path):
    learner = _learner_with_flips(tmp_path, ["p1"])
    client = _client_with_history({"p1": _mv_history([(20000, 1_000_000), (20001, 1_100_000)])})

    run_mv_backfill(client, learner, dry_run=False)
    run_mv_backfill(client, learner, dry_run=False)

    with sqlite3.connect(learner.db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM player_mv_history").fetchone()[0]
    # Two runs against the same data still yield 2 rows (UNIQUE on (pid, snapshot_at))
    assert count == 2


def test_backfill_dry_run_writes_no_rows(tmp_path):
    learner = _learner_with_flips(tmp_path, ["p1"])
    client = _client_with_history({"p1": _mv_history([(20000, 1_000_000), (20001, 1_100_000)])})

    stats = run_mv_backfill(client, learner, dry_run=True)

    assert stats.rows_attempted == 2
    with sqlite3.connect(learner.db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM player_mv_history").fetchone()[0]
    assert count == 0


def test_backfill_isolates_per_player_failures(tmp_path):
    learner = _learner_with_flips(tmp_path, ["p1", "pfail", "p3"])
    client = _client_with_history(
        {
            "p1": _mv_history([(20000, 1_000_000)]),
            "pfail": RuntimeError("API timeout"),
            "p3": _mv_history([(20000, 3_000_000)]),
        }
    )

    stats = run_mv_backfill(client, learner, dry_run=False)

    assert stats.players_failed == 1
    assert stats.players_processed == 2
    with sqlite3.connect(learner.db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM player_mv_history").fetchone()[0]
    assert count == 2


def test_backfill_counts_empty_mv_data_separately(tmp_path):
    """Newly-listed players may legitimately return no history. Don't
    confuse them with HTTP failures."""
    learner = _learner_with_flips(tmp_path, ["p1", "pempty"])
    client = _client_with_history(
        {
            "p1": _mv_history([(20000, 1_000_000)]),
            "pempty": {"it": []},
        }
    )

    stats = run_mv_backfill(client, learner, dry_run=False)

    assert stats.players_processed == 1
    assert stats.players_skipped_no_data == 1
    assert stats.players_failed == 0


def test_backfill_no_flips_is_noop(tmp_path):
    learner = BidLearner(db_path=tmp_path / "bid_learning.db")
    client = MagicMock()

    stats = run_mv_backfill(client, learner)

    assert stats == MvBackfillStats()
    client.get_player_market_value_history_v2.assert_not_called()
