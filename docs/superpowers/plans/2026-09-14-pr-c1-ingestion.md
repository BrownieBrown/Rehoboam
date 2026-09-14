# PR C1: ingestion on the second Function app — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `func-rehoboam-external` gets its job: twice a day it refreshes the league-wide corpus in the store — universe, a new daily status table, per-match history, market-value series — within a time and request budget, resuming where it stopped; once a week it exports every store table to the Blob container as insurance. The trading session is untouched (that switch is PR C2).

**Architecture:** The response-flattening logic in `TrainingCorpus` (SQLite) moves into pure functions that both it and a new store-backed writer, `store.corpus_store.CorpusStore`, share; `TrainingCorpus` stays the offline reader and the `corpus-pull` target. A new `enrichment/ingest.py` runs the twice-daily pass with an `IngestBudget` (deadline + request cap), refreshing each kind for players whose last fetch is older than 20 hours, oldest first, so a run that stops resumes at the stalest player next time. The shared HTTP session gets a retry adapter (429 + 5xx, exponential backoff, Retry-After honoured), which hardens every endpoint at once. `store/export.py` streams `COPY ... TO STDOUT` per table into gzip bytes for a caller-supplied uploader. `deploy/azure_function_external/` mirrors the trading app's shape: `ensure_ready()` first, timers at 05:00/17:00 UTC (ingest) and Sunday 03:00 UTC (export).

**Tech Stack:** Python 3.10+ (CI 3.10/3.11/3.12; Azure runtime 3.11), psycopg 3, requests + urllib3 `Retry`, azure-functions, azure-storage-blob (already a dependency), pytest-postgresql fixtures from PR B2 (`store_dsn`), Bicep.

**Spec:** `docs/superpowers/specs/2026-09-11-data-foundation-design.md` §2 ("Ingestion: the second Function app gets a job"), §1's "Insurance" paragraph (weekly COPY export), Rollout row **C**. Ruled 2026-09-14 with Marco: C splits into C1 (this plan) and C2 (the trading session reads `api_cache`/`player_status_daily` first — later, after the data proves out); the external app gets the Kickbase credentials and `LEAGUE_INDEX` in Bicep because the status endpoint is league-scoped (a stated deviation from §2's "Bicep gains DATABASE_URL and nothing else"); the deploy directory is the spec's `deploy/azure_function_external/` and `deploy.sh` changes to match.

## Global Constraints

- Branch `marcobraun2013/pr-c1-ingestion` in worktree `/Users/marco/dev/rehoboam/.claude/worktrees/pr-c1-ingestion`, based on `main` at 74fe9c6 (PR B2 merged). Work only there; never `cd` elsewhere; never commit to `main`.
- `.env` in the worktree is git-ignored and holds real credentials (`DATABASE_URL` = bot role, `DATABASE_ADMIN_URL` = admin). **Implementers never read, print or edit `.env`, and never run `rehoboam ingest|enrich-corpus|migrate|auto|status` or anything that logs in to Kickbase or connects to a non-test database.** Tests get databases from the `store_dsn` fixture; an autouse fixture pins `DATABASE_URL` to a test database.
- Every SQL statement schema-qualifies tables as the literal `rehoboam.<table>`; placeholders `%s`; psycopg rows are dicts, never indexed by position; one `with self.connection()` block per method (commits on exit); `executemany` only via `conn.cursor()`.
- No new dependencies. `requests`, `urllib3`, `psycopg`, `azure-functions`, `azure-storage-blob` are already in `pyproject.toml`.
- Line length 100. Formatting is judged by CI's pinned black: `uvx --from "black>=25.1,<26" black --check --line-length=100 rehoboam/`; `uvx ruff check rehoboam/` clean. Stage only files you changed. Pre-commit hooks run on commit; if a hook reformats a file and fails the commit, stage again and re-run the same commit.
- Run tests with `uv run pytest -q -p no:cacheprovider`. Baseline: 1615 passed, 2 skipped. Delete `logs/*.db` before a full run.
- Every commit message ends with these two lines exactly:
  ```
  Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01F4d1fpUbNh28evK1pNy4Le
  ```
- Docstrings and comments explain *why*; never narrate the diff.
- The 20-hour staleness window, the 8-minute deadline (480 s) and the 1500-request cap are `Settings` fields (env `INGEST_STALE_AFTER_HOURS`, `INGEST_DEADLINE_SECONDS`, `INGEST_MAX_REQUESTS`) so they can be retuned without a deploy.

______________________________________________________________________

## File structure

| file                                                                                                    | responsibility                                                                                                                          |
| ------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| `rehoboam/enrichment/rows.py` (new)                                                                     | pure functions: Kickbase responses → row dicts (`universe_rows`, `match_history_rows`, `mv_series_rows`, `transfer_rows`, `status_row`) |
| `rehoboam/enrichment/corpus.py`                                                                         | SQLite `TrainingCorpus`: unchanged interface; its writers call `rows.py`                                                                |
| `rehoboam/store/migrations/002_player_status_daily.sql` (new)                                           | `player_status_daily` table; `sweep_progress.status_fetched_at`                                                                         |
| `rehoboam/store/corpus_store.py` (new)                                                                  | `CorpusStore`: the store-backed writer the sweep and the ingestion use                                                                  |
| `rehoboam/kickbase_client.py`                                                                           | retry adapter on the session                                                                                                            |
| `rehoboam/enrichment/ingest.py` (new)                                                                   | `IngestBudget`, `IngestStats`, `run_ingestion`                                                                                          |
| `rehoboam/enrichment/sweep.py`                                                                          | `run_sweep` typed against the writer protocol; otherwise unchanged                                                                      |
| `rehoboam/store/export.py` (new)                                                                        | `export_tables(conn, upload, day)`                                                                                                      |
| `rehoboam/config.py`                                                                                    | three ingest settings                                                                                                                   |
| `rehoboam/cli.py`                                                                                       | `enrich-corpus` writes to the store; new `ingest` command                                                                               |
| `deploy/azure_function_external/{function_app.py,host.json,requirements.txt}` (new)                     | the second app                                                                                                                          |
| `deploy/deploy.sh`, `scripts/sync-azure-deps.sh`, `.github/workflows/ci.yml`, `deploy/bicep/main.bicep` | plumbing for two apps                                                                                                                   |
| `CLAUDE.md`, `.env.example`                                                                             | docs                                                                                                                                    |

______________________________________________________________________

### Task 1: Pure row builders; `TrainingCorpus` delegates to them

**Files:**

- Create: `rehoboam/enrichment/rows.py`
- Modify: `rehoboam/enrichment/corpus.py` (`record_match_history`, `record_mv_series`, `record_player_transfers` bodies; `_to_epoch` and `parse_minutes` stay where they are and are imported by `rows.py`)
- Modify: `rehoboam/enrichment/sweep.py` (`_universe_to_rows` becomes a re-export of `rows.universe_rows`)
- Test: `tests/test_enrichment/test_rows.py` (new); `tests/test_enrichment/test_corpus.py` and `tests/test_enrichment/test_sweep.py` must stay green unchanged

**Interfaces:**

