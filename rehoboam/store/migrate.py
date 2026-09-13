"""Apply the numbered SQL files under ``migrations/`` exactly once each.

The runner bootstraps the schema and its ``schema_migrations`` table itself,
so a fresh database needs nothing but a connection. Each file runs in its
own transaction and is recorded on success; a failing file leaves the
database at the previous version.
"""

from __future__ import annotations

import time
from importlib import resources

import psycopg

from rehoboam.store import SCHEMA

MIGRATIONS = resources.files("rehoboam.store") / "migrations"


def _bootstrap(conn: psycopg.Connection) -> None:
    """Create the schema and its ``schema_migrations`` table, but only if missing.

    PostgreSQL checks the ACL *before* the IF NOT EXISTS short-circuit, so
    ``create schema if not exists`` is "permission denied for database" for a
    role holding only USAGE — even when the schema is already there. The bot
    role runs this on every ``migrate`` call against an up-to-date database,
    so look first and issue DDL only for what is actually absent; then the
    ordinary path needs nothing but SELECT.
    """
    table = f"{SCHEMA}.schema_migrations"
    if conn.execute("select to_regclass(%s) as oid", (table,)).fetchone()["oid"] is not None:
        return  # the table exists, therefore so does the schema
    has_schema = conn.execute(
        "select 1 from information_schema.schemata where schema_name = %s", (SCHEMA,)
    ).fetchone()
    with conn.transaction():
        if not has_schema:
            conn.execute(f"create schema if not exists {SCHEMA}")
        conn.execute(
            f"""
            create table if not exists {table} (
                version    integer primary key,
                name       text not null,
                applied_at double precision not null
            )
            """
        )


def applied_versions(conn: psycopg.Connection) -> set[int]:
    """The migration versions already recorded, bootstrapping the table if absent.

    Commits whatever transaction is open on the connection before returning,
    so that each migration file below runs as its own top-level transaction.
    """
    _bootstrap(conn)
    rows = conn.execute(f"select version from {SCHEMA}.schema_migrations").fetchall()
    # That select, run outside an explicit conn.transaction(), leaves an
    # implicit transaction open on the connection. If it stays open, the next
    # caller's conn.transaction() (each migration file, in migrate() below)
    # nests as a savepoint inside it rather than starting its own top-level
    # transaction — so a file's COMMIT is only a SAVEPOINT release, and a
    # later file's failure rolls back everything still open, earlier
    # successful files included. Commit here so each file is its own
    # top-level transaction.
    conn.commit()
    return {r["version"] for r in rows}


def migrate(conn: psycopg.Connection) -> list[str]:
    """Apply every unapplied migration in version order; return their file names.

    When at least one file is applied, also refreshes the bot role's grants —
    covering tables a newly-applied migration created, whoever ran it.
    """
    done = applied_versions(conn)
    applied: list[str] = []
    files = sorted(
        (p for p in MIGRATIONS.iterdir() if p.name.endswith(".sql")),
        key=lambda p: p.name,
    )
    for path in files:
        version = int(path.name.split("_", 1)[0])
        if version in done:
            continue
        try:
            with conn.transaction():
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute(
                    f"insert into {SCHEMA}.schema_migrations (version, name, applied_at) "
                    "values (%s, %s, %s)",
                    (version, path.name, time.time()),
                )
        except psycopg.errors.InsufficientPrivilege as exc:
            # The bot role stays USAGE-only by design; new DDL is an operator step.
            raise PermissionError(
                f"migration {path.name} needs privileges this role lacks; "
                "run `rehoboam migrate` as the postgres admin first"
            ) from exc
        applied.append(path.name)
    if applied:
        from rehoboam.store.bootstrap import refresh_grants

        with conn.transaction():
            refresh_grants(conn)
    return applied
