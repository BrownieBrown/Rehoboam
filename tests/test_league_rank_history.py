"""Tests for REH-24: league_rank_history persistence.

The bot calls /ranking every session for competitor_player_ids but
historically discarded the rest of the response. record_league_rank_snapshot()
persists rank, points, and team_value per manager so goals 3 (team value
growth), 4 (matchday points), and 5 (rank trajectory) become measurable.
"""

import sqlite3

import pytest

from rehoboam.bid_learner import BidLearner


@pytest.fixture
def learner(tmp_path):
    return BidLearner(db_path=tmp_path / "bid_learning.db")


def _row(
    manager_id: str,
    *,
    snapshot_at: float = 1_000.0,
    league_id: str = "L1",
    day_number: int = 32,
    rank_overall: int | None = 5,
    rank_matchday: int | None = 7,
    total_points: int | None = 23990,
    matchday_points: int | None = 749,
    team_value: int | None = 135_000_000,
    is_self: bool = False,
) -> dict:
    return {
        "snapshot_at": snapshot_at,
        "league_id": league_id,
        "manager_id": manager_id,
        "day_number": day_number,
        "rank_overall": rank_overall,
        "rank_matchday": rank_matchday,
        "total_points": total_points,
        "matchday_points": matchday_points,
        "team_value": team_value,
        "is_self": is_self,
    }


class TestLeagueRankSnapshot:
    def test_round_trip_one_row(self, learner):
        n = learner.record_league_rank_snapshot([_row("m1", is_self=True)])
        assert n == 1

        with sqlite3.connect(learner.db_path) as conn:
            row = conn.execute(
                "SELECT manager_id, day_number, rank_overall, rank_matchday, "
                "total_points, matchday_points, team_value, is_self "
                "FROM league_rank_history"
            ).fetchone()
        assert row == ("m1", 32, 5, 7, 23990, 749, 135_000_000, 1)

    def test_bulk_insert_multiple_managers(self, learner):
        rows = [
            _row("m1", rank_overall=1, is_self=True),
            _row("m2", rank_overall=2),
            _row("m3", rank_overall=3),
        ]
        learner.record_league_rank_snapshot(rows)

        with sqlite3.connect(learner.db_path) as conn:
            ranks = conn.execute(
                "SELECT manager_id, rank_overall FROM league_rank_history " "ORDER BY rank_overall"
            ).fetchall()
        assert ranks == [("m1", 1), ("m2", 2), ("m3", 3)]

    def test_empty_input_is_noop(self, learner):
        assert learner.record_league_rank_snapshot([]) == 0

    def test_collision_at_same_pk_is_silently_dropped(self, learner):
        # Composite PK = (snapshot_at, manager_id). Same-second retry on the
        # same manager → second write is dropped, first preserved.
        learner.record_league_rank_snapshot([_row("m1", snapshot_at=42.0, rank_overall=5)])
        learner.record_league_rank_snapshot(
            [_row("m1", snapshot_at=42.0, rank_overall=999)],
        )

        with sqlite3.connect(learner.db_path) as conn:
            rows = conn.execute(
                "SELECT manager_id, rank_overall FROM league_rank_history"
            ).fetchall()
        assert rows == [("m1", 5)]

    def test_optional_numeric_fields_accept_none(self, learner):
        # Mid-season the ranking response may legitimately omit a manager's
        # matchday placement (e.g., they didn't field a lineup that week).
        # The schema and writer must tolerate None on those columns.
        learner.record_league_rank_snapshot(
            [
                _row(
                    "m1",
                    rank_matchday=None,
                    matchday_points=None,
                )
            ]
        )
        with sqlite3.connect(learner.db_path) as conn:
            row = conn.execute(
                "SELECT rank_matchday, matchday_points FROM league_rank_history"
            ).fetchone()
        assert row == (None, None)

    def test_is_self_flag_round_trips_as_int(self, learner):
        learner.record_league_rank_snapshot(
            [
                _row("m1", is_self=True),
                _row("m2", is_self=False),
            ]
        )
        with sqlite3.connect(learner.db_path) as conn:
            self_row = conn.execute(
                "SELECT manager_id FROM league_rank_history WHERE is_self=1"
            ).fetchone()
        assert self_row == ("m1",)

    def test_float_team_value_coerced_to_int(self, learner):
        # /ranking returned tv=135691003.0 in probe — schema is INTEGER, so
        # coerce to keep round-trip equality predictable.
        learner.record_league_rank_snapshot(
            [
                _row(
                    "m1",
                    team_value=135_691_003.5,  # type: ignore[arg-type]
                )
            ]
        )
        with sqlite3.connect(learner.db_path) as conn:
            row = conn.execute("SELECT team_value FROM league_rank_history").fetchone()
        assert row == (135_691_003,)