- Produces (all pure, no I/O):

  - `rows.universe_rows(items: list[dict]) -> list[dict]` — keys `player_id, first_name, last_name, position, team_id, market_value, average_points` (exactly today's `_universe_to_rows`).
  - `rows.match_history_rows(player_id: str, team_id: str | None, performance: dict) -> list[dict]` — keys `player_id, season, day_number, match_date, points, minutes, team_id, opponent_team_id, is_home, status` (exactly today's flattening in `record_match_history`, including the `pt`-first team rule, `parse_minutes`, and the skip of day-less matches).
  - `rows.mv_series_rows(player_id: str, history: dict) -> list[dict]` — keys `player_id, snapshot_at, market_value` (`dt*86400.0`; non-positive `mv` dropped).
  - `rows.transfer_rows(player_id: str, history: dict) -> list[dict]` — keys `player_id, transfer_at, price, transfer_type, counterparty_id, counterparty_name` (items without a parseable `dt` skipped).
  - `rows.status_row(player_id: str, day: datetime.date, details: dict, fetched_at: float) -> dict` — keys `player_id, day, status, lineup_probability, market_value, team_id, fetched_at`; `status = details.get("st")`, `lineup_probability = details.get("prob")`, `market_value = details.get("mv")`, `team_id = str(details["tid"]) if details.get("tid") is not None else None` (ints coerced with `int()` when present, else `None`).

- [ ] **Step 1: Write the failing tests**

`tests/test_enrichment/test_rows.py`:

```python
"""The row builders are pure: Kickbase payload in, row dicts out."""

from __future__ import annotations

from datetime import date

from rehoboam.enrichment import rows


def test_match_history_rows_use_pt_for_team_and_skip_dayless_matches():
    perf = {
        "it": [
            {
                "ti": "2025/2026",
                "ph": [
                    {
                        "day": 1,
                        "p": 80,
                        "mp": "90'",
                        "t1": "3",
                        "t2": "4",
                        "pt": "4",
                        "st": 5,
                    },
                    {"p": 10},  # no day: cannot be placed on a timeline
                ],
            }
        ]
    }
    out = rows.match_history_rows("p1", "3", perf)
    assert out == [
        {
            "player_id": "p1",
            "season": "2025/2026",
            "day_number": 1,
            "match_date": None,
            "points": 80,
            "minutes": 90,
            "team_id": "4",
            "opponent_team_id": "3",
            "is_home": 0,
            "status": 5,
        }
    ]


def test_match_history_rows_fall_back_to_caller_team_when_pt_missing():
    perf = {"it": [{"ti": "s", "ph": [{"day": 2, "p": 0, "t1": "3", "t2": "4"}]}]}
    (row,) = rows.match_history_rows("p1", "3", perf)
    assert (row["team_id"], row["is_home"], row["opponent_team_id"]) == ("3", 1, "4")


def test_mv_series_rows_drop_sentinels_and_scale_days():
    out = rows.mv_series_rows(
        "p1", {"it": [{"dt": 20000, "mv": 5}, {"dt": 20001, "mv": 0}]}
    )
    assert out == [
        {"player_id": "p1", "snapshot_at": 20000 * 86400.0, "market_value": 5}
    ]


def test_transfer_rows_skip_items_without_dt():
    hist = {
        "it": [
            {"u": "9", "unm": "X", "dt": "2026-08-01T10:00:00Z", "trp": 7, "t": 2},
            {"trp": 1},
        ]
    }
    (row,) = rows.transfer_rows("p1", hist)
    assert (
        row["counterparty_id"] == "9"
        and row["price"] == 7
        and row["transfer_type"] == 2
    )
    assert isinstance(row["transfer_at"], float)


def test_status_row_reads_st_prob_mv_tid():
    row = rows.status_row(
        "p1", date(2026, 9, 14), {"st": 0, "prob": 1, "mv": 1_000_000, "tid": 7}, 123.0
    )
    assert row == {
        "player_id": "p1",
        "day": date(2026, 9, 14),
        "status": 0,
        "lineup_probability": 1,
        "market_value": 1_000_000,
        "team_id": "7",
        "fetched_at": 123.0,
    }


def test_status_row_tolerates_missing_fields():
    row = rows.status_row("p1", date(2026, 9, 14), {}, 1.0)
    assert (
        row["status"],
        row["lineup_probability"],
        row["market_value"],
        row["team_id"],
    ) == (
        None,
        None,
        None,
        None,
    )


def test_universe_rows_map_live_field_names():
    out = rows.universe_rows(
        [{"pi": 5, "n": "Kane", "pos": 4, "tid": "2", "mv": 9, "ap": 1.5}, {}]
    )
    assert out == [
        {
            "player_id": "5",
            "first_name": None,
            "last_name": "Kane",
            "position": "Forward",
            "team_id": "2",
            "market_value": 9,
            "average_points": 1.5,
        }
    ]
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/test_enrichment/test_rows.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'rows'`.

- [ ] **Step 3: Write `rehoboam/enrichment/rows.py`**

```python
"""Kickbase payloads → corpus rows, with no I/O.

Both corpus writers — the SQLite ``TrainingCorpus`` the offline tools read
and the store's ``CorpusStore`` the ingestion writes — persist the same
rows, so the parsing lives once, here. Every rule in this module was
verified against live responses (dates in the docstrings) and must not be
re-derived from field names.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from rehoboam.enrichment.corpus import _to_epoch, parse_minutes

POSITIONS = {1: "Goalkeeper", 2: "Defender", 3: "Midfielder", 4: "Forward"}


def universe_rows(items: list[dict]) -> list[dict]:
    """Lineup-selection items → ``player_universe`` rows.

    The id is ``pi`` and there is no first-name field on this endpoint
    (measured live, 2026-07-29), so ``first_name`` is always None.
    """
    out = []
    for item in items:
        pid = item.get("pi")
        if pid is None:
            continue
        out.append(
            {
                "player_id": str(pid),
                "first_name": None,
                "last_name": item.get("n"),
                "position": POSITIONS.get(item.get("pos")),
                "team_id": item.get("tid"),
                "market_value": item.get("mv"),
                "average_points": item.get("ap"),
            }
        )
    return out


def match_history_rows(
    player_id: str, team_id: str | None, performance: dict
) -> list[dict]:
    """Performance response → one row per placed match.

    ``pt`` is the player's team for that match and is the primary source for
    home/away and opponent; the caller's ``team_id`` is the *current* team and
    is wrong for every match before the player's last transfer, so it is only
    a fallback. ``ap``/``tp``/``asp`` are season-end totals stamped on every
    row (verified live) and are deliberately not stored — they would leak the
    season outcome into training rows.
    """
    rows: list[dict] = []
    fallback_team = str(team_id) if team_id is not None else None
    for season in performance.get("it") or []:
        title = season.get("ti")
        if not title:
            continue
        for m in season.get("ph") or []:
            day = m.get("day")
            if day is None:
                continue
            t1 = str(m.get("t1", "")) or None
            t2 = str(m.get("t2", "")) or None
            pt = m.get("pt")
            team = str(pt) if pt is not None else fallback_team
            is_home = 1 if team is not None and team == t1 else 0
            st = m.get("st")
            rows.append(
                {
                    "player_id": str(player_id),
                    "season": str(title),
                    "day_number": int(day),
                    "match_date": m.get("md"),
                    "points": int(m.get("p") or 0),
                    "minutes": parse_minutes(m.get("mp")),
                    "team_id": team,
                    "opponent_team_id": t2 if is_home else t1,
                    "is_home": is_home,
                    "status": int(st) if st is not None else None,
                }
            )
    return rows


def mv_series_rows(player_id: str, history: dict) -> list[dict]:
    """Market-value history → rows; ``dt`` is days since epoch; ``mv <= 0`` is a sentinel."""
    return [
        {
            "player_id": str(player_id),
            "snapshot_at": float(item["dt"]) * 86400.0,
            "market_value": int(item["mv"]),
        }
        for item in (history.get("it") or [])
        if item.get("dt") is not None and item.get("mv") and item["mv"] > 0
    ]


def transfer_rows(player_id: str, history: dict) -> list[dict]:
    """Transfer history → rows; ``dt`` here is ISO-8601 text (verified 2026-07-29)."""
    rows: list[dict] = []
    for item in history.get("it") or []:
        dt = item.get("dt")
        if not dt:
            continue
        try:
            transfer_at = _to_epoch(dt)
        except (ValueError, TypeError):
            continue
        counterparty_id = item.get("u")
        rows.append(
            {
                "player_id": str(player_id),
                "transfer_at": transfer_at,
                "price": item.get("trp"),
                "transfer_type": item.get("t"),
                "counterparty_id": (
                    str(counterparty_id) if counterparty_id is not None else None
                ),
                "counterparty_name": item.get("unm"),
            }
        )
    return rows


def _opt_int(value: Any) -> int | None:
    return int(value) if value is not None else None


def status_row(player_id: str, day: date, details: dict, fetched_at: float) -> dict:
    """League player details → one ``player_status_daily`` row.

    ``st`` is the injury/availability status (0 healthy), ``prob`` the lineup
    probability (1 starter … 5 unlikely) — the two fields the scorer never had
    day by day. Missing fields stay None rather than becoming a fake healthy
    starter.
    """
    tid = details.get("tid")
    return {
        "player_id": str(player_id),
        "day": day,
        "status": _opt_int(details.get("st")),
        "lineup_probability": _opt_int(details.get("prob")),
        "market_value": _opt_int(details.get("mv")),
        "team_id": str(tid) if tid is not None else None,
        "fetched_at": float(fetched_at),
    }
```

Check `corpus.py` for the names `_to_epoch` and `parse_minutes` (both exist there today; `parse_minutes` may live in `rehoboam/enrichment/corpus.py` or be imported into it — import from wherever it is defined, never copy it). The import of `corpus` from `rows` and of `rows` from `corpus` would be circular: in `corpus.py`, import `rows` **inside** the three writer methods, not at module top.

- [ ] **Step 4: Make `TrainingCorpus` delegate**

In `rehoboam/enrichment/corpus.py`:

- `record_match_history`: replace the flattening loop with `from rehoboam.enrichment import rows as _rows` (inside the method) and `rows = [(r["player_id"], r["season"], r["day_number"], r["match_date"], r["points"], r["minutes"], r["team_id"], r["opponent_team_id"], r["is_home"], r["status"]) for r in _rows.match_history_rows(player_id, team_id, performance)]`; keep the docstring's *why* (trim the parts now living in `rows.py` to one sentence pointing there).
- `record_mv_series`: `rows = [(r["player_id"], r["snapshot_at"], r["market_value"]) for r in _rows.mv_series_rows(player_id, history)]`.
- `record_player_transfers`: `rows = [(r["player_id"], r["transfer_at"], r["price"], r["transfer_type"], r["counterparty_id"], r["counterparty_name"]) for r in _rows.transfer_rows(player_id, history)]`.

In `rehoboam/enrichment/sweep.py`: delete `_POSITIONS` and `_universe_to_rows`; `from rehoboam.enrichment.rows import POSITIONS, universe_rows`; use `universe_rows(items)` in `fetch_universe` and `POSITIONS.get(details.get("pos"))` in the historical-ids block.

- [ ] **Step 5: Run the enrichment tests**

Run: `uv run pytest tests/test_enrichment -q -p no:cacheprovider`
Expected: all pass (the existing corpus and sweep tests unchanged, plus 7 new).

- [ ] **Step 6: Commit**

```bash
git add rehoboam/enrichment/rows.py rehoboam/enrichment/corpus.py rehoboam/enrichment/sweep.py tests/test_enrichment/test_rows.py
git commit -m "refactor(enrichment): the row builders are pure and shared; TrainingCorpus delegates"
```

______________________________________________________________________

### Task 2: Migration 002 and `CorpusStore`

**Files:**

- Create: `rehoboam/store/migrations/002_player_status_daily.sql`
- Create: `rehoboam/store/corpus_store.py`
- Modify: `rehoboam/store/import_sqlite.py` — no change to `CORPUS_TABLES` (the offline pull does not need status rows); nothing else
- Test: `tests/store/test_corpus_store.py` (new); `tests/store/test_migrate.py` (one assertion: 002 is applied and the table exists)

**Interfaces:**

- Consumes: `rehoboam.store.connect`, `rehoboam.enrichment.rows`.

- Produces:

  - Table `rehoboam.player_status_daily(player_id text, day date, status integer, lineup_probability integer, market_value bigint, team_id text, fetched_at double precision, primary key (player_id, day))`; column `rehoboam.sweep_progress.status_fetched_at double precision`.
  - `CorpusStore(dsn: str | None = None)`, `connection()`.
  - Writers (return rows written): `upsert_players(players: list[dict]) -> int`, `ensure_players(player_ids: list[str]) -> int`, `record_match_history(player_id, team_id, performance) -> int`, `record_mv_series(player_id, history) -> int`, `record_player_transfers(player_id, history) -> int`, `record_status_daily(player_id: str, day: date, details: dict, fetched_at: float) -> int`, `mark_fetched(player_id, *, performance=False, mv=False, transfers=False, status=False) -> None`, `clear_performance_fetched(player_ids=None) -> int`.
  - Readers: `players_needing_fetch(kind) -> list[str]` (never fetched; kinds `performance|mv|transfers|status`), `players_needing_refresh(kind, older_than: float) -> list[str]` (never fetched or fetched before `older_than`, **oldest first**, ties by `player_id`), `players_missing_position(player_ids) -> list[str]`, `positions_for(player_ids) -> dict[str, str]`, `status_on(player_id, day) -> dict | None`.

- [ ] **Step 1: Write the failing tests**

`tests/store/test_corpus_store.py`:

```python
"""CorpusStore writes the corpus tables in the store, idempotently, and reports staleness."""

from __future__ import annotations

from datetime import date

from rehoboam.store import connect
from rehoboam.store.corpus_store import CorpusStore

PERF = {
    "it": [
        {
            "ti": "2025/2026",
            "ph": [{"day": 1, "p": 80, "mp": "90'", "t1": "3", "t2": "4", "pt": "3"}],
        }
    ]
}
MV = {"it": [{"dt": 20000, "mv": 5_000_000}, {"dt": 20001, "mv": 0}]}
TRANSFERS = {
    "it": [{"u": "9", "unm": "X", "dt": "2026-08-01T10:00:00Z", "trp": 7, "t": 2}]
}


def _universe(store: CorpusStore, *ids: str) -> None:
    store.upsert_players(
        [
            {"player_id": i, "last_name": i, "position": "Forward", "team_id": "3"}
            for i in ids
        ]
    )


def test_upsert_players_is_idempotent_and_updates(store_dsn):
    store = CorpusStore(dsn=store_dsn)
    _universe(store, "p1")
    store.upsert_players(
        [{"player_id": "p1", "last_name": "New", "position": "Forward"}]
    )
    with connect(store_dsn) as conn:
        rows = conn.execute("select last_name from rehoboam.player_universe").fetchall()
    assert [r["last_name"] for r in rows] == ["New"]


def test_ensure_players_never_overwrites(store_dsn):
    store = CorpusStore(dsn=store_dsn)
    _universe(store, "p1")
    assert store.ensure_players(["p1", "p2"]) == 1
    assert store.positions_for(["p1", "p2"]) == {"p1": "Forward"}
    assert store.players_missing_position(["p1", "p2", "p3"]) == ["p2", "p3"]


def test_match_history_mv_and_transfers_round_trip(store_dsn):
    store = CorpusStore(dsn=store_dsn)
    _universe(store, "p1")
    assert store.record_match_history("p1", "3", PERF) == 1
    assert store.record_match_history("p1", "3", PERF) == 1  # replace, not duplicate
    assert store.record_mv_series("p1", MV) == 1
    assert store.record_player_transfers("p1", TRANSFERS) == 1
    with connect(store_dsn) as conn:
        n = conn.execute(
            "select count(*) as n from rehoboam.player_match_history"
        ).fetchone()
        mv = conn.execute("select market_value from rehoboam.mv_series").fetchone()
        tr = conn.execute(
            "select counterparty_id from rehoboam.player_transfers"
        ).fetchone()
    assert (n["n"], mv["market_value"], tr["counterparty_id"]) == (1, 5_000_000, "9")


def test_status_daily_is_one_row_per_player_per_day_and_refetch_replaces(store_dsn):
    store = CorpusStore(dsn=store_dsn)
    _universe(store, "p1")
    day = date(2026, 9, 14)
    store.record_status_daily("p1", day, {"st": 0, "prob": 1, "mv": 5, "tid": 3}, 10.0)
    store.record_status_daily("p1", day, {"st": 1, "prob": 5, "mv": 4, "tid": 3}, 20.0)
    row = store.status_on("p1", day)
    assert (
        row["status"],
        row["lineup_probability"],
        row["market_value"],
        row["fetched_at"],
    ) == (
        1,
        5,
        4,
        20.0,
    )
    assert store.status_on("p1", date(2026, 9, 13)) is None


def test_players_needing_refresh_orders_never_fetched_then_oldest(store_dsn):
    store = CorpusStore(dsn=store_dsn)
    _universe(store, "a", "b", "c", "d")
    store.mark_fetched("a", status=True)  # now
    with connect(store_dsn) as conn:
        conn.execute(
            "update rehoboam.sweep_progress set status_fetched_at = %s where player_id = 'a'",
            (1_000.0,),
        )
    store.mark_fetched("b", status=True)  # fresh
    assert store.players_needing_refresh("status", older_than=5_000.0) == [
        "c",
        "d",
        "a",
    ]
    assert store.players_needing_fetch("status") == ["c", "d"]


def test_clear_performance_fetched_is_scoped(store_dsn):
    store = CorpusStore(dsn=store_dsn)
    _universe(store, "a", "b")
    store.mark_fetched("a", performance=True)
    store.mark_fetched("b", performance=True)
    assert store.clear_performance_fetched(["a"]) == 1
    assert store.players_needing_fetch("performance") == ["a"]
```

Add to `tests/store/test_migrate.py`:

```python
def test_migrate_applies_002_and_creates_player_status_daily(blank_dsn):
    from rehoboam.store import SCHEMA, connect
    from rehoboam.store.migrate import migrate

    with connect(blank_dsn) as conn:
        applied = migrate(conn)
        exists = conn.execute(
            "select to_regclass(%s) as t", (f"{SCHEMA}.player_status_daily",)
        )
        col = conn.execute(
            "select 1 from information_schema.columns where table_schema = %s "
            "and table_name = 'sweep_progress' and column_name = 'status_fetched_at'",
            (SCHEMA,),
        ).fetchone()
    assert "002_player_status_daily.sql" in applied
    assert exists.fetchone()["t"] is not None and col is not None
```

(Adjust the `to_regclass` call so it is executed before the `with` block closes: `exists = conn.execute(...).fetchone()["t"]` inside the block.)

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/store/test_corpus_store.py tests/store/test_migrate.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError` and the 002 assertion.

- [ ] **Step 3: The migration**

`rehoboam/store/migrations/002_player_status_daily.sql`:

```sql
-- PR C1: the availability history the scorer never had — one row per player per day.
create table if not exists rehoboam.player_status_daily (
    player_id          text not null,
    day                date not null,
    status             integer,          -- Kickbase st: 0 healthy, 1/2/4/256 unavailable
    lineup_probability integer,          -- Kickbase prob: 1 starter … 5 unlikely
    market_value       bigint,
    team_id            text,
    fetched_at         double precision not null,
    primary key (player_id, day)
);
create index if not exists idx_player_status_daily_day on rehoboam.player_status_daily (day);
alter table rehoboam.sweep_progress add column if not exists status_fetched_at double precision;
```

The migration runner applies files in name order and refreshes the bot role's grants after applying (B1); nothing else is needed for tests, whose template database is migrated by `tests/conftest.py`'s load hook.

- [ ] **Step 4: Write `rehoboam/store/corpus_store.py`**

```python
"""The store-backed corpus writer: what the ingestion and the sweep write.

Same rows as the SQLite ``TrainingCorpus`` (they share ``enrichment.rows``),
but in ``rehoboam.*``. The SQLite class stays the offline reader; this one
is where the league-wide data lands from now on (spec §2).
"""

from __future__ import annotations

import time
from datetime import date
from typing import Any

from rehoboam.enrichment import rows as _rows

_PROGRESS_COLUMNS = {
    "performance": "performance_fetched_at",
    "mv": "mv_fetched_at",
    "transfers": "transfers_fetched_at",
    "status": "status_fetched_at",
}


class CorpusStore:
    """One transaction per call, on the store."""

    def __init__(self, dsn: str | None = None):
        self.dsn = dsn

    def connection(self):
        from rehoboam.store import connect

        return connect(self.dsn)

    # ---- writers -------------------------------------------------------

    def upsert_players(self, players: list[dict[str, Any]]) -> int:
        if not players:
            return 0
        with self.connection() as conn, conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO rehoboam.player_universe (
                    player_id, first_name, last_name, position,
                    team_id, market_value, average_points
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (player_id) DO UPDATE SET
                    first_name = excluded.first_name,
                    last_name = excluded.last_name,
                    position = excluded.position,
                    team_id = excluded.team_id,
                    market_value = excluded.market_value,
                    average_points = excluded.average_points
                """,
                [
                    (
                        str(p["player_id"]),
                        p.get("first_name"),
                        p.get("last_name"),
                        p.get("position"),
                        str(p["team_id"]) if p.get("team_id") is not None else None,
                        p.get("market_value"),
                        p.get("average_points"),
                    )
                    for p in players
                ],
            )
        return len(players)

    def ensure_players(self, player_ids: list[str]) -> int:
        """Stub rows for ids new to the corpus; never overwrites (see TrainingCorpus)."""
        ids = [str(p) for p in player_ids]
        if not ids:
            return 0
        inserted = 0
        with self.connection() as conn:
            for pid in ids:
                cur = conn.execute(
                    "INSERT INTO rehoboam.player_universe (player_id) VALUES (%s) "
                    "ON CONFLICT (player_id) DO NOTHING",
                    (pid,),
                )
                inserted += cur.rowcount
        return inserted

    def record_match_history(
        self, player_id: str, team_id: str | None, performance: dict[str, Any]
    ) -> int:
        rows = _rows.match_history_rows(player_id, team_id, performance)
        if not rows:
            return 0
        with self.connection() as conn, conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO rehoboam.player_match_history (
                    player_id, season, day_number, match_date, points,
                    minutes, team_id, opponent_team_id, is_home, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (player_id, season, day_number) DO UPDATE SET
                    match_date = excluded.match_date,
                    points = excluded.points,
                    minutes = excluded.minutes,
                    team_id = excluded.team_id,
                    opponent_team_id = excluded.opponent_team_id,
                    is_home = excluded.is_home,
                    status = excluded.status
                """,
                [
                    (
                        r["player_id"],
                        r["season"],
                        r["day_number"],
                        r["match_date"],
                        r["points"],
                        r["minutes"],
                        r["team_id"],
                        r["opponent_team_id"],
                        r["is_home"],
                        r["status"],
                    )
                    for r in rows
                ],
            )
        return len(rows)

    def record_mv_series(self, player_id: str, history: dict[str, Any]) -> int:
        rows = _rows.mv_series_rows(player_id, history)
        if not rows:
            return 0
        with self.connection() as conn, conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO rehoboam.mv_series (player_id, snapshot_at, market_value) "
                "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                [(r["player_id"], r["snapshot_at"], r["market_value"]) for r in rows],
            )
        return len(rows)

    def record_player_transfers(self, player_id: str, history: dict[str, Any]) -> int:
        rows = _rows.transfer_rows(player_id, history)
        if not rows:
            return 0
        with self.connection() as conn, conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO rehoboam.player_transfers (
                    player_id, transfer_at, price, transfer_type,
                    counterparty_id, counterparty_name
                ) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING
                """,
                [
                    (
                        r["player_id"],
                        r["transfer_at"],
                        r["price"],
                        r["transfer_type"],
                        r["counterparty_id"],
                        r["counterparty_name"],
                    )
                    for r in rows
                ],
            )
        return len(rows)

    def record_status_daily(
        self, player_id: str, day: date, details: dict[str, Any], fetched_at: float
    ) -> int:
        """One row per player per day; a second fetch the same day replaces it,
        so the row always carries the latest reading before kickoff."""
        r = _rows.status_row(player_id, day, details, fetched_at)
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO rehoboam.player_status_daily (
                    player_id, day, status, lineup_probability, market_value, team_id, fetched_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (player_id, day) DO UPDATE SET
                    status = excluded.status,
                    lineup_probability = excluded.lineup_probability,
                    market_value = excluded.market_value,
                    team_id = excluded.team_id,
                    fetched_at = excluded.fetched_at
                """,
                (
                    r["player_id"],
                    r["day"],
                    r["status"],
                    r["lineup_probability"],
                    r["market_value"],
                    r["team_id"],
                    r["fetched_at"],
                ),
            )
        return 1

    def mark_fetched(
        self,
        player_id: str,
        *,
        performance: bool = False,
        mv: bool = False,
        transfers: bool = False,
        status: bool = False,
    ) -> None:
        """Record progress so an interrupted run resumes where it stopped."""
        now = time.time()
        wanted = [
            col
            for kind, col in _PROGRESS_COLUMNS.items()
            if {
                "performance": performance,
                "mv": mv,
                "transfers": transfers,
                "status": status,
            }[kind]
        ]
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO rehoboam.sweep_progress (player_id) VALUES (%s) "
                "ON CONFLICT (player_id) DO NOTHING",
                (str(player_id),),
            )
            for col in wanted:
                conn.execute(
                    f"UPDATE rehoboam.sweep_progress SET {col} = %s WHERE player_id = %s",
                    (now, str(player_id)),
                )

    def clear_performance_fetched(self, player_ids: list[str] | None = None) -> int:
        with self.connection() as conn:
            if player_ids is None:
                cur = conn.execute(
                    "UPDATE rehoboam.sweep_progress SET performance_fetched_at = NULL "
                    "WHERE performance_fetched_at IS NOT NULL"
                )
            else:
                ids = [str(p) for p in player_ids]
                if not ids:
                    return 0
                cur = conn.execute(
                    "UPDATE rehoboam.sweep_progress SET performance_fetched_at = NULL "
                    "WHERE performance_fetched_at IS NOT NULL AND player_id = ANY(%s)",
                    (ids,),
                )
            return cur.rowcount

    # ---- readers -------------------------------------------------------

    def players_needing_fetch(self, kind: str) -> list[str]:
        """Universe players never fetched for ``kind``, by id."""
        col = _PROGRESS_COLUMNS[kind]
        with self.connection() as conn:
            rows = conn.execute(f"""
                SELECT u.player_id FROM rehoboam.player_universe u
                LEFT JOIN rehoboam.sweep_progress s ON s.player_id = u.player_id
                WHERE s.{col} IS NULL
                ORDER BY u.player_id
                """).fetchall()
        return [r["player_id"] for r in rows]

    def players_needing_refresh(self, kind: str, *, older_than: float) -> list[str]:
        """Never fetched first, then stalest first — the order a budgeted run
        must process so an interrupted pass continues where it stopped."""
        col = _PROGRESS_COLUMNS[kind]
        with self.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT u.player_id FROM rehoboam.player_universe u
                LEFT JOIN rehoboam.sweep_progress s ON s.player_id = u.player_id
                WHERE s.{col} IS NULL OR s.{col} < %s
                ORDER BY s.{col} ASC NULLS FIRST, u.player_id
                """,
                (older_than,),
            ).fetchall()
        return [r["player_id"] for r in rows]

    def players_missing_position(self, player_ids: list[str]) -> list[str]:
        """Ids with no real position: stubs and ids unknown to the universe."""
        ids = [str(p) for p in player_ids]
        if not ids:
            return []
        with self.connection() as conn:
            known = conn.execute(
                "SELECT player_id FROM rehoboam.player_universe "
                "WHERE player_id = ANY(%s) AND position IS NOT NULL",
                (ids,),
            ).fetchall()
        resolved = {r["player_id"] for r in known}
        return sorted(i for i in ids if i not in resolved)

    def positions_for(self, player_ids: list[str]) -> dict[str, str]:
        ids = [str(p) for p in player_ids]
        if not ids:
            return {}
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT player_id, position FROM rehoboam.player_universe "
                "WHERE player_id = ANY(%s) AND position IS NOT NULL",
                (ids,),
            ).fetchall()
        return {r["player_id"]: r["position"] for r in rows}

    def status_on(self, player_id: str, day: date) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT player_id, day, status, lineup_probability, market_value, team_id, "
                "fetched_at FROM rehoboam.player_status_daily WHERE player_id = %s AND day = %s",
                (str(player_id), day),
            ).fetchone()
        return dict(row) if row else None
```

The f-strings interpolate only `_PROGRESS_COLUMNS` values (module constants), never input; bandit will flag B608 there as it does elsewhere in the repo — acceptable, it is non-gating.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/store -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add rehoboam/store/migrations/002_player_status_daily.sql rehoboam/store/corpus_store.py tests/store/test_corpus_store.py tests/store/test_migrate.py
git commit -m "feat(store): player_status_daily and CorpusStore — the corpus writer on the store"
```

______________________________________________________________________

### Task 3: The client retries 429 and 5xx with backoff

**Files:**

- Modify: `rehoboam/kickbase_client.py` (`__init__` only)
- Test: `tests/test_kickbase_client_retry.py` (new)

**Interfaces:**

- Produces: every request through `KickbaseV4Client.session` retries up to 3 times on 429/500/502/503/504 with exponential backoff (1 s, 2 s, 4 s), honouring `Retry-After`; other statuses are returned as today (the client's own status checks keep raising).

- [ ] **Step 1: Write the failing test**

```python
"""Every endpoint shares one session; the session retries the statuses a sweep meets."""

from __future__ import annotations

from rehoboam.kickbase_client import KickbaseV4Client


def test_session_retries_429_and_5xx_with_backoff():
    client = KickbaseV4Client()
    retry = client.session.get_adapter("https://api.kickbase.com").max_retries
    assert retry.total == 3
    assert set(retry.status_forcelist) == {429, 500, 502, 503, 504}
    assert retry.backoff_factor == 1.0
    assert retry.respect_retry_after_header is True
    assert retry.raise_on_status is False
    assert "GET" in retry.allowed_methods and "POST" not in retry.allowed_methods
```

- [ ] **Step 2: Run to see it fail**

Run: `uv run pytest tests/test_kickbase_client_retry.py -q -p no:cacheprovider`
Expected: FAIL — `retry.total` is `0` on the default adapter.

- [ ] **Step 3: Mount the adapter**

In `KickbaseV4Client.__init__`, after the headers update:

```python
        # A league-wide pass is ~1,500 requests; without this a single 429 or
        # a 502 from the CDN failed the player and the sweep moved on. Retries
        # are GET-only: a retried POST could place a bid twice.
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry

        retry = Retry(
            total=3,
            backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
```

`raise_on_status=False` keeps the client's own `if response.status_code == 200` branches in charge of error reporting after the retries are exhausted.

- [ ] **Step 4: Run the test and the client tests**

Run: `uv run pytest tests/test_kickbase_client_retry.py tests -q -p no:cacheprovider -k "client or api"`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add rehoboam/kickbase_client.py tests/test_kickbase_client_retry.py
git commit -m "feat(client): GETs retry 429 and 5xx with exponential backoff"
```

______________________________________________________________________

### Task 4: `run_ingestion` with a budget; the sweep and `enrich-corpus` write to the store; the `ingest` command

**Files:**

- Create: `rehoboam/enrichment/ingest.py`
- Modify: `rehoboam/enrichment/sweep.py` (type hint only: `corpus: CorpusWriter`), `rehoboam/config.py` (three settings), `rehoboam/cli.py` (`enrich-corpus` uses `CorpusStore`; new `ingest`)
- Test: `tests/test_enrichment/test_ingest.py` (new); `tests/test_cli_ingest.py` (new, CLI wiring only)

**Interfaces:**

- Consumes: `CorpusStore` (Task 2), `rows` (Task 1), `fetch_universe` (sweep).

- Produces:

  - `Settings.ingest_stale_after_hours: float = 20.0`, `Settings.ingest_deadline_seconds: float = 480.0`, `Settings.ingest_max_requests: int = 1500` (env names uppercase).
  - `ingest.CorpusWriter` — a `typing.Protocol` with the writer/reader method names both corpus classes implement (`upsert_players`, `ensure_players`, `record_match_history`, `record_mv_series`, `record_player_transfers`, `mark_fetched`, `clear_performance_fetched`, `players_needing_fetch`, `players_missing_position`, `positions_for`); `run_sweep`'s `corpus` parameter is annotated with it.
  - `ingest.IngestBudget(deadline: float, max_requests: int, now: Callable[[], float] = time.time)` with `spend() -> None` (counts one request), `exhausted() -> str | None` (`"deadline"`, `"cap"` or `None`), `requests: int`.
  - `ingest.IngestStats(universe_size, status_written, performance_fetched, mv_fetched, failed, requests, stopped_by: str | None, started_at: float, duration_s: float)`.
  - `ingest.run_ingestion(client, store: CorpusStore, *, league_id: str, budget: IngestBudget, stale_after_seconds: float, throttle_seconds: float = 0.25, timeframe_days: int = 365, today: date | None = None) -> IngestStats`.
  - CLI `rehoboam ingest [--deadline-seconds] [--max-requests] [--throttle]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_enrichment/test_ingest.py`:

```python
"""The budgeted pass: stalest first, stops cleanly, resumes next time."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from rehoboam.enrichment.ingest import IngestBudget, run_ingestion
from rehoboam.store import connect
from rehoboam.store.corpus_store import CorpusStore

