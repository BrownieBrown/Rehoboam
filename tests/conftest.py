"""Shared fixtures.

`permissive_gate` exists so tests that are about something *else* — the
kickoff-lockout guard, emergency fill ordering — can satisfy
`ExecutionService.buy`'s required gate without restating seven fields each
time. Tests that are about the gate build their own, so a permissive default
can never accidentally be what makes a gate assertion pass.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from pytest_postgresql import factories
from pytest_postgresql.janitor import DatabaseJanitor

from rehoboam.services.bid_ceiling import BidCeilingPolicy, Tier
from rehoboam.services.safety_gate import BuyGate

# ---------------------------------------------------------------------------
# A real PostgreSQL for the whole suite.
#
# Order of preference: a server named by TEST_PG_HOST (CI's service
# container), else a local cluster started from PostgreSQL binaries
# (Homebrew's postgresql@17 on macOS, /usr/lib/postgresql/* on Debian), else
# no database — tests that need one skip, and the incidental BidLearner()
# constructed by trader tests fails with StoreUnconfigured on first use.
#
# The template database is migrated ONCE (`load=[_apply_schema]`); every test
# database is a clone of it, so a fresh, fully-migrated database per test
# costs a CREATE DATABASE ... TEMPLATE, not a schema replay.
# ---------------------------------------------------------------------------

_CANDIDATE_PG_CTL = (
    "/opt/homebrew/opt/postgresql@17/bin/pg_ctl",
    "/usr/local/opt/postgresql@17/bin/pg_ctl",
    "/usr/lib/postgresql/17/bin/pg_ctl",
    "/usr/lib/postgresql/16/bin/pg_ctl",
)


def _local_pg_ctl() -> str | None:
    for candidate in _CANDIDATE_PG_CTL:
        if Path(candidate).exists():
            return candidate
    found = shutil.which("pg_ctl")
    if found and (Path(found).resolve().parent / "postgres").exists():
        return found
    return None


def _apply_schema(**kwargs) -> None:
    """pytest-postgresql `load` hook: migrate the template database.

    Called with psycopg connection keywords (host, port, user, dbname,
    password, ...), the same way pytest-postgresql's own SQL-file loader is.
    `row_factory=dict_row` is added on top of those kwargs: `migrate` reads
    rows by key (`rehoboam/store/migrate.py`'s `_bootstrap`), and the raw
    kwargs from the loader carry no row factory, so plain `psycopg.connect`
    hands back tuples and `_bootstrap` breaks on its first `fetchone()["oid"]`.
    """
    from psycopg.rows import dict_row

    from rehoboam.store.migrate import migrate

    with psycopg.connect(row_factory=dict_row, **kwargs) as conn:
        migrate(conn)


if os.environ.get("TEST_PG_HOST"):
    pg_proc = factories.postgresql_noproc(
        host=os.environ["TEST_PG_HOST"],
        port=int(os.environ.get("TEST_PG_PORT", "5432")),
        user=os.environ.get("TEST_PG_USER", "postgres"),
        password=os.environ.get("TEST_PG_PASSWORD", "postgres"),
        load=[_apply_schema],
    )
elif _local_pg_ctl():
    pg_proc = factories.postgresql_proc(executable=_local_pg_ctl(), load=[_apply_schema])
else:
    pg_proc = None

if os.environ.get("CI") and pg_proc is None:
    # A skip here would be indistinguishable from a pass: CI would go green
    # with every database-backed test never run. GitHub Actions sets CI=true.
    raise RuntimeError("CI has no PostgreSQL: TEST_PG_HOST unset and no local pg_ctl")

if pg_proc is not None:
    pg_conn = factories.postgresql("pg_proc")


def _dsn(*, host, port, user, dbname, password) -> str:
    parts = {"host": host, "port": port, "user": user, "dbname": dbname}
    if password:
        parts["password"] = password
    return make_conninfo(**parts)


@pytest.fixture
def store_dsn(request, monkeypatch) -> str:
    """A fresh database carrying every migration; DATABASE_URL points at it."""
    if pg_proc is None:
        pytest.skip("no PostgreSQL: set TEST_PG_HOST or install postgresql@17")
    info = request.getfixturevalue("pg_conn").info
    dsn = _dsn(
        host=info.host,
        port=info.port,
        user=info.user,
        dbname=info.dbname,
        password=info.password,
    )
    monkeypatch.setenv("DATABASE_URL", dsn)
    return dsn


@pytest.fixture
def blank_dsn(store_dsn) -> str:
    """`store_dsn` with the rehoboam schema dropped — for tests of migrate itself."""
    with psycopg.connect(store_dsn, autocommit=True) as conn:
        conn.execute("drop schema if exists rehoboam cascade")
    return store_dsn


@pytest.fixture
def learner(store_dsn):
    """A BidLearner on a fresh database."""
    from rehoboam.bid_learner import BidLearner

    return BidLearner(dsn=store_dsn)


@pytest.fixture(scope="session")
def _shared_store_dsn(request):
    """One migrated database for the whole session, for code paths that build a
    learner without asking for one (AutoTrader's default BidLearner()). Tests
    that assert on what a learner wrote must use `store_dsn` instead: this one
    is shared and never reset."""
    if pg_proc is None:
        yield None
        return
    proc = request.getfixturevalue("pg_proc")
    janitor = DatabaseJanitor(
        user=proc.user,
        host=proc.host,
        port=proc.port,
        dbname="rehoboam_shared",
        template_dbname=proc.template_dbname,
        password=proc.password,
    )
    with janitor:
        yield _dsn(
            host=proc.host,
            port=proc.port,
            user=proc.user,
            dbname="rehoboam_shared",
            password=proc.password,
        )


@pytest.fixture(autouse=True)
def _store_env(monkeypatch, _shared_store_dsn):
    """No test may reach the .env's real DATABASE_URL.

    Environment variables win over the dotenv file in pydantic-settings, so
    pinning them here is what keeps a test-constructed learner off the live
    Supabase project. `store_dsn` overrides this for the tests that ask for a
    fresh database (an explicitly requested fixture runs after autouse ones).
    """
    monkeypatch.setenv("DATABASE_URL", _shared_store_dsn or "")
    monkeypatch.setenv("DATABASE_ADMIN_URL", "")


#: Settings defaults, restated so a config change cannot silently retune tests.
CEILING_POLICY = BidCeilingPolicy(
    floor_eur=250_000,
    tier_pcts={
        Tier.MARGINAL: 8.0,
        Tier.SOLID: 15.0,
        Tier.STRONG: 25.0,
        Tier.MUST_HAVE: 35.0,
    },
)


def permissive_buy_gate(player_id: str = "p1", **overrides) -> BuyGate:
    """A gate that permits any realistic test bid for `player_id`.

    The market value and allowance are deliberately enormous so no assertion
    in an unrelated test ever turns on the ceiling or the budget rule.
    """
    fields: dict = {
        "market_value": 1_000_000_000,
        "spendable_budget": 1_000_000_000,
        "known_player_ids": (player_id,),
        "free_slots": 1,
        "tier": None,
        "ceiling_policy": CEILING_POLICY,
    }
    fields.update(overrides)
    return BuyGate(**fields)


@pytest.fixture
def permissive_gate() -> BuyGate:
    return permissive_buy_gate()
