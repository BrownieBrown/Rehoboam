"""The player overlay's three views (migration 013): season aggregates, the
raw match log (played and missed alike), and daily market-value history."""

from __future__ import annotations

import time
from datetime import date, datetime, timezone

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
    # Strict, not just `== 1`/`== 0`: `is_home` is an `integer` column
    # (confirmed live), and `bool` is a subclass of `int` in Python, so the
    # `not isinstance(..., bool)` half is what actually catches a
    # driver/type regression that silently swapped it for a Python bool.
    assert isinstance(day1["is_home"], int) and not isinstance(day1["is_home"], bool)
    assert day1["is_home"] == 1
    # Day 4's opponent team ("55") was never upserted into rehoboam.teams --
    # the left join must give null, not drop the row or raise.
    assert day4["opponent"] is None
    assert isinstance(day4["is_home"], int) and not isinstance(day4["is_home"], bool)
    assert day4["is_home"] == 0


def test_match_at_is_parsed_for_past_and_future_rows_and_null_for_junk_text(store_dsn):
    """Mirrors the live symptom directly: `player_match_history` holds the
    whole fixture list, including matchdays not yet played, as rows with a
    real future date (on the live store, Matanović's 2026/2027 matchdays
    34/33/32 are dated May 2027). One future-dated unplayed row, one
    past-dated played row, and one row whose date text never parses -- all
    three still appear, and only the first two get a real `match_at`."""
    corpus = CorpusStore(dsn=store_dsn)
    corpus.upsert_players(
        [
            {
                "player_id": "p2",
                "first_name": None,
                "last_name": "Player Two",
                "position": "Forward",
                "team_id": "1",
            }
        ]
    )
    corpus.record_match_history(
        "p2",
        "1",
        _perf(
            [
                (
                    "2026/2027",
                    [
                        # Played, well in the past.
                        _m(1, 5, 55, md="2026-08-23T13:30:00Z"),
                        # Not yet played -- a real date far in the future,
                        # same shape as the live Matanović rows.
                        _m(34, 0, 0, minutes="0", md="2027-05-10T13:30:00Z"),
                        # Junk date text: never parses into a timestamp.
                        _m(2, 0, 0, minutes="0", md="unknown"),
                    ],
                )
            ]
        ),
    )
    with corpus.connection() as conn:
        rows = {
            r["day_number"]: r
            for r in conn.execute(
                "select day_number, match_at from rehoboam.web_player_matches "
                "where player_id = 'p2'"
            ).fetchall()
        }
    now = datetime.now(timezone.utc)
    assert rows[1]["match_at"] is not None and rows[1]["match_at"] < now
    assert rows[34]["match_at"] is not None and rows[34]["match_at"] > now
    # Junk text gives a null match_at, but the row is not dropped.
    assert 2 in rows
    assert rows[2]["match_at"] is None


def test_appearances_excludes_a_played_row_with_null_points(store_dsn):
    """`points` is `not null` in the live schema (no played row has ever
    carried a null value there), so this relaxes it on this throwaway test
    database only, to prove the view itself doesn't silently inflate
    `appearances` past what its points-based aggregates (avg/median/max/
    sum(minutes)) cover -- independent of the table constraint that happens
    to make the scenario unreachable in production today."""
    corpus = _seed(store_dsn)
    with corpus.connection() as conn:
        conn.execute("alter table rehoboam.player_match_history alter column points drop not null")
        conn.execute(
            "insert into rehoboam.player_match_history "
            "(player_id, season, day_number, match_date, points, minutes, team_id, "
            "opponent_team_id, is_home, status) "
            "values ('p1', '2026/2027', 5, '2026-09-26T13:30:00Z', null, 90, '1', '2', 1, 5)"
        )
        season_row = conn.execute(
            "select * from rehoboam.web_player_seasons "
            "where player_id = 'p1' and season = '2026/2027'"
        ).fetchone()
        match_row = conn.execute(
            "select points from rehoboam.web_player_matches "
            "where player_id = 'p1' and season = '2026/2027' and day_number = 5"
        ).fetchone()
    # The null-points started row does not count toward appearances (still 2,
    # from _seed's two real played rows) -- count(points), not count(*).
    assert season_row["appearances"] == 2
    # ...but the raw match log still shows it, points and all.
    assert match_row is not None
    assert match_row["points"] is None


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


def test_web_player_mv_uses_the_utc_date_not_the_session_timezone(store_dsn):
    """Every seed above lands at exact UTC midnight, where the session's
    timezone can't move the date -- this host's Postgres session defaults to
    Europe/Berlin (confirmed live), so a snapshot near the UTC day boundary
    is the one case that actually discriminates `at time zone 'UTC'` from
    plain `::date`: without it, 23:30 UTC on the 4th reads as the 5th."""
    corpus = _seed(store_dsn)
    late = datetime(2026, 1, 4, 23, 30, 0, tzinfo=timezone.utc).timestamp()
    corpus.record_mv_series("p1", {"it": [{"dt": late / 86400.0, "mv": 11_000_000}]})
    with corpus.connection() as conn:
        row = conn.execute(
            "select day, market_value from rehoboam.web_player_mv "
            "where player_id = 'p1' and market_value = 11000000"
        ).fetchone()
    assert row["day"] == date(2026, 1, 4)