LEAGUE = "L"
DAY = date(2026, 9, 14)


def _client(ids: list[str]) -> MagicMock:
    client = MagicMock()
    items = [{"pi": i, "n": i, "pos": 4, "tid": "3", "mv": 1, "ap": 1.0} for i in ids]
    client.get_lineup_selection.side_effect = (
        lambda *, league_id, position, start, max_items: (
            {"it": items} if position == 4 and start == 0 else {"it": []}
        )
    )
    client.get_player_details.return_value = {"st": 0, "prob": 1, "mv": 1, "tid": "3"}
    client.get_competition_player_performance.return_value = {
        "it": [
            {
                "ti": "2025/2026",
                "ph": [
                    {"day": 1, "p": 5, "mp": "90'", "t1": "3", "t2": "4", "pt": "3"}
                ],
            }
        ]
    }
    client.get_player_market_value_history_v2.return_value = {
        "it": [{"dt": 20000, "mv": 9}]
    }
    return client


class Clock:
    def __init__(self, t: float = 1_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def test_full_pass_writes_status_history_and_mv_for_every_player(store_dsn):
    store, client, clock = CorpusStore(dsn=store_dsn), _client(["a", "b"]), Clock()
    budget = IngestBudget(deadline=clock.t + 480, max_requests=1_500, now=clock)
    stats = run_ingestion(
        client,
        store,
        league_id=LEAGUE,
        budget=budget,
        stale_after_seconds=72_000,
        throttle_seconds=0,
        today=DAY,
    )
    assert (
        stats.universe_size,
        stats.status_written,
        stats.performance_fetched,
        stats.mv_fetched,
    ) == (2, 2, 2, 2)
    assert stats.stopped_by is None and stats.failed == 0
    # Universe: positions 1-3 answer one empty page each, position 4 a full page
    # then an empty one = 5 requests; then 3 per player.
    assert stats.requests == 5 + 2 * 3
    assert store.status_on("a", DAY)["lineup_probability"] == 1
    with connect(store_dsn) as conn:
        n = conn.execute(
            "select count(*) as n from rehoboam.player_match_history"
        ).fetchone()["n"]
    assert n == 2


def test_fresh_players_are_skipped_and_stale_ones_refreshed_oldest_first(store_dsn):
    store, client, clock = (
        CorpusStore(dsn=store_dsn),
        _client(["a", "b", "c"]),
        Clock(100_000.0),
    )
    store.upsert_players(
        [{"player_id": i, "position": "Forward"} for i in ("a", "b", "c")]
    )
    for pid, at in (("a", 10_000.0), ("b", 99_000.0), ("c", 20_000.0)):
        store.mark_fetched(pid, status=True, performance=True, mv=True)
        with connect(store_dsn) as conn:
            conn.execute(
                "update rehoboam.sweep_progress set status_fetched_at = %s, "
                "performance_fetched_at = %s, mv_fetched_at = %s where player_id = %s",
                (at, at, at, pid),
            )
    budget = IngestBudget(deadline=clock.t + 480, max_requests=1_500, now=clock)
    stats = run_ingestion(
        client,
        store,
        league_id=LEAGUE,
        budget=budget,
        stale_after_seconds=72_000,
        throttle_seconds=0,
        today=DAY,
    )
    assert stats.status_written == 2  # b is fresh
    calls = [c.kwargs["player_id"] for c in client.get_player_details.call_args_list]
    assert calls == ["a", "c"]  # oldest first


def test_deadline_stops_cleanly_and_next_run_resumes(store_dsn):
    store, client, clock = CorpusStore(dsn=store_dsn), _client(["a", "b", "c"]), Clock()
    budget = IngestBudget(deadline=clock.t + 480, max_requests=1_500, now=clock)

    def _details(*, league_id, player_id):
        clock.t += 300  # each details call burns five minutes
        return {"st": 0, "prob": 2, "mv": 1, "tid": "3"}

    client.get_player_details.side_effect = _details
    stats = run_ingestion(
        client,
        store,
        league_id=LEAGUE,
        budget=budget,
        stale_after_seconds=72_000,
        throttle_seconds=0,
        today=DAY,
    )
    assert stats.stopped_by == "deadline"
    assert stats.status_written == 2 and stats.performance_fetched == 0
    # Next run: the remaining player is first in line.
    client.get_player_details.side_effect = None
    budget2 = IngestBudget(deadline=clock.t + 480, max_requests=1_500, now=clock)
    stats2 = run_ingestion(
        client,
        store,
        league_id=LEAGUE,
        budget=budget2,
        stale_after_seconds=72_000,
        throttle_seconds=0,
        today=DAY,
    )
    assert stats2.status_written == 1 and stats2.stopped_by is None


def test_request_cap_stops_cleanly(store_dsn):
    store, client, clock = CorpusStore(dsn=store_dsn), _client(["a", "b"]), Clock()
    budget = IngestBudget(
        deadline=clock.t + 480, max_requests=6, now=clock
    )  # 5 pages + 1
    stats = run_ingestion(
        client,
        store,
        league_id=LEAGUE,
        budget=budget,
        stale_after_seconds=72_000,
        throttle_seconds=0,
        today=DAY,
    )
    assert stats.stopped_by == "cap" and stats.requests == 6
    assert stats.status_written == 1


def test_a_failing_player_is_counted_and_the_pass_continues(store_dsn):
    store, client, clock = CorpusStore(dsn=store_dsn), _client(["a", "b"]), Clock()
    client.get_player_details.side_effect = [RuntimeError("boom"), {"st": 0, "prob": 1}]
    budget = IngestBudget(deadline=clock.t + 480, max_requests=1_500, now=clock)
    stats = run_ingestion(
        client,
        store,
        league_id=LEAGUE,
        budget=budget,
        stale_after_seconds=72_000,
        throttle_seconds=0,
        today=DAY,
    )
    assert stats.failed == 1 and stats.status_written == 1
    assert store.players_needing_fetch("status") == [
        "a"
    ]  # not marked, retried next run
```

`tests/test_cli_ingest.py`:

```python
"""The ingest command refuses to start without the store, before any login."""

from __future__ import annotations

from typer.testing import CliRunner

from rehoboam.cli import app

runner = CliRunner()


def test_ingest_without_database_url_fails_before_login(monkeypatch):
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "test")
    monkeypatch.setenv("DATABASE_URL", "")
    result = runner.invoke(app, ["ingest"])
    assert result.exit_code == 1
    assert "DATABASE_URL" in result.output and "Traceback" not in result.output
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/test_enrichment/test_ingest.py tests/test_cli_ingest.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: rehoboam.enrichment.ingest`; the CLI test fails with "No such command".

- [ ] **Step 3: Settings**

In `rehoboam/config.py`, after `database_admin_url`:

```python
    ingest_stale_after_hours: float = Field(
        default=20.0,
        description=(
            "A player's status/performance/MV is refreshed by the ingestion when its last "
            "fetch is older than this. 20 h means twice-daily runs refresh everyone once a day "
            "and the second run only catches what the first could not. Env: INGEST_STALE_AFTER_HOURS."
        ),
    )
    ingest_deadline_seconds: float = Field(
        default=480.0,
        description="Ingestion stops cleanly after this many seconds (the Function has 600). "
        "Env: INGEST_DEADLINE_SECONDS.",
    )
    ingest_max_requests: int = Field(
        default=1500,
        description="Ingestion stops cleanly after this many Kickbase requests. Env: INGEST_MAX_REQUESTS.",
    )
