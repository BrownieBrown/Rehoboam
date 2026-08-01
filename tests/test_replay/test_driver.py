import sqlite3

import pytest

from rehoboam.enrichment.corpus import TrainingCorpus
from rehoboam.replay.driver import build_matchdays, load_standings


@pytest.fixture
def corpus(tmp_path):
    c = TrainingCorpus(tmp_path / "c.db")
    with sqlite3.connect(c.db_path) as conn:
        conn.executemany(
            "INSERT INTO player_match_history (player_id, season, day_number, match_date,"
            " points, minutes, team_id, opponent_team_id, is_home, status)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                ("1", "2025/2026", 1, "2025-08-23T13:30:00Z", 80, 90, "40", "7", 1, 5),
                ("2", "2025/2026", 1, "2025-08-23T15:30:00Z", 40, 45, "7", "40", 0, 3),
                ("1", "2025/2026", 2, "2025-08-30T13:30:00Z", 60, 90, "40", "9", 1, 5),
                ("9", "2024/2025", 1, "2024-08-23T13:30:00Z", 10, 90, "40", "7", 1, 5),
            ],
        )
    return c


def test_build_matchdays_returns_one_entry_per_matchday(corpus):
    mds = build_matchdays(corpus, season="2025/2026")
    assert [m.day_number for m in mds] == [1, 2]


def test_build_matchdays_collects_points_per_player(corpus):
    md1 = build_matchdays(corpus, season="2025/2026")[0]
    assert md1.points == {"1": 80.0, "2": 40.0}


def test_build_matchdays_excludes_other_seasons(corpus):
    assert all("9" not in m.points for m in build_matchdays(corpus, season="2025/2026"))


def test_build_matchdays_kickoff_is_the_earliest_match(corpus):
    md1 = build_matchdays(corpus, season="2025/2026")[0]
    from datetime import datetime, timezone

    assert datetime.fromtimestamp(md1.kickoff, timezone.utc) == datetime(
        2025, 8, 23, 13, 30, tzinfo=timezone.utc
    )


def test_load_standings_excludes_our_own_manager(tmp_path):
    db = tmp_path / "learn.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE league_rank_history (snapshot_at REAL, league_id TEXT,"
            " manager_id TEXT, day_number INTEGER, rank_overall INTEGER,"
            " rank_matchday INTEGER, total_points INTEGER, matchday_points INTEGER,"
            " team_value INTEGER, is_self INTEGER)"
        )
        conn.executemany(
            "INSERT INTO league_rank_history VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                (1.0, "L", "a", 34, 1, 1, 37_857, 900, 0, 0),
                (1.0, "L", "us", 34, 10, 10, 26_170, 700, 0, 1),
                (0.5, "L", "a", 33, 1, 1, 36_428, 800, 0, 0),
            ],
        )
    standings = load_standings(db, league_id="L", exclude_manager_id="us")
    assert [(s.manager_id, s.total_points) for s in standings] == [("a", 37_857)]
