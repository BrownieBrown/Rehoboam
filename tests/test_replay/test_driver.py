import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from rehoboam.enrichment.corpus import TrainingCorpus
from rehoboam.replay.driver import (
    LEAGUE_ID,
    build_matchdays,
    day_for_kickoff,
    load_calendar,
    load_standings,
)

REAL_LEARNING_DB = Path("logs") / "bid_learning.db"


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


LEARNING_SCHEMA = (
    "CREATE TABLE matchday_lineup_results (league_id TEXT, day_number INTEGER,"
    " matchday_date TEXT, total_points INTEGER, lineup_player_ids TEXT,"
    " lineup_count INTEGER, snapshot_at REAL)"
)


def _calendar_db(path, rows, league_id="1933872"):
    with sqlite3.connect(path) as conn:
        conn.execute(LEARNING_SCHEMA)
        conn.executemany(
            "INSERT INTO matchday_lineup_results VALUES (?,?,?,?,?,?,?)",
            [(lid, d, dt, 0, "[]", 11, 0.0) for lid, d, dt in rows],
        )
    return path


def test_load_calendar_returns_one_kickoff_per_matchday(tmp_path):
    db = _calendar_db(
        tmp_path / "learn.db",
        [("1933872", 1, "2025-08-23T13:30:00Z"), ("1933872", 2, "2025-08-31T13:30:00Z")],
    )
    cal = load_calendar(db, league_id="1933872")
    assert set(cal) == {1, 2}
    assert datetime.fromtimestamp(cal[1], timezone.utc) == datetime(
        2025, 8, 23, 13, 30, tzinfo=timezone.utc
    )


def test_load_calendar_ignores_other_leagues(tmp_path):
    db = _calendar_db(
        tmp_path / "learn.db",
        [("1933872", 1, "2025-08-23T13:30:00Z"), ("other", 2, "2025-08-31T13:30:00Z")],
    )
    assert set(load_calendar(db, league_id="1933872")) == {1}


def test_build_matchdays_prefers_the_supplied_calendar(corpus):
    """Points still come from the corpus; only the dates are overridden.

    ``player_match_history`` shares one ``day_number`` space across two
    competitions with different calendars, so its earliest match_date is not
    our league's kickoff. The per-player points keyed by day_number are fine.
    """
    mds = build_matchdays(corpus, season="2025/2026", kickoffs={1: 999.0, 2: 1999.0})
    assert [m.kickoff for m in mds] == [999.0, 1999.0]
    assert mds[0].points == {"1": 80.0, "2": 40.0}


def test_day_for_kickoff_is_the_next_matchday_to_kick_off():
    cal = {1: 100.0, 2: 200.0, 3: 300.0}
    assert day_for_kickoff(cal, 50.0) == 1
    assert day_for_kickoff(cal, 150.0) == 2
    assert day_for_kickoff(cal, 250.0) == 3


def test_day_for_kickoff_past_the_final_matchday_makes_all_history_visible():
    assert day_for_kickoff({1: 100.0, 2: 200.0}, 500.0) == 3


@pytest.mark.integration
@pytest.mark.skipif(
    not REAL_LEARNING_DB.exists(), reason="production learning DB not present locally"
)
def test_real_calendar_has_34_matchdays_starting_on_the_official_day_one():
    cal = load_calendar(REAL_LEARNING_DB, league_id=LEAGUE_ID)
    assert sorted(cal) == list(range(1, 35))
    assert datetime.fromtimestamp(cal[1], timezone.utc) == datetime(
        2025, 8, 23, 13, 30, tzinfo=timezone.utc
    )