```

- [ ] **Step 4: Write `rehoboam/enrichment/ingest.py`**

```python
"""The twice-daily league-wide pass (spec §2), within a budget.

Three reads per player — league player details (status + lineup
probability), competition performance (per-match history) and the
market-value series — for every player whose last fetch of that kind is
older than the staleness window, stalest first. The budget is a wall-clock
deadline and a request cap; a run that hits either stops between requests,
records why, and the next run starts with exactly the players it did not
reach, because progress is marked only after a successful write.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Protocol

from rehoboam.enrichment.sweep import fetch_universe

logger = logging.getLogger(__name__)


class CorpusWriter(Protocol):
    def upsert_players(self, players: list[dict]) -> int: ...
    def ensure_players(self, player_ids: list[str]) -> int: ...
    def record_match_history(
        self, player_id: str, team_id: str | None, performance: dict
    ) -> int: ...
    def record_mv_series(self, player_id: str, history: dict) -> int: ...
    def record_player_transfers(self, player_id: str, history: dict) -> int: ...
    def mark_fetched(
        self,
        player_id: str,
        *,
        performance: bool = ...,
        mv: bool = ...,
        transfers: bool = ...,
    ) -> None: ...
    def clear_performance_fetched(self, player_ids: list[str] | None = None) -> int: ...
    def players_needing_fetch(self, kind: str) -> list[str]: ...
    def players_missing_position(self, player_ids: list[str]) -> list[str]: ...
    def positions_for(self, player_ids: list[str]) -> dict[str, str]: ...


class BudgetExhausted(Exception):
    """Raised by ``IngestBudget.spend`` between requests; never mid-write."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class IngestBudget:
    deadline: float
    max_requests: int
    now: Callable[[], float] = time.time
    requests: int = 0

    def exhausted(self) -> str | None:
        if self.now() >= self.deadline:
            return "deadline"
        if self.requests >= self.max_requests:
            return "cap"
        return None

    def spend(self) -> None:
        """Account for one request about to be made; raise if the budget is gone."""
        reason = self.exhausted()
        if reason:
            raise BudgetExhausted(reason)
        self.requests += 1


