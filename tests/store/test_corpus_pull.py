"""corpus-pull round-trips the corpus into a SQLite file the replay can read."""

from __future__ import annotations

import sqlite3

from rehoboam.store import SCHEMA, connect
from rehoboam.store.corpus_pull import pull_corpus
from rehoboam.store.migrate import migrate


def test_pull_writes_every_corpus_table_and_is_idempotent(store_dsn, tmp_path):
    with connect(store_dsn) as conn:
        migrate(conn)
        conn.execute(
            f"insert into {SCHEMA}.player_universe (player_id, first_name, last_name, position, "
            "team_id, market_value, average_points) values ('p1', 'O', 'One', 'Defender', 't', 5, 1.5)"
        )
        conn.execute(
            f"insert into {SCHEMA}.player_match_history (player_id, season, day_number, match_date, "
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
        again = pull_corpus(conn, out)
    assert again["player_match_history"] == 0
    with sqlite3.connect(out) as db:
        row = db.execute(
            "select points, minutes, status from player_match_history where player_id = 'p1'"
        ).fetchone()
        universe = db.execute("select count(*) from player_universe").fetchone()[0]
    assert row == (88, 90, 5)
    assert universe == 1
