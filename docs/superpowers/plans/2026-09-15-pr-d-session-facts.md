# PR D: session facts and the integrity check — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every run of either Function app leaves one `session_facts` row in the store; the trading session ends with a pure integrity check over those facts (rules I1–I7), whose failures are written to `integrity_failures`, printed on the session board, and sent as one Telegram message per session only when something failed. The next kickoff comes from the competition schedule, cross-checked against `/myeleven`.

**Architecture:** Facts are collected into a pure `SessionFacts` dataclass as the session runs and written by a small `store/session_store.py` at every exit (normal, lineup-only, pipeline-failure). `services/integrity.py` is pure: facts plus two clock inputs in, a list of failures out, tested exhaustively like `safety_gate`. A pure `kickoff.py` parses both kickoff sources; `Trader.next_kickoff()` composes them (schedule primary, `/myeleven` fallback and cross-check) and `get_days_until_match()` derives its day count from the same answer, so phase and facts cannot disagree. The `full`-mode I3 refusal reuses the buy gate: `BuyGate` gains a `session_refusal` reason that `check_buy` reports like any other, set on the context right after the budget and open offers are known; the emergency fill is exempt.

**Tech Stack:** Python 3.10+, psycopg 3 (`rehoboam/store/`), the existing Telegram sender, pytest with the `store_dsn` fixture (real PostgreSQL).

**Spec:** `docs/superpowers/specs/2026-09-11-data-foundation-design.md` §3 ("Session facts and the integrity check"), Rollout row **D**. Probe 2026-09-15: `GET /v4/competitions/1/matchdays` returns `{day, it[]}`; each matchday `{day, mdln, it[]}`; each match `dt` (ISO-8601 kickoff), `st` (2 finished, 0 not started), `t1`, `t2`, `mi`. Next kickoff = earliest `dt` with `st == 0` (and `dt > now`). Rulings 2026-09-15 (Marco-approved design): facts in their own store module; "sellable value" for I3 = the squad's total market value; "manual" open offers = market bids we hold with no `pending_bids` row; the I3 refusal is evaluated early in `full` mode and exempts the emergency fill; `status` (dry-run) sessions record facts with `dry_run=true`.

## Global Constraints

- Branch `marcobraun2013/pr-d-session-facts` in worktree `/Users/marco/dev/rehoboam/.claude/worktrees/pr-d-session-facts`, based on `main` at 0ca96bf. Work only there; never `cd` elsewhere; never commit to `main`.
- `.env` holds real credentials. **Implementers never read, print or edit `.env`, and never run `rehoboam auto|status|ingest|export|migrate` or anything that logs in to Kickbase or connects to a non-test database.** Tests use `store_dsn`; the autouse fixture pins `DATABASE_URL`.
- Every SQL statement schema-qualifies `rehoboam.<table>`; `%s` placeholders; dict rows never indexed by position; one `with self.connection()` block per method.
- No new dependencies. Line length 100; `uvx --from "black>=25.1,<26" black --check --line-length=100 rehoboam/` and `uvx ruff check rehoboam/` clean. Stage only files you changed; pre-commit hooks may reformat — re-stage and re-run the same commit.
- Run tests with `uv run pytest -q -p no:cacheprovider`. Baseline: 1650 passed, 2 skipped.
- Existing tests that assert the `session-start` / `session-end` log lines (`tests/test_trading_mode.py`) and the `AutoTradeSession` dataclass (`tests/test_daily_email.py`) must pass unchanged; new fields on `AutoTradeSession` get defaults.
- The integrity check never blocks lineup setting and never raises into the session: every facts/integrity/alert call in `auto_trader.py` sits in its own `try/except` that logs with `exc_info` and continues.
- Every commit message ends with these two lines exactly:
  ```
  Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01F4d1fpUbNh28evK1pNy4Le
  ```
- Docstrings and comments explain *why*; never narrate the diff.

______________________________________________________________________

## File structure

| file                                                                                      | responsibility                                                           |
| ----------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| `rehoboam/store/migrations/003_session_facts.sql` (new)                                   | `session_facts`, `integrity_failures`                                    |
| `rehoboam/services/session_facts.py` (new)                                                | `SessionFacts` dataclass (pure), `IntegrityFailure`                      |
| `rehoboam/store/session_store.py` (new)                                                   | `SessionStore`: write facts and failures; read the last completed ingest |
| `rehoboam/services/integrity.py` (new)                                                    | pure rules I1–I7                                                         |
| `rehoboam/kickoff.py` (new)                                                               | pure parsers for both kickoff sources                                    |
| `rehoboam/trader.py`                                                                      | `next_kickoff()`; `get_days_until_match()` derives from it               |
| `rehoboam/services/safety_gate.py`                                                        | `BuyGate.session_refusal`, `check_buy(session_refusal=)`                 |
| `rehoboam/auto_trader.py`                                                                 | facts collection, I3 early refusal, exits record + check + alert + board |
| `deploy/azure_function/function_app.py`, `deploy/azure_function_external/function_app.py` | `app_name`; external rows per ingest/export                              |
| `rehoboam/cli.py`                                                                         | `app_name="cli"`                                                         |
| `CLAUDE.md`                                                                               | docs                                                                     |

______________________________________________________________________

### Task 1: Migration 003, `SessionFacts`, `SessionStore`

**Files:**

