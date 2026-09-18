"""His next matches: who he plays, home or away, when, and how good the
opponent is (migration 020) -- only fixtures that have not kicked off yet,
carrying the opponent's newest league place the same way
`web_player_profile` (019) resolves his own club's."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from rehoboam.store import connect
from rehoboam.store.corpus_store import CorpusStore
from rehoboam.store.league_store import LeagueStore

NOW = datetime.now(timezone.utc).timestamp()


def _fx(conn, match_id, day_number, home, away, kickoff):
    conn.execute(
        "insert into rehoboam.fixtures (match_id, season, day_number, kickoff,"
        " home_team_id, away_team_id, status, updated_at)"
        " values (%s, '2026/2027', %s, %s, %s, %s, 0, 0)",
        (match_id, day_number, kickoff.timestamp(), home, away),
    )


def test_upcoming_fixtures_carry_the_opponent_and_his_place(store_dsn):
    soon = datetime.now(timezone.utc) + timedelta(days=2)
    later = datetime.now(timezone.utc) + timedelta(days=9)
    past = datetime.now(timezone.utc) - timedelta(days=2)
    LeagueStore(dsn=store_dsn).upsert_teams(
        [
            {"team_id": "t-me", "name": "Freiburg", "short_name": None, "updated_at": NOW},
            {"team_id": "t-op", "name": "Frankfurt", "short_name": None, "updated_at": NOW},
        ]
    )
    CorpusStore(dsn=store_dsn).upsert_players(
        [
            {
                "player_id": "p-fx",
                "last_name": "Fix",
                "position": "Forward",
                "team_id": "t-me",
            }
        ]
    )
    with connect(store_dsn) as conn:
        conn.execute(
            "insert into rehoboam.league_table (season, day_number, team_id, place,"
            " points, played, goal_difference, updated_at)"
            " values ('2026/2027', 3, 't-op', 9, 4, 3, 0, 0)"
        )
        _fx(conn, "m-past", 3, "t-me", "t-op", past)
        _fx(conn, "m-1", 4, "t-op", "t-me", soon)
        _fx(conn, "m-2", 5, "t-me", "t-op", later)
        rows = conn.execute(
            "select * from rehoboam.web_player_fixtures where player_id = 'p-fx'"
            " order by kickoff"
        ).fetchall()
    assert [r["day_number"] for r in rows] == [4, 5]  # the past match is gone
    assert rows[0]["is_home"] is False and rows[0]["opponent"] == "Frankfurt"
    assert rows[0]["opponent_place"] == 9
    assert rows[1]["is_home"] is True


def test_a_player_with_no_club_has_no_fixtures(store_dsn):
    CorpusStore(dsn=store_dsn).upsert_players(
        [{"player_id": "p-none", "last_name": "None", "position": "Forward"}]
    )
    with connect(store_dsn) as conn:
        rows = conn.execute(
            "select * from rehoboam.web_player_fixtures where player_id = 'p-none'"
        ).fetchall()
    assert rows == []


def test_opponent_place_ignores_a_higher_day_number_from_an_older_season(store_dsn):
    """`league_table` is append-only across seasons and never purged, so a
    previous season's final day_number can be higher than the current
    season's newest one. The view must resolve the newest *season* first --
    not just the highest day_number anywhere in the table -- or the
    opponent's place would come from a season that already finished."""
    soon = datetime.now(timezone.utc) + timedelta(days=2)
    LeagueStore(dsn=store_dsn).upsert_teams(
        [
            {"team_id": "t-me2", "name": "Freiburg", "short_name": None, "updated_at": NOW},
            {"team_id": "t-op2", "name": "Union", "short_name": None, "updated_at": NOW},
        ]
    )
    CorpusStore(dsn=store_dsn).upsert_players(
        [
            {
                "player_id": "p-season",
                "last_name": "Season",
                "position": "Forward",
                "team_id": "t-me2",
            }
        ]
    )
    with connect(store_dsn) as conn:
        conn.execute(
            "insert into rehoboam.league_table"
            " (season, day_number, team_id, place, points, played, goal_difference, updated_at)"
            " values ('2025/2026', 34, 't-op2', 7, 40, 34, -10, 0),"
            "        ('2026/2027', 2, 't-op2', 2, 6, 2, 3, 0)"
        )
        _fx(conn, "m-season", 3, "t-me2", "t-op2", soon)
        rows = conn.execute(
            "select * from rehoboam.web_player_fixtures where player_id = 'p-season'"
        ).fetchall()
    assert len(rows) == 1
    # Current season's place (2), not the older season's higher-day_number row (7).
    assert rows[0]["opponent_place"] == 2
