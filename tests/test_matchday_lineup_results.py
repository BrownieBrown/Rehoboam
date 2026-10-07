"""Tests for REH-25: matchday_lineup_results persistence.

The bot fetches /users/{uid}/teamcenter?dayNumber=N once per session and
records the actual fielded lineup + total points for the most-recently-
completed matchday. Goal 4 (more matchday points each week) is unmeasurable
without this — matchday_outcomes (REH-20) tracks per-player EP accuracy
but not lineup-level totals.
"""

import json

from rehoboam.store import connect


class TestRecordMatchdayLineupResult:
    def test_round_trip_one_row(self, learner, store_dsn):
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

        with connect(store_dsn) as conn:
            row = conn.execute(
                "SELECT league_id, day_number, matchday_date, total_points, "
                "lineup_player_ids, lineup_count, snapshot_at "
                "FROM rehoboam.matchday_lineup_results"
            ).fetchone()
        assert row["league_id"] == "L1"
        assert row["day_number"] == 32
        assert row["matchday_date"] == "2026-05-02T13:30:00Z"
        assert row["total_points"] == 749
        assert json.loads(row["lineup_player_ids"]) == [
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
        assert row["lineup_count"] == 11
        assert row["snapshot_at"] == 1_700_000_000.0

    def test_default_timestamp_is_now(self, learner, store_dsn):
        learner.record_matchday_lineup_result(
            league_id="L1",
            day_number=1,
            matchday_date="2025-08-23T13:30:00Z",
            total_points=893,
            lineup_player_ids=["a"] * 11,
            lineup_count=11,
        )
        with connect(store_dsn) as conn:
            row = conn.execute(
                "SELECT snapshot_at FROM rehoboam.matchday_lineup_results"
            ).fetchone()
        assert row["snapshot_at"] > 1_767_225_600  # > 2026-01-01 sentinel

    def test_collision_returns_false_and_preserves_first(self, learner, store_dsn):
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

        with connect(store_dsn) as conn:
            row = conn.execute(
                "SELECT total_points, snapshot_at FROM rehoboam.matchday_lineup_results"
            ).fetchone()
        assert (row["total_points"], row["snapshot_at"]) == (749, 100.0)  # first preserved

    def test_lineup_count_below_eleven_is_recorded(self, learner, store_dsn):
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
        with connect(store_dsn) as conn:
            row = conn.execute(
                "SELECT lineup_count, total_points FROM rehoboam.matchday_lineup_results"
            ).fetchone()
        assert (row["lineup_count"], row["total_points"]) == (10, 520)

    def test_player_ids_coerced_to_str(self, learner, store_dsn):
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
        with connect(store_dsn) as conn:
            row = conn.execute(
                "SELECT lineup_player_ids FROM rehoboam.matchday_lineup_results"
            ).fetchone()
        assert json.loads(row["lineup_player_ids"]) == [
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


class TestTheRowIsKeyedBySeason:
    """2026-10-07: nothing was recorded this season. The writer asked "is day 4
    already there?" with no season, and last season's day 4 said yes."""

    def _record(self, learner, season, day=4):
        return learner.record_matchday_lineup_result(
            league_id="L1",
            day_number=day,
            matchday_date=(
                "2025-09-20T13:30:00Z" if season == "2025/2026" else "2026-09-20T13:30:00Z"
            ),
            total_points=500,
            lineup_player_ids=["a"] * 11,
            lineup_count=11,
            season=season,
        )

    def test_last_seasons_row_does_not_block_this_seasons(self, learner):
        assert self._record(learner, "2025/2026") is True
        assert learner.has_matchday_lineup_result("L1", 4, season="2026/2027") is False
        assert self._record(learner, "2026/2027") is True
        assert learner.has_matchday_lineup_result("L1", 4, season="2026/2027") is True

    def test_the_same_season_is_still_recorded_once(self, learner):
        assert self._record(learner, "2026/2027") is True
        assert self._record(learner, "2026/2027") is False

    def test_the_migration_backfills_the_season_from_the_date(self, learner, store_dsn):
        """Rows that predate the column: a date before July belongs to the
        season that started the previous summer."""
        with connect(store_dsn) as conn:
            conn.execute(
                "INSERT INTO rehoboam.matchday_lineup_results "
                "(league_id, day_number, matchday_date, total_points, lineup_player_ids, "
                "lineup_count, snapshot_at, season) VALUES "
                "('L9', 34, '2026-05-16T13:30:00Z', 1041, '[]', 11, 0, "
                "rehoboam.season_of_date('2026-05-16T13:30:00Z'))"
            )
            row = conn.execute(
                "SELECT season FROM rehoboam.matchday_lineup_results WHERE league_id='L9'"
            ).fetchone()
        assert row["season"] == "2025/2026"
