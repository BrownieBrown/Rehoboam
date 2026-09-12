"""Copy the bot's SQLite state into the store, once, and safely more than once.

Every table is staged with ``COPY`` into a temporary table shaped like the
target, then inserted ``ON CONFLICT DO NOTHING``. That keeps the copy fast
over a 40 ms round trip (one COPY and one INSERT per table instead of one
statement per row) and makes a second run a no-op. Identity columns are
imported with their SQLite ids and the sequence is moved past them, so new
rows continue where history stopped.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from rehoboam.store import SCHEMA

logger = logging.getLogger(__name__)

LEARNING_TABLES: tuple[str, ...] = (
    "auction_outcomes",
    "flip_outcomes",
    "matchday_outcomes",
    "pending_bids",
    "pending_bid_sell_plans",
    "tracked_purchases",
    "recently_sold",
    "predicted_eps",
    "team_value_history",
    "player_mv_history",
    "matchday_lineup_results",
    "league_rank_history",
    "manager_profile_history",
    "buy_decisions",
    "manager_transfers",
    "forced_sales",
    "trade_proposals",
    "league_transfers",
    "market_value_snapshots",
)

CORPUS_TABLES: tuple[str, ...] = (
    "player_universe",
    "player_match_history",
    "mv_series",
    "sweep_progress",
    "player_transfers",
)

IDENTITY_TABLES: frozenset[str] = frozenset(
    {
        "auction_outcomes",
        "flip_outcomes",
        "matchday_outcomes",
        "buy_decisions",
        "league_transfers",
        "market_value_snapshots",
    }
)


@dataclass(frozen=True)
class ImportReport:
    table: str
    sqlite_rows: int  # -1 when the source table or file was absent
    postgres_rows: int
    skipped_columns: tuple[str, ...] = ()


def _sqlite_table(src: sqlite3.Connection, table: str) -> tuple[list[str], list[tuple]] | None:
    exists = src.execute(
        "select 1 from sqlite_master where type = 'table' and name = ?", (table,)
    ).fetchone()
    if not exists:
        return None
    cols = [r[1] for r in src.execute(f"pragma table_info({table})")]
    rows = src.execute(f"select {', '.join(cols)} from {table}").fetchall()
    return cols, rows


def _count(conn: psycopg.Connection, table: str) -> int:
    q = sql.SQL("select count(*) as n from {}.{}").format(
        sql.Identifier(SCHEMA), sql.Identifier(table)
    )
    return conn.execute(q).fetchone()["n"]


def _target_columns(conn: psycopg.Connection, table: str) -> set[str]:
    """The target table's real columns — a live SQLite table can carry ALTER-added
    columns (e.g. flip_outcomes.trend_pct_at_buy, pending_bids.tier) that
    001_schema.sql never modeled; importing intersects rather than fails."""
    rows = conn.execute(
        "select column_name from information_schema.columns "
        "where table_schema = %s and table_name = %s",
        (SCHEMA, table),
    ).fetchall()
    return {r["column_name"] for r in rows}


def _stage_and_insert(
    conn: psycopg.Connection, table: str, cols: list[str], rows: list[tuple]
) -> None:
    """COPY into a temp table shaped like the target, then insert with conflicts ignored."""
    target = sql.SQL("{}.{}").format(sql.Identifier(SCHEMA), sql.Identifier(table))
    col_list = sql.SQL(", ").join(sql.Identifier(c) for c in cols)
    with conn.transaction():
        # A prior call's temp table can outlive its own transaction block: within
        # an already-open outer transaction, ``conn.transaction()`` is a savepoint,
        # so ``on commit drop`` only fires when the whole connect() block commits —
        # not between tables. Drop defensively before (re)creating.
        conn.execute(sql.SQL("drop table if exists _stage"))
        conn.execute(
            sql.SQL("create temp table _stage (like {} including defaults) on commit drop").format(
                target
            )
        )
        with conn.cursor() as cur:
            with cur.copy(sql.SQL("copy _stage ({}) from stdin").format(col_list)) as copy:
                for row in rows:
                    copy.write_row(row)
        conn.execute(
            sql.SQL("insert into {} ({}) select {} from _stage on conflict do nothing").format(
                target, col_list, col_list
            )
        )
        if table in IDENTITY_TABLES:
            conn.execute(
                sql.SQL(
                    "select setval(pg_get_serial_sequence({}, 'id'), "
                    "greatest((select coalesce(max(id), 1) from {}), 1))"
                ).format(sql.Literal(f"{SCHEMA}.{table}"), target)
            )


def _import_tables(
    conn: psycopg.Connection, sqlite_path: Path, tables: tuple[str, ...]
) -> list[ImportReport]:
    reports: list[ImportReport] = []
    with sqlite3.connect(sqlite_path) as src:
        for table in tables:
            found = _sqlite_table(src, table)
            if found is None:
                reports.append(ImportReport(table, -1, _count(conn, table)))
                continue
            cols, rows = found
            target_cols = _target_columns(conn, table)
            skipped = tuple(c for c in cols if c not in target_cols)
            if skipped:
                logger.warning(
                    "import-sqlite: %s: source columns not in the store, skipped: %s",
                    table,
                    ", ".join(skipped),
                )
                keep = [i for i, c in enumerate(cols) if c in target_cols]
                cols = [cols[i] for i in keep]
                rows = [tuple(r[i] for i in keep) for r in rows]
            if rows:
                _stage_and_insert(conn, table, cols, rows)
            reports.append(ImportReport(table, len(rows), _count(conn, table), skipped))
    return reports


def import_learning(conn: psycopg.Connection, sqlite_path: Path) -> list[ImportReport]:
    """bid_learning.db → the 19 learning tables."""
    return _import_tables(conn, sqlite_path, LEARNING_TABLES)


def import_corpus(conn: psycopg.Connection, sqlite_path: Path) -> list[ImportReport]:
    """training_corpus.db → the 5 corpus tables."""
    return _import_tables(conn, sqlite_path, CORPUS_TABLES)


def import_cache(conn: psycopg.Connection, sqlite_path: Path) -> list[ImportReport]:
    """player_history.db's two caches → api_cache, payload as jsonb."""
    cols = ["kind", "player_id", "league_id", "key", "fetched_at", "payload"]
    rows: list[tuple] = []
    with sqlite3.connect(sqlite_path) as src:
        perf = _sqlite_table(src, "performance_cache")
        if perf is not None:
            pcols, prows = perf
            idx = {c: i for i, c in enumerate(pcols)}
            for r in prows:
                rows.append(
                    (
                        "performance",
                        r[idx["player_id"]],
                        r[idx["league_id"]],
                        "",
                        float(r[idx["fetched_at"]]),
                        Jsonb(json.loads(r[idx["data"]])),
                    )
                )
        mv = _sqlite_table(src, "market_value_cache")
        if mv is not None:
            mcols, mrows = mv
            idx = {c: i for i, c in enumerate(mcols)}
            for r in mrows:
                rows.append(
                    (
                        "mv",
                        r[idx["player_id"]],
                        r[idx["league_id"]],
                        str(r[idx["timeframe"]]),
                        float(r[idx["fetched_at"]]),
                        Jsonb(json.loads(r[idx["data"]])),
                    )
                )
        if perf is None and mv is None:
            return [ImportReport("api_cache", -1, _count(conn, "api_cache"))]
    if rows:
        _stage_and_insert(conn, "api_cache", cols, rows)
    return [ImportReport("api_cache", len(rows), _count(conn, "api_cache"))]


def import_all(
    conn: psycopg.Connection,
    *,
    learning: Path | None,
    corpus: Path | None,
    cache: Path | None,
) -> list[ImportReport]:
    """Import whichever files exist; absent ones report -1 SQLite rows."""
    reports: list[ImportReport] = []
    for path, tables, fn in (
        (learning, LEARNING_TABLES, import_learning),
        (corpus, CORPUS_TABLES, import_corpus),
        (cache, ("api_cache",), import_cache),
    ):
        if path is None or not Path(path).exists():
            reports.extend(ImportReport(t, -1, _count(conn, t)) for t in tables)
            continue
        reports.extend(fn(conn, Path(path)))
    return reports
