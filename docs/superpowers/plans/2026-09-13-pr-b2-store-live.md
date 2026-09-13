# PR B2: the store is live — learners on Postgres, blob sync deleted — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every row the bot persists is written to and read from Supabase Postgres through `rehoboam/store/`; the SQLite files, the Azure Blob state sync and the JSON-era leftovers are deleted; the offline tools keep reading local SQLite files that `corpus-pull` now produces.

**Architecture:** `BidLearner`, `ActivityFeedLearner` and `ValueHistoryCache` keep their method signatures and become thin Postgres clients: each method is one `with self.connection() as conn:` block, i.e. one transaction committed on exit — the same unit of work the SQLite `with sqlite3.connect(...)` block gave. The schema comes from migrations, never from constructors, so constructing a learner touches nothing. A missing `DATABASE_URL` or an unapplied migration is a startup error (`store.ensure_ready()`), never a silent fallback. Tests run against a real PostgreSQL: every test gets a database cloned from a migrated template, and one shared database catches the incidental `BidLearner()` that trader tests construct without a fixture. The replay, backtest and diagnosis tools still read local SQLite files, because they scan in a loop; `corpus-pull` writes both the corpus file and a small `bid_learning.db` holding the three learning tables they read.

**Tech Stack:** Python 3.10+ (CI 3.10/3.11/3.12), psycopg 3, pytest-postgresql 9.x, Typer, Rich, Bicep, Azure Functions (Python v2 model). PostgreSQL 17.

**Spec:** `docs/superpowers/specs/2026-09-11-data-foundation-design.md` §1 ("The store: Supabase Postgres") and the Rollout table's row **B**. B1 (PR #106, merged 2026-09-13) delivered `rehoboam/store/`, the schema, `import-sqlite`, `corpus-pull` and the deploy plumbing; this plan is the cut-over. Design decisions ruled 2026-09-13 with Marco: one PR; no autocommit (one transaction per learner call); tests on real Postgres; local `.env` uses the bot role like prod, the admin URL moves to `DATABASE_ADMIN_URL`; the compliance checker's legacy JSON reader is replaced by the learner; the training-corpus SQLite writer (`enrichment/corpus.py`, used by `enrich-corpus`) stays until PR C.

## Global Constraints

