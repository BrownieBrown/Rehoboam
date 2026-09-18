"""The player overlay's three views (migration 013): season aggregates, the
raw match log (played and missed alike), and daily market-value history."""

from __future__ import annotations

import time
from datetime import date

from rehoboam.store.corpus_store import CorpusStore
from rehoboam.store.league_store import LeagueStore

NOW = time.time()

EPOCH = date(1970, 1, 1)


def _days_since_epoch(d: date) -> int:
    return (d - EPOCH).days


def _perf(seasons):
    return {"it": [{"ti": title, "ph": matches} for title, matches in seasons]}


def _m(day, st, p, minutes="90", md="2026-08-22T13:30:00Z", t1="1", t2="2", pt="1"):
    return {
        "day": day,
        "md": md,
        "st": st,
        "p": p,
        "mp": minutes,
        "t1": t1,
        "t2": t2,
        "pt": pt,
    }


def _seed(dsn):
    corpus = CorpusStore(dsn=dsn)
    corpus.upsert_players(
        [
            {
                "player_id": "p1",
                "first_name": None,
                "last_name": "Player One",
                "position": "Midfielder",
                "team_id": "1",
                "market_value": 10_000_000,
                "average_points": 40.0,
            }
        ]
    )
    corpus.record_match_history(
        "p1",
        "1",
        _perf(
            [
                (
                    "2026/2027",
                    [
                        # Started: counts.
                        _m(1, 5, 60, minutes="90"),
                        # Came on: counts.
                        _m(2, 3, 20, minutes="30"),
                        # Not in squad: does not count.
                        _m(3, 1, 0, minutes="0"),
                        # Not played (future fixture, unknown opponent):
                        # does not count, and must still show up in the log.
                        _m(4, 0, 0, minutes="0", t1="55", t2="1", pt="1"),
                    ],
                ),
                (
                    "2025/2026",
                    [_m(1, 5, 50, minutes="90")],
                ),
            ]
        ),
    )
    league = LeagueStore(dsn=dsn)
    league.upsert_teams(
        [
            {"team_id": "1", "name": "Club One", "short_name": "ONE", "updated_at": NOW},
            {"team_id": "2", "name": "Club Two", "short_name": "TWO", "updated_at": NOW},
            # "55" deliberately left unseeded: an unknown opponent.
        ]
    )
    return corpus


def test_a_season_row_aggregates_only_played_matches(store_dsn):
    corpus = _seed(store_dsn)
    with corpus.connection() as conn:
        row = conn.execute(
            "select * from rehoboam.web_player_seasons "
            "where player_id = 'p1' and season = '2026/2027'"
        ).fetchone()
    assert row is not None
    # Only statuses 5 (started) and 3 (came on) count; 1 (not in squad) and
    # 0 (not played) are excluded from every figure.
    assert row["appearances"] == 2
    assert row["starts"] == 1
    assert row["points"] == 80
    assert float(row["avg_points"]) == 40.0
    assert float(row["median_points"]) == 40.0
    assert row["best_points"] == 60
    assert row["minutes"] == 120


def test_two_seasons_for_one_player_come_back_as_two_rows(store_dsn):
    corpus = _seed(store_dsn)
    with corpus.connection() as conn:
        rows = conn.execute(
            "select season from rehoboam.web_player_seasons where player_id = 'p1'"
        ).fetchall()
    assert {r["season"] for r in rows} == {"2026/2027", "2025/2026"}
    assert len(rows) == 2


def test_web_player_matches_returns_unplayed_rows_with_opponent_names(store_dsn):
    corpus = _seed(store_dsn)
    with corpus.connection() as conn:
        rows = conn.execute(
            "select day_number, status, opponent, is_home from rehoboam.web_player_matches "
            "where player_id = 'p1' and season = '2026/2027' order by day_number"
        ).fetchall()
    # All four stored matches are present, including the two that were never
    # played (status 1 and status 0) -- a missed matchday must stay visible.
    assert [r["day_number"] for r in rows] == [1, 2, 3, 4]
    assert [r["status"] for r in rows] == [5, 3, 1, 0]
    day1, day4 = rows[0], rows[3]
    assert day1["opponent"] == "Club Two"
    assert day1["is_home"] == 1
    # Day 4's opponent team ("55") was never upserted into rehoboam.teams --
    # the left join must give null, not drop the row or raise.
    assert day4["opponent"] is None
    assert day4["is_home"] == 0


def test_web_player_mv_returns_one_row_per_day_ascending(store_dsn):
    corpus = _seed(store_dsn)
    corpus.record_mv_series(
        "p1",
        {
            "it": [
                {"dt": _days_since_epoch(date(2026, 1, 3)), "mv": 10_000_000},
                {"dt": _days_since_epoch(date(2026, 1, 1)), "mv": 8_000_000},
                {"dt": _days_since_epoch(date(2026, 1, 2)), "mv": 9_000_000},
            ]
        },
    )
    with corpus.connection() as conn:
        rows = conn.execute(
            "select day, market_value from rehoboam.web_player_mv "
            "where player_id = 'p1' order by day asc"
        ).fetchall()
    assert [r["day"] for r in rows] == [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)]
    assert [r["market_value"] for r in rows] == [8_000_000, 9_000_000, 10_000_000]