@dataclass
class IngestStats:
    universe_size: int = 0
    status_written: int = 0
    performance_fetched: int = 0
    mv_fetched: int = 0
    failed: int = 0
    requests: int = 0
    stopped_by: str | None = None
    started_at: float = field(default_factory=time.time)
    duration_s: float = 0.0


def _counting_client(client, budget: IngestBudget):
    """Wrap the client so every call spends one unit of budget first."""

    class _Counting:
        def __getattr__(self, name):
            fn = getattr(client, name)

            def call(*a, **kw):
                budget.spend()
                return fn(*a, **kw)

            return call

    return _Counting()


def run_ingestion(
    client,
    store,
    *,
    league_id: str,
    budget: IngestBudget,
    stale_after_seconds: float,
    throttle_seconds: float = 0.25,
    timeframe_days: int = 365,
    today: date | None = None,
) -> IngestStats:
    stats = IngestStats(started_at=budget.now())
    day = today or datetime.now(tz=timezone.utc).date()
    api = _counting_client(client, budget)
    try:
        rows = fetch_universe(api, league_id, throttle_seconds=throttle_seconds)
        stats.universe_size = len(rows)
        store.upsert_players(rows)
        team_by_id = {r["player_id"]: r.get("team_id") for r in rows}
        older_than = budget.now() - stale_after_seconds

        def _pass(kind: str, fetch, write) -> int:
            done = 0
            for pid in store.players_needing_refresh(kind, older_than=older_than):
                try:
                    payload = fetch(pid)
                except BudgetExhausted:
                    raise
                except Exception as e:
                    stats.failed += 1
                    logger.warning("ingest %s failed for %s: %s", kind, pid, e)
                else:
                    write(pid, payload)
                    store.mark_fetched(pid, **{kind: True})
                    done += 1
                if throttle_seconds:
                    time.sleep(throttle_seconds)
            return done

        stats.status_written = _pass(
            "status",
            lambda pid: api.get_player_details(league_id=league_id, player_id=pid),
            lambda pid, d: store.record_status_daily(pid, day, d, budget.now()),
        )
        stats.performance_fetched = _pass(
            "performance",
            lambda pid: api.get_competition_player_performance(player_id=pid),
            lambda pid, p: store.record_match_history(pid, team_by_id.get(pid), p),
        )
        stats.mv_fetched = _pass(
            "mv",
            lambda pid: api.get_player_market_value_history_v2(
                player_id=pid, timeframe=timeframe_days
            ),
            lambda pid, h: store.record_mv_series(pid, h),
        )
    except BudgetExhausted as stop:
        stats.stopped_by = stop.reason
    stats.requests = budget.requests
    stats.duration_s = budget.now() - stats.started_at
    logger.info(
        "ingestion-end universe=%d status=%d perf=%d mv=%d failed=%d requests=%d "
        "stopped_by=%s duration=%.0fs",
        stats.universe_size,
        stats.status_written,
        stats.performance_fetched,
        stats.mv_fetched,
        stats.failed,
        stats.requests,
        stats.stopped_by,
        stats.duration_s,
    )
    return stats
