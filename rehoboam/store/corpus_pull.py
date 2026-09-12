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
    written: dict[str, int] = {}
    with sqlite3.connect(out_path) as db:
        for table in CORPUS_TABLES:
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
