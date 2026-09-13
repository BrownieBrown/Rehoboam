"""Materialise the corpus tables into a local SQLite file.

The replay and backtest scan tens of thousands of rows in a loop and must
never do that over a metered network (spec 2026-09-11 §1). They keep their
``--corpus`` path argument; this writes what that path expects.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import psycopg
from psycopg import sql

from rehoboam.enrichment.corpus import TrainingCorpus
from rehoboam.store import SCHEMA
from rehoboam.store.import_sqlite import CORPUS_TABLES

#: The learning tables the replay, backtest and flip diagnosis read from a
#: local ``bid_learning.db``. Nothing else reads that file any more.
REPLAY_TABLES: tuple[str, ...] = (
    "flip_outcomes",
    "matchday_lineup_results",
    "league_rank_history",
)

_REPLAY_DDL = """
create table if not exists flip_outcomes (
    id integer primary key,
    player_id text not null,
    player_name text not null,
    buy_price integer not null,
    sell_price integer not null,
    profit integer not null,
    profit_pct real not null,
    hold_days integer not null,
    buy_date real not null,
    sell_date real not null,
    trend_at_buy text,
    average_points real,
    position text,
    was_injured integer not null default 0,
    trend_pct_at_buy real,
    mv_at_buy integer,
    pct_below_peak_30d_at_buy real
);
create table if not exists matchday_lineup_results (
    league_id text not null,
    day_number integer not null,
    matchday_date text not null,
    total_points integer not null,
    lineup_player_ids text not null,
    lineup_count integer not null,
    snapshot_at real not null,
    primary key (league_id, day_number)
);
create table if not exists league_rank_history (
    snapshot_at real not null,
    league_id text not null,
    manager_id text not null,
    day_number integer not null,
    rank_overall integer,
    rank_matchday integer,
    total_points integer,
    matchday_points integer,
    team_value integer,
    is_self integer not null default 0,
    primary key (snapshot_at, manager_id)
);
"""


def create_replay_tables(path: Path) -> None:
    """The SQLite shape of the three replay tables, mirroring 001_schema.sql.

    The column list must stay a superset of what the store's tables carry,
    because ``pull_replay_tables`` inserts every column the store returns;
    a column added to the store later must be added here too.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.executescript(_REPLAY_DDL)


def _pull_tables(
    conn: psycopg.Connection, db: sqlite3.Connection, tables: tuple[str, ...]
) -> dict[str, int]:
    written: dict[str, int] = {}
    for table in tables:
        rows = conn.execute(
            sql.SQL("select * from {}.{}").format(sql.Identifier(SCHEMA), sql.Identifier(table))
        ).fetchall()
        if not rows:
            written[table] = 0
            continue
        cols = list(rows[0].keys())
        placeholders = ", ".join("?" for _ in cols)
        db.executemany(
            f"insert or replace into {table} ({', '.join(cols)}) values ({placeholders})",
            [tuple(r[c] for c in cols) for r in rows],
        )
        written[table] = len(rows)
    db.commit()
    return written


def pull_replay_tables(conn: psycopg.Connection, out_path: Path) -> dict[str, int]:
    """Copy the three replay tables from the store into ``out_path``; return rows written.

    ``insert or replace`` for the same reason as the corpus: a re-pull must
    reflect the store, and ``flip_outcomes.id`` comes from the store so the
    diagnosis's trip ids stay stable across pulls.
    """
    out_path = Path(out_path)
    create_replay_tables(out_path)
    with sqlite3.connect(out_path) as db:
        return _pull_tables(conn, db, REPLAY_TABLES)


def pull_corpus(conn: psycopg.Connection, out_path: Path) -> dict[str, int]:
    """Copy every corpus table from the store into ``out_path``; return rows written.

    ``INSERT OR REPLACE``, not ``OR IGNORE``: corpus rows are not immutable.
    A ``player_match_history`` row is written as a placeholder (0 points, 0
    minutes) for a fixture that has not been played and rewritten with the
    real result once the match finishes, so a re-pull that skipped existing
    primary keys would leave the local file frozen at the placeholders. None
    of the five tables has a foreign key or an identity column, so replacing
    a row costs nothing but the write.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    TrainingCorpus(out_path)  # creates the SQLite schema when the file is new
    with sqlite3.connect(out_path) as db:
        return _pull_tables(conn, db, CORPUS_TABLES)