```

Note on the `_pass` counters: when a pass is interrupted by `BudgetExhausted`, the `done` count of that pass is lost because the exception unwinds before assignment. Fix that inside `_pass` by writing progress to `stats` directly: pass the `IngestStats` attribute name instead of returning — `def _pass(kind, attr, fetch, write)` with `setattr(stats, attr, getattr(stats, attr) + 1)` after each successful write, and no return value. The tests above (`status_written == 2` after a deadline stop) require this. Implement it that way; the sketch above shows the shape, not the final counter plumbing.

In `rehoboam/enrichment/sweep.py`: `from rehoboam.enrichment.ingest import CorpusWriter` would be circular (ingest imports `fetch_universe`). Put `CorpusWriter` in `rehoboam/enrichment/rows.py`? No — it is not a row builder. Put it in a tiny new module `rehoboam/enrichment/writer.py` and import it from both `sweep.py` (`corpus: CorpusWriter`) and `ingest.py`. Update the file-structure table accordingly in your report.

- [ ] **Step 5: The CLI**

In `rehoboam/cli.py`:

- `enrich-corpus`: `from .store.corpus_store import CorpusStore`; `_ensure_store()` first; `corpus = CorpusStore()`; the final `console.print(f"[dim]Corpus: {corpus.db_path}[/dim]")` becomes `console.print("[dim]Corpus: the store[/dim]")`. Its docstring: the sweep now writes to the store; `corpus-pull` materialises it for the offline tools.
- New command, next to `enrich-corpus`:

```python
@app.command("ingest")
def ingest_cmd(
    deadline_seconds: float | None = typer.Option(None, "--deadline-seconds"),
    max_requests: int | None = typer.Option(None, "--max-requests"),
    throttle: float = typer.Option(
        0.25, "--throttle", help="Seconds between requests."
    ),
):
    """One budgeted ingestion pass — what func-rehoboam-external runs twice a day."""
    import time

    from .enrichment.ingest import IngestBudget, run_ingestion
    from .store.corpus_store import CorpusStore

    _ensure_store()
    api, settings, league = _login_and_get_league(0)
    budget = IngestBudget(
        deadline=time.time() + (deadline_seconds or settings.ingest_deadline_seconds),
        max_requests=max_requests or settings.ingest_max_requests,
    )
    stats = run_ingestion(
        api.client,
        CorpusStore(),
        league_id=league.id,
        budget=budget,
        stale_after_seconds=settings.ingest_stale_after_hours * 3600.0,
        throttle_seconds=throttle,
    )
    table = Table(title="Ingestion")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    for name in (
        "universe_size",
        "status_written",
        "performance_fetched",
        "mv_fetched",
        "failed",
        "requests",
    ):
        table.add_row(name, str(getattr(stats, name)))
    table.add_row("stopped_by", stats.stopped_by or "—")
    table.add_row("duration_s", f"{stats.duration_s:.0f}")
    console.print(table)
```

(`_login_and_get_league` already exists in `cli.py` and returns `(api, settings, league)`; check its exact name and return shape before use.)

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_enrichment tests/test_cli_ingest.py tests/store -q -p no:cacheprovider`
Expected: all pass. Then the whole suite: `rm -f logs/*.db; uv run pytest -q -p no:cacheprovider` — all pass.

- [ ] **Step 7: Commit**

```bash
git add rehoboam/enrichment/ingest.py rehoboam/enrichment/writer.py rehoboam/enrichment/sweep.py rehoboam/config.py rehoboam/cli.py tests/test_enrichment/test_ingest.py tests/test_cli_ingest.py
git commit -m "feat(ingest): the budgeted league-wide pass — stalest first, stops cleanly, resumes"
```

______________________________________________________________________

### Task 5: Weekly COPY export

**Files:**

- Create: `rehoboam/store/export.py`
- Test: `tests/store/test_export.py` (new)

**Interfaces:**

- Produces: `export.export_tables(conn: psycopg.Connection, upload: Callable[[str, bytes], None], *, day: date, tables: list[str] | None = None) -> dict[str, int]` — for every table in schema `rehoboam` (or `tables`), streams `COPY (SELECT * FROM rehoboam.<t>) TO STDOUT WITH (FORMAT csv, HEADER)` into gzip bytes and calls `upload(f"exports/{day.isoformat()}/{t}.csv.gz", data)`; returns compressed bytes per table. `export.store_tables(conn) -> list[str]` lists the schema's base tables in name order.

- [ ] **Step 1: Write the failing test**

```python
"""The weekly export is one gzip CSV per table, header included, readable back."""

from __future__ import annotations

import csv
import gzip
import io
from datetime import date

from rehoboam.store import connect
from rehoboam.store.export import export_tables, store_tables


def test_export_writes_every_table_as_gzip_csv_with_header(store_dsn):
    uploaded: dict[str, bytes] = {}
    with connect(store_dsn) as conn:
        conn.execute(
            "insert into rehoboam.team_value_history (snapshot_at, league_id, team_value, budget, "
            "squad_size) values (1.5, 'L', 100, 10, 15)"
        )
        sizes = export_tables(conn, uploaded.__setitem__, day=date(2026, 9, 14))
        names = store_tables(conn)
    assert "player_status_daily" in names and "schema_migrations" in names
    assert set(sizes) == set(names)
    blob = uploaded["exports/2026-09-14/team_value_history.csv.gz"]
    rows = list(csv.DictReader(io.StringIO(gzip.decompress(blob).decode())))
    assert rows[0]["league_id"] == "L" and rows[0]["team_value"] == "100"
    assert sizes["team_value_history"] == len(blob)
```

- [ ] **Step 2: Run to see it fail**

Run: `uv run pytest tests/store/test_export.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write `rehoboam/store/export.py`**

```python
"""Weekly insurance: every store table as gzip CSV in the Blob container (spec §1).

The free plan's backup policy is not something to depend on; a CSV per table
restores onto any Postgres. ``COPY ... TO STDOUT`` streams, so a 160k-row
table never sits in memory twice.
"""

from __future__ import annotations

import gzip
import io
from collections.abc import Callable
from datetime import date

import psycopg
from psycopg import sql

from rehoboam.store import SCHEMA


def store_tables(conn: psycopg.Connection) -> list[str]:
    rows = conn.execute(
        "select table_name from information_schema.tables "
        "where table_schema = %s and table_type = 'BASE TABLE' order by table_name",
        (SCHEMA,),
    ).fetchall()
    return [r["table_name"] for r in rows]


def export_tables(
    conn: psycopg.Connection,
    upload: Callable[[str, bytes], None],
    *,
    day: date,
    tables: list[str] | None = None,
) -> dict[str, int]:
    """Upload ``exports/<day>/<table>.csv.gz`` for each table; return bytes per table."""
    sizes: dict[str, int] = {}
    for table in tables or store_tables(conn):
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb") as gz, conn.cursor() as cur:
            with cur.copy(
                sql.SQL(
                    "COPY (SELECT * FROM {}.{}) TO STDOUT WITH (FORMAT csv, HEADER)"
                ).format(sql.Identifier(SCHEMA), sql.Identifier(table))
            ) as copy:
                for chunk in copy:
                    gz.write(bytes(chunk))
        data = buf.getvalue()
        upload(f"exports/{day.isoformat()}/{table}.csv.gz", data)
        sizes[table] = len(data)
    return sizes
```

- [ ] **Step 4: Run the test**

Run: `uv run pytest tests/store/test_export.py -q -p no:cacheprovider`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add rehoboam/store/export.py tests/store/test_export.py
git commit -m "feat(store): weekly export — every table as gzip CSV for a caller's uploader"
```

______________________________________________________________________

### Task 6: The second Function app and the plumbing for two apps

**Files:**

