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
    with conn.transaction():
        conn.execute(f"create schema if not exists {SCHEMA}")
        conn.execute(
            f"""
            create table if not exists {SCHEMA}.schema_migrations (
                version    integer primary key,
                name       text not null,
                applied_at double precision not null
            )
            """
        )


def applied_versions(conn: psycopg.Connection) -> set[int]:
    _bootstrap(conn)
    rows = conn.execute(f"select version from {SCHEMA}.schema_migrations").fetchall()
    return {r["version"] for r in rows}


def migrate(conn: psycopg.Connection) -> list[str]:
    """Apply every unapplied migration in version order; return their file names."""
    done = applied_versions(conn)
    applied: list[str] = []
    files = sorted(p for p in MIGRATIONS.iterdir() if p.name.endswith(".sql"))
    for path in files:
        version = int(path.name.split("_", 1)[0])
        if version in done:
            continue
        with conn.transaction():
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute(
                f"insert into {SCHEMA}.schema_migrations (version, name, applied_at) "
                "values (%s, %s, %s)",
                (version, path.name, time.time()),
            )
        applied.append(path.name)
    return applied
