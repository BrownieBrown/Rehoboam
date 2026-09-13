"""corpus-pull round-trips the corpus into a SQLite file the replay can read."""

from __future__ import annotations

import sqlite3

from rehoboam.store import SCHEMA, connect
from rehoboam.store.corpus_pull import pull_corpus
from rehoboam.store.migrate import migrate


def test_pull_writes_every_corpus_table_and_rewrites_on_repull(store_dsn, tmp_path):
    with connect(store_dsn) as conn:
        migrate(conn)
        conn.execute(
            f"insert into {SCHEMA}.player_universe (player_id, first_name, last_name, position, "
            "team_id, market_value, average_points) values ('p1', 'O', 'One', "
            "'Defender', 't', 5, 1.5)"
        )
        conn.execute(
            f"insert into {SCHEMA}.player_match_history (player_id, season, "
            "day_number, match_date, "
            "points, minutes, team_id, opponent_team_id, is_home, status) "
            "values ('p1', '2025/2026', 1, '2025-08-23T13:30:00Z', 88, 90, 't', 'u', 1, 5)"
        )
        conn.execute(
            f"insert into {SCHEMA}.mv_series (player_id, snapshot_at, market_value) "
            "values ('p1', 1.0, 5)"
        )
        out = tmp_path / "training_corpus.db"
        written = pull_corpus(conn, out)
        assert written["player_universe"] == 1
        assert written["player_match_history"] == 1
        assert written["mv_series"] == 1
        # A finished match rewrites its row in the store: the placeholder's
        # 0 points become the real result. The local file must follow.
        conn.execute(
            f"update {SCHEMA}.player_match_history set points = 99, minutes = 90 "
            "where player_id = 'p1'"
        )
        again = pull_corpus(conn, out)
    assert again["player_match_history"] == 1
    with sqlite3.connect(out) as db:
        row = db.execute(
            "select points, minutes, status from player_match_history where player_id = 'p1'"
        ).fetchone()
        universe = db.execute("select count(*) from player_universe").fetchone()[0]
    assert row == (99, 90, 5)
    assert universe == 1


def test_replay_tables_are_pulled_into_a_local_learning_file(store_dsn, tmp_path):
    from rehoboam.store import connect
    from rehoboam.store.corpus_pull import REPLAY_TABLES, pull_replay_tables

    with connect(store_dsn) as conn:
        conn.execute(
            "insert into rehoboam.matchday_lineup_results (league_id, day_number, matchday_date, "
            "total_points, lineup_player_ids, lineup_count, snapshot_at) "
            "values ('L', 1, '2026-08-22T13:30:00Z', 600, '[\"p1\"]', 11, 1.0)"
        )
        conn.execute(
            "insert into rehoboam.flip_outcomes (player_id, player_name, buy_price, sell_price, "
            "profit, profit_pct, hold_days, buy_date, sell_date) "
            "values ('p1', 'One', 10, 12, 2, 20.0, 3, 100.0, 400.0)"
        )
        conn.execute(
            "insert into rehoboam.league_rank_history (snapshot_at, league_id, manager_id, "
            "day_number, total_points, matchday_points, is_self) "
            "values (1.0, 'L', 'me', 1, 600, 600, 1)"
        )
        out = tmp_path / "bid_learning.db"
        written = pull_replay_tables(conn, out)
        # A second pull rewrites rather than duplicates.
        again = pull_replay_tables(conn, out)
    assert set(written) == set(REPLAY_TABLES)
    assert written == again == {t: 1 for t in REPLAY_TABLES}
    with sqlite3.connect(out) as db:
        assert db.execute("select total_points from matchday_lineup_results").fetchone()[0] == 600
        assert db.execute("select id, player_id from flip_outcomes").fetchone() == (
            1,
            "p1",
        )
        assert db.execute("select count(*) from league_rank_history").fetchone()[0] == 1


def test_create_replay_tables_is_idempotent(tmp_path):
    from rehoboam.store.corpus_pull import create_replay_tables

    path = tmp_path / "bid_learning.db"
    create_replay_tables(path)
    create_replay_tables(path)
    with sqlite3.connect(path) as db:
        names = {r[0] for r in db.execute("select name from sqlite_master where type='table'")}
    assert {"flip_outcomes", "matchday_lineup_results", "league_rank_history"} <= names
