"""The one live database: Supabase Postgres (spec 2026-09-11 §1).

Everything the bot persists lives in schema ``rehoboam``; ``public`` stays
empty. Connections go through Supabase's transaction pooler (port 6543),
which rejects prepared statements, so ``prepare_threshold=None`` is a
requirement, not a tuning choice. Every statement schema-qualifies its
tables: a pooled backend may not carry ``search_path`` from one transaction
to the next, and a query that silently lands in ``public`` is the failure
this module exists to prevent.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

#: The schema every table lives in. ``public`` is deliberately empty.
SCHEMA = "rehoboam"


class StoreUnconfigured(RuntimeError):
    """``DATABASE_URL`` is not set, so nothing can reach the store."""


def resolve_dsn(dsn: str | None = None) -> str:
    """The connection string to use: the argument, else ``Settings.database_url``."""
    if dsn:
        return dsn
    from rehoboam.config import get_settings

    url = get_settings().database_url
    if not url:
        raise StoreUnconfigured("DATABASE_URL is not set — see .env.example")
    return url


@contextmanager
def connect(dsn: str | None = None) -> Iterator[psycopg.Connection]:
    """A connection with dict rows and no prepared statements.

    The whole block is ONE transaction unless the caller commits inside it:
    psycopg opens an implicit transaction at the first statement, commits it
    when the block exits cleanly and rolls it back when it raises. Entering
    ``conn.transaction()`` once that implicit transaction is already open
    gives a *savepoint*, not an independent transaction — its "commit" only
    releases the savepoint, so a later failure still discards it. A caller
    that needs genuinely independent transactions must therefore either
    ``conn.commit()`` first (the idiom ``applied_versions`` uses, for exactly
    this reason) or open ``conn.transaction()`` before any other statement on
    the connection. ``import_sqlite`` and ``migrate`` both work around this.
    Turning on ``autocommit`` would remove the trap; that is a PR B2 decision
    and is deliberately not taken here.
    """
    with psycopg.connect(
        resolve_dsn(dsn),
        prepare_threshold=None,
        row_factory=dict_row,
        connect_timeout=15,
    ) as conn:
        yield conn
