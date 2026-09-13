"""One-time setup of the role the bot connects as.

``rehoboam_bot`` owns nothing but may do everything inside schema
``rehoboam``: existing tables and sequences, and every future one created by
the migrating role. ``public`` is never granted, so a query that lands there
fails instead of quietly succeeding. Idempotent: re-running changes nothing.

``alter default privileges`` only covers objects later created by the role
that ran it, so a migration applied under a different admin identity would
otherwise create tables the bot can't touch. ``refresh_grants`` re-grants
against the schema's *current* tables and sequences regardless of who made
them; ``migrate`` calls it after applying new migration files.
"""

from __future__ import annotations

import psycopg
from psycopg import sql

from rehoboam.store import SCHEMA

ROLE = "rehoboam_bot"


def refresh_grants(conn: psycopg.Connection) -> bool:
    """Grant the bot role usage plus full rights on every table/sequence now in the
    schema, and pin its search_path. No-op returning False when the role is absent."""
    exists = conn.execute("select 1 from pg_roles where rolname = %s", (ROLE,)).fetchone()
    if not exists:
        return False
    schema = sql.Identifier(SCHEMA)
    role = sql.Identifier(ROLE)
    conn.execute(sql.SQL("grant usage on schema {} to {}").format(schema, role))
    conn.execute(
        sql.SQL("grant all privileges on all tables in schema {} to {}").format(schema, role)
    )
    conn.execute(
        sql.SQL("grant all privileges on all sequences in schema {} to {}").format(schema, role)
    )
    conn.execute(sql.SQL("alter role {} set search_path = {}, public").format(role, schema))
    return True


def bootstrap(conn: psycopg.Connection, role_password: str) -> dict:
    """Create the bot role if absent and grant it the schema. Returns what changed."""
    created = False
    with conn.transaction():
        exists = conn.execute("select 1 from pg_roles where rolname = %s", (ROLE,)).fetchone()
        if not exists:
            conn.execute(
                sql.SQL("create role {} login password {}").format(
                    sql.Identifier(ROLE), sql.Literal(role_password)
                )
            )
            created = True
        schema = sql.Identifier(SCHEMA)
        role = sql.Identifier(ROLE)
        conn.execute(sql.SQL("create schema if not exists {}").format(schema))
        refresh_grants(conn)
        conn.execute(
            sql.SQL(
                "alter default privileges in schema {} grant all privileges on tables to {}"
            ).format(schema, role)
        )
        conn.execute(
            sql.SQL(
                "alter default privileges in schema {} grant all privileges on sequences to {}"
            ).format(schema, role)
        )
    return {"role_created": created}