- Create: `deploy/azure_function_external/function_app.py`, `deploy/azure_function_external/host.json` (copy of the trading app's), `deploy/azure_function_external/requirements.txt` (generated), `deploy/azure_function_external/local.settings.json.example`
- Modify: `deploy/deploy.sh` (external source dir → `azure_function_external`), `scripts/sync-azure-deps.sh` (writes both files), `.github/workflows/ci.yml` (deps-sync checks both; deploy job publishes both apps), `deploy/bicep/main.bicep` (external app settings gain `KICKBASE_EMAIL`, `KICKBASE_PASSWORD`, `LEAGUE_INDEX`), `CLAUDE.md`, `.env.example`
- Test: none executable locally beyond `python -c "import ast; ast.parse(open(...).read())"` on the new app and `bash -n` on the scripts; the deploy is verified live in Task 7

**Interfaces:**

- Consumes: `ensure_ready`, `IngestBudget`, `run_ingestion`, `CorpusStore`, `export_tables`, `get_settings`, `KickbaseAPI`.

- Produces: timers `ingest` (`0 0 5,17 * * *`) and `weekly_export` (`0 0 3 * * 0`, Sunday 03:00 UTC).

- [ ] **Step 1: `function_app.py`**

```python
"""Azure Functions handler for func-rehoboam-external: ingestion and the weekly export.

Mirrors deploy/azure_function/function_app.py: heavy imports inside the
handlers, /tmp as the working directory, and `ensure_ready()` before any
work — a run against a half-migrated store must not start.
"""

import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import azure.functions as func

app = func.FunctionApp()
sys.path.insert(0, str(Path(__file__).parent))
TEMP_DIR = "/tmp"


def _prepare() -> None:
    os.chdir(TEMP_DIR)
    os.makedirs(f"{TEMP_DIR}/logs", exist_ok=True)


# 05:00 and 17:00 UTC — three hours before each trading session (spec §2).
@app.timer_trigger(schedule="0 0 5,17 * * *", arg_name="timer", run_on_startup=False)
def ingest(timer: func.TimerRequest):
    from rehoboam.api import KickbaseAPI
    from rehoboam.config import get_settings
    from rehoboam.enrichment.ingest import IngestBudget, run_ingestion
    from rehoboam.store import ensure_ready
    from rehoboam.store.corpus_store import CorpusStore

    _prepare()
    logging.info("ingestion-start")
    try:
        ensure_ready()
        settings = get_settings()
        api = KickbaseAPI(settings.kickbase_email, settings.kickbase_password)
        api.login()
        leagues = api.get_leagues()
        if not leagues:
            logging.error("ingestion: no leagues found")
            return
        league = leagues[int(os.getenv("LEAGUE_INDEX", "0"))]
        budget = IngestBudget(
            deadline=time.time() + settings.ingest_deadline_seconds,
            max_requests=settings.ingest_max_requests,
        )
        run_ingestion(
            api.client,
            CorpusStore(),
            league_id=league.id,
            budget=budget,
            stale_after_seconds=settings.ingest_stale_after_hours * 3600.0,
        )
    except Exception as e:
        logging.error(f"Ingestion failed: {e}", exc_info=True)


def _blob_uploader():
    """The container the SQLite era synced to; now it holds the weekly exports."""
    from azure.storage.blob import BlobServiceClient

    conn_str = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
    container = os.getenv("BLOB_CONTAINER", "rehoboam-data")
    client = BlobServiceClient.from_connection_string(conn_str).get_container_client(
        container
    )

    def upload(name: str, data: bytes) -> None:
        client.upload_blob(name=name, data=data, overwrite=True)

    return upload


# Sunday 03:00 UTC, when nothing else runs.
@app.timer_trigger(schedule="0 0 3 * * 0", arg_name="timer", run_on_startup=False)
def weekly_export(timer: func.TimerRequest):
    from rehoboam.store import connect, ensure_ready
    from rehoboam.store.export import export_tables

    _prepare()
    logging.info("export-start")
    try:
        ensure_ready()
        with connect() as conn:
            sizes = export_tables(
                conn, _blob_uploader(), day=datetime.now(tz=timezone.utc).date()
            )
        logging.info("export-end tables=%d bytes=%d", len(sizes), sum(sizes.values()))
    except Exception as e:
        logging.error(f"Export failed: {e}", exc_info=True)
```

`host.json`: identical to `deploy/azure_function/host.json`. `local.settings.json.example`: like the trading one with `DATABASE_URL`, `KICKBASE_EMAIL`, `KICKBASE_PASSWORD`, `LEAGUE_INDEX`, `AZURE_STORAGE_CONNECTION_STRING`, `BLOB_CONTAINER`, `AzureWebJobsStorage`.

- [ ] **Step 2: Plumbing**

- `scripts/sync-azure-deps.sh`: run `uv export ... -o deploy/azure_function/requirements.txt` as today, then `cp deploy/azure_function/requirements.txt deploy/azure_function_external/requirements.txt`; the echo names both. Run it to generate the file.

- `.github/workflows/ci.yml` `deploy-deps-sync`: the `git diff --exit-code` covers both paths (`git diff --exit-code deploy/azure_function/requirements.txt deploy/azure_function_external/requirements.txt`); message updated.

- `.github/workflows/ci.yml` `deploy` job: after the existing publish step, add "Prepare external package" (same copy steps from `deploy/azure_function_external/` into `deploy_package_external`, plus `rehoboam/`, `pyproject.toml`, `README.md`), the same docker pip-install step targeting that directory, and "Deploy external app" running `func azure functionapp publish func-rehoboam-external --python` in it. Keep the failure-issue step last.

- `deploy/deploy.sh`: `publish_function "func-rehoboam-external" "$SCRIPT_DIR/azure_function_external"`; update the header comment.

- `deploy/bicep/main.bicep` `externalAppSettings`: add `KICKBASE_EMAIL`, `KICKBASE_PASSWORD` (same Key Vault references as the trading app) and `LEAGUE_INDEX: leagueIndex`.

- `.env.example`: add the three ingest settings with their defaults, commented.

- `CLAUDE.md`: in the command list add `uv run rehoboam ingest` ("one budgeted ingestion pass — what func-rehoboam-external runs at 05:00/17:00 UTC"); in "The store" add a bullet: "**Ingestion (PR C1, 2026-09-14)**: `func-rehoboam-external` runs `enrichment/ingest.run_ingestion` twice a day into `store/corpus_store.CorpusStore` — universe, `player_status_daily` (status + lineup probability per player per day), match history, MV series — stalest first within `INGEST_DEADLINE_SECONDS` / `INGEST_MAX_REQUESTS`, and exports every table as gzip CSV to the Blob container on Sunday 03:00 UTC (`store/export.py`). The trading session still fetches per player; PR C2 switches it to read the store first."; fix the `deploy.sh code external` comment ("publish the ingestion app") and `enrich-corpus`'s comment ("writes to the store").

- [ ] **Step 3: Checks**

```bash
uv run python -c "import ast,sys; ast.parse(open('deploy/azure_function_external/function_app.py').read()); print('ok')"
bash -n deploy/deploy.sh && bash -n scripts/sync-azure-deps.sh && echo ok
bash scripts/sync-azure-deps.sh && git diff --exit-code deploy/azure_function/requirements.txt
uvx --from "black>=25.1,<26" black --check --line-length=100 rehoboam/ deploy/azure_function_external/function_app.py
uvx ruff check rehoboam/ deploy/azure_function_external/function_app.py
rm -f logs/*.db; uv run pytest -q -p no:cacheprovider
```

- [ ] **Step 4: Commit**

```bash
git add deploy/azure_function_external deploy/deploy.sh scripts/sync-azure-deps.sh .github/workflows/ci.yml deploy/bicep/main.bicep CLAUDE.md .env.example
git commit -m "feat(deploy): func-rehoboam-external ingests twice a day and exports weekly; CI publishes both apps"
```

______________________________________________________________________

### Task 7: Migration, first pass, deploy (controller-run)

- [ ] **Step 1: Apply 002 to prod as the admin** — `uv run rehoboam migrate` (uses `DATABASE_ADMIN_URL`); expect `applied 1 migration(s): 002_player_status_daily.sql`. This must precede any deploy of code that reads the table.
- [ ] **Step 2: Infra** — `bash deploy/deploy.sh infra --what-if` must show only the external app's three new settings; then `bash deploy/deploy.sh infra`, then `bash /Users/marco/dev/rehoboam/deploy/deploy.sh code trading` from the main checkout (an infra deploy resets the trading package) — or skip the republish if the merge and its CI deploy follow within the same quiet window.
- [ ] **Step 3: First pass locally** — `uv run rehoboam ingest` against the live store as the bot role (the spec's verification: ~450 universe rows, one `player_status_daily` row per player for today, non-zero finished `player_match_history` rows for the played matchdays). Record the stats table in the PR body; a second run should report `status_written` near 0 (fresh) unless the first stopped on the budget.
- [ ] **Step 4: Open the PR, merge** (`superpowers:finishing-a-development-branch`); CI publishes both apps. Verify the external app's functions: `az functionapp function list -n func-rehoboam-external -g rg-rehoboam --query "[].name" -o tsv` lists `ingest` and `weekly_export`.
- [ ] **Step 5: Verify the 17:00 UTC run** — App Insights `ingestion-start`/`ingestion-end` on `func-rehoboam-external`; store: `select count(*) from rehoboam.player_status_daily where day = current_date`.
- [ ] **Step 6: Memory** — update `project_data_foundation_rollout.md` (C1 merged, C2 and D next).

______________________________________________________________________

### Task 8: Interleave the three kinds per player (ruled after the first live pass)

**Why:** the first live pass (2026-09-14, 462 players) wrote 435 status rows in 480 s and stopped on the deadline before a single performance or MV fetch: the cost was two fresh pooler connections per kind (TLS + SCRAM), not the endpoint — Task 8's interleave fixes the starvation, the final fix wave's pinned connection fixes the rate. With per-kind passes, every run spends its whole budget on status and performance/MV starve forever. Processing *players* stalest-first, fetching every stale kind for each before moving on, spreads the budget across kinds; a stopped run resumes with the next stalest player, and MV series (one request per player, changes slowly) refresh weekly rather than daily.

**Files:**

- Modify: `rehoboam/store/corpus_store.py` (new reader), `rehoboam/enrichment/ingest.py` (`run_ingestion` loop), `rehoboam/config.py` (one setting), `rehoboam/cli.py` (`ingest` passes the MV window), `deploy/azure_function_external/function_app.py` (same), `.env.example`
- Test: `tests/store/test_corpus_store.py`, `tests/test_enrichment/test_ingest.py`

**Interfaces:**

- `Settings.ingest_mv_stale_after_hours: float = 144.0` (env `INGEST_MV_STALE_AFTER_HOURS`) — six days.

- `CorpusStore.players_needing_any_refresh(older_than: dict[str, float]) -> list[tuple[str, list[str]]]` — `older_than` maps kind (`status`, `performance`, `mv`) to the epoch before which that kind is stale. Returns `(player_id, stale_kinds)` for every universe player with at least one stale kind, ordered by the oldest of that player's relevant `fetched_at` values, never-fetched first (`NULLS FIRST`), ties by `player_id`; `stale_kinds` keeps the order `status, performance, mv`.

- `run_ingestion(client, store, *, league_id, budget, stale_after_seconds, mv_stale_after_seconds, throttle_seconds=0.25, timeframe_days=365, today=None)` — new required keyword `mv_stale_after_seconds`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/store/test_corpus_store.py`:

```python
def test_players_needing_any_refresh_orders_by_the_stalest_kind(store_dsn):
    store = CorpusStore(dsn=store_dsn)
    _universe(store, "a", "b", "c")
    with connect(store_dsn) as conn:
        # a: status fresh, performance stale (oldest of all); b: everything fresh; c: never fetched
        conn.execute(
            "insert into rehoboam.sweep_progress (player_id, status_fetched_at, "
            "performance_fetched_at, mv_fetched_at) values "
            "('a', 9_000.0, 1_000.0, 9_000.0), ('b', 9_000.0, 9_000.0, 9_000.0)"
        )
    out = store.players_needing_any_refresh(
        {"status": 5_000.0, "performance": 5_000.0, "mv": 5_000.0}
    )
    assert out == [("c", ["status", "performance", "mv"]), ("a", ["performance"])]


def test_players_needing_any_refresh_applies_per_kind_windows(store_dsn):
    store = CorpusStore(dsn=store_dsn)
    _universe(store, "a")
    with connect(store_dsn) as conn:
        conn.execute(
            "insert into rehoboam.sweep_progress (player_id, status_fetched_at, "
            "performance_fetched_at, mv_fetched_at) values ('a', 1_000.0, 1_000.0, 1_000.0)"
        )
    # MV window is wider: 1_000 is fresh for mv, stale for the other two.
    out = store.players_needing_any_refresh(
        {"status": 5_000.0, "performance": 5_000.0, "mv": 500.0}
    )
    assert out == [("a", ["status", "performance"])]
```

Replace the body of `tests/test_enrichment/test_ingest.py` so that every `run_ingestion(...)` call also passes `mv_stale_after_seconds=72_000`, and change these expectations to the interleaved semantics:

- `test_fresh_players_are_skipped_and_stale_ones_refreshed_oldest_first`: unchanged assertions (`status_written == 2`, details calls `["a", "c"]`) — still true.

- `test_deadline_stops_cleanly_and_next_run_resumes`: with each details call burning 300 s, the run now completes player `a` fully (details, performance, mv = 3 requests, the deadline only trips on the *next* `spend`) then starts `b`: its details call at t+300 succeeds (t+300 \< t+480), so `b`'s status is written and its performance fetch trips the deadline. Assert `stats.stopped_by == "deadline"`, `stats.status_written == 2`, `stats.performance_fetched == 1`, `stats.mv_fetched == 1`. The second run then refreshes `b`'s remaining kinds and all of `c`: assert `stats2.stopped_by is None`, `stats2.status_written == 1` (only `c`; `b`'s status is fresh), `stats2.performance_fetched == 2`, `stats2.mv_fetched == 2`.

- `test_request_cap_stops_cleanly`: `max_requests=6` → 5 universe pages + `a`'s details; assert `stats.stopped_by == "cap"`, `stats.requests == 6`, `stats.status_written == 1`, `stats.performance_fetched == 0`.

- `test_a_failing_player_is_counted_and_the_pass_continues`: `a`'s details fail, its performance and mv still run (a failed kind does not skip the player's other kinds), `b` completes: assert `stats.failed == 1`, `stats.status_written == 1`, `stats.performance_fetched == 2`, and `store.players_needing_fetch("status") == ["a"]`.

- Add `test_mv_refreshes_on_its_own_wider_window`: universe `a` with all three fetched at `clock.t - 100_000`, `stale_after_seconds=72_000`, `mv_stale_after_seconds=200_000` → status and performance fetched (1 each), `mv_fetched == 0`.

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/store/test_corpus_store.py tests/test_enrichment/test_ingest.py -q -p no:cacheprovider`
Expected: FAIL — missing reader, unexpected keyword `mv_stale_after_seconds`.

- [ ] **Step 3: The reader**

In `CorpusStore`:

```python
def players_needing_any_refresh(
    self, older_than: dict[str, float]
) -> list[tuple[str, list[str]]]:
    """Players with at least one stale kind, stalest player first.

    A player's staleness is the oldest of its relevant fetch times, never
    fetched counting as oldest, so a budgeted run that stops mid-list
    resumes next time with exactly the players it did not reach. Each
    kind carries its own window: MV series change slowly and refresh
    weekly, status and performance daily.
    """
    kinds = [k for k in ("status", "performance", "mv") if k in older_than]
    if not kinds:
        return []
    cols = [_PROGRESS_COLUMNS[k] for k in kinds]
    select_cols = ", ".join(f"s.{c}" for c in cols)
    stale_clause = " OR ".join(f"s.{c} IS NULL OR s.{c} < %s" for c in cols)
    least = ", ".join(f"coalesce(s.{c}, 0)" for c in cols)
    with self.connection() as conn:
        rows = conn.execute(
            f"""
                SELECT u.player_id, {select_cols}
                FROM rehoboam.player_universe u
                LEFT JOIN rehoboam.sweep_progress s ON s.player_id = u.player_id
                WHERE {stale_clause}
                ORDER BY least({least}) ASC, u.player_id
                """,
            [older_than[k] for k in kinds],
        ).fetchall()
    out: list[tuple[str, list[str]]] = []
    for r in rows:
        stale = [k for k, c in zip(kinds, cols) if r[c] is None or r[c] < older_than[k]]
        out.append((r["player_id"], stale))
    return out
```

(`coalesce(..., 0)` makes a never-fetched kind sort first, matching `NULLS FIRST`. The f-strings interpolate only `_PROGRESS_COLUMNS` values.)

- [ ] **Step 4: The loop**

In `run_ingestion`, replace the three `_pass` calls with one player loop:

```python
        now = budget.now()
        windows = {
            "status": now - stale_after_seconds,
            "performance": now - stale_after_seconds,
            "mv": now - mv_stale_after_seconds,
        }
        fetchers = {
            "status": (
                lambda pid: api.get_player_details(league_id=league_id, player_id=pid),
                lambda pid, d: store.record_status_daily(pid, day, d, budget.now()),
                "status_written",
            ),
            "performance": (
                lambda pid: api.get_competition_player_performance(player_id=pid),
                lambda pid, p: store.record_match_history(pid, team_by_id.get(pid), p),
                "performance_fetched",
            ),
            "mv": (
                lambda pid: api.get_player_market_value_history_v2(
                    player_id=pid, timeframe=timeframe_days
                ),
                lambda pid, h: store.record_mv_series(pid, h),
                "mv_fetched",
            ),
        }
        for pid, stale_kinds in store.players_needing_any_refresh(windows):
            for kind in stale_kinds:
                fetch, write, attr = fetchers[kind]
                try:
                    payload = fetch(pid)
                except BudgetExhausted:
                    raise
                except Exception as e:
                    stats.failed += 1
                    logger.warning("ingest %s failed for %s: %s", kind, pid, e)
                else:
                    write(pid, payload)
                    store.mark_fetched(pid, **{kind: True})
                    setattr(stats, attr, getattr(stats, attr) + 1)
                if throttle_seconds:
                    time.sleep(throttle_seconds)
```

Delete `_pass`. Update the module docstring: "stalest player first, every stale kind for that player, then the next player".

- [ ] **Step 5: Settings, CLI, Function app, docs**

`config.py`: `ingest_mv_stale_after_hours: float = Field(default=144.0, description="MV series refresh when older than this (six days: they change slowly and cost one request per player). Env: INGEST_MV_STALE_AFTER_HOURS.")`. `cli.py` `ingest_cmd` and `deploy/azure_function_external/function_app.py` `ingest` pass `mv_stale_after_seconds=settings.ingest_mv_stale_after_hours * 3600.0`. `.env.example`: add the line. `CLAUDE.md` ingestion bullet: "stalest player first, every stale kind for that player (status/performance daily, MV weekly)".

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/store tests/test_enrichment tests/test_cli_ingest.py -q -p no:cacheprovider`, then the whole suite. Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add rehoboam/store/corpus_store.py rehoboam/enrichment/ingest.py rehoboam/config.py rehoboam/cli.py deploy/azure_function_external/function_app.py .env.example CLAUDE.md tests/store/test_corpus_store.py tests/test_enrichment/test_ingest.py docs/superpowers/plans/2026-09-14-pr-c1-ingestion.md
git commit -m "fix(ingest): one player at a time, every stale kind — status and performance no longer starve MV"
```

______________________________________________________________________

## Self-review against spec §2 and the ruling

| requirement                                                                                                                                                                                                      | task                                          |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------- |
| timer at 05:00 and 17:00 UTC on `func-rehoboam-external`                                                                                                                                                         | 6                                             |
| stops after 8 of 10 minutes; oldest `fetched_at` first; resumes                                                                                                                                                  | 4 (`IngestBudget`, `players_needing_refresh`) |
| writes `player_universe`, `player_status_daily` (new), `player_match_history`, `mv_series`; `player_transfers` "as today" (the sweep's opt-in flag; not in the twice-daily pass — transfers do not change daily) | 2, 4                                          |
| 429/5xx backoff, per-run request cap, existing throttle                                                                                                                                                          | 3, 4                                          |
| `deploy.sh code external` works; Bicep: `DATABASE_URL` for both apps (+ the ruled Kickbase credentials)                                                                                                          | 6                                             |
| weekly `COPY ... TO STDOUT` export, one gzip per table, into the existing container (§1 Insurance)                                                                                                               | 5, 6                                          |
| "Effect on the trading session" (reads from `api_cache`/`player_status_daily`)                                                                                                                                   | **PR C2**, ruled                              |
| First ingestion pass evidence: ~450 players, one status row each, non-zero finished rows                                                                                                                         | 7                                             |