- Create: `rehoboam/store/migrations/003_session_facts.sql`, `rehoboam/services/session_facts.py`, `rehoboam/store/session_store.py`
- Test: `tests/store/test_session_store.py` (new); `tests/store/test_migrate.py` (extend the "every table" expectation with the two new tables; the 002 test's `applied` assertion stays true)

**Interfaces:**

- Produces:

  - Tables `rehoboam.session_facts` and `rehoboam.integrity_failures` (columns below).
  - `SessionFacts` dataclass (all fields keyword, defaults shown) and `IntegrityFailure(rule: str, detail: str)` (frozen).
  - `SessionStore(dsn=None)`: `record(facts: SessionFacts) -> None` (upsert on `session_id`), `record_failures(session_id: str, failures: list[IntegrityFailure], *, at: float) -> int`, `last_ingest_completed_at() -> float | None` (max `started_at + duration_s` over rows with `app = 'external'`, `mode = 'ingest'`, `errors = 0`), `facts(session_id) -> dict | None`, `failures(session_id) -> list[dict]`.

- [ ] **Step 1: Write the failing tests**

`tests/store/test_session_store.py`:

```python
"""One row per run, failures per session, and the last completed ingest for I7."""

from __future__ import annotations

from rehoboam.services.session_facts import IntegrityFailure, SessionFacts
from rehoboam.store.session_store import SessionStore


def _facts(**over) -> SessionFacts:
    base = dict(
        session_id="s1",
        app="function",
        mode="lineup_only",
        dry_run=False,
        started_at=1_000.0,
        duration_s=12.5,
        phase="moderate",
        next_kickoff=90_000.0,
        next_kickoff_source="schedule",
        squad_gk=1,
        squad_def=4,
        squad_mid=4,
        squad_fw=2,
        fieldable_count=11,
        legal_formation="4-4-2",
        budget=5_000_000,
        sellable_value=150_000_000,
        open_offers_total=0,
        open_offers_manual=0,
        cost_basis_missing=0,
        predictions_written=15,
        lineup_result="set",
        errors=0,
        error_text="",
    )
    base.update(over)
    return SessionFacts(**base)


def test_record_is_an_upsert_on_session_id(store_dsn):
    store = SessionStore(dsn=store_dsn)
    store.record(_facts())
    store.record(_facts(duration_s=20.0, errors=1, error_text="lineup: boom"))
    row = store.facts("s1")
    assert (row["duration_s"], row["errors"], row["error_text"]) == (
        20.0,
        1,
        "lineup: boom",
    )
    assert (
        row["legal_formation"] == "4-4-2" and row["next_kickoff_source"] == "schedule"
    )


def test_failures_are_recorded_per_session(store_dsn):
    store = SessionStore(dsn=store_dsn)
    store.record(_facts())
    n = store.record_failures(
        "s1",
        [
            IntegrityFailure("I1", "next kickoff unknown"),
            IntegrityFailure("I5", "0 rows"),
        ],
        at=2_000.0,
    )
    assert n == 2
    assert [f["rule"] for f in store.failures("s1")] == ["I1", "I5"]
    assert store.failures("nope") == []


def test_last_ingest_completed_at_ignores_failed_runs_and_other_apps(store_dsn):
    store = SessionStore(dsn=store_dsn)
    store.record(
        _facts(session_id="t1", app="function", started_at=5_000.0, duration_s=10.0)
    )
    store.record(
        _facts(
            session_id="i1",
            app="external",
            mode="ingest",
            started_at=3_000.0,
            duration_s=400.0,
        )
    )
    store.record(
        _facts(
            session_id="i2",
            app="external",
            mode="ingest",
            started_at=4_000.0,
            duration_s=100.0,
            errors=1,
        )
    )
    assert store.last_ingest_completed_at() == 3_400.0


def test_last_ingest_completed_at_is_none_when_nothing_ran(store_dsn):
    assert SessionStore(dsn=store_dsn).last_ingest_completed_at() is None


def test_extra_is_stored_as_json(store_dsn):
    store = SessionStore(dsn=store_dsn)
    store.record(
        _facts(
            session_id="i1",
            app="external",
            mode="ingest",
            extra={"status_written": 462},
        )
    )
    assert store.facts("i1")["extra"] == {"status_written": 462}
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/store/test_session_store.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: The migration**

`rehoboam/store/migrations/003_session_facts.sql`:

```sql
-- PR D: one row per run from either app, and what the integrity check found.
create table if not exists rehoboam.session_facts (
    session_id          text primary key,
    app                 text not null,            -- function | cli | external
    mode                text not null,            -- full | lineup_only | ingest | export
    dry_run             integer not null default 0,
    started_at          double precision not null,
    duration_s          double precision not null default 0,
    phase               text,
    next_kickoff        double precision,         -- epoch; null = unknown
    next_kickoff_source text,                     -- schedule | myeleven | none
    squad_gk            integer,
    squad_def           integer,
    squad_mid           integer,
    squad_fw            integer,
    fieldable_count     integer,
    legal_formation     text,
    budget              bigint,
    sellable_value      bigint,
    open_offers_total   bigint,
    open_offers_manual  bigint,
    cost_basis_missing  integer,
    predictions_written integer,
    lineup_result       text,                     -- set | dry_run | illegal | failed | skipped
    errors              integer not null default 0,
    error_text          text not null default '',
    extra               jsonb
);
create index if not exists idx_session_facts_app_started on rehoboam.session_facts (app, started_at);

create table if not exists rehoboam.integrity_failures (
    id         bigint generated by default as identity primary key,
    session_id text not null,
    rule       text not null,
    detail     text not null,
    created_at double precision not null
);
create index if not exists idx_integrity_failures_session on rehoboam.integrity_failures (session_id);
```

- [ ] **Step 4: `rehoboam/services/session_facts.py`**

```python
"""What one run knew about itself, as data (spec §3).

Pure: the trading session fills this in as it goes and hands it to the
store and to the integrity rules at the end. Every field is optional except
the identity, so a run that dies early still leaves a row that says so.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class IntegrityFailure:
    rule: str  # I1 … I7
    detail: str


@dataclass
class SessionFacts:
    session_id: str
    app: str  # function | cli | external
    mode: str  # full | lineup_only | ingest | export
    started_at: float
    dry_run: bool = False
    duration_s: float = 0.0
    phase: str | None = None
    next_kickoff: float | None = None  # epoch seconds
    next_kickoff_source: str | None = None  # schedule | myeleven | none
    squad_gk: int | None = None
    squad_def: int | None = None
    squad_mid: int | None = None
    squad_fw: int | None = None
    fieldable_count: int | None = None
    legal_formation: str | None = None
    budget: int | None = None
    sellable_value: int | None = None
    open_offers_total: int | None = None
    open_offers_manual: int | None = None
    cost_basis_missing: int | None = None
    predictions_written: int | None = None
    lineup_result: str | None = None  # set | dry_run | illegal | failed | skipped
    errors: int = 0
    error_text: str = ""
    extra: dict[str, Any] | None = None

    def as_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["dry_run"] = 1 if self.dry_run else 0
        return row

    @property
    def squad_size(self) -> int | None:
        parts = [self.squad_gk, self.squad_def, self.squad_mid, self.squad_fw]
        return None if any(p is None for p in parts) else sum(parts)
```

- [ ] **Step 5: `rehoboam/store/session_store.py`**

```python
"""Session facts and integrity failures in the store (spec §3)."""

from __future__ import annotations

import time
from typing import Any

from psycopg.types.json import Jsonb

from rehoboam.services.session_facts import IntegrityFailure, SessionFacts

_COLUMNS = (
    "session_id",
    "app",
    "mode",
    "dry_run",
    "started_at",
    "duration_s",
    "phase",
    "next_kickoff",
    "next_kickoff_source",
    "squad_gk",
    "squad_def",
    "squad_mid",
    "squad_fw",
    "fieldable_count",
    "legal_formation",
    "budget",
    "sellable_value",
    "open_offers_total",
    "open_offers_manual",
    "cost_basis_missing",
    "predictions_written",
    "lineup_result",
    "errors",
    "error_text",
    "extra",
)


class SessionStore:
    def __init__(self, dsn: str | None = None):
        self.dsn = dsn

    def connection(self):
        from rehoboam.store import connect

        return connect(self.dsn)

    def record(self, facts: SessionFacts) -> None:
        """Upsert: a session writes its row early and again at every exit, so
        the last write wins and a run that died mid-way still left a row."""
        row = facts.as_row()
        row["extra"] = Jsonb(row["extra"]) if row["extra"] is not None else None
        cols = ", ".join(_COLUMNS)
        placeholders = ", ".join(["%s"] * len(_COLUMNS))
        updates = ", ".join(
            f"{c} = excluded.{c}" for c in _COLUMNS if c != "session_id"
        )
        with self.connection() as conn:
            conn.execute(
                f"INSERT INTO rehoboam.session_facts ({cols}) VALUES ({placeholders}) "
                f"ON CONFLICT (session_id) DO UPDATE SET {updates}",
                [row[c] for c in _COLUMNS],
            )

    def record_failures(
        self,
        session_id: str,
        failures: list[IntegrityFailure],
        *,
        at: float | None = None,
    ) -> int:
        if not failures:
            return 0
        stamp = at if at is not None else time.time()
        with self.connection() as conn, conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO rehoboam.integrity_failures (session_id, rule, detail, created_at) "
                "VALUES (%s, %s, %s, %s)",
                [(session_id, f.rule, f.detail, stamp) for f in failures],
            )
        return len(failures)

    def last_ingest_completed_at(self) -> float | None:
        """When the ingestion app last finished a run without errors (rule I7)."""
        with self.connection() as conn:
            row = conn.execute(
                "SELECT MAX(started_at + duration_s) AS at FROM rehoboam.session_facts "
                "WHERE app = 'external' AND mode = 'ingest' AND errors = 0"
            ).fetchone()
        return float(row["at"]) if row and row["at"] is not None else None

    def facts(self, session_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM rehoboam.session_facts WHERE session_id = %s",
                (session_id,),
            ).fetchone()
        return dict(row) if row else None

    def failures(self, session_id: str) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT rule, detail, created_at FROM rehoboam.integrity_failures "
                "WHERE session_id = %s ORDER BY id",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]
```

(The f-strings interpolate only the `_COLUMNS` constant.)

- [ ] **Step 6: Run and commit**

Run: `uv run pytest tests/store -q -p no:cacheprovider` — all pass (update `tests/store/test_migrate.py`'s expected-table set with `session_facts`, `integrity_failures` and, if it asserts the applied file list, `003_session_facts.sql`).

```bash
git add rehoboam/store/migrations/003_session_facts.sql rehoboam/services/session_facts.py rehoboam/store/session_store.py tests/store/test_session_store.py tests/store/test_migrate.py
git commit -m "feat(store): session_facts and integrity_failures — one row per run, failures per session"
```

______________________________________________________________________

### Task 2: `services/integrity.py` — the seven rules, pure

**Files:**

- Create: `rehoboam/services/integrity.py`
- Test: `tests/test_integrity.py` (new)

**Interfaces:**

- Produces: `check_integrity(facts: SessionFacts, *, now: float, last_ingest_completed_at: float | None) -> list[IntegrityFailure]`; constants `KICKOFF_LINEUP_WINDOW_S = 48 * 3600`, `INGEST_MAX_AGE_S = 36 * 3600`; `i3_budget_covered(budget, open_offers_total, sellable_value) -> tuple[bool, str]` (shared with the early refusal in Task 5).

- [ ] **Step 1: Write the failing tests**

`tests/test_integrity.py` — one test per rule passing, one per rule failing, and one all-pass; the fixture `_facts()` is the same helper as in Task 1's tests (copy it; tests do not import each other):

```python
"""Rules I1–I7 (spec §3): facts in, failures out, nothing else."""

from __future__ import annotations

import pytest

from rehoboam.services.integrity import (
    INGEST_MAX_AGE_S,
    KICKOFF_LINEUP_WINDOW_S,
    check_integrity,
    i3_budget_covered,
)
from rehoboam.services.session_facts import SessionFacts

NOW = 1_000_000.0


def _facts(**over) -> SessionFacts:
    base = dict(
        session_id="s",
        app="function",
        mode="lineup_only",
        started_at=NOW - 10,
        phase="moderate",
        next_kickoff=NOW + 3 * 86_400,
        next_kickoff_source="schedule",
        squad_gk=1,
        squad_def=4,
        squad_mid=4,
        squad_fw=2,
        fieldable_count=11,
        legal_formation="4-4-2",
        budget=5_000_000,
        sellable_value=150_000_000,
        open_offers_total=0,
        open_offers_manual=0,
        cost_basis_missing=0,
        predictions_written=15,
        lineup_result="set",
        errors=0,
    )
    base.update(over)
    return SessionFacts(**base)


def _rules(failures):
    return [f.rule for f in failures]


def test_all_rules_pass_on_a_healthy_session():
    assert check_integrity(_facts(), now=NOW, last_ingest_completed_at=NOW - 3600) == []


def test_i1_unknown_kickoff():
    assert _rules(
        check_integrity(
            _facts(next_kickoff=None, next_kickoff_source="none"),
            now=NOW,
            last_ingest_completed_at=NOW,
        )
    ) == ["I1"]


@pytest.mark.parametrize("over", [dict(fieldable_count=10), dict(legal_formation=None)])
def test_i2_unfieldable_or_no_legal_formation(over):
    assert "I2" in _rules(
        check_integrity(_facts(**over), now=NOW, last_ingest_completed_at=NOW)
    )


def test_i3_deficit_covered_by_sellable_value_passes():
    f = _facts(
        budget=-2_000_000, open_offers_total=1_000_000, sellable_value=10_000_000
    )
    assert "I3" not in _rules(check_integrity(f, now=NOW, last_ingest_completed_at=NOW))


def test_i3_deficit_not_covered_fails():
    f = _facts(budget=-2_000_000, open_offers_total=1_000_000, sellable_value=2_000_000)
    assert "I3" in _rules(check_integrity(f, now=NOW, last_ingest_completed_at=NOW))


def test_i3_helper_reports_the_deficit():
    ok, detail = i3_budget_covered(budget=-500, open_offers_total=500, sellable_value=0)
    assert ok is False and "1,000" in detail


def test_i4_cost_basis_missing():
    assert "I4" in _rules(
        check_integrity(
            _facts(cost_basis_missing=2), now=NOW, last_ingest_completed_at=NOW
        )
    )


def test_i5_no_predictions():
    assert "I5" in _rules(
        check_integrity(
            _facts(predictions_written=0), now=NOW, last_ingest_completed_at=NOW
        )
    )


def test_i6_lineup_failed_inside_48h_fails_but_outside_passes():
    inside = _facts(
        next_kickoff=NOW + KICKOFF_LINEUP_WINDOW_S - 1, lineup_result="illegal"
    )
    outside = _facts(
        next_kickoff=NOW + KICKOFF_LINEUP_WINDOW_S + 1, lineup_result="illegal"
    )
    assert "I6" in _rules(
        check_integrity(inside, now=NOW, last_ingest_completed_at=NOW)
    )
    assert "I6" not in _rules(
        check_integrity(outside, now=NOW, last_ingest_completed_at=NOW)
    )


def test_i6_dry_run_lineup_counts_as_success():
    f = _facts(next_kickoff=NOW + 3600, lineup_result="dry_run", dry_run=True)
    assert "I6" not in _rules(check_integrity(f, now=NOW, last_ingest_completed_at=NOW))


def test_i7_stale_or_missing_ingest():
    stale = check_integrity(
        _facts(), now=NOW, last_ingest_completed_at=NOW - INGEST_MAX_AGE_S - 1
    )
    missing = check_integrity(_facts(), now=NOW, last_ingest_completed_at=None)
    assert _rules(stale) == ["I7"] and _rules(missing) == ["I7"]


def test_unknown_facts_do_not_fail_rules_that_need_them():
    """A run that died before it knew its squad reports I1/I5/I6 as applicable, not I2/I4."""
    f = _facts(
        fieldable_count=None,
        legal_formation=None,
        cost_basis_missing=None,
        predictions_written=None,
    )
    rules = _rules(check_integrity(f, now=NOW, last_ingest_completed_at=NOW))
    assert "I2" not in rules and "I4" not in rules and "I5" in rules
```

- [ ] **Step 2: Run to see them fail**, then **Step 3: implement**

```python
"""The integrity check (spec §3): pure, exhaustive, never blocks a lineup.

Each rule looks at facts the session already collected and says what is
wrong in words a Telegram reader can act on. A fact the session never
learned (None) skips the rule that needs it — I5 is the exception, because
"no predictions" is exactly what a missing count means.
"""

from __future__ import annotations

from rehoboam.services.session_facts import IntegrityFailure, SessionFacts

KICKOFF_LINEUP_WINDOW_S = 48 * 3600
INGEST_MAX_AGE_S = 36 * 3600


def i3_budget_covered(
    *, budget: int, open_offers_total: int, sellable_value: int
) -> tuple[bool, str]:
    """Budget minus open offers must be non-negative, or the deficit covered by
    what the squad could be sold for before kickoff."""
    net = int(budget) - int(open_offers_total)
    if net >= 0:
        return (
            True,
            f"budget EUR {budget:,} minus open offers EUR {open_offers_total:,} = EUR {net:,}",
        )
    deficit = -net
    if deficit <= int(sellable_value):
        return (
            True,
            f"deficit EUR {deficit:,} covered by sellable value EUR {sellable_value:,}",
        )
    return False, (
        f"budget EUR {budget:,} minus open offers EUR {open_offers_total:,} leaves a deficit of "
        f"EUR {deficit:,} that sellable value EUR {sellable_value:,} does not cover"
    )


def check_integrity(
    facts: SessionFacts, *, now: float, last_ingest_completed_at: float | None
) -> list[IntegrityFailure]:
    out: list[IntegrityFailure] = []

    if facts.next_kickoff is None:
        out.append(
            IntegrityFailure(
                "I1", "next kickoff unknown (schedule and /myeleven both empty)"
            )
        )

    if facts.fieldable_count is not None and facts.fieldable_count < 11:
        out.append(
            IntegrityFailure(
                "I2", f"only {facts.fieldable_count} fieldable after the emergency step"
            )
        )
    elif facts.fieldable_count is not None and facts.legal_formation is None:
        out.append(
            IntegrityFailure("I2", "no legal formation for the fieldable eleven")
        )

    if facts.budget is not None and facts.open_offers_total is not None:
        ok, detail = i3_budget_covered(
            budget=facts.budget,
            open_offers_total=facts.open_offers_total,
            sellable_value=facts.sellable_value or 0,
        )
        if not ok:
            out.append(IntegrityFailure("I3", detail))

    if facts.cost_basis_missing:
        out.append(
            IntegrityFailure(
                "I4", f"{facts.cost_basis_missing} owned player(s) without a cost basis"
            )
        )

    if not facts.predictions_written:
        out.append(IntegrityFailure("I5", "no predictions written this session"))

    if (
        facts.next_kickoff is not None
        and facts.next_kickoff - now <= KICKOFF_LINEUP_WINDOW_S
        and facts.lineup_result not in ("set", "dry_run")
    ):
        out.append(
            IntegrityFailure(
                "I6",
                f"lineup {facts.lineup_result or 'not attempted'} with kickoff within 48 h",
            )
        )

    if (
        last_ingest_completed_at is None
        or now - last_ingest_completed_at > INGEST_MAX_AGE_S
    ):
        age = (
            "never"
            if last_ingest_completed_at is None
            else f"{(now - last_ingest_completed_at) / 3600:.0f} h ago"
        )
        out.append(
            IntegrityFailure("I7", f"ingestion last completed {age} (limit 36 h)")
        )

    return out
```

- [ ] **Step 4: Run, commit**

```bash
git add rehoboam/services/integrity.py tests/test_integrity.py
git commit -m "feat(integrity): rules I1–I7 as a pure check over session facts"
```

______________________________________________________________________

### Task 3: Kickoff from the schedule, cross-checked against `/myeleven`

**Files:**

- Create: `rehoboam/kickoff.py`
- Modify: `rehoboam/trader.py` (`get_days_until_match` body; new `next_kickoff`)
- Test: `tests/test_kickoff.py` (new); `tests/test_trader_days_until_match.py` or wherever `get_days_until_match` is tested today (`grep -rl get_days_until_match tests`) must stay green — those tests fake `get_starting_eleven`; make `get_competition_matchdays` on their fakes raise or return `{}` so the fallback path keeps their assertions true, or extend the fakes minimally.

**Interfaces:**

- Produces:

  - `kickoff.next_kickoff_from_matchdays(payload: dict, now: datetime) -> datetime | None` — earliest `dt` across `payload["it"][*]["it"][*]` with `st == 0` and `dt > now`; tolerant of missing keys; ISO `Z` handled on Python 3.10 (`replace("Z", "+00:00")`).
  - `kickoff.fixtures_from_myeleven(payload: dict) -> list[datetime]` — the candidate list `get_days_until_match` builds today (legacy `nm`/`nextMatch`, then `lp`/`nlp` `md`), moved out unchanged.
  - `kickoff.NextKickoff(at: datetime | None, source: str, cross_check: datetime | None, matchday_in_progress: bool)` (frozen dataclass).
  - `Trader.next_kickoff(league) -> NextKickoff`: fetches the schedule (`self.api.get_competition_matchdays()`, wrapped in try/except → None) and `/myeleven` (as today, try/except → None); `at` = schedule's answer if any, else the earliest upcoming `/myeleven` fixture; `source` = `"schedule"`, `"myeleven"` or `"none"`; `cross_check` = the other source's answer; `matchday_in_progress` = `matchday_in_progress(myeleven_fixtures, now)` as today. Logs one line: `next-kickoff at=%s source=%s cross_check=%s`, and a WARNING when both sources disagree by more than 24 h.
  - `Trader.get_days_until_match(league) -> int | None` becomes `nk = self.next_kickoff(league); self._last_matchday_in_progress = nk.matchday_in_progress; return max((nk.at - now).days, 0) if nk.at else None` — same return semantics, same side-effect flag.

- [ ] **Step 1: Write the failing tests** — `tests/test_kickoff.py`:

```python
"""The next kickoff: schedule first, /myeleven as the cross-check (spec §3; probe 2026-09-15)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from rehoboam.kickoff import fixtures_from_myeleven, next_kickoff_from_matchdays
from rehoboam.trader import Trader

NOW = datetime(2026, 9, 15, 6, 0, tzinfo=timezone.utc)

SCHEDULE = {
    "day": 4,
    "it": [
        {
            "day": 3,
            "mdln": "3 Match Day",
            "it": [
                {
                    "mi": "1",
                    "day": 3,
                    "dt": "2026-09-12T13:30:00Z",
                    "st": 2,
                    "t1": "2",
                    "t2": "9",
                },
            ],
        },
        {
            "day": 4,
            "mdln": "4 Match Day",
            "it": [
                {
                    "mi": "2",
                    "day": 4,
                    "dt": "2026-09-19T13:30:00Z",
                    "st": 0,
                    "t1": "5",
                    "t2": "6",
                },
                {
                    "mi": "3",
                    "day": 4,
                    "dt": "2026-09-18T18:30:00Z",
                    "st": 0,
                    "t1": "2",
                    "t2": "40",
                },
            ],
        },
    ],
}
MYELEVEN = {
    "lp": [{"md": "2026-09-19T13:30:00Z"}],
    "nlp": [{"md": "2026-09-12T13:30:00Z"}, {}],
}


def test_schedule_gives_the_earliest_not_started_fixture():
    assert next_kickoff_from_matchdays(SCHEDULE, NOW) == datetime(
        2026, 9, 18, 18, 30, tzinfo=timezone.utc
    )


def test_schedule_ignores_finished_and_past_fixtures_and_tolerates_junk():
    assert (
        next_kickoff_from_matchdays(
            {"it": [{"it": [{"dt": "2026-09-12T13:30:00Z", "st": 2}]}]}, NOW
        )
        is None
    )
    assert next_kickoff_from_matchdays({}, NOW) is None
    assert (
        next_kickoff_from_matchdays(
            {"it": [{"it": [{"st": 0}, {"dt": "garbage", "st": 0}]}]}, NOW
        )
        is None
    )


def test_myeleven_fixtures_read_lp_and_nlp():
    assert fixtures_from_myeleven(MYELEVEN) == [
        datetime(2026, 9, 19, 13, 30, tzinfo=timezone.utc),
        datetime(2026, 9, 12, 13, 30, tzinfo=timezone.utc),
    ]


def _trader(schedule, myeleven):
    api = SimpleNamespace(
        get_competition_matchdays=lambda: (
            (_ for _ in ()).throw(schedule)
            if isinstance(schedule, Exception)
            else schedule
        ),
        get_starting_eleven=lambda league: myeleven,
    )
    return Trader(api, SimpleNamespace())


def test_next_kickoff_prefers_the_schedule_and_records_the_cross_check():
    nk = _trader(SCHEDULE, MYELEVEN).next_kickoff(SimpleNamespace(id="L"), now=NOW)
    assert nk.source == "schedule"
    assert nk.at == datetime(2026, 9, 18, 18, 30, tzinfo=timezone.utc)
    assert nk.cross_check == datetime(2026, 9, 19, 13, 30, tzinfo=timezone.utc)


def test_next_kickoff_falls_back_to_myeleven_when_the_schedule_fails():
    nk = _trader(RuntimeError("503"), MYELEVEN).next_kickoff(
        SimpleNamespace(id="L"), now=NOW
    )
    assert (nk.source, nk.at) == (
        "myeleven",
        datetime(2026, 9, 19, 13, 30, tzinfo=timezone.utc),
    )


def test_next_kickoff_is_none_when_both_sources_are_empty():
    nk = _trader({}, {}).next_kickoff(SimpleNamespace(id="L"), now=NOW)
    assert (nk.source, nk.at, nk.cross_check) == ("none", None, None)


def test_days_until_match_derives_from_next_kickoff():
    trader = _trader(SCHEDULE, MYELEVEN)
    assert trader.get_days_until_match(SimpleNamespace(id="L"), now=NOW) == 3
```

`Trader.next_kickoff(league, *, now: datetime | None = None)` and `get_days_until_match(league, *, now: datetime | None = None)` accept an injected clock (default `datetime.now(tz=timezone.utc)`); `Trader.__init__` must tolerate the bare `SimpleNamespace()` settings the test passes (check what it reads from `settings` at construction and use `getattr` with defaults, or construct with `Settings`-like defaults — read `Trader.__init__` first and pick the smaller change). Run the file: FAIL on `ModuleNotFoundError: rehoboam.kickoff`.

- [ ] **Step 2–4: implement, run `tests/test_kickoff.py` + the existing trader tests + the whole suite, commit**

```bash
git add rehoboam/kickoff.py rehoboam/trader.py tests/test_kickoff.py <any adjusted trader test>
git commit -m "feat(kickoff): the schedule is the primary source, /myeleven the cross-check; one answer for phase and facts"
```

______________________________________________________________________

### Task 4: `BuyGate.session_refusal`

**Files:**

- Modify: `rehoboam/services/safety_gate.py` (`check_buy(..., session_refusal: str | None = None)` appends `session_refusal` to `reasons` when set; `BuyGate.session_refusal: str | None = None` passed through in `check`)
- Modify: `rehoboam/auto_trader.py` (`_build_buy_gate(..., session_refusal: str | None = None)` forwards it; the three non-emergency call sites — trade pair pre-flight and buy, flip buy, plain buy — pass `session_refusal=getattr(ctx, "session_refusal", None)`; the emergency fill's call site passes nothing). `EPSessionContext.session_refusal: str | None = None`.
- Test: `tests/test_safety_gate.py` (append):

```python
def test_session_refusal_is_reported_like_any_other_reason():
    from tests.conftest import permissive_buy_gate

    gate = permissive_buy_gate(
        "p1", session_refusal="I3: deficit EUR 1,000 not covered"
    )
    verdict = gate.check(player_id="p1", bid=1_000)
    assert not verdict.ok
    assert any(r.startswith("I3:") for r in verdict.reasons)
    assert permissive_buy_gate("p1").check(player_id="p1", bid=1_000).ok
```

(`permissive_buy_gate(**overrides)` already forwards unknown fields into `BuyGate`; `GateResult` has `ok` and `reasons` — confirm the attribute names in `safety_gate.py` and adjust the test to them.)

And in `tests/test_autonomous_buy_gate.py` (append; use that file's existing fixtures for a context and a plain-buy call):

```python
def test_a_session_refusal_blocks_plain_buys_but_not_the_emergency_fill(...):
    # Build ctx as the file's other tests do, set ctx.session_refusal = "I3: …",
    # drive one plain buy and one emergency fill; assert the plain buy's result is a
    # refusal carrying "I3" and the emergency fill's buy went through the execution
    # service (its gate was built without the refusal).
```

Write it against the helpers that file already has (read it first); the two assertions above are the contract.

Commit: `feat(gate): a session-level refusal reason the buy gate reports; the emergency fill is exempt`.

______________________________________________________________________

### Task 5: Wire the session — facts, early I3, exits, alert, board

**Files:**

- Modify: `rehoboam/auto_trader.py`, `deploy/azure_function/function_app.py`, `rehoboam/cli.py`
- Test: `tests/test_session_facts_wiring.py` (new), reusing `tests/test_trading_mode.py`'s `_Api`/`_legal_squad` pattern with `store_dsn`

**Interfaces:**

- `AutoTrader.__init__(..., app_name: str = "cli")`; `AutoTradeSession` gains `session_id: str = ""` and `integrity_failures: list = field(default_factory=list)`.
- `AutoTrader._session_store` (a `SessionStore()` built lazily) — tests inject via `AutoTrader(..., session_store=SessionStore(dsn=store_dsn))` (optional kwarg).

Steps (each a `try/except` that logs and continues):

1. **Start** (`run_full_session`, right after `self._session_batch_id`): `self._facts = SessionFacts(session_id=self._session_batch_id, app=self.app_name, mode=self.settings.trading_mode, dry_run=self.dry_run, started_at=start_time)`; `self._session_store.record(self._facts)` (an early row: a run that dies leaves evidence).
1. **Context** (`_build_session_context`): after `phase` is known and after `team_info`/`my_bids`/`squad`: fill `phase`, `next_kickoff` (epoch of `trader.next_kickoff(...)` — call it once and derive `days` from it inside `_build_session_context` instead of `get_days_until_match`, or keep `get_days_until_match` and read `trader._last_next_kickoff` it stores; pick the former), `next_kickoff_source`, `squad_*` via `get_position_counts(squad)`, `budget=current_budget`, `sellable_value=sum(p.market_value for p in squad)`, `open_offers_total=pending_bid_total`, `open_offers_manual=sum(price for bids whose player_id is not in {b["player_id"] for b in self.learner.get_pending_bids()})`. Then in `full` mode: `ok, detail = i3_budget_covered(...)`; if not ok, `ctx.session_refusal = f"I3: {detail}"` and log a WARNING `session-refusal I3 …`.
1. **Cost basis** (step 1 block): `rec = self.tracker.reconcile_squad_cost_basis(...)` → `self._facts.cost_basis_missing = len(rec.still_missing)`.
1. **Predictions** (step 2a): `written = self.tracker.snapshot_predictions(...)` → `self._facts.predictions_written = int(written)`.
1. **Emergency** (step 3): after the fill, `fresh = self.api.get_squad(league)` is already fetched before; recompute after the fill only if `slots_short > 0`: `fb = fieldability(fresh_after)`; `self._facts.fieldable_count = 11 - fb.purchases if fb.purchases else 11` — simpler: `self._facts.fieldable_count = 11 if fb.ok else 11 - fb.purchases`. When no emergency: `fieldable_count = 11 if fieldability(fresh_squad).ok else ...` same formula.
1. **Lineup** (`_set_optimal_lineup`): set `self._facts.lineup_result` to `"illegal"` on the two illegal paths, `"dry_run"` on the dry-run return, `"set"` after `set_lineup`, `"failed"` in the except; `self._facts.legal_formation = formation` when computed.
1. **Every exit** (normal, `_finish_lineup_only`, pipeline-failure): call `self._finish_facts(errors, start_time)` which sets `duration_s`, `errors=len(errors)`, `error_text="; ".join(errors)[:2000]`, records the row, runs `check_integrity(self._facts, now=time.time(), last_ingest_completed_at=self._session_store.last_ingest_completed_at())`, records failures, prints them on the board (`console.print("[red]Integrity: I3 … [/red]")` per failure, or `[green]Integrity: all seven rules pass[/green]`), sends ONE Telegram message when failures exist and `settings.telegram_bot_token`/`telegram_chat_id` are set: text `"Rehoboam integrity — <app> <mode> session <id>\n• I1 …\n• I5 …"` via `notify.telegram.send_message`, logs `integrity-failures n=%d rules=%s`, and returns the failures for `AutoTradeSession.integrity_failures`. The pipeline-failure exit also logs a `session-end` line (`phase=unknown`) — the third exit today logs nothing.
1. `deploy/azure_function/function_app.py`: `AutoTrader(..., app_name="function")`; `rehoboam/cli.py` `auto` and `status`: `app_name="cli"`.

Tests — `tests/test_session_facts_wiring.py`. Copy `_Api`, `_player`, `_legal_squad` and the `Settings(...)` construction from `tests/test_trading_mode.py` (read that file first; it patches `AutoTrader._build_session_context` to return an `EPSessionContext` — reuse that shape), extend `_Api` with `get_starting_eleven` (returning `{"lp": [{"md": "<3 days from now, ISO Z>"}], "nlp": []}`), `get_competition_matchdays` (returning `{}`), and `set_lineup` (records the call). Patch `rehoboam.auto_trader.send_message` (import it into `auto_trader` at module level so the patch target exists).

```python
def _trader(store_dsn, squad, *, mode="lineup_only", dry_run=True, telegram=False):
    settings = Settings(
        kickbase_email="t@e.com",
        kickbase_password="x",
        trading_mode=mode,
        telegram_bot_token="tok" if telegram else "",
        telegram_chat_id="1" if telegram else "",
    )
    return AutoTrader(
        api=_Api(squad),
        settings=settings,
        dry_run=dry_run,
        app_name="cli",
        session_store=SessionStore(dsn=store_dsn),
    )


def test_a_session_leaves_a_facts_row_with_its_exit(store_dsn, ctx_factory):
    trader = _trader(store_dsn, _legal_squad())
    with patch.object(AutoTrader, "_build_session_context", return_value=ctx_factory()):
        session = trader.run_full_session(LEAGUE)
    row = SessionStore(dsn=store_dsn).facts(session.session_id)
    assert row["app"] == "cli" and row["mode"] == "lineup_only" and row["dry_run"] == 1
    assert row["lineup_result"] == "dry_run" and row["duration_s"] >= 0
    assert row["errors"] == 0


def test_context_facts_come_from_the_real_context_builder(store_dsn):
    trader = _trader(store_dsn, _legal_squad())
    with patch(
        "rehoboam.trader.Trader.get_ep_recommendations_with_trends",
        return_value={"squad_scores": [], "lineup_map": {}, "market_players": {}},
    ):
        ctx = trader._build_session_context(LEAGUE)
    f = trader._facts
    assert (f.squad_gk, f.squad_def, f.squad_mid, f.squad_fw) == (1, 4, 4, 2)
    assert f.next_kickoff_source == "myeleven" and f.phase == "moderate"
    assert (
        f.budget == 5_868_658
        and f.open_offers_total == 0
        and f.sellable_value == 11_000_000
    )
    assert ctx.session_refusal is None


def test_full_mode_i3_failure_sets_the_session_refusal(store_dsn):
    trader = _trader(store_dsn, _legal_squad(), mode="full")
    trader.api.get_team_info = lambda league: {"budget": -50_000_000, "team_value": 1}
    with patch(
        "rehoboam.trader.Trader.get_ep_recommendations_with_trends",
        return_value={"squad_scores": [], "lineup_map": {}, "market_players": {}},
    ):
        ctx = trader._build_session_context(LEAGUE)
    assert ctx.session_refusal.startswith("I3")


def test_lineup_only_i3_failure_only_alerts(store_dsn):
    trader = _trader(store_dsn, _legal_squad(), mode="lineup_only")
    trader.api.get_team_info = lambda league: {"budget": -50_000_000, "team_value": 1}
    with patch(
        "rehoboam.trader.Trader.get_ep_recommendations_with_trends",
        return_value={"squad_scores": [], "lineup_map": {}, "market_players": {}},
    ):
        ctx = trader._build_session_context(LEAGUE)
    assert ctx.session_refusal is None


def test_failures_are_recorded_and_sent_once(store_dsn, ctx_factory):
    trader = _trader(store_dsn, _legal_squad(), telegram=True)
    with patch.object(
        AutoTrader, "_build_session_context", return_value=ctx_factory()
    ), patch("rehoboam.auto_trader.send_message", return_value=True) as send:
        session = trader.run_full_session(LEAGUE)
    rules = [
        f["rule"] for f in SessionStore(dsn=store_dsn).failures(session.session_id)
    ]
    assert "I7" in rules  # no ingest row exists in a fresh database
    assert send.call_count == 1 and "I7" in send.call_args.args[2]
    assert [f.rule for f in session.integrity_failures] == rules


def test_no_failures_means_no_message(store_dsn, ctx_factory):
    store = SessionStore(dsn=store_dsn)
    store.record(
        SessionFacts(
            session_id="i",
            app="external",
            mode="ingest",
            started_at=time.time() - 60,
            duration_s=30,
        )
    )
    trader = _trader(store_dsn, _legal_squad(), telegram=True)
    healthy = (
        ctx_factory()
    )  # kickoff known, 11 fieldable, predictions written → make the fixture satisfy I1–I6
    with patch.object(
        AutoTrader, "_build_session_context", return_value=healthy
    ), patch("rehoboam.auto_trader.send_message") as send:
        session = trader.run_full_session(LEAGUE)
    assert session.integrity_failures == [] and send.call_count == 0


def test_pipeline_failure_exit_still_records_and_logs_session_end(store_dsn, caplog):
    trader = _trader(store_dsn, _legal_squad())
    with patch.object(
        AutoTrader, "_build_session_context", side_effect=RuntimeError("boom")
    ), caplog.at_level(logging.INFO, logger="rehoboam.auto_trader"):
        session = trader.run_full_session(LEAGUE)
    row = SessionStore(dsn=store_dsn).facts(session.session_id)
    assert row["errors"] >= 1 and "boom" in row["error_text"]
    assert any(m.startswith("session-end") for m in caplog.messages)
```

`ctx_factory` is a small fixture in the test module building an `EPSessionContext` the way `test_trading_mode.py` does; because the context builder is patched in those tests, the facts the builder fills (phase, kickoff, counts) come from `trader._facts` defaults — so `test_no_failures_means_no_message` must set `trader._facts` fields that I1–I6 read (`next_kickoff`, `fieldable_count`, `legal_formation`, `predictions_written`, budget fields) before `run_full_session`, or have the patched builder call a helper `trader._facts_from_context(ctx, next_kickoff)` that the real builder also uses — implement that helper (it keeps the fact-filling in one place) and call it in the patched path's `side_effect`.

Commit: `feat(session): facts at every exit, the integrity check on the board and in Telegram, I3 refuses new offers in full mode`.

______________________________________________________________________

### Task 6: The external app's rows

**Files:**

- Modify: `deploy/azure_function_external/function_app.py`, `rehoboam/cli.py` (`ingest`/`export` commands record too, `app="cli"`)
- Test: `tests/test_cli_ingest.py` — the `ingest`/`export` tests that already run with `store_dsn` assert a `session_facts` row exists afterwards (`mode="ingest"` with `extra["status_written"]`, `mode="export"` with `extra["tables"]`).

Implementation: in `run_ingestion`'s callers (Function `ingest`, CLI `ingest`), build `SessionFacts(session_id=uuid4().hex[:12], app=..., mode="ingest", started_at=stats.started_at, duration_s=stats.duration_s, errors=stats.failed and 0 ...)` — errors = 0 when the run completed (a per-player failure is not a run failure; `failed` goes into `extra`), errors = 1 in the `except` path with `error_text=str(e)`; `extra = asdict(stats)`. Same for export (`extra={"tables": len(sizes), "bytes": sum(...)}`). Record via `SessionStore()`. Also log `ingestion-end`… already logged by `run_ingestion`.

Commit: `feat(external): every ingest and export run leaves a session_facts row (rule I7 reads it)`.

______________________________________________________________________

### Task 7: Docs and whole-branch verification

- `CLAUDE.md`: in "The store" add a bullet "**Session facts + integrity (PR D, 2026-09-15)**: every run of either app writes `rehoboam.session_facts` (`store/session_store.py`); the trading session ends with `services/integrity.check_integrity` (I1–I7, pure), failures go to `integrity_failures`, the board, and one Telegram message per session when any rule fails; the next kickoff comes from `/v4/competitions/1/matchdays` (`kickoff.py`, `Trader.next_kickoff`) with `/myeleven` as the cross-check; in `full` mode a failing I3 sets `EPSessionContext.session_refusal`, which the buy gate reports and the emergency fill ignores." Update the Data Flow list (step 8 → "…and `_finish_facts` records the session and runs the integrity check").
- Verification: `rm -f logs/*.db; uv run pytest -q -p no:cacheprovider`; CI's black; ruff; `bash scripts/sync-azure-deps.sh && git diff --exit-code deploy/azure_function/requirements.txt deploy/azure_function_external/requirements.txt`.

Commit: `docs: session facts, the integrity check, and where the kickoff comes from`.

______________________________________________________________________

### Task 8: Migration, live smoke, deploy (controller-run)

1. `uv run rehoboam migrate` (admin) → `003_session_facts.sql` applied; grants refreshed.
1. Live smoke: `uv run rehoboam -v status` (dry run) → a `session_facts` row with `app='cli'`, `dry_run=1`, `next_kickoff_source='schedule'`, `next_kickoff` = 2026-09-18 18:30 UTC, phase `moderate`; the integrity result on the board (I7 should pass — the 05:00 run recorded no row yet, so it will FAIL until the external app is redeployed and runs; note that in the PR).
1. PR, merge (CI publishes both apps), verify the 20:00 UTC session's row and the 17:00 UTC ingest row.
1. Memory: rollout note.

______________________________________________________________________

## Self-review against spec §3

| requirement                                                                                                                  | task    |
| ---------------------------------------------------------------------------------------------------------------------------- | ------- |
| `session_facts` with the listed columns, one row per session from either app                                                 | 1, 5, 6 |
| next kickoff from the schedule (probed 2026-09-15), `/myeleven` cross-check, source recorded                                 | 3, 5    |
| `services/integrity.py` pure, I1–I7, tested exhaustively                                                                     | 2       |
| failures → `integrity_failures`, one Telegram message per session only on failure, printed on the board; never blocks lineup | 1, 5    |
| `full` mode: I3 refuses new offers; `lineup_only`: alert only                                                                | 4, 5    |
| I7 reads the ingestion app's completed runs                                                                                  | 1, 6    |
