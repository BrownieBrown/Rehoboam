"""Tests for REH-23: team_value_history persistence.

The bot fetches budget + team_value every session via get_team_info() but
historically discarded the result. record_team_value_snapshot() persists
one row per session into team_value_history, giving us a longitudinal
series for goal 3 (team value growth) and feeding REH-37 (rank-trajectory
regression).
"""

import sqlite3

import pytest

from rehoboam.bid_learner import BidLearner


@pytest.fixture
def learner(tmp_path):
    return BidLearner(db_path=tmp_path / "bid_learning.db")


class TestTeamValueSnapshot:
    def test_round_trip_single_row(self, learner):
        inserted = learner.record_team_value_snapshot(
            league_id="L1",
            team_value=85_000_000,
            budget=12_500_000,
            squad_size=15,
            snapshot_at=1_700_000_000.0,
        )
        assert inserted is True

        with sqlite3.connect(learner.db_path) as conn:
            row = conn.execute(
                "SELECT snapshot_at, league_id, team_value, budget, squad_size "
                "FROM team_value_history"
            ).fetchone()
        assert row == (1_700_000_000.0, "L1", 85_000_000, 12_500_000, 15)

    def test_default_timestamp_is_now(self, learner):
        # When snapshot_at is omitted, should default to roughly current time.
        # Don't assert exact; assert > a fixed past sentinel.
        learner.record_team_value_snapshot(
            league_id="L1",
            team_value=50_000_000,
            budget=5_000_000,
            squad_size=11,
        )
        with sqlite3.connect(learner.db_path) as conn:
            ts = conn.execute("SELECT snapshot_at FROM team_value_history").fetchone()[0]
        # 2026-01-01 = 1767225600. Anything written today is well past that.
        assert ts > 1_767_225_600

    def test_multiple_snapshots_ordered_monotonically(self, learner):
        for i, ts in enumerate([100.0, 200.0, 300.0]):
            learner.record_team_value_snapshot(
                league_id="L1",
                team_value=50_000_000 + i * 1_000_000,
                budget=5_000_000,
                squad_size=15,
                snapshot_at=ts,
            )
        with sqlite3.connect(learner.db_path) as conn:
            rows = conn.execute(
                "SELECT snapshot_at, team_value FROM team_value_history " "ORDER BY snapshot_at"
            ).fetchall()
        assert rows == [
            (100.0, 50_000_000),
            (200.0, 51_000_000),
            (300.0, 52_000_000),
        ]

    def test_collision_at_same_timestamp_silently_dropped(self, learner):
        # PK is snapshot_at. Two writes at the same timestamp (e.g. Azure
        # cold-start retry) should not raise; second insert returns False.
        first = learner.record_team_value_snapshot(
            league_id="L1",
            team_value=50_000_000,
            budget=5_000_000,
            squad_size=15,
            snapshot_at=42.0,
        )
        second = learner.record_team_value_snapshot(
            league_id="L1",
            team_value=99_999_999,  # different values — should NOT overwrite
            budget=99_999_999,
            squad_size=99,
            snapshot_at=42.0,
        )
        assert first is True
        assert second is False

        # Verify the first row was preserved (INSERT OR IGNORE — not REPLACE).
        with sqlite3.connect(learner.db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM team_value_history").fetchone()[0]
            kept = conn.execute(
                "SELECT team_value FROM team_value_history WHERE snapshot_at = 42.0"
            ).fetchone()[0]
        assert count == 1
        assert kept == 50_000_000

    def test_int_coercion_for_team_value_and_budget(self, learner):
        # Caller might pass float (Kickbase API sometimes returns floats).
        # Schema is INTEGER — record method should coerce.
        learner.record_team_value_snapshot(
            league_id="L1",
            team_value=85_500_000.5,  # type: ignore[arg-type]
            budget=12_500_000.7,  # type: ignore[arg-type]
            squad_size=15,
            snapshot_at=999.0,
        )
        with sqlite3.connect(learner.db_path) as conn:
            row = conn.execute("SELECT team_value, budget FROM team_value_history").fetchone()
        assert row == (85_500_000, 12_500_000)