- Branch `marcobraun2013/pr-b2-store-live` in worktree `/Users/marco/dev/rehoboam/.claude/worktrees/pr-b2-store-live`, based on `main` at 4abc2c9. Work only in that directory; never `cd` to the main checkout; never commit to `main`.
- `.env` in the worktree is git-ignored and holds real credentials. **Implementers never read, print or edit `.env`.** Only Task 8 (controller-run) touches it.
- Tests must never reach the real database. Task 1's autouse fixture pins `DATABASE_URL` for every test; do not remove or bypass it.
- Every SQL statement schema-qualifies its tables as the literal `rehoboam.<table>` (no f-strings, no `search_path`). Placeholders are `%s`. Rows are dicts (`row_factory=dict_row`); never index a row by position.
- No new dependencies. `psycopg[binary]` and `pytest-postgresql` are already in `pyproject.toml`; `azure-storage-blob` stays (PR C's weekly COPY export uses it).
- Line length 100 (black + ruff). CI pins `black>=25.1,<26`; the local black is 26 and disagrees on one hug style, so check formatting with `uvx --from "black>=25.1,<26" black --check --line-length=100 rehoboam/`. Stage only files you changed.
- Run tests with `uv run pytest -q -p no:cacheprovider`. Local PostgreSQL 17 binaries live at `/opt/homebrew/opt/postgresql@17/bin/pg_ctl`. Baseline on this branch: 1643 passed, 1-2 skipped; `tests/test_replay/test_driver.py::test_real_calendar_has_34_matchdays_starting_on_the_official_day_one` reads a real `logs/bid_learning.db` and skips when absent — delete `logs/bid_learning.db` and `logs/player_history.db` from the worktree before running the suite until Task 6 lands (after it, nothing creates them).
- Every commit message ends with these two lines exactly:
  ```
  Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01F4d1fpUbNh28evK1pNy4Le
  ```
- Docstrings and comments explain *why*; do not narrate the diff ("changed from SQLite"). Where a SQLite-specific remark in an existing docstring becomes false (e.g. "sqlite3 doesn't expose per-row outcomes from executemany"), rewrite the sentence rather than leaving it.

______________________________________________________________________

## File structure

| file                                                                             | responsibility after this PR                                                                                                           |
| -------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `tests/conftest.py`                                                              | the Postgres fixtures for the whole suite (`store_dsn`, `blank_dsn`, `learner`, the autouse env guard, the shared incidental database) |
| `tests/store/conftest.py`                                                        | deleted (merged into `tests/conftest.py`)                                                                                              |
| `rehoboam/store/__init__.py`                                                     | `connect()`, `resolve_dsn()`, `StoreUnconfigured`, new `ensure_ready()`                                                                |
| `rehoboam/store/corpus_pull.py`                                                  | corpus tables → `training_corpus.db`; new: replay tables → `bid_learning.db`; `create_replay_tables()`                                 |
| `rehoboam/bid_learner.py`                                                        | Postgres client for the 17 operating and league tables; no DDL                                                                         |
| `rehoboam/activity_feed_learner.py`                                              | Postgres client for `league_transfers`, `market_value_snapshots`; no DDL                                                               |
| `rehoboam/value_history.py`                                                      | `ValueHistoryCache` over `rehoboam.api_cache` (jsonb)                                                                                  |
| `rehoboam/mv_backfill.py`, `rehoboam/enrichment/historical_ids.py`               | read through the learner's connection, not a file                                                                                      |
| `rehoboam/league_compliance.py`                                                  | cost basis from the learner's `tracked_purchases`                                                                                      |
| `rehoboam/learning/tracker.py`                                                   | no JSON migration on construction                                                                                                      |
| `rehoboam/learning/migration.py`, `rehoboam/azure_blob.py`                       | deleted                                                                                                                                |
| `rehoboam/config.py`                                                             | `Settings.database_admin_url`; `AzureBlobSettings` deleted                                                                             |
| `rehoboam/cli.py`                                                                | `fetch-azure-state`/`push-azure-state` deleted; `auto`/`status` call `ensure_ready`; `migrate`/`db-bootstrap` prefer the admin URL     |
| `deploy/azure_function/function_app.py`                                          | no blob download/upload; `ensure_ready()` at the start of both triggers                                                                |
| `CLAUDE.md`, `.env.example`, `deploy/azure_function/local.settings.json.example` | docs                                                                                                                                   |

______________________________________________________________________

### Task 1: Test foundation, `ensure_ready`, and the admin URL setting

**Files:**

- Modify: `tests/conftest.py`
- Delete: `tests/store/conftest.py`
- Modify: `tests/store/test_migrate.py`, `tests/store/test_cli.py` (use `blank_dsn` where a test needs an un-migrated database)
- Modify: `rehoboam/store/__init__.py`
- Modify: `rehoboam/config.py` (one field after `database_url`)
- Modify: `.env.example`
- Test: `tests/store/test_connect.py`

**Interfaces:**

- Consumes: `rehoboam.store.connect`, `rehoboam.store.migrate.migrate`, `pytest_postgresql.factories`, `pytest_postgresql.janitor.DatabaseJanitor`.

- Produces:

  - fixture `store_dsn` (function): DSN of a fresh database already carrying every migration; also sets env `DATABASE_URL` to it for the test's duration.
  - fixture `blank_dsn` (function): `store_dsn` with schema `rehoboam` dropped — for tests of `migrate`/`db-bootstrap` themselves.
  - fixture `learner` (function): `BidLearner(dsn=store_dsn)` — usable from Task 3 on.
  - autouse fixture `_store_env`: pins `DATABASE_URL` to one shared, migrated test database (or `""` when no PostgreSQL) and `DATABASE_ADMIN_URL` to `""`.
  - `rehoboam.store.ensure_ready(dsn: str | None = None) -> None`.
  - `Settings.database_admin_url: str` (env `DATABASE_ADMIN_URL`, default `""`).

- [ ] **Step 1: Write the failing tests for `ensure_ready`**

Append to `tests/store/test_connect.py`:

```python
def test_ensure_ready_is_a_no_op_on_a_migrated_database(store_dsn):
    from rehoboam.store import ensure_ready

    ensure_ready(store_dsn)  # must not raise, must not print


def test_ensure_ready_applies_migrations_to_a_blank_database(blank_dsn):
    from rehoboam.store import SCHEMA, connect, ensure_ready

    ensure_ready(blank_dsn)
    with connect(blank_dsn) as conn:
        n = conn.execute(
            "select count(*) as n from information_schema.tables where table_schema = %s",
            (SCHEMA,),
        ).fetchone()["n"]
    assert n >= 26


def test_ensure_ready_without_database_url_is_a_hard_error(monkeypatch):
    from rehoboam.store import StoreUnconfigured, ensure_ready

    monkeypatch.setenv("DATABASE_URL", "")
    with pytest.raises(StoreUnconfigured):
        ensure_ready()
```

(`import pytest` is already at the top of that file; add it if not.)

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/store/test_connect.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'ensure_ready'` and `fixture 'blank_dsn' not found`.

- [ ] **Step 3: Replace `tests/conftest.py`'s top with the Postgres fixtures**

Keep everything already in `tests/conftest.py` (`CEILING_POLICY`, `permissive_buy_gate`, `permissive_gate`). Add these imports and fixtures **above** them, and delete `tests/store/conftest.py` (`git rm tests/store/conftest.py`):

```python
import os
import shutil
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from pytest_postgresql import factories
from pytest_postgresql.janitor import DatabaseJanitor

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
    """
    from rehoboam.store.migrate import migrate

    with psycopg.connect(**kwargs) as conn:
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
    pg_proc = factories.postgresql_proc(
        executable=_local_pg_ctl(), load=[_apply_schema]
    )
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
        version=proc.version,
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
```

Then in `tests/store/test_migrate.py` and `tests/store/test_cli.py`, change every test whose assertion depends on the database being **un-migrated** (a first `migrate` that must report `001_schema.sql`, a bootstrap that must see the table appear, the grant-refresh test that creates a table under another role and then migrates) to take `blank_dsn` instead of `store_dsn`, using `blank_dsn` wherever the test used `store_dsn`. Tests that only need a working schema keep `store_dsn`. Run `uv run pytest tests/store -q -p no:cacheprovider` and adjust until green; the expected count is the 25 existing tests plus the 3 new ones.

- [ ] **Step 4: Add `ensure_ready` to `rehoboam/store/__init__.py`**

Append:

```python
def ensure_ready(dsn: str | None = None) -> None:
    """Fail at startup, not mid-session.

    Connects and applies any unapplied migration. Under the bot role that is
    a check: `migrate()` issues DDL only for what is missing, so an
    up-to-date database costs one SELECT, and a migration file the role
    cannot apply raises PermissionError naming it — the deploy order is
    "operator applies migrations as postgres, then code ships", and a session
    that would run against a half-migrated schema must not start. Raises
    StoreUnconfigured when DATABASE_URL is unset and psycopg.OperationalError
    when the server is unreachable; neither is caught here on purpose.
    """
    import logging

    from rehoboam.store.migrate import migrate

    with connect(dsn) as conn:
        applied = migrate(conn)
    if applied:
        logging.getLogger(__name__).info("store: applied %s", ", ".join(applied))
```

- [ ] **Step 5: Add the admin URL setting and document both URLs**

In `rehoboam/config.py`, directly after the `database_url` field:

```python
database_admin_url: str = Field(
    default="",
    repr=False,
    description=(
        "Connection string of the postgres admin role, used only by `rehoboam migrate` "
        "and `rehoboam db-bootstrap` (DDL and grants). Empty falls back to DATABASE_URL. "
        "Never logged. Env: DATABASE_ADMIN_URL."
    ),
)
```

In `.env.example`, replace the three-line store block at the bottom with:

```
# The store (spec 2026-09-11 §1): Supabase Postgres via the transaction pooler.
# Both are the "Transaction pooler" URI from the project's Connect dialog; port must be 6543.
# DATABASE_URL is the rehoboam_bot role — the same URL prod runs with.
DATABASE_URL=
# DATABASE_ADMIN_URL is the postgres role; only migrate and db-bootstrap use it.
DATABASE_ADMIN_URL=
```

- [ ] **Step 6: Run the store tests and the whole suite**

Run: `uv run pytest tests/store -q -p no:cacheprovider`
Expected: 28 passed.
Run: `rm -f logs/bid_learning.db logs/player_history.db; uv run pytest -q -p no:cacheprovider`
Expected: everything that passed before still passes (1643 passed + 3 new; 1-2 skipped). Note the PostgreSQL cluster now starts for every run because the autouse fixture needs the shared database; that is intended.

- [ ] **Step 7: Commit**

```bash
git add tests/conftest.py tests/store rehoboam/store/__init__.py rehoboam/config.py .env.example
git commit -m "test(store): one Postgres for the whole suite; ensure_ready fails at startup; the admin URL is its own setting"
```

______________________________________________________________________

### Task 2: `corpus-pull` also writes the replay's learning tables

**Why first:** the replay, backtest and diagnosis tools read `flip_outcomes`, `matchday_lineup_results` and `league_rank_history` from a local `bid_learning.db`. Today that file comes from the blob fetch, which this PR deletes; after Task 3 `BidLearner` cannot write SQLite either, and the tests that build such a file through `BidLearner(db_path=...)` (`tests/test_backtest/test_baseline_driver.py`) need another writer. This task supplies it.

**Files:**

- Modify: `rehoboam/store/corpus_pull.py`
- Modify: `rehoboam/cli.py` (`corpus-pull` command: new `--learning-out` option)
- Test: `tests/store/test_corpus_pull.py`

**Interfaces:**

- Produces:

  - `rehoboam.store.corpus_pull.REPLAY_TABLES: tuple[str, ...] = ("flip_outcomes", "matchday_lineup_results", "league_rank_history")`
  - `rehoboam.store.corpus_pull.create_replay_tables(path: Path) -> None` — creates the three tables in a SQLite file (idempotent).
  - `rehoboam.store.corpus_pull.pull_replay_tables(conn: psycopg.Connection, out_path: Path) -> dict[str, int]`
  - CLI: `rehoboam corpus-pull --out logs/training_corpus.db --learning-out logs/bid_learning.db`

- [ ] **Step 1: Write the failing tests**

Append to `tests/store/test_corpus_pull.py`:

```python
def test_replay_tables_are_pulled_into_a_local_learning_file(store_dsn, tmp_path):
    from rehoboam.store import connect
    from rehoboam.store.corpus_pull import REPLAY_TABLES, pull_replay_tables

    with connect(store_dsn) as conn:
        conn.execute(
            "insert into rehoboam.matchday_lineup_results (league_id, day_number, matchday_date, "
            "total_points, lineup_player_ids, lineup_count, snapshot_at) "
            "values ('L', 1, '2026-08-22T13:30:00Z', 600, '[\"p1\"]', 11, 1.0)"
        )
        conn.execute(
            "insert into rehoboam.flip_outcomes (player_id, player_name, buy_price, sell_price, "
            "profit, profit_pct, hold_days, buy_date, sell_date) "
            "values ('p1', 'One', 10, 12, 2, 20.0, 3, 100.0, 400.0)"
        )
        conn.execute(
            "insert into rehoboam.league_rank_history (snapshot_at, league_id, manager_id, "
            "day_number, total_points, matchday_points, is_self) "
            "values (1.0, 'L', 'me', 1, 600, 600, 1)"
        )
        out = tmp_path / "bid_learning.db"
        written = pull_replay_tables(conn, out)
        # A second pull rewrites rather than duplicates.
        again = pull_replay_tables(conn, out)
    assert set(written) == set(REPLAY_TABLES)
    assert written == again == {t: 1 for t in REPLAY_TABLES}
    with sqlite3.connect(out) as db:
        assert (
            db.execute("select total_points from matchday_lineup_results").fetchone()[0]
            == 600
        )
        assert db.execute("select id, player_id from flip_outcomes").fetchone() == (
            1,
            "p1",
        )
        assert db.execute("select count(*) from league_rank_history").fetchone()[0] == 1


def test_create_replay_tables_is_idempotent(tmp_path):
    from rehoboam.store.corpus_pull import create_replay_tables

    path = tmp_path / "bid_learning.db"
    create_replay_tables(path)
    create_replay_tables(path)
    with sqlite3.connect(path) as db:
        names = {
            r[0]
            for r in db.execute("select name from sqlite_master where type='table'")
        }
    assert {"flip_outcomes", "matchday_lineup_results", "league_rank_history"} <= names
```

(`import sqlite3` is already in that test file; add it if not.)

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/store/test_corpus_pull.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'REPLAY_TABLES'`.

- [ ] **Step 3: Implement in `rehoboam/store/corpus_pull.py`**

Add after the imports:

```python
#: The learning tables the replay, backtest and flip diagnosis read from a
#: local ``bid_learning.db``. Nothing else reads that file any more.
REPLAY_TABLES: tuple[str, ...] = (
    "flip_outcomes",
    "matchday_lineup_results",
    "league_rank_history",
)

_REPLAY_DDL = """
create table if not exists flip_outcomes (
    id integer primary key,
    player_id text not null,
    player_name text not null,
    buy_price integer not null,
    sell_price integer not null,
    profit integer not null,
    profit_pct real not null,
    hold_days integer not null,
    buy_date real not null,
    sell_date real not null,
    trend_at_buy text,
    average_points real,
    position text,
    was_injured integer not null default 0,
    trend_pct_at_buy real,
    mv_at_buy integer,
    pct_below_peak_30d_at_buy real
);
create table if not exists matchday_lineup_results (
    league_id text not null,
    day_number integer not null,
    matchday_date text not null,
    total_points integer not null,
    lineup_player_ids text not null,
    lineup_count integer not null,
    snapshot_at real not null,
    primary key (league_id, day_number)
);
create table if not exists league_rank_history (
    snapshot_at real not null,
    league_id text not null,
    manager_id text not null,
    day_number integer not null,
    rank_overall integer,
    rank_matchday integer,
    total_points integer,
    matchday_points integer,
    team_value integer,
    is_self integer not null default 0,
    primary key (snapshot_at, manager_id)
);
"""


def create_replay_tables(path: Path) -> None:
    """The SQLite shape of the three replay tables, mirroring 001_schema.sql.

    The column list must stay a superset of what the store's tables carry,
    because ``pull_replay_tables`` inserts every column the store returns;
    a column added to the store later must be added here too.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.executescript(_REPLAY_DDL)


def _pull_tables(
    conn: psycopg.Connection, db: sqlite3.Connection, tables: tuple[str, ...]
) -> dict[str, int]:
    written: dict[str, int] = {}
    for table in tables:
        rows = conn.execute(
            sql.SQL("select * from {}.{}").format(
                sql.Identifier(SCHEMA), sql.Identifier(table)
            )
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


def pull_replay_tables(conn: psycopg.Connection, out_path: Path) -> dict[str, int]:
    """Copy the three replay tables from the store into ``out_path``; return rows written.

    ``insert or replace`` for the same reason as the corpus: a re-pull must
    reflect the store, and ``flip_outcomes.id`` comes from the store so the
    diagnosis's trip ids stay stable across pulls.
    """
    out_path = Path(out_path)
    create_replay_tables(out_path)
    with sqlite3.connect(out_path) as db:
        return _pull_tables(conn, db, REPLAY_TABLES)
```

Then rewrite `pull_corpus` to use `_pull_tables` (behaviour unchanged):

```python
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
    with sqlite3.connect(out_path) as db:
        return _pull_tables(conn, db, CORPUS_TABLES)
```

- [ ] **Step 4: Extend the CLI command**

In `rehoboam/cli.py`, `corpus_pull_cmd`: add the option and the second pull.

```python
@app.command("corpus-pull")
def corpus_pull_cmd(
    out: Path = typer.Option(  # noqa: B008
        Path("logs/training_corpus.db"),
        "--out",
        help="SQLite file for the corpus tables.",
    ),
    learning_out: Path = typer.Option(  # noqa: B008
        Path("logs/bid_learning.db"),
        "--learning-out",
        help="SQLite file for the replay's learning tables (flip_outcomes, "
        "matchday_lineup_results, league_rank_history).",
    ),
    dsn: str | None = typer.Option(
        None, "--dsn", help="Override DATABASE_URL for this run."
    ),
):
    """Write the corpus and the replay's learning tables from the store into local SQLite files."""
    from .store import StoreUnconfigured, connect
    from .store.corpus_pull import pull_corpus, pull_replay_tables

    try:
        with connect(dsn) as conn:
            written = pull_corpus(conn, out)
            replay_written = pull_replay_tables(conn, learning_out)
    except StoreUnconfigured as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from e
    for name, n in written.items():
        console.print(f"{name}: {n} row(s) written")
    console.print(f"[green]corpus written to {out}[/green]")
    for name, n in replay_written.items():
        console.print(f"{name}: {n} row(s) written")
    console.print(f"[green]replay tables written to {learning_out}[/green]")
```

Update `tests/store/test_cli.py::test_import_and_pull_commands_round_trip` to pass `--learning-out str(tmp_path / "pulled_learning.db")` and assert that file exists afterwards.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/store -q -p no:cacheprovider`
Expected: all pass (30).

- [ ] **Step 6: Commit**

```bash
git add rehoboam/store/corpus_pull.py rehoboam/cli.py tests/store/test_corpus_pull.py tests/store/test_cli.py
git commit -m "feat(store): corpus-pull writes the replay's three learning tables too"
```

______________________________________________________________________

### Translation rules for Tasks 3–5

These rules are the whole method for turning a SQLite method into a Postgres one. Every method in the three learners follows them; the task bodies list only the spots where a rule needs a decision.

| #   | SQLite                                                                           | Postgres                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| --- | -------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| R1  | `with sqlite3.connect(self.db_path) as conn:`                                    | `with self.connection() as conn:` — and delete every `conn.commit()`; the block commits on exit and rolls back on an exception                                                                                                                                                                                                                                                                                                                                        |
| R2  | `?`                                                                              | `%s`                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| R3  | `FROM pending_bids`, `INTO flip_outcomes`, `UPDATE x`, `DELETE FROM x`, `JOIN x` | the literal `rehoboam.pending_bids` etc. — in every statement, tests included                                                                                                                                                                                                                                                                                                                                                                                         |
| R4  | `INSERT OR IGNORE INTO t (...) VALUES (...)`                                     | `INSERT INTO rehoboam.t (...) VALUES (...) ON CONFLICT DO NOTHING`                                                                                                                                                                                                                                                                                                                                                                                                    |
| R5  | `INSERT OR REPLACE INTO t (...) VALUES (...)`                                    | `INSERT INTO rehoboam.t (...) VALUES (...) ON CONFLICT (<key columns>) DO UPDATE SET c1 = excluded.c1, c2 = excluded.c2, ...` for every non-key column in the insert list. Key columns per table: `pending_bids (player_id)`, `tracked_purchases (player_id)`, `recently_sold (player_id)`, `predicted_eps (player_id, predicted_at)`, `matchday_outcomes (player_id, matchday_date)`, `trade_proposals (proposal_id)`, `api_cache (kind, player_id, league_id, key)` |
| R6  | `conn.executemany(sql, rows)`                                                    | `with conn.cursor() as cur: cur.executemany(sql, rows)` — psycopg connections have no `executemany`                                                                                                                                                                                                                                                                                                                                                                   |
| R7  | `conn.row_factory = sqlite3.Row`                                                 | delete the line; rows are already dicts, `dict(row)` stays valid                                                                                                                                                                                                                                                                                                                                                                                                      |
| R8  | `row[0]`, `fetchone()[0]`, `for a, b in conn.execute(...)`, `x, y, z = row`      | give every selected expression a name in SQL (`select count(*) as n`, `select abs(transfer_price) as price`) and read `row["n"]`; iterate `for r in conn.execute(...): r["a"], r["b"]`                                                                                                                                                                                                                                                                                |
| R9  | `AVG(x)`, `SUM(x)` on integer columns                                            | `avg(x)::float8 as <name>`, `sum(x)::bigint as <name>` — Postgres returns `Decimal` for these otherwise and callers do arithmetic on the result                                                                                                                                                                                                                                                                                                                       |
| R10 | `cur.rowcount`                                                                   | unchanged for a single statement: 0/1 after `ON CONFLICT DO NOTHING`, matched rows after `UPDATE`/`DELETE`                                                                                                                                                                                                                                                                                                                                                            |
| R11 | `ORDER BY ..., rowid`                                                            | `trade_proposals` has no rowid; order by `proposal_id` as the tiebreak                                                                                                                                                                                                                                                                                                                                                                                                |
| R12 | `datetime(timestamp) > datetime('now', '-30 days')`                              | compute the ISO cutoff in Python (the column is ISO text such as `2026-09-01T10:00:00Z`, so the comparison is lexicographic): `cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")` and `timestamp > %s`                                                                                                                                                                                                                     |
| R13 | `_init_db` and every `CREATE TABLE` / `ALTER TABLE` / `DROP TABLE`               | deleted; the schema is `store/migrations/001_schema.sql`                                                                                                                                                                                                                                                                                                                                                                                                              |
| R14 | `import sqlite3`                                                                 | deleted from the module once no reference remains (ruff F401 will say)                                                                                                                                                                                                                                                                                                                                                                                                |

Test-porting rules:

| #   | in a test                                                                                                              | becomes                                                                                                                                                                    |
| --- | ---------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| T1  | `BidLearner(db_path=tmp_path / "x.db")` (or a `learner` fixture built that way)                                        | `BidLearner(dsn=store_dsn)`; add `store_dsn` to the test's or fixture's parameters — or use the shared `learner` fixture from `tests/conftest.py` and delete the local one |
| T2  | `with sqlite3.connect(learner.db_path) as conn: row = conn.execute("SELECT a, b FROM t WHERE x = ?", (v,)).fetchone()` | `with connect(store_dsn) as conn: row = conn.execute("select a, b from rehoboam.t where x = %s", (v,)).fetchone()` then `row["a"]` — `from rehoboam.store import connect`  |
| T3  | `conn.execute("INSERT INTO t (...) VALUES (?, ?)", ...)` seeding a table                                               | `%s` placeholders and `rehoboam.t`                                                                                                                                         |
| T4  | two learners on the same `db_path` to prove persistence                                                                | two `BidLearner(dsn=store_dsn)`                                                                                                                                            |
| T5  | a test asserting the file exists, the `logs/` directory is created, or the table exists after construction             | delete the assertion or the test; constructing a learner creates nothing                                                                                                   |
| T6  | `trader.learner.db_path`                                                                                               | `store_dsn` (the trader's learner was built on `DATABASE_URL`, which `store_dsn` set) — the test must request `store_dsn`                                                  |

______________________________________________________________________

### Task 3: `BidLearner` on the store — constructor and the operating tables

**Files:**

- Modify: `rehoboam/bid_learner.py` (lines 1–131 imports/constructor, 131–630 `_init_db` deleted, 632–921, 1270–1322, 1903–2102)
- Modify tests (T1–T6): `tests/test_approval_webhook.py`, `tests/test_approve_all_batch.py`, `tests/test_bid_state_db.py`, `tests/test_emergency_fill_approval.py`, `tests/test_manual_bids_are_not_cancelled.py`, `tests/test_min_hold_and_emergency_fill.py`, `tests/test_proposal_store.py`, `tests/test_tier_aware_bid_evaluation.py`, `tests/test_top5.py`, `tests/test_wash_trade_guard.py`, `tests/test_decline_recording_wiring.py`, `tests/test_proposal_wiring.py`, `tests/test_autonomous_buy_gate.py`, `tests/test_buy_decisions.py` (its `record_buy_decision` half; the rest in Task 4)

**Interfaces:**

- Consumes: `rehoboam.store.connect`.

- Produces:

  - `BidLearner.__init__(self, dsn: str | None = None)`; attribute `self.dsn`.
  - `BidLearner.connection(self)` — returns `connect(self.dsn)`; used as `with self.connection() as conn:`.
  - Every existing public method name and signature unchanged.

- [ ] **Step 1: Port the tests for this task's methods**

Apply T1–T6 to the files listed. Run `uv run pytest <those files> -q -p no:cacheprovider` and confirm they now fail with `TypeError: __init__() got an unexpected keyword argument 'dsn'` or `psycopg` errors — the new contract, not the old one.

- [ ] **Step 2: Rewrite the constructor and delete the DDL**

Replace lines 120–130 (`class BidLearner` through `self._init_db()`) and delete `_init_db` entirely (131–630):

```python
class BidLearner:
    """Learn from auction outcomes to improve bidding strategy.

    Every method is one transaction on the store: `with self.connection()`
    commits on a clean exit and rolls back on an exception, exactly the unit
    of work the SQLite era's `with sqlite3.connect(...)` gave. The schema is
    `store/migrations/001_schema.sql`; constructing a learner touches nothing.
    """

    def __init__(self, dsn: str | None = None):
        """`dsn` overrides DATABASE_URL — tests pass a fresh database.

        Resolution is lazy: a session that never records anything never needs
        the store, and a missing DATABASE_URL surfaces at the first write as
        StoreUnconfigured — or, for the paths that matter, at startup through
        `store.ensure_ready()`.
        """
        self.dsn = dsn

    def connection(self):
        """A connection with dict rows; one transaction per `with` block."""
        from rehoboam.store import connect

        return connect(self.dsn)
```

Remove `import sqlite3` and the now-unused `Path` import if nothing else uses them (ruff will say).

- [ ] **Step 3: Translate the operating-table methods**

Apply R1–R14 to: `record_outcome`, `record_flip`, `add_pending_bid`, `get_pending_bids`, `delete_pending_bid`, `add_tracked_purchase`, `get_tracked_purchase`, `delete_tracked_purchase`, `record_recent_sell`, `was_recently_sold`, `get_recent_sell`, `prune_recent_sells`, `record_buy_decision`, `record_proposal`, `pending_in_batch`, `due_auto_approvals`, `get_proposal`, `mark_proposal`, `set_proposal_status`, `record_forced_sale`, `forced_sale_settled`, `proposals_for_player`, `proposals_since`, `pending_proposals`. The decisions:

`record_flip` — the unique index is `(player_id, buy_date)`:

```python
        with self.connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO rehoboam.flip_outcomes (
                    player_id, player_name, buy_price, sell_price, profit, profit_pct,
                    hold_days, buy_date, sell_date, trend_at_buy, average_points, position,
                    was_injured, trend_pct_at_buy, mv_at_buy, pct_below_peak_30d_at_buy
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (player_id, buy_date) DO NOTHING
                """,
                ( ...same tuple as today... ),
            )
            return cur.rowcount > 0
```

`add_pending_bid` — R5 on `pending_bids`, then the sell-plan rows through a cursor:

```python
with self.connection() as conn:
    conn.execute(
        """
                INSERT INTO rehoboam.pending_bids (
                    player_id, player_name, our_bid, asking_price,
                    our_overbid_pct, timestamp, market_value, player_value_score, tier
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (player_id) DO UPDATE SET
                    player_name = excluded.player_name,
                    our_bid = excluded.our_bid,
                    asking_price = excluded.asking_price,
                    our_overbid_pct = excluded.our_overbid_pct,
                    timestamp = excluded.timestamp,
                    market_value = excluded.market_value,
                    player_value_score = excluded.player_value_score,
                    tier = excluded.tier
                """,
        (
            player_id,
            player_name,
            our_bid,
            asking_price,
            our_overbid_pct,
            timestamp,
            market_value,
            player_value_score,
            tier,
        ),
    )
    # Sell-plan rows are replaced wholesale: an upsert on pending_bids
    # alone would leave stale join rows behind.
    conn.execute(
        "DELETE FROM rehoboam.pending_bid_sell_plans WHERE pending_bid_player_id = %s",
        (player_id,),
    )
    if sell_plan_player_ids:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO rehoboam.pending_bid_sell_plans "
                "(pending_bid_player_id, sell_player_id) VALUES (%s, %s)",
                [(player_id, sp) for sp in sell_plan_player_ids],
            )
```

`get_pending_bids` — drop the `row_factory` line; `{**dict(row), ...}` stays.

`add_tracked_purchase`, `record_recent_sell` — R5 with key `(player_id)`, updating every other inserted column.

`was_recently_sold` — `row["sold_at"]`:

```python
with self.connection() as conn:
    row = conn.execute(
        "SELECT sold_at FROM rehoboam.recently_sold WHERE player_id = %s", (player_id,)
    ).fetchone()
return bool(row and row["sold_at"] >= cutoff)
```

`record_proposal` — R5 on `trade_proposals (proposal_id)`; `status` is inserted as the literal `'pending'` and must also be reset on conflict (`status = excluded.status`) so a re-proposal of the same id starts pending again, matching the SQLite replace semantics.

`pending_in_batch` — `ORDER BY created_at ASC, proposal_id ASC` (R11).

`record_forced_sale` — `ON CONFLICT DO NOTHING`, keep `return cur.rowcount > 0`.

- [ ] **Step 4: Run this task's tests**

Run: `uv run pytest tests/test_approval_webhook.py tests/test_approve_all_batch.py tests/test_bid_state_db.py tests/test_emergency_fill_approval.py tests/test_manual_bids_are_not_cancelled.py tests/test_min_hold_and_emergency_fill.py tests/test_proposal_store.py tests/test_tier_aware_bid_evaluation.py tests/test_top5.py tests/test_wash_trade_guard.py tests/test_decline_recording_wiring.py tests/test_proposal_wiring.py tests/test_autonomous_buy_gate.py -q -p no:cacheprovider`
Expected: all pass.

Then run the whole suite and record which files still fail. Expected: only files that call Task 4's methods (`tests/test_buy_decisions.py` partially, `tests/test_cost_basis_reconciliation.py`, `tests/test_enrichment/test_historical_ids.py`, `tests/test_flip_entry_context_persistence.py`, `tests/test_high_bidder_losses.py`, `tests/test_league_rank_history.py`, `tests/test_manager_profile_history.py`, `tests/test_matchday_lineup_results.py`, `tests/test_matchday_outcomes.py`, `tests/test_matchday_reconciliation.py`, `tests/test_mv_backfill.py`, `tests/test_overbid_floor_precedence.py`, `tests/test_pacing_prices.py`, `tests/test_player_mv_history.py`, `tests/test_team_value_history.py`, `tests/test_backfill.py`, `tests/test_backtest/test_baseline_driver.py`) and Task 5/6's (`tests/test_ep_bidding.py`, `tests/store/test_import_sqlite.py`, `tests/test_state_migration.py`). Anything else failing is a defect in this task — fix it before committing. The 17 trader test files that build `AutoTrader` without a learner run against the shared database and must pass here.

- [ ] **Step 5: Commit**

```bash
git add rehoboam/bid_learner.py tests/
git commit -m "feat(learner): BidLearner is a Postgres client — constructor, operating tables"
```

______________________________________________________________________

### Task 4: `BidLearner` on the store — league, history and calibration tables; the two file readers

**Files:**

- Modify: `rehoboam/bid_learner.py` (remaining methods: 923–1260, 1323–1620, 1624–1900)
- Modify: `rehoboam/mv_backfill.py:42-47`, `rehoboam/enrichment/historical_ids.py`, `rehoboam/cli.py` (`backfill-flip-entry-context` at ~526–556 and `enrich-corpus` at ~486)
- Modify tests (T1–T6): the Task 4 list from Task 3 Step 4.

**Interfaces:**

- Consumes: `BidLearner.connection()` (Task 3).

- Produces:

  - `rehoboam.enrichment.historical_ids.gather_historical_player_ids(learner: BidLearner) -> list[str]` (was `db_path: Path`).
  - `rehoboam.mv_backfill._distinct_flip_player_ids(learner)` reads through `learner.connection()`.
  - `BidLearner.flip_entry_context_coverage() -> tuple[int, int]` — `(annotated, total)` over `flip_outcomes`, for the CLI line that used a raw SQLite count.

- [ ] **Step 1: Port the tests**

Apply T1–T6 to every file in the Task 4 list. `tests/test_backtest/test_baseline_driver.py` is different: it builds a SQLite learning **file** for `run_baseline` to read. Replace its `BidLearner(db_path=...)` construction with `create_replay_tables(path)` from Task 2 plus direct `sqlite3` inserts into `matchday_lineup_results` and `flip_outcomes` carrying the same values the learner calls used (`lineup_player_ids` is `json.dumps([...])`). `tests/test_enrichment/test_historical_ids.py` passes a learner instead of a path and seeds through learner methods (`record_flip`, `record_player_mv_snapshot`, `record_matchday_lineup_result`). Run the files; they must fail on the new contract.

- [ ] **Step 2: Translate the methods**

Apply R1–R14 to: `snapshot_predictions`, `get_latest_prediction_before`, `record_team_value_snapshot`, `mv_history_for`, `backfill_flip_entry_context`, `record_player_mv_snapshot`, `has_matchday_lineup_result`, `record_matchday_lineup_result`, `record_league_rank_snapshot`, `record_manager_profile_snapshot`, `resolve_auction_winners`, `high_bidder_losses`, `record_manager_transfers`, `get_last_purchase`, `recent_buy_prices`, `has_matchday_outcome`, `record_matchday_outcome`, `get_position_calibration_multiplier`, `_decayed_accuracy`, `_get_won_player_outcome_quality`, `get_ep_recommended_overbid`. The decisions:

`snapshot_predictions` — R5 on `predicted_eps (player_id, predicted_at)` via `cur.executemany`, updating `league_id, predicted_ep, position, was_in_best_11, marginal_ep_gain`. Keep `return len(rows)`.

`record_matchday_outcome` — R5 on `matchday_outcomes (player_id, matchday_date)`, updating `player_position, predicted_ep, actual_points, was_in_best_11, opponent_strength, purchase_price, marginal_ep_gain_at_purchase`. Do not touch `timestamp`; the column default sets it.

`mv_history_for` — R8:

```python
with self.connection() as conn:
    rows = conn.execute(
        "SELECT snapshot_at, market_value FROM rehoboam.player_mv_history "
        "WHERE player_id = %s ORDER BY snapshot_at",
        (str(player_id),),
    ).fetchall()
return [(float(r["snapshot_at"]), int(r["market_value"])) for r in rows]
```

`backfill_flip_entry_context` — the loop calls `self.mv_history_for(...)`, which opens a second connection inside the first's transaction; that is fine (reads only) and stays.

`resolve_auction_winners` — R8 everywhere: `for row in pending: row_id = row["id"]; player_id = row["player_id"]; ts = row["timestamp"]; our_bid = row["our_bid"]; asking = row["asking_price"]; player_name = row["player_name"]`, and the inner loop `for t in conn.execute(...)` reads `t["manager_id"], t["transfer_price"], t["transfer_dt"]`. Iterating a cursor while executing an UPDATE on the same connection is not allowed in psycopg: collect the inner query with `.fetchall()` before the loop body runs the UPDATE.

`recent_buy_prices` — `SELECT ABS(transfer_price) AS price ...` and `[int(r["price"]) for r in rows]`.

`get_position_calibration_multiplier` and `_decayed_accuracy` — the loop `for predicted_ep, actual_points, ts in rows:` becomes `for r in rows:` with `r["predicted_ep"], r["actual_points"], r["timestamp"]`.

`_get_won_player_outcome_quality` — R8 + R9:

```python
            row = conn.execute(
                """
                SELECT COUNT(*) AS n,
                       AVG(mo.actual_points)::float8 AS avg_actual,
                       AVG(mo.predicted_ep)::float8 AS avg_predicted
                FROM rehoboam.matchday_outcomes mo
                INNER JOIN rehoboam.auction_outcomes ao ON ao.player_id = mo.player_id
                WHERE ao.won = 1 AND mo.predicted_ep > 0
                """
            ).fetchone()
        if not row:
            return 1.0
        count, avg_actual, avg_predicted = row["n"], row["avg_actual"], row["avg_predicted"]
```

`get_ep_recommended_overbid` (the query near line 1835) — name both aggregates (`COUNT(*) AS total_auctions, SUM(won)::bigint AS total_wins`, or whatever the two columns are) and read them by name; keep the `(0, 0)` fallback semantics with `row["total_wins"] or 0`.

Docstrings that say "sqlite3 doesn't expose per-row outcomes from executemany" (`record_league_rank_snapshot`, `record_manager_profile_snapshot`) — rewrite the sentence: "Returns the number of rows attempted; conflicts are silent, tests read back."

Add the coverage helper (next to `backfill_flip_entry_context`):

```python
def flip_entry_context_coverage(self) -> tuple[int, int]:
    """(flips carrying entry context, flips in total)."""
    with self.connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS total, COUNT(mv_at_buy) AS annotated FROM rehoboam.flip_outcomes"
        ).fetchone()
    return int(row["annotated"]), int(row["total"])
```

- [ ] **Step 3: The two file readers and the CLI**

`rehoboam/mv_backfill.py`:

```python
def _distinct_flip_player_ids(learner: BidLearner) -> list[str]:
    with learner.connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT player_id FROM rehoboam.flip_outcomes ORDER BY player_id"
        ).fetchall()
    return [r["player_id"] for r in rows]
```

`rehoboam/enrichment/historical_ids.py`: signature `gather_historical_player_ids(learner: "BidLearner") -> list[str]`; body uses `with learner.connection() as conn:` and `rehoboam.`-qualified tables; `for r in conn.execute(...)` reading `r["player_id"]` / `r["lineup_player_ids"]`. Update the module docstring's "bid_learning.db" wording to "the store"; drop the `sqlite3` import; the malformed-JSON paragraph stays true (a row can still be hand-edited in Supabase) — reword "opening the SQLite file directly" to "editing a row in the Supabase dashboard".

`rehoboam/cli.py`:

- `enrich-corpus` (~486): `extra_player_ids = gather_historical_player_ids(BidLearner())` and the message `f"[dim]Recovered {len(extra_player_ids)} historical player ids from the store[/dim]"`.

- `backfill-flip-entry-context` (~526–556): remove the `learning_db` option and the file-existence check; `learner = BidLearner()`; replace the `sqlite3.connect` block with `annotated, total = learner.flip_entry_context_coverage()`. Remove `import sqlite3` from `cli.py` if nothing else uses it.

- `backfill-mv-history` and `backfill-history` docstrings: replace the three-line "fetch-azure-state / … / push-azure-state" workflow with one line: "Writes straight to the store; run during a quiet window between Function sessions."

- [ ] **Step 4: Run the suite**

Run: `rm -f logs/bid_learning.db logs/player_history.db; uv run pytest -q -p no:cacheprovider`
Expected: only `tests/test_ep_bidding.py`, `tests/store/test_import_sqlite.py` and `tests/test_state_migration.py` fail (Task 5/6). `grep -n "sqlite3\|db_path" rehoboam/bid_learner.py` prints nothing.

- [ ] **Step 5: Commit**

```bash
git add rehoboam/bid_learner.py rehoboam/mv_backfill.py rehoboam/enrichment/historical_ids.py rehoboam/cli.py tests/
git commit -m "feat(learner): BidLearner on the store — history, league and calibration tables; file readers go through the learner"
```

______________________________________________________________________

### Task 5: `ActivityFeedLearner` and `ValueHistoryCache` on the store

**Files:**

- Modify: `rehoboam/activity_feed_learner.py`
- Modify: `rehoboam/value_history.py`
- Modify: `tests/test_ep_bidding.py` (`TestAggressiveCompetitorsHelper`, ~390–430)
- Modify: `tests/store/test_import_sqlite.py` (its SQLite source fixtures)
- Test: new `tests/test_activity_feed_learner.py`, new `tests/test_value_history_cache.py`

**Interfaces:**

- Consumes: `rehoboam.store.connect`, `psycopg.types.json.Jsonb`.

- Produces:

  - `ActivityFeedLearner.__init__(self, dsn: str | None = None)`; `connection()` as on `BidLearner`; every public method unchanged.
  - `ValueHistoryCache.__init__(self, dsn: str | None = None)`; `connection()`; `get_cached_history`, `cache_history`, `get_trend_analysis`, `get_cached_performance`, `cache_performance`, `cleanup_old_cache` unchanged in signature. Row mapping in `rehoboam.api_cache`: market-value history is `kind='mv', key=str(timeframe)`; performance is `kind='performance', key=''` — the mapping `import_cache` already uses, so imported rows and live rows are the same rows.

- [ ] **Step 1: Write the failing tests**

`tests/test_value_history_cache.py`:

```python
"""ValueHistoryCache over rehoboam.api_cache: the TTL and the replace-on-refetch semantics."""

from __future__ import annotations

from rehoboam.store import connect
from rehoboam.value_history import ValueHistoryCache


def test_performance_round_trip_and_ttl(store_dsn):
    cache = ValueHistoryCache(dsn=store_dsn)
    assert cache.get_cached_performance("p1", "L") is None
    cache.cache_performance("p1", "L", {"it": [{"ti": "2026/2027"}]})
    assert cache.get_cached_performance("p1", "L")["it"][0]["ti"] == "2026/2027"
    assert cache.get_cached_performance("p1", "L", max_age_hours=0) is None


def test_history_is_keyed_by_timeframe_and_refetch_replaces(store_dsn):
    cache = ValueHistoryCache(dsn=store_dsn)
    cache.cache_history("p1", "L", 30, {"it": [1]})
    cache.cache_history("p1", "L", 365, {"it": [2]})
    cache.cache_history("p1", "L", 30, {"it": [3]})
    assert cache.get_cached_history("p1", "L", timeframe=30)["it"] == [3]
    assert cache.get_cached_history("p1", "L", timeframe=365)["it"] == [2]
    with connect(store_dsn) as conn:
        rows = conn.execute(
            "select kind, key from rehoboam.api_cache where player_id = 'p1' order by key"
        ).fetchall()
    assert [(r["kind"], r["key"]) for r in rows] == [("mv", "30"), ("mv", "365")]


def test_cleanup_deletes_old_rows_of_both_kinds(store_dsn):
    cache = ValueHistoryCache(dsn=store_dsn)
    cache.cache_history("p1", "L", 30, {"it": []})
    cache.cache_performance("p1", "L", {"it": []})
    assert cache.cleanup_old_cache(days_to_keep=0) == 2
```

`tests/test_activity_feed_learner.py`:

```python
"""ActivityFeedLearner on the store: dedupe by activity id, and the windowed readers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from rehoboam.activity_feed_learner import ActivityFeedLearner


def _feed(
    activity_id: str, *, when: str, price: int = 5_000_000, buyer: str = "Rival"
) -> dict:
    return {
        "af": [
            {
                "i": activity_id,
                "t": 15,
                "dt": when,
                "data": {
                    "pi": "p1",
                    "pn": "One",
                    "byr": buyer,
                    "slr": "Kickbase",
                    "trp": price,
                    "t": 1,
                },
            }
        ]
    }


def test_transfers_dedupe_by_activity_id(store_dsn):
    learner = ActivityFeedLearner(dsn=store_dsn)
    first = learner.process_activity_feed(_feed("a1", when="2026-09-01T10:00:00Z"))
    second = learner.process_activity_feed(_feed("a1", when="2026-09-01T10:00:00Z"))
    assert (first["transfers_new"], second["transfers_duplicate"]) == (1, 1)
    stats = learner.get_competitive_bidding_stats("p1")
    assert stats["total_transfers"] == 1 and stats["avg_transfer_price"] == 5_000_000


def test_demand_score_counts_only_the_last_30_days(store_dsn):
    learner = ActivityFeedLearner(dsn=store_dsn)
    recent = (datetime.now(tz=timezone.utc) - timedelta(days=2)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    old = (datetime.now(tz=timezone.utc) - timedelta(days=40)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    learner.process_activity_feed(_feed("a1", when=recent))
    learner.process_activity_feed(_feed("a2", when=old))
    assert learner.get_player_demand_score("p1") == 60.0


def test_top_competitors_threat_score_is_a_float(store_dsn):
    learner = ActivityFeedLearner(dsn=store_dsn)
    for i in range(3):
        learner.process_activity_feed(
            _feed(f"a{i}", when="2026-09-01T10:00:00Z", price=20_000_000, buyer="Whale")
        )
    top = learner.get_top_competitors(limit=1)[0]
    assert top["name"] == "Whale" and isinstance(top["threat_score"], float)
    assert learner.has_aggressive_competitors() is False  # 3*10 + 20 = 50, not > 100
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_value_history_cache.py tests/test_activity_feed_learner.py -q -p no:cacheprovider`
Expected: FAIL with `unexpected keyword argument 'dsn'`.

- [ ] **Step 3: Rewrite `ActivityFeedLearner`**

Constructor and `connection()` exactly as on `BidLearner` (Task 3 Step 2), `_init_db` deleted. Then R1–R14 on `_process_transfer`, `_process_market_value`, `get_competitive_bidding_stats`, `get_player_demand_score`, `get_competitor_analysis`, `get_top_competitors`. Decisions:

- `_process_transfer` / `_process_market_value`: the "already have this activity" SELECT followed by INSERT becomes one statement — `INSERT INTO rehoboam.league_transfers (...) VALUES (...) ON CONFLICT (activity_id) DO NOTHING` and `return "new" if cur.rowcount else "duplicate"` (same for `market_value_snapshots`). The `activity_id` column is `unique` in the schema.

- Aggregates (R9): `AVG(transfer_price)::float8 AS avg_price, MIN(transfer_price) AS min_price, MAX(transfer_price) AS max_price, COUNT(*) AS n`; `GROUP BY buyer_name ... COUNT(*) AS purchases, AVG(transfer_price)::float8 AS avg_price`.

- `datetime('now', '-30 days')` and `'-7 days'` (R12): compute the cutoff in Python and compare `timestamp > %s`.

- `LIMIT ?` → `LIMIT %s`.

- `threat_score = (purchases * 10) + (avg_price / 1_000_000)` stays; with the `::float8` cast it is a float.

- [ ] **Step 4: Rewrite `ValueHistoryCache`**

Replace the module body:

```python
"""Per-player API response cache on the store (rehoboam.api_cache).

One row per (kind, player, league, key), payload as jsonb. Market-value
history is kind ``mv`` keyed by the timeframe in days; performance is kind
``performance`` with an empty key — the same mapping ``store.import_sqlite``
used to fold the SQLite-era files in, so imported rows and live rows are
the same rows. Read one row at a time: the point of the table is that a
session never downloads a 27 MB cache file to look up a dozen players.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from psycopg.types.json import Jsonb


class ValueHistoryCache:
    """Caches per-player API responses to minimize Kickbase calls."""

    def __init__(self, dsn: str | None = None):
        self.dsn = dsn

    def connection(self):
        from rehoboam.store import connect

        return connect(self.dsn)

    def _get(self, kind: str, player_id: str, league_id: str, key: str, cutoff: float):
        with self.connection() as conn:
            row = conn.execute(
                "SELECT payload FROM rehoboam.api_cache "
                "WHERE kind = %s AND player_id = %s AND league_id = %s AND key = %s "
                "AND fetched_at >= %s",
                (kind, str(player_id), str(league_id), key, cutoff),
            ).fetchone()
        return row["payload"] if row else None

    def _put(
        self, kind: str, player_id: str, league_id: str, key: str, data: dict[str, Any]
    ):
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO rehoboam.api_cache "
                "(kind, player_id, league_id, key, fetched_at, payload) "
                "VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (kind, player_id, league_id, key) DO UPDATE SET "
                "fetched_at = excluded.fetched_at, payload = excluded.payload",
                (
                    kind,
                    str(player_id),
                    str(league_id),
                    key,
                    datetime.now().timestamp(),
                    Jsonb(data),
                ),
            )

    def get_cached_history(
        self,
        player_id: str,
        league_id: str,
        timeframe: int = 30,
        max_age_hours: int = 24,
    ) -> dict[str, Any] | None:
        cutoff = (datetime.now() - timedelta(hours=max_age_hours)).timestamp()
        return self._get("mv", player_id, league_id, str(timeframe), cutoff)

    def cache_history(
        self, player_id: str, league_id: str, timeframe: int, data: dict[str, Any]
    ):
        self._put("mv", player_id, league_id, str(timeframe), data)

    def get_trend_analysis(
        self, history_data: dict[str, Any], current_market_value: int = 0
    ) -> dict[str, Any]:
        """DEPRECATED: use TrendService.analyze(); kept for remaining callers."""
        from .services.trend_service import TrendService

        return TrendService.analyze(history_data, current_market_value).to_dict()

    def get_cached_performance(
        self, player_id: str, league_id: str, max_age_hours: int = 6
    ) -> dict[str, Any] | None:
        cutoff = (datetime.now() - timedelta(hours=max_age_hours)).timestamp()
        return self._get("performance", player_id, league_id, "", cutoff)

    def cache_performance(self, player_id: str, league_id: str, data: dict[str, Any]):
        self._put("performance", player_id, league_id, "", data)

    def cleanup_old_cache(self, days_to_keep: int = 7) -> int:
        cutoff = (datetime.now() - timedelta(days=days_to_keep)).timestamp()
        with self.connection() as conn:
            cur = conn.execute(
                "DELETE FROM rehoboam.api_cache WHERE fetched_at < %s", (cutoff,)
            )
            return cur.rowcount
```

Note `fetched_at` is now a float epoch (the column is `double precision`); the SQLite era stored ints, which compare fine.

- [ ] **Step 5: Fix the tests that built SQLite files through these classes**

`tests/test_ep_bidding.py::TestAggressiveCompetitorsHelper`: `ActivityFeedLearner(dsn=store_dsn)`; the seeding loop inserts through `connect(store_dsn)` with `%s` into `rehoboam.league_transfers` (same columns as today's INSERT).

`tests/store/test_import_sqlite.py`: `_learning_db` and `_cache_db` can no longer create SQLite schemas through the learners. Give the test module its own minimal SQLite DDL for exactly the tables it inserts into — `pending_bids`, `buy_decisions`, `flip_outcomes`, `league_rank_history`, `league_transfers`, `predicted_eps`, `performance_cache`, `market_value_cache` — as a `_SQLITE_DDL` string (`create table` statements listing the columns those inserts use, plus `id integer primary key autoincrement` on `buy_decisions`, `flip_outcomes`, `league_transfers`), applied with `executescript`. The `predicted_eps` row is inserted with plain SQL instead of `snapshot_predictions`; the cache rows with plain SQL (`data` as `json.dumps(...)`, `fetched_at` an int). The assertions do not change: the import copies what the SQLite file holds.

- [ ] **Step 6: Run the suite**

Run: `rm -f logs/bid_learning.db logs/player_history.db; uv run pytest -q -p no:cacheprovider`
Expected: only `tests/test_state_migration.py` fails (deleted in Task 6). `grep -rn "sqlite3" rehoboam/bid_learner.py rehoboam/activity_feed_learner.py rehoboam/value_history.py` prints nothing.

- [ ] **Step 7: Commit**

```bash
git add rehoboam/activity_feed_learner.py rehoboam/value_history.py tests/test_ep_bidding.py tests/store/test_import_sqlite.py tests/test_activity_feed_learner.py tests/test_value_history_cache.py
git commit -m "feat(learner): the activity feed and the API cache live in the store"
```

______________________________________________________________________

### Task 6: Cut the cord — Function app, CLI startup, blob sync and JSON-era code deleted, compliance reads the learner

**Files:**

- Modify: `deploy/azure_function/function_app.py`
- Modify: `rehoboam/cli.py` (`auto`, `status`, `migrate`, `db-bootstrap`, `import-sqlite`; delete `fetch-azure-state`, `push-azure-state` and their renderers)
- Delete: `rehoboam/azure_blob.py`, `tests/test_azure_blob.py`, `rehoboam/learning/migration.py`, `tests/test_state_migration.py`
- Modify: `rehoboam/learning/tracker.py` (constructor), `rehoboam/config.py` (delete `AzureBlobSettings`), `rehoboam/league_compliance.py`, `rehoboam/auto_trader.py` (~2298, pass the learner)
- Modify: `deploy/azure_function/local.settings.json.example`
- Test: `tests/test_league_compliance_cost_basis.py` (new), `tests/test_cli_store_startup.py` (new)

**Interfaces:**

- Consumes: `rehoboam.store.ensure_ready`, `Settings.database_admin_url`, `BidLearner.get_tracked_purchase`, `BidLearner.delete_tracked_purchase`.

- Produces:

  - `LeagueComplianceChecker.__init__(self, api, settings, learner: BidLearner | None = None)`; `self.learner = learner or BidLearner()`.
  - `rehoboam.cli._ensure_store() -> None` — prints a red one-liner and exits 1 on `StoreUnconfigured`, `PermissionError` or `psycopg.OperationalError`.
  - `rehoboam.cli._admin_dsn(explicit: str | None) -> str | None`.

- [ ] **Step 1: Write the failing tests**

`tests/test_league_compliance_cost_basis.py`:

```python
"""The compliance checker's cost basis comes from tracked_purchases, not a JSON file."""

from __future__ import annotations

from types import SimpleNamespace

from rehoboam.bid_learner import BidLearner
from rehoboam.league_compliance import LeagueComplianceChecker


def _player(pid: str, mv: int):
    return SimpleNamespace(id=pid, first_name="A", last_name=pid, market_value=mv)


def test_purchase_below_market_value_is_an_issue(store_dsn):
    learner = BidLearner(dsn=store_dsn)
    learner.add_tracked_purchase(
        player_id="p1", player_name="A p1", buy_price=1_000_000, buy_date=1.0
    )
    api = SimpleNamespace(
        get_squad=lambda league: [_player("p1", 1_500_000), _player("p2", 9)]
    )
    checker = LeagueComplianceChecker(api, settings=None, learner=learner)
    issues = checker.check_market_value_compliance(league=None)
    assert [i.player_id for i in issues] == ["p1"]
    assert issues[0].violation_amount == 500_000


def test_resolving_an_issue_forgets_the_purchase(store_dsn):
    learner = BidLearner(dsn=store_dsn)
    learner.add_tracked_purchase(
        player_id="p1", player_name="A p1", buy_price=1, buy_date=1.0
    )
    listed: list[tuple] = []
    api = SimpleNamespace(
        get_squad=lambda league: [_player("p1", 2)],
        list_player=lambda **kw: listed.append(tuple(sorted(kw.items()))),
    )
    league = SimpleNamespace(id="L")
    checker = LeagueComplianceChecker(api, settings=None, learner=learner)
    sold = checker.resolve_compliance_issues(
        league, checker.check_market_value_compliance(league)
    )
    assert sold == 1 and len(listed) == 1
    assert learner.get_tracked_purchase("p1") is None
```

`tests/test_cli_store_startup.py`:

```python
"""auto and status refuse to start without a reachable, migrated store."""

from __future__ import annotations

from typer.testing import CliRunner

from rehoboam.cli import app

runner = CliRunner()


def test_status_without_database_url_fails_before_login(monkeypatch):
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "test")
    monkeypatch.setenv("DATABASE_URL", "")
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 1
    assert "DATABASE_URL" in result.output
    assert "Traceback" not in result.output


def test_auto_without_database_url_fails_before_login(monkeypatch):
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "test")
    monkeypatch.setenv("DATABASE_URL", "")
    result = runner.invoke(app, ["auto", "--dry-run"])
    assert result.exit_code == 1
    assert "DATABASE_URL" in result.output
```

(`Settings.model_config["env_file"]` may still point at the worktree's `.env`; that file's `DATABASE_URL` is overridden by the env var, which is the guarantee Task 1's autouse fixture relies on. If `get_settings()` fails for another required field, set it in the test the way `tests/store/test_cli.py` does.)

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_league_compliance_cost_basis.py tests/test_cli_store_startup.py -q -p no:cacheprovider`
Expected: FAIL (`unexpected keyword argument 'learner'`; `status` reaches the login and fails differently).

- [ ] **Step 3: The compliance checker**

In `rehoboam/league_compliance.py`:

```python
def __init__(self, api, settings, learner=None):
    """
    Args:
        api: KickbaseAPI instance
        settings: Bot settings
        learner: BidLearner holding tracked_purchases (the cost basis); built on demand
    """
    from .bid_learner import BidLearner

    self.api = api
    self.settings = settings
    self.learner = learner or BidLearner()
```

`check_market_value_compliance`: delete the `json`/`Path` imports and the `purchases_file` block; in the loop:

```python
for player in my_team:
    purchase = self.learner.get_tracked_purchase(player.id)
    if purchase is None:
        # Not tracked (bought manually or before tracking started)
        continue
    purchase_price = int(purchase["buy_price"])
    current_market_value = player.market_value
```

`resolve_compliance_issues`: replace the JSON block after the successful listing with `self.learner.delete_tracked_purchase(issue.player_id)`. In `rehoboam/auto_trader.py` (~2298) pass `learner=self.learner` to the constructor.

- [ ] **Step 4: The Function app**

In `deploy/azure_function/function_app.py`:

- Delete `_blob_settings`, `download_databases`, `upload_databases`, `_APPROVAL_DB_FILES`, and the `Collection` import.
- `trading_session`: replace `download_databases()` with

```python
        from rehoboam.store import ensure_ready

        # The store must be reachable and fully migrated before anything else
        # runs: a session against a half-migrated schema must not start.
        ensure_ready()
```

and delete the `upload_databases()` call and its comment. The surrounding `try/except Exception` already logs a failure as "Trading session failed" with the traceback.

- `telegram_approval`: delete `downloaded = False`, the `download_databases(only=...)` call, and the whole `finally:` block that uploaded; insert `ensure_ready()` (same import) as the first statement inside the `try:` that logs in. Every learner write inside `handle_callback` commits on its own, so the "NOT SAVED - do not tap again" prefix has no remaining meaning and goes with the block. Update the module docstring's mention of blob storage.

- [ ] **Step 5: The CLI**

In `rehoboam/cli.py`:

- Delete the `from . import azure_blob` and `AzureBlobSettings` imports, `_FETCH_STATUS_STYLE`, `_PUSH_STATUS_STYLE`, `_render_fetch_table`, `_render_push_table`, `fetch_azure_state`, `push_azure_state`, and `_fmt_dt`/`_fmt_size` if no other command uses them (grep).
- Add near `_get_api`:

```python
def _ensure_store() -> None:
    """Refuse to start a session the store cannot serve; say why in one line."""
    import psycopg

    from .store import StoreUnconfigured, ensure_ready

    try:
        ensure_ready()
    except StoreUnconfigured as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from e
    except PermissionError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from e
    except psycopg.OperationalError as e:
        console.print(f"[red]store unreachable: {e}[/red]")
        raise typer.Exit(code=1) from e


def _admin_dsn(explicit: str | None) -> str | None:
    """DATABASE_ADMIN_URL when set; None lets connect() fall back to DATABASE_URL."""
    if explicit:
        return explicit
    return get_settings().database_admin_url or None
```

- Call `_ensure_store()` as the first statement of the `auto` and `status` command bodies (before `_get_api()` / login).

- `migrate_cmd`: `with connect(_admin_dsn(dsn)) as conn:`; help text "Override DATABASE_ADMIN_URL / DATABASE_URL for this run."

- `db_bootstrap_cmd`: drop `envvar="DATABASE_ADMIN_URL"` from the option (Settings now reads it from `.env` too) and use `connect(_admin_dsn(admin_dsn))`.

- `import_sqlite_cmd`: `connect(_admin_dsn(dsn))` as well — the import calls `migrate` and moves sequences.

- [ ] **Step 6: Delete the JSON-era code and the blob module**

```bash
git rm rehoboam/azure_blob.py tests/test_azure_blob.py rehoboam/learning/migration.py tests/test_state_migration.py
```

`rehoboam/learning/tracker.py`: delete the `from .migration import ...` line, the `_LOG_DIR`/`_PENDING_BIDS_JSON`/`_TRACKED_PURCHASES_JSON` constants, and the `try: migrate_json_state_if_needed(...)` block in `__init__` (the constructor keeps `self.bid_learner = bid_learner`). Remove the `Path` import if unused.

`rehoboam/config.py`: delete `class AzureBlobSettings` (everything after `get_settings`). Check `tests/test_config.py` for references and remove those tests.

`deploy/azure_function/local.settings.json.example`: add `"DATABASE_URL": "postgresql://rehoboam_bot.<project-ref>:<password>@<pooler-host>:6543/postgres"`; leave `AzureWebJobsStorage` (the Functions runtime needs it) and remove `AZURE_STORAGE_CONNECTION_STRING`/`BLOB_CONTAINER`. The Bicep app settings for those two stay: PR C's export writes to that container.

- [ ] **Step 7: Run everything**

Run: `rm -f logs/bid_learning.db logs/player_history.db; uv run pytest -q -p no:cacheprovider`
Expected: all pass. Then:

```bash
grep -rn "sqlite3" rehoboam --include='*.py' | grep -v 'enrichment/corpus.py\|store/corpus_pull.py\|store/import_sqlite.py\|backtest/\|replay/\|diagnostics/\|scoring/v2/dataset.py'
```

Expected: no output — the only SQLite left is the corpus writer, the two store tools that read or write local files, and the offline readers.

```bash
grep -rn "azure_blob\|fetch_state\|push_state\|AzureBlobSettings\|tracked_purchases.json\|pending_bids.json" rehoboam deploy tests
```

Expected: no output. `uvx ruff check rehoboam/` and `uvx --from "black>=25.1,<26" black --check --line-length=100 rehoboam/` clean; `ls logs/` shows no `.db` file created by the run.

- [ ] **Step 8: Commit**

```bash
git add -A rehoboam deploy tests
git commit -m "feat(store): the store is the bot's only state — blob sync and JSON-era code deleted; sessions start with ensure_ready"
```

______________________________________________________________________

### Task 7: Docs, then the whole-branch verification

**Files:**

- Modify: `CLAUDE.md`

- [ ] **Step 1: CLAUDE.md**

1. In the "Run the CLI" comment and command list: remove the `fetch-azure-state` and `push-azure-state` lines; change the `corpus-pull` line's comment to "Materialise the corpus + the replay's learning tables into logs/\*.db for replay/backtest".
1. Replace the whole "## Prod-state debugging workflow (REH-15 / REH-39)" section with a "## Store workflow (data foundation PR B2)" section saying: the bot's state lives in Supabase Postgres, schema `rehoboam`; local runs and prod use the same database through the same bot-role `DATABASE_URL`; there is no file to fetch or push. Show three commands in a fenced block — inspecting with any psql client on `DATABASE_URL`; schema changes as "add `store/migrations/NNN_<name>.sql`, then `uv run rehoboam migrate` as the admin (`DATABASE_ADMIN_URL`) BEFORE deploying code"; and `uv run rehoboam corpus-pull` writing `logs/training_corpus.db` + `logs/bid_learning.db` for the offline tools. Close with: a local `status` run writes its learning snapshots (predicted EPs, rank history, MV history) into the live tables by design; `import-sqlite` remains only for the SQLite-era files, nothing produces new ones.
1. In "Learning System": "`BidLearner`: SQLite writer + reader for all learning tables" → "`BidLearner`: the store's writer + reader for all learning tables (one transaction per call)".
1. In "The store" bullets: replace "The bot's live read/write path still uses the SQLite files until PR B2." with "PR B2 (2026-09-13): `BidLearner`, `ActivityFeedLearner` and `ValueHistoryCache` are Postgres clients; the Function app and the `auto`/`status` commands call `store.ensure_ready()` first and refuse to run without a migrated store; the blob sync is gone. Tests share one PostgreSQL: `store_dsn` is a fresh migrated database, and an autouse fixture pins `DATABASE_URL` so no test can reach the real project." Add to the `corpus_pull` bullet: "and the three replay tables into `bid_learning.db`".
1. In "Current state": add a bullet "**The store is live (2026-09-13)**: every learner writes to Supabase Postgres; `DATABASE_URL` is required at startup."
1. `grep -n -i blob CLAUDE.md` and fix any remaining sentence that implies a blob download.

- [ ] **Step 2: Whole-branch verification (record every number)**

```bash
rm -f logs/bid_learning.db logs/player_history.db
uv run pytest -q -p no:cacheprovider                       # expect: all passed, 1 skipped (test_config)
uvx --from "black>=25.1,<26" black --check --line-length=100 rehoboam/
uvx ruff check rehoboam/
bash scripts/sync-azure-deps.sh && git diff --exit-code deploy/azure_function/requirements.txt
uv run bandit -r rehoboam/ -c pyproject.toml -q | tail -6   # no new HIGH; B608 count must not rise
uv run mypy rehoboam/store rehoboam/bid_learner.py --ignore-missing-imports | tail -1  # informational
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: the store is the bot's state; the blob workflow is gone"
```

______________________________________________________________________

### Task 8: Cut-over (controller-run, not a subagent task)

Prod-touching; every step is Marco-approved in the 2026-09-13 design. Run from the worktree unless stated.

- [ ] **Step 1: Local `.env`** — set `DATABASE_URL` to the bot-role pooler URI (the value currently in `DATABASE_BOT_URL`) and `DATABASE_ADMIN_URL` to the current admin URI. Verify without printing secrets: `uv run rehoboam migrate` prints `applied 0 migration(s): none`; a one-line Python `select current_user` through `connect()` prints `rehoboam_bot.<ref>`.
- [ ] **Step 2: Infra deploy before the merge** — `bash deploy/deploy.sh infra --what-if` must show only the `database-url` secret and the two apps' `DATABASE_URL` setting changing; then `bash deploy/deploy.sh infra`. Verify: `az functionapp config appsettings list -n func-rehoboam -g rg-rehoboam --query "[?name=='DATABASE_URL'].name" -o tsv` prints `DATABASE_URL`. **Then republish the current main code** (`bash deploy/deploy.sh code trading`): an infra deploy can reset the package (see deploy.sh's header comment).
- [ ] **Step 3: Final import in the quiet window** (after 08:05 UTC, before 19:55 UTC) from the main checkout, which still has the B1 commands: `uv run --directory /Users/marco/dev/rehoboam rehoboam fetch-azure-state` then `uv run --directory /Users/marco/dev/rehoboam rehoboam import-sqlite --dsn "$DATABASE_ADMIN_URL"`. Record the counts table. Idempotent: only rows written since 2026-09-12 are new.
- [ ] **Step 4: Open the PR and merge** — `superpowers:finishing-a-development-branch`; the push-to-main workflow deploys the trading Function. Watch it to completion.
- [ ] **Step 5: Verify the 20:00 UTC session** — App Insights: `session-start` and `session-end` present, no `Trading session failed`. Store: `select max(snapshot_at) from rehoboam.league_rank_history` and `select max(predicted_at) from rehoboam.predicted_eps` are within the session's minute. Record both.
- [ ] **Step 6: Rollback path if Step 5 fails** — `git revert -m 1 <merge sha>` on a branch, PR, merge; the blobs were never deleted, so the reverted code resumes from them. Then diagnose.
- [ ] **Step 7: Memory** — update `project_data_foundation_rollout.md` (B2 merged, the numbers, C next) and delete `project_local_db_is_hybrid.md` (no longer true).

______________________________________________________________________

## Self-review against spec §1 and the design ruling

| requirement                                                                                                                                   | task                                          |
| --------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------- |
| `BidLearner` and `ActivityFeedLearner` rewritten onto the store; mechanical translations                                                      | 3, 4, 5 (rules table)                         |
| `api_cache` replaces the two 6-hour caches; read one row at a time                                                                            | 5                                             |
| `migrate()` at the start of every Function invocation (a check under the bot role)                                                            | 1 (`ensure_ready`), 6                         |
| Missing `DATABASE_URL` is a hard error at startup, no SQLite fallback                                                                         | 1, 6 (`_ensure_store`, function app)          |
| Blob sync, `DB_FILES`, `fetch-azure-state`, `push-azure-state` deleted; `azure-storage-blob` kept for PR C                                    | 6                                             |
| `corpus-pull` for the offline tools; their `--corpus` argument unchanged                                                                      | 2                                             |
| Tests against a real PostgreSQL in CI; no test reaches the live project                                                                       | 1                                             |
| CLAUDE.md rewritten                                                                                                                           | 7                                             |
| Prod: `DATABASE_URL` on both apps, import run once (again, at cut-over)                                                                       | 8                                             |
| Ruled 2026-09-13: one transaction per call, no autocommit                                                                                     | 3 (`connection()`), `store.connect` unchanged |
| Ruled: local `.env` uses the bot role; admin URL separate                                                                                     | 1, 8                                          |
| Ruled: compliance checker off the JSON file                                                                                                   | 6                                             |
| Not in B2: `enrich-corpus`'s SQLite corpus writer (PR C); `value_tracking.db`/`market_prices.db` removal from disk (PR F, nothing reads them) | —                                             |
