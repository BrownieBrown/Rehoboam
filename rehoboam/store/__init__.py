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

    Commits when the block exits cleanly and rolls back when it raises,
    which is psycopg's own context-manager contract; callers that need
    several independent transactions use ``conn.transaction()`` inside.
    """
    with psycopg.connect(
        resolve_dsn(dsn),
        prepare_threshold=None,
        row_factory=dict_row,
        connect_timeout=15,
    ) as conn:
        yield conn
