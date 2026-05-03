"""Tests for REH-25: matchday_lineup_results persistence.

The bot fetches /users/{uid}/teamcenter?dayNumber=N once per session and
records the actual fielded lineup + total points for the most-recently-
completed matchday. Goal 4 (more matchday points each week) is unmeasurable
without this — matchday_outcomes (REH-20) tracks per-player EP accuracy
but not lineup-level totals.
"""

import json
import sqlite3

import pytest

from rehoboam.bid_learner import BidLearner


@pytest.fixture
def learner(tmp_path):
    return BidLearner(db_path=tmp_path / "bid_learning.db")


class TestRecordMatchdayLineupResult:
    def test_round_trip_one_row(self, learner):
        ok = learner.record_matchday_lineup_result(
            league_id="L1",
            day_number=32,
            matchday_date="2026-05-02T13:30:00Z",
            total_points=749,
            lineup_player_ids=["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11"],
            lineup_count=11,
            snapshot_at=1_700_000_000.0,
        )
        assert ok is True

        with sqlite3.connect(learner.db_path) as conn:
            row = conn.execute(
                "SELECT league_id, day_number, matchday_date, total_points, "
                "lineup_player_ids, lineup_count, snapshot_at "
                "FROM matchday_lineup_results"
            ).fetchone()
        assert row[0] == "L1"
        assert row[1] == 32
        assert row[2] == "2026-05-02T13:30:00Z"
        assert row[3] == 749
        assert json.loads(row[4]) == [
            "1",
            "2",
            "3",
            "4",
            "5",
            "6",
            "7",
            "8",
            "9",
            "10",
            "11",
        ]
        assert row[5] == 11
        assert row[6] == 1_700_000_000.0

    def test_default_timestamp_is_now(self, learner):
        learner.record_matchday_lineup_result(
            league_id="L1",
            day_number=1,
            matchday_date="2025-08-23T13:30:00Z",
            total_points=893,
            lineup_player_ids=["a"] * 11,
            lineup_count=11,
        )
        with sqlite3.connect(learner.db_path) as conn:
            ts = conn.execute("SELECT snapshot_at FROM matchday_lineup_results").fetchone()[0]
        assert ts > 1_767_225_600  # > 2026-01-01 sentinel

    def test_collision_returns_false_and_preserves_first(self, learner):
        # PK is (league_id, day_number) — second call with same key is a no-op.
        first = learner.record_matchday_lineup_result(
            league_id="L1",
            day_number=32,
            matchday_date="2026-05-02T13:30:00Z",
            total_points=749,
            lineup_player_ids=["a"] * 11,
            lineup_count=11,
            snapshot_at=100.0,
        )
        second = learner.record_matchday_lineup_result(
            league_id="L1",
            day_number=32,
            matchday_date="2026-05-02T13:30:00Z",
            total_points=999,  # different value
            lineup_player_ids=["b"] * 11,
            lineup_count=11,
            snapshot_at=200.0,
        )
        assert first is True
        assert second is False

        with sqlite3.connect(learner.db_path) as conn:
            row = conn.execute(
                "SELECT total_points, snapshot_at FROM matchday_lineup_results"
            ).fetchone()
        assert row == (749, 100.0)  # first preserved

    def test_lineup_count_below_eleven_is_recorded(self, learner):
        # If the bot took a -100 penalty (empty lineup slot) the row still
        # records, with lineup_count < 11 as the canonical signal.
        ok = learner.record_matchday_lineup_result(
            league_id="L1",
            day_number=5,
            matchday_date="2025-09-15T13:30:00Z",
            total_points=520,  # short on points due to empty slot
            lineup_player_ids=["a"] * 10,  # 10 not 11
            lineup_count=10,
        )
        assert ok is True
        with sqlite3.connect(learner.db_path) as conn:
            row = conn.execute(
                "SELECT lineup_count, total_points FROM matchday_lineup_results"
            ).fetchone()
        assert row == (10, 520)

    def test_player_ids_coerced_to_str(self, learner):
        # Trader builds the list with int IDs sometimes; the writer normalizes
        # so JSON round-trips as a list of strings.
        learner.record_matchday_lineup_result(
            league_id="L1",
            day_number=1,
            matchday_date="2025-08-23T13:30:00Z",
            total_points=100,
            lineup_player_ids=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11],  # type: ignore[list-item]
            lineup_count=11,
        )
        with sqlite3.connect(learner.db_path) as conn:
            ids_json = conn.execute(
                "SELECT lineup_player_ids FROM matchday_lineup_results"
            ).fetchone()[0]
        assert json.loads(ids_json) == [
            "1",
            "2",
            "3",
            "4",
            "5",
            "6",
            "7",
            "8",
            "9",
            "10",
            "11",
        ]


class TestHasMatchdayLineupResult:
    def test_returns_false_when_empty(self, learner):
        assert learner.has_matchday_lineup_result("L1", 32) is False

    def test_returns_true_after_record(self, learner):
        learner.record_matchday_lineup_result(
            league_id="L1",
            day_number=32,
            matchday_date="2026-05-02T13:30:00Z",
            total_points=749,
            lineup_player_ids=["a"] * 11,
            lineup_count=11,
        )
        assert learner.has_matchday_lineup_result("L1", 32) is True
        # Different day → still false (independent matchdays).
        assert learner.has_matchday_lineup_result("L1", 33) is False
        # Different league → still false (multi-league guard).
        assert learner.has_matchday_lineup_result("L2", 32) is False
