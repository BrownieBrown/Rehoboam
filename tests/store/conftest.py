"""A real PostgreSQL for the store tests.

Order of preference: a server named by ``TEST_PG_HOST`` (CI's service
container), else a local cluster started from PostgreSQL binaries
(Homebrew's ``postgresql@17`` on macOS, ``/usr/lib/postgresql/*`` on Debian),
else skip with a message. Every test gets a fresh, empty database.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from psycopg.conninfo import make_conninfo
from pytest_postgresql import factories

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


if os.environ.get("TEST_PG_HOST"):
    pg_proc = factories.postgresql_noproc(
        host=os.environ["TEST_PG_HOST"],
        port=int(os.environ.get("TEST_PG_PORT", "5432")),
        user=os.environ.get("TEST_PG_USER", "postgres"),
        password=os.environ.get("TEST_PG_PASSWORD", "postgres"),
    )
elif _local_pg_ctl():
    pg_proc = factories.postgresql_proc(executable=_local_pg_ctl())
else:
    pg_proc = None

if pg_proc is not None:
    pg_conn = factories.postgresql("pg_proc")


@pytest.fixture
def store_dsn(request) -> str:
    """DSN of a fresh database for this test, or a skip when no server exists."""
    if pg_proc is None:
        pytest.skip("no PostgreSQL: set TEST_PG_HOST or install postgresql@17")
    conn = request.getfixturevalue("pg_conn")
    info = conn.info
    parts = {
        "host": info.host,
        "port": info.port,
        "user": info.user,
        "dbname": info.dbname,
    }
    if info.password:
        parts["password"] = info.password
    return make_conninfo(**parts)
