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
    """Copy every corpus table from the store into ``out_path``; return rows written."""
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
            before = db.total_changes
            db.executemany(
                f"insert or ignore into {table} ({', '.join(cols)}) values ({placeholders})",
                [tuple(r[c] for c in cols) for r in rows],
            )
            written[table] = db.total_changes - before
        db.commit()
    return written
