"""The player panel's one-row view (migration 016): season stats from the
newest status row, the market-value move in euros, and his rank."""

from __future__ import annotations

import time
from datetime import date, timedelta

from rehoboam.store.corpus_store import CorpusStore

NOW = time.time()


def _perf(seasons):
    return {"it": [{"ti": title, "ph": matches} for title, matches in seasons]}


def _m(day, st, p, md="2026-08-22T13:30:00Z"):
    return {
        "day": day,
        "md": md,
        "st": st,
        "p": p,
        "mp": "90",
        "t1": "1",
        "t2": "2",
        "pt": "1",
    }


def _universe(store: CorpusStore) -> None:
    store.upsert_players(
        [
            {
                "player_id": "a",
                "first_name": None,
                "last_name": "Alpha",
                "position": "Midfielder",
                "team_id": "7",
                "market_value": 12_000_000,
                "average_points": 50.0,
            },
            {
                "player_id": "b",
                "first_name": None,
                "last_name": "Beta",
                "position": "Midfielder",
                "team_id": "7",
                "market_value": 12_000_000,
                "average_points": 50.0,
            },
            {
                "player_id": "c",
                "first_name": None,
                "last_name": "Gamma",
                "position": "Forward",
                "team_id": "7",
                "market_value": 5_000_000,
                "average_points": 0.0,
            },
        ]
    )


def _played(store: CorpusStore, player_id: str, points: int) -> None:
    store.record_match_history(player_id, "7", _perf([("2026/2027", [_m(1, 5, points)])]))


def _row(store: CorpusStore, player_id: str) -> dict:
    with store.connection() as conn:
        return conn.execute(
            "select * from rehoboam.web_player_profile where player_id = %s", (player_id,)
        ).fetchone()


def test_season_stats_arrive_from_the_newest_status_row(store_dsn):
    store = CorpusStore(dsn=store_dsn)
    _universe(store)
    store.record_status_daily(
        "a",
        date.today() - timedelta(days=1),
        {"mv": 11_000_000, "g": 0, "a": 0, "y": 0, "r": 0, "sec": 1000, "tp": 10, "ap": 10.0},
        NOW - 86400,
    )
    store.record_status_daily(
        "a",
        date.today(),
        {
            "mv": 12_000_000,
            "tfhmvt": 1_000_000,
            "g": 3,
            "a": 4,
            "y": 1,
            "r": 0,
            "sec": 11508,
            "tp": 242,
            "ap": 121.0,
        },
        NOW,
    )
    row = _row(store, "a")
    assert row["goals"] == 3
    assert row["assists"] == 4
    assert row["yellow_cards"] == 1
    assert row["red_cards"] == 0
    assert row["seconds_played"] == 11508
    assert row["season_points"] == 242
    assert float(row["season_average"]) == 121.0


def test_trend_24h_eur_is_the_newest_rows_mv_change(store_dsn):
    store = CorpusStore(dsn=store_dsn)
    _universe(store)
    store.record_status_daily("a", date.today(), {"mv": 12_000_000, "tfhmvt": -500_000}, NOW)
    assert _row(store, "a")["trend_24h_eur"] == -500_000


def test_trend_7d_eur_is_the_difference_against_a_week_old_series_point(store_dsn):
    store = CorpusStore(dsn=store_dsn)
    _universe(store)
    with store.connection() as conn:
        conn.execute(
            "INSERT INTO rehoboam.mv_series (player_id, snapshot_at, market_value) "
            "VALUES ('a', %s, 10_000_000)",
            (NOW - 8 * 86400,),
        )
    row = _row(store, "a")
    assert row["trend_7d_eur"] == 12_000_000 - 10_000_000


def test_trend_7d_eur_is_null_with_no_week_old_series_point(store_dsn):
    store = CorpusStore(dsn=store_dsn)
    _universe(store)
    assert _row(store, "a")["trend_7d_eur"] is None


def test_ranks_put_the_higher_scorer_first_overall_and_within_position(store_dsn):
    store = CorpusStore(dsn=store_dsn)
    _universe(store)
    _played(store, "a", 80)
    _played(store, "b", 40)
    _played(store, "c", 60)
    a, b, c = _row(store, "a"), _row(store, "b"), _row(store, "c")
    assert a["rank_overall"] == 1
    assert c["rank_overall"] == 2
    assert b["rank_overall"] == 3
    # "a" and "b" are both Midfielders; "c" is a Forward alone in his position.
    assert a["rank_position"] == 1
    assert b["rank_position"] == 2
    assert c["rank_position"] == 1


def test_a_player_with_no_points_has_null_ranks(store_dsn):
    store = CorpusStore(dsn=store_dsn)
    _universe(store)
    _played(store, "a", 80)
    # "c" never appears in player_match_history: points is null.
    row = _row(store, "c")
    assert row["rank_overall"] is None
    assert row["rank_position"] is None


def test_two_players_with_equal_points_share_a_rank(store_dsn):
    store = CorpusStore(dsn=store_dsn)
    _universe(store)
    _played(store, "a", 50)
    _played(store, "b", 50)
    a, b = _row(store, "a"), _row(store, "b")
    assert a["rank_overall"] == b["rank_overall"] == 1
    assert a["rank_position"] == b["rank_position"] == 1
