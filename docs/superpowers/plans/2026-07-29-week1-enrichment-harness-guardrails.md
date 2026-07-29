# Week 1: Enrichment, Harness, Guardrails Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the durable training corpus, the leak-proof backtest harness, and the penalty guardrails that weeks 2–4 depend on — while deleting only code proven dead.

**Architecture:** Three independent workstreams. (1) A league-wide data sweep writes into a new non-expiring `training_corpus.db`, kicked off early because it is API-bound and slow. (2) A backtest harness replays matchdays through a pluggable scorer function, with a point-in-time truncation primitive as its anti-leakage core and the season-average baseline as its first subject. (3) Guardrail fixes for the ~1,400 points lost to penalties, each written as a regression test reproducing the actual 2025/26 failure first.

**Tech Stack:** Python 3.12, `uv`, pytest, SQLite, Typer CLI, `requests`. No new runtime dependencies.

## Global Constraints

- **No new runtime dependencies.** Metrics (Spearman) are implemented by hand rather than pulling in `scipy`/`numpy` — the Azure Function packages this codebase.
- **Probe-first.** Per CLAUDE.md, no code depends on a Kickbase endpoint shape until a read-only `scripts/probe_*.py` has validated it against the live API.
- **Training data must not live in `performance_cache`.** That table is a 6-hour TTL cache with a 7-day cleanup path (`value_history.py:171`). The corpus gets its own database.
- **Minimal diffs.** Do not run `black` on whole files — the repo is not black-clean and whole-file formatting causes massive collateral churn. Only the pre-commit hook's staged-file formatting applies.
- **Feature branch + PR.** Never commit to `main`. Branch: `feat/week1-enrichment-harness`.
- **Every persistence call in the live bot path stays best-effort** (`try/except`, log, never block) — matches the existing pattern. New offline tooling (corpus, harness) may raise freely; it is not on the trading path.
- **Season identifiers are Kickbase `ti` strings** in `YYYY/YYYY` form (e.g. `"2025/2026"`). Lexicographic comparison is correct for this format and is relied upon.

______________________________________________________________________

## Scope correction from the spec

Reading the code invalidated part of spec §7. **`value_calculator.py` and `expected_points.py` are live, not dead:**

`auto_trader.py:1294` calls `_legacy_expected_points`, which imports `expected_points.calculate_expected_points` (`auto_trader.py:1327`), which imports `value_calculator.PlayerValue` (`expected_points.py:48`). This is the lineup fallback for players the EP pipeline did not score.

Note that `expected_points.py` carries the **same saturation defect** as the main scorer (`min(avg_points * 2, 40)`). It dies with the scorer rewrite in weeks 2–3, not now.

**Week 1 therefore deletes only provably-dead code** (Task 15). `profit_trader.py`, `bid_evaluator.py`, and `league_compliance.py` each have a live lazy import and are removed in week 4 alongside the decision-layer rewrite that replaces their call sites.

______________________________________________________________________

## File Structure

**Created:**

| File                                        | Responsibility                                         |
| ------------------------------------------- | ------------------------------------------------------ |
| `scripts/probe_competition_endpoints.py`    | Read-only validation of the four competition endpoints |
| `rehoboam/match_parsing.py`                 | `parse_minutes` — shared by scorer and corpus          |
| `rehoboam/enrichment/__init__.py`           | Package marker                                         |
| `rehoboam/enrichment/corpus.py`             | `TrainingCorpus` — durable store + schema              |
| `rehoboam/enrichment/sweep.py`              | League-wide fetch orchestration, resumable             |
| `rehoboam/backtest/__init__.py`             | Package marker                                         |
| `rehoboam/backtest/snapshot.py`             | `matches_before` — the anti-leakage primitive          |
| `rehoboam/backtest/squad_reconstruction.py` | Squad membership per matchday from flips + lineups     |
| `rehoboam/backtest/metrics.py`              | `spearman`, `lineup_regret`                            |
| `rehoboam/backtest/baselines.py`            | `season_average_baseline`                              |
| `rehoboam/backtest/harness.py`              | `run_backtest` — replay + report                       |

**Modified:** `rehoboam/scoring/scorer.py` (import shared parser), `rehoboam/kickbase_client.py` (two endpoints), `rehoboam/cli.py` (two commands), `rehoboam/config.py` (squad floor), `rehoboam/formation.py` (fillability check), `pyproject.toml` (drop `web` extra).

______________________________________________________________________

## Task 1: Probe the competition endpoints

**Files:**

- Create: `scripts/probe_competition_endpoints.py`

**Interfaces:**

- Consumes: `KickbaseV4Client` (existing), `get_competition_players`, `get_competition_matchdays`
- Produces: validated knowledge of response shapes for Tasks 3–5. Records findings in the PR description.

No unit test — this is a read-only probe against the live API, following the existing `scripts/probe_*.py` convention.

- [ ] **Step 1: Write the probe script**

```python
#!/usr/bin/env python3
"""Probe the competition-level endpoints for the v2 training corpus.

Validates, read-only:
  1. /v4/competitions/1/players                          -> full player universe?
  2. /v4/competitions/1/players/{pid}/performance        -> per-match history?
  3. /v4/competitions/1/players/{pid}/marketValue/365    -> MV series (already used)
  4. /v4/competitions/1/table                            -> Bundesliga standings?

Answers the questions Tasks 3-5 depend on:
  - How many players does the universe endpoint return? Is it paginated?
  - Does competition-level performance match the league-level shape ({it:[{ph:[...]}]})?
  - Does the table endpoint expose a usable team-strength ordering?

Usage: uv run python scripts/probe_competition_endpoints.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _load_env() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

from rehoboam.kickbase_client import KickbaseV4Client  # noqa: E402

OUT_DIR = Path("/tmp/rehoboam_probe")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def dump(name: str, data) -> None:
    (OUT_DIR / f"{name}.json").write_text(json.dumps(data, indent=2)[:200_000])
    if isinstance(data, dict):
        print(f"  {name}: dict keys={list(data.keys())}")
    elif isinstance(data, list):
        print(f"  {name}: list len={len(data)}")


def main() -> int:
    client = KickbaseV4Client()
    if not client.login(os.environ["KICKBASE_EMAIL"], os.environ["KICKBASE_PASSWORD"]):
        print("LOGIN FAILED")
        return 1

    print("\n1. Player universe: /v4/competitions/1/players")
    universe = client.get_competition_players(competition_id="1")
    dump("competition_players", universe)
    items = universe.get("it") or universe.get("players") or []
    print(f"   -> {len(items)} players returned")
    if items:
        print(f"   -> sample player keys: {list(items[0].keys())}")
        print(f"   -> sample: {json.dumps(items[0])[:300]}")

    if not items:
        print("   !! empty universe — record this; Task 5 needs a fallback source")
        return 1

    pid = str(items[0].get("i") or items[0].get("id"))
    print(f"\n2. Performance for player {pid}")
    url = f"{client.BASE_URL}/v4/competitions/1/players/{pid}/performance"
    resp = client.session.get(url)
    print(f"   -> HTTP {resp.status_code}")
    if resp.status_code == 200:
        perf = resp.json()
        dump("competition_performance", perf)
        seasons = perf.get("it", [])
        print(
            f"   -> {len(seasons)} seasons; titles={[s.get('ti') for s in seasons][:5]}"
        )
        if seasons and seasons[0].get("ph"):
            print(f"   -> sample match: {json.dumps(seasons[0]['ph'][0])[:300]}")

    print(f"\n3. MV history for player {pid}")
    mv = client.get_player_market_value_history_v2(player_id=pid, timeframe=365)
    dump("competition_marketvalue", mv)
    print(f"   -> {len(mv.get('it', []))} daily points")

    print("\n4. Table: /v4/competitions/1/table")
    resp = client.session.get(f"{client.BASE_URL}/v4/competitions/1/table")
    print(f"   -> HTTP {resp.status_code}")
    if resp.status_code == 200:
        dump("competition_table", resp.json())

    print(f"\nDumps written to {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run the probe**

Run: `uv run python scripts/probe_competition_endpoints.py`

Expected: HTTP 200 for endpoints 1–3. Record the universe player count and the sample player key names — Task 5 needs the exact ID and team-ID field names.

**If the universe endpoint returns few or zero players**, stop and report. The fallback is sweeping `/v4/leagues/{lid}/market` plus squad endpoints across managers, which changes Task 5's design.

- [ ] **Step 3: Commit**

```bash
git add scripts/probe_competition_endpoints.py
git commit -m "chore: probe competition endpoints for v2 training corpus"
```

______________________________________________________________________

## Task 2: Shared minutes parsing

**Files:**

- Create: `rehoboam/match_parsing.py`
- Create: `tests/test_match_parsing.py`
- Modify: `rehoboam/scoring/scorer.py:38-61` (replace private copy with import)

**Interfaces:**

- Produces: `parse_minutes(mp) -> int`. Used by Task 3 (corpus) and by the existing scorer.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for rehoboam.match_parsing."""

from __future__ import annotations

from rehoboam.match_parsing import parse_minutes


def test_plain_minutes():
    assert parse_minutes("67'") == 67


def test_stoppage_time_is_summed():
    # "90+5'" is regulation + stoppage; a 95-minute appearance counts as 95.
    assert parse_minutes("90+5'") == 95


def test_missing_and_malformed_degrade_to_zero():
    assert parse_minutes(None) == 0
    assert parse_minutes("") == 0
    assert parse_minutes("not-a-number") == 0


def test_integer_input_passes_through():
    assert parse_minutes(90) == 90
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_match_parsing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rehoboam.match_parsing'`

- [ ] **Step 3: Create the module**

```python
"""Shared parsing helpers for Kickbase match records.

Extracted from ``scoring/scorer.py`` so the training corpus and the scorer
agree on what a minutes value means. Behaviour is byte-for-byte the original.
"""

from __future__ import annotations


def parse_minutes(mp) -> int:
    """Parse Kickbase ``mp`` minutes-played values (e.g. ``"13'"``) to int.

    Kickbase ships minutes as a string with a trailing apostrophe.
    Extra-time matches arrive as ``"90+5'"`` per common football
    convention (regulation + stoppage); both components are summed so
    a 95-minute appearance counts as 95, not 0. Anything else (None,
    empty string, future matches without minutes, truly malformed
    entries) degrades silently to 0 — a single odd entry must not
    poison the whole player score.
    """
    if not mp:
        return 0
    s = str(mp).rstrip("'")
    try:
        return int(s)
    except ValueError:
        pass
    if "+" in s:
        try:
            return sum(int(part) for part in s.split("+"))
        except ValueError:
            return 0
    return 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_match_parsing.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Point the scorer at the shared parser**

In `rehoboam/scoring/scorer.py`, delete the `_parse_minutes` function body (lines 38–61) and replace with an import alias. Add to the existing import block near line 8:

```python
from rehoboam.match_parsing import parse_minutes as _parse_minutes
```

Leave every call site (`_parse_minutes(...)`) untouched — the alias keeps the diff to two hunks.

- [ ] **Step 6: Verify the scorer still passes its full suite**

Run: `uv run pytest tests/test_scoring/ -v`
Expected: PASS, same count as before the change. Any failure means the extraction changed behaviour — revert and investigate rather than adjusting the test.

- [ ] **Step 7: Commit**

```bash
git add rehoboam/match_parsing.py tests/test_match_parsing.py rehoboam/scoring/scorer.py
git commit -m "refactor: extract parse_minutes into shared module"
```

______________________________________________________________________

## Task 3: Training corpus store

**Files:**

- Create: `rehoboam/enrichment/__init__.py`, `rehoboam/enrichment/corpus.py`
- Create: `tests/test_enrichment/__init__.py`, `tests/test_enrichment/test_corpus.py`

**Interfaces:**

- Consumes: `parse_minutes` (Task 2)

- Produces:

  - `TrainingCorpus(db_path: Path | None = None)`
  - `.upsert_players(players: list[dict]) -> int`
  - `.record_match_history(player_id: str, team_id: str | None, performance: dict) -> int`
  - `.record_mv_series(player_id: str, history: dict) -> int`
  - `.mark_fetched(player_id: str, *, performance: bool = False, mv: bool = False) -> None`
  - `.players_needing_fetch(kind: str) -> list[str]` where `kind` is `"performance"` or `"mv"`
  - `.matches_for_player(player_id: str) -> list[dict]` returning dicts with keys
    `season, day_number, match_date, points, minutes, team_id, opponent_team_id, is_home`
  - `DEFAULT_CORPUS_PATH: Path`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for rehoboam.enrichment.corpus — the v2 training corpus store."""

from __future__ import annotations

from rehoboam.enrichment.corpus import TrainingCorpus


def _perf(season: str, matches: list[dict]) -> dict:
    return {"it": [{"ti": season, "ph": matches}]}


def test_upsert_players_is_idempotent(tmp_path):
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    players = [
        {
            "player_id": "1",
            "last_name": "Musiala",
            "position": "Midfielder",
            "team_id": "2",
            "market_value": 30_000_000,
            "average_points": 120.0,
        }
    ]

    assert corpus.upsert_players(players) == 1
    assert corpus.upsert_players(players) == 1
    assert corpus.players_needing_fetch("performance") == ["1"]


def test_record_match_history_parses_minutes_and_home_away(tmp_path):
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    corpus.upsert_players([{"player_id": "1", "team_id": "3"}])

    perf = _perf(
        "2025/2026",
        [
            {
                "day": 21,
                "p": 17,
                "mp": "1'",
                "md": "2026-02-07T14:30:00Z",
                "t1": "11",
                "t2": "3",
            },
            {
                "day": 22,
                "p": 72,
                "mp": "90+5'",
                "md": "2026-02-13T19:30:00Z",
                "t1": "3",
                "t2": "18",
            },
        ],
    )
    assert corpus.record_match_history("1", "3", perf) == 2

    rows = corpus.matches_for_player("1")
    assert [r["day_number"] for r in rows] == [21, 22]
    assert rows[0]["minutes"] == 1
    assert rows[1]["minutes"] == 95  # stoppage time summed
    assert rows[0]["is_home"] == 0  # team 3 is t2 -> away
    assert rows[1]["is_home"] == 1  # team 3 is t1 -> home
    assert rows[0]["opponent_team_id"] == "11"
    assert rows[1]["opponent_team_id"] == "18"


def test_record_match_history_is_idempotent(tmp_path):
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    perf = _perf("2025/2026", [{"day": 1, "p": 50, "mp": "90'", "t1": "3", "t2": "4"}])

    corpus.record_match_history("1", "3", perf)
    corpus.record_match_history("1", "3", perf)
    assert len(corpus.matches_for_player("1")) == 1


def test_matches_without_day_number_are_skipped(tmp_path):
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    perf = _perf(
        "2025/2026", [{"p": 50, "mp": "90'"}, {"day": 2, "p": 60, "mp": "90'"}]
    )
    assert corpus.record_match_history("1", None, perf) == 1


def test_record_mv_series_drops_sentinel_rows(tmp_path):
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    history = {
        "it": [
            {"dt": 20000, "mv": 5_000_000},
            {"dt": 20001, "mv": 0},  # sentinel for newly-listed
            {"dt": 20002, "mv": 5_100_000},
        ]
    }
    assert corpus.record_mv_series("1", history) == 2


def test_mark_fetched_removes_player_from_pending(tmp_path):
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    corpus.upsert_players([{"player_id": "1"}, {"player_id": "2"}])

    corpus.mark_fetched("1", performance=True)
    assert corpus.players_needing_fetch("performance") == ["2"]
    # marking performance must not satisfy the mv sweep
    assert set(corpus.players_needing_fetch("mv")) == {"1", "2"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_enrichment/test_corpus.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rehoboam.enrichment'`

- [ ] **Step 3: Create the package marker files**

`rehoboam/enrichment/__init__.py`:

```python
"""Data enrichment for the v2 scorer training corpus."""
```

`tests/test_enrichment/__init__.py`: empty file.

- [ ] **Step 4: Implement the corpus**

`rehoboam/enrichment/corpus.py`:

```python
"""Durable training corpus for the v2 scorer.

Deliberately NOT ``value_history.performance_cache``: that table is a
6-hour TTL cache with a 7-day cleanup path (``value_history.py:171``).
Training data has to outlive both, so it gets its own database.

Schema is append-mostly and idempotent — the league-wide sweep in
``sweep.py`` is long-running, gets interrupted, and must be resumable.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from rehoboam.match_parsing import parse_minutes

DEFAULT_CORPUS_PATH = Path("logs") / "training_corpus.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS player_universe (
    player_id      TEXT PRIMARY KEY,
    first_name     TEXT,
    last_name      TEXT,
    position       TEXT,
    team_id        TEXT,
    market_value   INTEGER,
    average_points REAL
);

CREATE TABLE IF NOT EXISTS player_match_history (
    player_id        TEXT NOT NULL,
    season           TEXT NOT NULL,
    day_number       INTEGER NOT NULL,
    match_date       TEXT,
    points           INTEGER NOT NULL,
    minutes          INTEGER NOT NULL,
    team_id          TEXT,
    opponent_team_id TEXT,
    is_home          INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (player_id, season, day_number)
);

CREATE INDEX IF NOT EXISTS idx_match_history_player
    ON player_match_history(player_id, season, day_number);

CREATE TABLE IF NOT EXISTS mv_series (
    player_id    TEXT NOT NULL,
    snapshot_at  REAL NOT NULL,
    market_value INTEGER NOT NULL,
    PRIMARY KEY (player_id, snapshot_at)
);

CREATE TABLE IF NOT EXISTS sweep_progress (
    player_id             TEXT PRIMARY KEY,
    performance_fetched_at REAL,
    mv_fetched_at          REAL
);
"""


class TrainingCorpus:
    """Read/write access to the training corpus database."""

    def __init__(self, db_path: Path | None = None):
        if db_path is None:
            db_path = DEFAULT_CORPUS_PATH
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    def upsert_players(self, players: list[dict[str, Any]]) -> int:
        """Insert or update universe rows. Returns rows written."""
        if not players:
            return 0
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                """
                INSERT INTO player_universe (
                    player_id, first_name, last_name, position,
                    team_id, market_value, average_points
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(player_id) DO UPDATE SET
                    first_name     = excluded.first_name,
                    last_name      = excluded.last_name,
                    position       = excluded.position,
                    team_id        = excluded.team_id,
                    market_value   = excluded.market_value,
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
            conn.commit()
        return len(players)

    def record_match_history(
        self, player_id: str, team_id: str | None, performance: dict[str, Any]
    ) -> int:
        """Flatten a performance response into per-match rows.

        Shape: ``{"it": [{"ti": "2025/2026", "ph": [{...match...}]}]}``.
        Matches without a ``day`` are skipped — they cannot be placed on a
        timeline and so are useless for both training and backtesting.
        """
        rows: list[tuple] = []
        team = str(team_id) if team_id is not None else None

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
                is_home = 1 if team is not None and team == t1 else 0
                opponent = t2 if is_home else t1
                rows.append(
                    (
                        str(player_id),
                        str(title),
                        int(day),
                        m.get("md"),
                        int(m.get("p") or 0),
                        parse_minutes(m.get("mp")),
                        team,
                        opponent,
                        is_home,
                    )
                )

        if not rows:
            return 0

        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO player_match_history (
                    player_id, season, day_number, match_date, points,
                    minutes, team_id, opponent_team_id, is_home
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()
        return len(rows)

    def record_mv_series(self, player_id: str, history: dict[str, Any]) -> int:
        """Persist a market-value series.

        Shape: ``{"it": [{"dt": <days_since_epoch>, "mv": <value>}]}``.
        Non-positive ``mv`` is a sentinel for newly-listed players and is
        dropped — same rule as ``mv_backfill._history_to_rows``.
        """
        rows = [
            (str(player_id), float(item["dt"]) * 86400.0, int(item["mv"]))
            for item in (history.get("it") or [])
            if item.get("dt") is not None and item.get("mv") and item["mv"] > 0
        ]
        if not rows:
            return 0
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO mv_series (player_id, snapshot_at, market_value) "
                "VALUES (?, ?, ?)",
                rows,
            )
            conn.commit()
        return len(rows)

    def mark_fetched(
        self, player_id: str, *, performance: bool = False, mv: bool = False
    ) -> None:
        """Record sweep progress so an interrupted run resumes cleanly."""
        import time

        now = time.time()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO sweep_progress (player_id) VALUES (?)",
                (str(player_id),),
            )
            if performance:
                conn.execute(
                    "UPDATE sweep_progress SET performance_fetched_at = ? WHERE player_id = ?",
                    (now, str(player_id)),
                )
            if mv:
                conn.execute(
                    "UPDATE sweep_progress SET mv_fetched_at = ? WHERE player_id = ?",
                    (now, str(player_id)),
                )
            conn.commit()

    def players_needing_fetch(self, kind: str) -> list[str]:
        """Universe players with no successful fetch of ``kind`` yet.

        ``kind`` is ``"performance"`` or ``"mv"``.
        """
        column = {"performance": "performance_fetched_at", "mv": "mv_fetched_at"}[kind]
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(f"""
                SELECT u.player_id
                FROM player_universe u
                LEFT JOIN sweep_progress s ON s.player_id = u.player_id
                WHERE s.{column} IS NULL
                ORDER BY u.player_id
                """).fetchall()
        return [r[0] for r in rows]

    def matches_for_player(self, player_id: str) -> list[dict[str, Any]]:
        """All recorded matches for a player, oldest first."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT season, day_number, match_date, points, minutes,
                       team_id, opponent_team_id, is_home
                FROM player_match_history
                WHERE player_id = ?
                ORDER BY season, day_number
                """,
                (str(player_id),),
            ).fetchall()
        return [dict(r) for r in rows]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_enrichment/test_corpus.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Commit**

```bash
git add rehoboam/enrichment/ tests/test_enrichment/
git commit -m "feat(enrichment): durable training corpus store"
```

______________________________________________________________________

## Task 4: Competition-level client methods

**Files:**

- Modify: `rehoboam/kickbase_client.py` (append after `get_competition_players`, ~line 678)
- Create: `tests/test_competition_endpoints.py`

**Interfaces:**

- Produces:
  - `KickbaseV4Client.get_competition_player_performance(player_id: str, competition_id: str = "1") -> dict`
  - `KickbaseV4Client.get_competition_table(competition_id: str = "1") -> dict`

**Note:** confirm the paths against the Task 1 probe output before implementing.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for competition-level client endpoints used by the v2 corpus sweep."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from rehoboam.kickbase_client import KickbaseV4Client


def _client_with_response(status_code: int, payload: dict) -> KickbaseV4Client:
    client = KickbaseV4Client()
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload
    response.text = "error body"
    client.session = MagicMock()
    client.session.get.return_value = response
    return client


def test_get_competition_player_performance_returns_payload():
    client = _client_with_response(200, {"it": [{"ti": "2025/2026", "ph": []}]})
    result = client.get_competition_player_performance(player_id="42")

    assert result == {"it": [{"ti": "2025/2026", "ph": []}]}
    called_url = client.session.get.call_args[0][0]
    assert called_url.endswith("/v4/competitions/1/players/42/performance")


def test_get_competition_player_performance_raises_on_error():
    client = _client_with_response(404, {})
    with pytest.raises(
        Exception, match="Failed to fetch competition player performance"
    ):
        client.get_competition_player_performance(player_id="42")


def test_get_competition_table_returns_payload():
    client = _client_with_response(200, {"it": [{"tid": "2", "pl": 1}]})
    result = client.get_competition_table()

    assert result == {"it": [{"tid": "2", "pl": 1}]}
    assert client.session.get.call_args[0][0].endswith("/v4/competitions/1/table")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_competition_endpoints.py -v`
Expected: FAIL with `AttributeError: 'KickbaseV4Client' object has no attribute 'get_competition_player_performance'`

- [ ] **Step 3: Add the two methods**

Insert into `rehoboam/kickbase_client.py` immediately after `get_competition_players` ends (~line 678), matching the surrounding style:

```python
def get_competition_player_performance(
    self, player_id: str, competition_id: str = "1"
) -> dict[str, Any]:
    """
    Get per-match performance history for any player in the competition
    GET /v4/competitions/{competition_id}/players/{player_id}/performance

    Competition-scoped twin of ``get_player_performance``. Needed by the
    v2 training corpus, which sweeps the whole league rather than only
    players in our own league view.

    Returns:
        dict with ``it``: list of seasons, each ``{"ti": title, "ph": [matches]}``
    """
    url = f"{self.BASE_URL}/v4/competitions/{competition_id}/players/{player_id}/performance"

    response = self.session.get(url)

    if response.status_code == 200:
        return response.json()
    else:
        raise Exception(
            f"Failed to fetch competition player performance: "
            f"{response.status_code} - {response.text}"
        )


def get_competition_table(self, competition_id: str = "1") -> dict[str, Any]:
    """
    Get the league table / standings for a competition
    GET /v4/competitions/{competition_id}/table

    Replaces the homegrown strength-of-schedule rating with real standings.
    """
    url = f"{self.BASE_URL}/v4/competitions/{competition_id}/table"

    response = self.session.get(url)

    if response.status_code == 200:
        return response.json()
    else:
        raise Exception(
            f"Failed to fetch competition table: {response.status_code} - {response.text}"
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_competition_endpoints.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add rehoboam/kickbase_client.py tests/test_competition_endpoints.py
git commit -m "feat(api): competition-level performance + table endpoints"
```

______________________________________________________________________

## Task 5: League-wide sweep orchestrator

**Files:**

- Create: `rehoboam/enrichment/sweep.py`
- Create: `tests/test_enrichment/test_sweep.py`

**Interfaces:**

- Consumes: `TrainingCorpus` (Task 3), `get_competition_players` / `get_competition_player_performance` / `get_player_market_value_history_v2` (Task 4)
- Produces:
  - `SweepStats` dataclass with fields `universe_size, performance_fetched, mv_fetched, failed, skipped`
  - `run_sweep(client, corpus, *, dry_run=False, throttle_seconds=0.25, limit=None, timeframe_days=365) -> SweepStats`

**Field-name note:** the universe item keys come from the Task 1 probe. The implementation below reads `i`/`id` for player ID and `tid` for team, matching the existing `Player.from_dict` convention (`kickbase_client.py:145`). Adjust if the probe shows otherwise.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for rehoboam.enrichment.sweep — the league-wide corpus sweep."""

from __future__ import annotations

from unittest.mock import MagicMock

from rehoboam.enrichment.corpus import TrainingCorpus
from rehoboam.enrichment.sweep import SweepStats, run_sweep


def _client(universe_items: list[dict]) -> MagicMock:
    client = MagicMock()
    client.get_competition_players.return_value = {"it": universe_items}
    client.get_competition_player_performance.return_value = {
        "it": [
            {
                "ti": "2025/2026",
                "ph": [{"day": 1, "p": 80, "mp": "90'", "t1": "3", "t2": "4"}],
            }
        ]
    }
    client.get_player_market_value_history_v2.return_value = {
        "it": [{"dt": 20000, "mv": 5_000_000}]
    }
    return client


def test_sweep_populates_universe_and_history(tmp_path):
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    client = _client(
        [
            {
                "i": "1",
                "fn": "Jamal",
                "n": "Musiala",
                "pos": 3,
                "tid": "2",
                "mv": 30_000_000,
                "ap": 120.0,
            },
            {
                "i": "2",
                "fn": "Harry",
                "n": "Kane",
                "pos": 4,
                "tid": "2",
                "mv": 40_000_000,
                "ap": 150.0,
            },
        ]
    )

    stats = run_sweep(client, corpus, throttle_seconds=0)

    assert stats.universe_size == 2
    assert stats.performance_fetched == 2
    assert stats.mv_fetched == 2
    assert stats.failed == 0
    assert len(corpus.matches_for_player("1")) == 1


def test_sweep_is_resumable(tmp_path):
    """A second run must not refetch players already marked complete."""
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    client = _client([{"i": "1", "tid": "2"}])

    run_sweep(client, corpus, throttle_seconds=0)
    client.get_competition_player_performance.reset_mock()

    stats = run_sweep(client, corpus, throttle_seconds=0)

    assert client.get_competition_player_performance.call_count == 0
    assert stats.performance_fetched == 0
    assert stats.skipped == 1


def test_sweep_survives_per_player_failure(tmp_path):
    """One bad player must not abort a multi-thousand-request sweep."""
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    client = _client([{"i": "1", "tid": "2"}, {"i": "2", "tid": "2"}])
    client.get_competition_player_performance.side_effect = [
        Exception("500 server error"),
        {"it": [{"ti": "2025/2026", "ph": [{"day": 1, "p": 80, "mp": "90'"}]}]},
    ]

    stats = run_sweep(client, corpus, throttle_seconds=0)

    assert stats.failed == 1
    assert stats.performance_fetched == 1
    # the failed player must remain pending so a rerun retries it
    assert "1" in corpus.players_needing_fetch("performance")


def test_dry_run_fetches_universe_but_writes_no_history(tmp_path):
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    client = _client([{"i": "1", "tid": "2"}])

    stats = run_sweep(client, corpus, dry_run=True, throttle_seconds=0)

    assert stats.universe_size == 1
    assert corpus.matches_for_player("1") == []


def test_limit_caps_players_processed(tmp_path):
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    client = _client([{"i": str(i), "tid": "2"} for i in range(10)])

    stats = run_sweep(client, corpus, limit=3, throttle_seconds=0)

    assert stats.performance_fetched == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_enrichment/test_sweep.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rehoboam.enrichment.sweep'`

- [ ] **Step 3: Implement the sweep**

```python
"""League-wide corpus sweep — the long pole of week 1.

Walks every player in the competition and pulls per-match performance plus
the full market-value series into ``TrainingCorpus``. Thousands of requests,
so it is throttled, resumable, and tolerant of individual failures.

Resumability is the important property: ``sweep_progress`` is only marked
after a successful write, so an interrupted or partially-failed run picks up
exactly where it stopped.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from rehoboam.enrichment.corpus import TrainingCorpus

logger = logging.getLogger(__name__)

DEFAULT_TIMEFRAME_DAYS = 365
DEFAULT_THROTTLE_SECONDS = 0.25

_POSITIONS = {1: "Goalkeeper", 2: "Defender", 3: "Midfielder", 4: "Forward"}


@dataclass
class SweepStats:
    universe_size: int = 0
    performance_fetched: int = 0
    mv_fetched: int = 0
    failed: int = 0
    skipped: int = 0


def _universe_to_rows(items: list[dict]) -> list[dict]:
    """Map competition player items to ``upsert_players`` rows.

    Field names follow the existing ``Player.from_dict`` convention
    (``kickbase_client.py:145``): ``i`` id, ``fn`` first name, ``n`` last
    name, ``pos`` position code, ``tid`` team, ``mv`` market value,
    ``ap`` average points.
    """
    rows = []
    for item in items:
        pid = item.get("i") or item.get("id")
        if pid is None:
            continue
        rows.append(
            {
                "player_id": str(pid),
                "first_name": item.get("fn"),
                "last_name": item.get("n") or item.get("ln"),
                "position": _POSITIONS.get(item.get("pos"), None),
                "team_id": item.get("tid"),
                "market_value": item.get("mv"),
                "average_points": item.get("ap"),
            }
        )
    return rows


def run_sweep(
    client,
    corpus: TrainingCorpus,
    *,
    dry_run: bool = False,
    throttle_seconds: float = DEFAULT_THROTTLE_SECONDS,
    limit: int | None = None,
    timeframe_days: int = DEFAULT_TIMEFRAME_DAYS,
) -> SweepStats:
    """Populate the training corpus from the competition endpoints.

    ``dry_run`` fetches the universe (so the size estimate is real) but
    performs no per-player fetches and no history writes.
    """
    stats = SweepStats()

    universe = client.get_competition_players(competition_id="1")
    items = universe.get("it") or universe.get("players") or []
    rows = _universe_to_rows(items)
    stats.universe_size = len(rows)
    corpus.upsert_players(rows)
    logger.info("Universe: %d players", stats.universe_size)

    if dry_run:
        return stats

    team_by_id = {r["player_id"]: r.get("team_id") for r in rows}

    pending_perf = corpus.players_needing_fetch("performance")
    pending_mv = corpus.players_needing_fetch("mv")
    stats.skipped = stats.universe_size - len(pending_perf)

    if limit is not None:
        pending_perf = pending_perf[:limit]
        pending_mv = pending_mv[:limit]

    for pid in pending_perf:
        try:
            perf = client.get_competition_player_performance(player_id=pid)
            corpus.record_match_history(pid, team_by_id.get(pid), perf)
            corpus.mark_fetched(pid, performance=True)
            stats.performance_fetched += 1
        except Exception as e:
            stats.failed += 1
            logger.warning("Performance fetch failed for player %s: %s", pid, e)
        if throttle_seconds:
            time.sleep(throttle_seconds)

    for pid in pending_mv:
        try:
            history = client.get_player_market_value_history_v2(
                player_id=pid, timeframe=timeframe_days
            )
            corpus.record_mv_series(pid, history)
            corpus.mark_fetched(pid, mv=True)
            stats.mv_fetched += 1
        except Exception as e:
            stats.failed += 1
            logger.warning("MV fetch failed for player %s: %s", pid, e)
        if throttle_seconds:
            time.sleep(throttle_seconds)

    logger.info(
        "Sweep done: %d perf, %d mv, %d failed, %d skipped",
        stats.performance_fetched,
        stats.mv_fetched,
        stats.failed,
        stats.skipped,
    )
    return stats
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_enrichment/test_sweep.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add rehoboam/enrichment/sweep.py tests/test_enrichment/test_sweep.py
git commit -m "feat(enrichment): resumable league-wide corpus sweep"
```

______________________________________________________________________

## Task 6: `enrich-corpus` CLI command — then start the sweep

**Files:**

- Modify: `rehoboam/cli.py` (add command after `backfill_mv_history`, ~line 397)

**Interfaces:**

- Consumes: `run_sweep`, `SweepStats` (Task 5), `TrainingCorpus` (Task 3)

- [ ] **Step 1: Add the command**

Follow the shape of the existing `backfill-mv-history` command (`cli.py:338`):

```python
@app.command("enrich-corpus")
def enrich_corpus(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Fetch the universe only; write no per-player history"
    ),
    limit: int = typer.Option(
        0,
        "--limit",
        help="Cap players processed this run (0 = no cap). Useful for a smoke run.",
    ),
    throttle: float = typer.Option(
        0.25, "--throttle", help="Seconds to sleep between API calls"
    ),
):
    """Sweep the full competition into logs/training_corpus.db (v2 scorer training data).

    Long-running and API-bound — thousands of requests. Safe to interrupt and
    rerun: progress is tracked per player, so a rerun resumes rather than
    restarting.

    Typical first run:
      1. rehoboam enrich-corpus --dry-run          # how many players?
      2. rehoboam enrich-corpus --limit 20         # smoke-test the shapes
      3. rehoboam enrich-corpus                    # the full sweep
    """
    from .enrichment.corpus import TrainingCorpus
    from .enrichment.sweep import run_sweep

    api = _get_api()
    if not api.login():
        console.print("[red]Login failed[/red]")
        raise typer.Exit(1)

    corpus = TrainingCorpus()
    stats = run_sweep(
        api.client,
        corpus,
        dry_run=dry_run,
        throttle_seconds=throttle,
        limit=limit or None,
    )

    table = Table(title="Corpus enrichment summary")
    table.add_column("Metric")
    table.add_column("Count", justify="right")
    table.add_row("Universe size", str(stats.universe_size))
    table.add_row("Performance fetched", str(stats.performance_fetched))
    table.add_row("MV series fetched", str(stats.mv_fetched))
    table.add_row("Skipped (already done)", str(stats.skipped))
    table.add_row("Failed", str(stats.failed))
    console.print(table)
    console.print(f"[dim]Corpus: {corpus.db_path}[/dim]")
```

- [ ] **Step 2: Verify the command registers**

Run: `uv run rehoboam --help`
Expected: `enrich-corpus` appears in the command list.

- [ ] **Step 3: Dry run against the live API**

Run: `uv run rehoboam enrich-corpus --dry-run`
Expected: a universe size in the hundreds. If it is 0 or implausibly small, stop — the Task 1 probe fallback applies.

- [ ] **Step 4: Smoke run**

Run: `uv run rehoboam enrich-corpus --limit 20`
Expected: 20 performance + 20 MV fetches, 0 failed. Then confirm the data landed:

```bash
sqlite3 logs/training_corpus.db \
  "SELECT (SELECT COUNT(*) FROM player_universe), (SELECT COUNT(*) FROM player_match_history), (SELECT COUNT(*) FROM mv_series);"
```

Expected: non-zero for all three.

- [ ] **Step 5: Commit, then launch the full sweep in the background**

```bash
git add rehoboam/cli.py
git commit -m "feat(cli): enrich-corpus command for league-wide training sweep"
```

Then start the long-running sweep — **this is the long pole; everything after this task proceeds while it runs**:

```bash
nohup uv run rehoboam enrich-corpus > logs/enrich-corpus.log 2>&1 &
```

Check progress periodically with `tail -f logs/enrich-corpus.log`.

______________________________________________________________________

## Task 7: Point-in-time snapshot (the anti-leakage primitive)

**Files:**

- Create: `rehoboam/backtest/__init__.py`, `rehoboam/backtest/snapshot.py`
- Create: `tests/test_backtest/__init__.py`, `tests/test_backtest/test_snapshot.py`

**Interfaces:**

- Produces: `matches_before(matches: list[dict], *, season: str, day_number: int) -> list[dict]`

Every scorer call in the harness routes through this function. If it leaks, every backtest number in the project is worthless — hence the deliberate-cheat test.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for rehoboam.backtest.snapshot — the anti-leakage boundary."""

from __future__ import annotations

import pytest

from rehoboam.backtest.snapshot import matches_before


def _m(season: str, day: int, points: int = 50) -> dict:
    return {"season": season, "day_number": day, "points": points, "minutes": 90}


def test_returns_only_earlier_days_in_same_season():
    matches = [_m("2025/2026", d) for d in (1, 2, 3, 4, 5)]
    result = matches_before(matches, season="2025/2026", day_number=3)
    assert [m["day_number"] for m in result] == [1, 2]


def test_includes_all_prior_seasons():
    matches = [
        _m("2024/2025", 30),
        _m("2024/2025", 34),
        _m("2025/2026", 1),
        _m("2025/2026", 5),
    ]
    result = matches_before(matches, season="2025/2026", day_number=2)
    assert [(m["season"], m["day_number"]) for m in result] == [
        ("2024/2025", 30),
        ("2024/2025", 34),
        ("2025/2026", 1),
    ]


def test_excludes_future_seasons():
    matches = [_m("2025/2026", 1), _m("2026/2027", 1)]
    result = matches_before(matches, season="2025/2026", day_number=5)
    assert [m["season"] for m in result] == ["2025/2026"]


def test_deliberate_cheat_finds_no_future_data():
    """The leak check. If this ever passes with future data present, every
    backtest number in the project is worthless."""
    matches = [_m("2025/2026", d, points=999) for d in range(1, 35)]

    for cutoff in range(1, 35):
        result = matches_before(matches, season="2025/2026", day_number=cutoff)
        assert all(
            m["day_number"] < cutoff for m in result
        ), f"LEAK at cutoff {cutoff}: {[m['day_number'] for m in result if m['day_number'] >= cutoff]}"


def test_day_zero_returns_nothing_from_current_season():
    matches = [_m("2025/2026", 1)]
    assert matches_before(matches, season="2025/2026", day_number=1) == []


def test_empty_input_returns_empty():
    assert matches_before([], season="2025/2026", day_number=5) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_backtest/test_snapshot.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rehoboam.backtest'`

- [ ] **Step 3: Create package markers**

`rehoboam/backtest/__init__.py`:

```python
"""Backtesting harness for the v2 scorer."""
```

`tests/test_backtest/__init__.py`: empty file.

- [ ] **Step 4: Implement the primitive**

```python
"""Point-in-time truncation — the anti-leakage core of the harness.

Every scorer invocation during a backtest must go through this function.
Performance data as stored contains the whole season; scoring matchday 12
while able to see matchday 20 produces a model that looks brilliant offline
and fails live. That failure mode is the single biggest risk to the v2
validation effort, so the boundary is one small, heavily tested function.

Seasons are Kickbase ``ti`` titles in ``YYYY/YYYY`` form, which sort
correctly under plain lexicographic comparison ("2024/2025" < "2025/2026").
"""

from __future__ import annotations

from typing import Any


def matches_before(
    matches: list[dict[str, Any]], *, season: str, day_number: int
) -> list[dict[str, Any]]:
    """Matches strictly before ``(season, day_number)``.

    Includes every match from earlier seasons, and matches from ``season``
    with a lower ``day_number``. Excludes the cutoff matchday itself — when
    predicting matchday N, matchday N's result is exactly what we are not
    allowed to see.

    Args:
        matches: rows as returned by ``TrainingCorpus.matches_for_player``
        season: the season being predicted, e.g. ``"2025/2026"``
        day_number: the matchday being predicted

    Returns:
        Filtered list preserving input order.
    """
    result: list[dict[str, Any]] = []
    for match in matches:
        match_season = match["season"]
        if match_season < season:
            result.append(match)
        elif match_season == season and match["day_number"] < day_number:
            result.append(match)
    return result
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_backtest/test_snapshot.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Commit**

```bash
git add rehoboam/backtest/ tests/test_backtest/
git commit -m "feat(backtest): point-in-time snapshot primitive"
```

______________________________________________________________________

## Task 8: Squad reconstruction

**Files:**

- Create: `rehoboam/backtest/squad_reconstruction.py`
- Create: `tests/test_backtest/test_squad_reconstruction.py`

**Interfaces:**

- Produces: `squad_on_matchday(flips: list[dict], fielded_ids: list[str], matchday_ts: float) -> set[str]`

**Why this exists:** `matchday_lineup_results` stores only the fielded 11. Lineup regret needs the full squad — with a squad of exactly the fielded 11, regret is trivially zero. Squad membership is reconstructed from `flip_outcomes` hold windows plus the fielded lineup. This is an approximation: players bought and never sold have no flip row, so the fielded set is unioned in to catch them. Fidelity is labelled "medium" in the report.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for rehoboam.backtest.squad_reconstruction."""

from __future__ import annotations

from rehoboam.backtest.squad_reconstruction import squad_on_matchday

DAY = 86400.0
T0 = 1_700_000_000.0


def _flip(pid: str, buy_offset_days: float, sell_offset_days: float) -> dict:
    return {
        "player_id": pid,
        "buy_date": T0 + buy_offset_days * DAY,
        "sell_date": T0 + sell_offset_days * DAY,
    }


def test_player_inside_hold_window_is_in_squad():
    flips = [_flip("1", 0, 10)]
    assert "1" in squad_on_matchday(flips, [], T0 + 5 * DAY)


def test_player_outside_hold_window_is_not_in_squad():
    flips = [_flip("1", 0, 10)]
    assert squad_on_matchday(flips, [], T0 + 20 * DAY) == set()
    assert squad_on_matchday(flips, [], T0 - 5 * DAY) == set()


def test_boundaries_are_inclusive():
    flips = [_flip("1", 0, 10)]
    assert "1" in squad_on_matchday(flips, [], T0)
    assert "1" in squad_on_matchday(flips, [], T0 + 10 * DAY)


def test_fielded_players_are_always_included():
    """Players bought and never sold have no flip row, so the fielded 11 is
    unioned in — otherwise long-term holds vanish from the reconstruction."""
    assert squad_on_matchday([], ["7", "8"], T0) == {"7", "8"}


def test_union_of_both_sources():
    flips = [_flip("1", 0, 10)]
    assert squad_on_matchday(flips, ["7"], T0 + 5 * DAY) == {"1", "7"}


def test_multiple_holds_of_same_player():
    flips = [_flip("1", 0, 5), _flip("1", 20, 30)]
    assert "1" in squad_on_matchday(flips, [], T0 + 3 * DAY)
    assert squad_on_matchday(flips, [], T0 + 10 * DAY) == set()
    assert "1" in squad_on_matchday(flips, [], T0 + 25 * DAY)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_backtest/test_squad_reconstruction.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
"""Reconstruct squad membership at a point in time.

``matchday_lineup_results`` records only the fielded 11, but lineup regret
needs the full squad — measured against a squad of exactly the fielded
players, regret is always zero and the metric says nothing.

Membership is derived from two sources, unioned:
  1. ``flip_outcomes`` hold windows (buy_date .. sell_date)
  2. the players actually fielded that matchday

Source 2 matters because a player bought and never sold has no flip row at
all. This is an approximation — a player held but benched all season, and
never flipped, is invisible. The harness labels the resulting regret figure
as medium fidelity.
"""

from __future__ import annotations

from typing import Any


def squad_on_matchday(
    flips: list[dict[str, Any]], fielded_ids: list[str], matchday_ts: float
) -> set[str]:
    """Player IDs plausibly in the squad at ``matchday_ts``.

    Args:
        flips: rows with ``player_id``, ``buy_date``, ``sell_date`` (unix seconds)
        fielded_ids: player IDs actually fielded that matchday
        matchday_ts: unix timestamp of the matchday

    Returns:
        Set of player IDs. Hold windows are inclusive at both ends.
    """
    squad = {str(pid) for pid in fielded_ids}
    for flip in flips:
        if flip["buy_date"] <= matchday_ts <= flip["sell_date"]:
            squad.add(str(flip["player_id"]))
    return squad
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_backtest/test_squad_reconstruction.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add rehoboam/backtest/squad_reconstruction.py tests/test_backtest/test_squad_reconstruction.py
git commit -m "feat(backtest): squad membership reconstruction"
```

______________________________________________________________________

## Task 9: Metrics

**Files:**

- Create: `rehoboam/backtest/metrics.py`
- Create: `tests/test_backtest/test_metrics.py`

**Interfaces:**

- Consumes: `formation.select_best_eleven` (existing, `formation.py:90`)
- Produces:
  - `spearman(xs: list[float], ys: list[float]) -> float`
  - `lineup_regret(squad: list, chosen_ids: list[str], actual_points: dict[str, float]) -> float`

Implemented without `numpy`/`scipy` per the global no-new-dependencies constraint.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for rehoboam.backtest.metrics."""

from __future__ import annotations

import pytest

from rehoboam.backtest.metrics import lineup_regret, spearman
from rehoboam.kickbase_client import Player


def _player(pid: str, position: str) -> Player:
    return Player(
        id=pid,
        first_name="F",
        last_name=f"P{pid}",
        position=position,
        team_id="1",
        team_name="T",
        market_value=1_000_000,
        points=0,
        average_points=0.0,
    )


def test_spearman_perfect_positive():
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)


def test_spearman_perfect_negative():
    assert spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)


def test_spearman_is_rank_based_not_value_based():
    # monotonic but wildly non-linear -> still a perfect rank correlation
    assert spearman([1, 2, 3, 4], [1, 10, 1000, 100000]) == pytest.approx(1.0)


def test_spearman_handles_ties_with_average_ranks():
    result = spearman([1, 1, 2, 2], [5, 5, 9, 9])
    assert result == pytest.approx(1.0)


def test_spearman_zero_variance_returns_zero():
    assert spearman([1, 1, 1], [1, 2, 3]) == 0.0


def test_spearman_too_few_points_returns_zero():
    assert spearman([1], [2]) == 0.0


def test_lineup_regret_is_zero_for_optimal_choice():
    squad = (
        [_player("1", "Goalkeeper")]
        + [_player(str(i), "Defender") for i in range(2, 7)]
        + [_player(str(i), "Midfielder") for i in range(7, 12)]
        + [_player(str(i), "Forward") for i in range(12, 15)]
    )
    actual = {p.id: 100.0 for p in squad}

    chosen = [p.id for p in squad[:11]]
    assert lineup_regret(squad, chosen, actual) == pytest.approx(0.0)


def test_lineup_regret_penalises_benching_the_best_player():
    squad = (
        [_player("1", "Goalkeeper")]
        + [_player(str(i), "Defender") for i in range(2, 7)]
        + [_player(str(i), "Midfielder") for i in range(7, 12)]
        + [_player(str(i), "Forward") for i in range(12, 15)]
    )
    actual = {p.id: 50.0 for p in squad}
    actual["14"] = 300.0  # a forward who exploded

    # deliberately field the two weaker forwards and bench "14"
    chosen = (
        ["1"] + [str(i) for i in range(2, 7)] + [str(i) for i in range(7, 11)] + ["12"]
    )
    regret = lineup_regret(squad, chosen, actual)
    assert regret > 0.0


def test_lineup_regret_missing_player_scores_zero():
    squad = [_player("1", "Goalkeeper")]
    assert lineup_regret(squad, ["missing"], {"1": 10.0}) >= 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_backtest/test_metrics.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
"""Backtest metrics.

Deliberately dependency-free — this codebase ships to an Azure Function and
does not carry numpy/scipy. Both metrics are small enough to implement and
test directly.

MAE is intentionally absent. Median per-player game-to-game standard
deviation is 54.8 points, so a perfect model of a player's true level still
scores MAE around 44. Optimising against MAE drives the model to fit noise.
Ranking is what the bot actually needs: field the right 11, buy the right
player.
"""

from __future__ import annotations

from typing import Any

from rehoboam.formation import select_best_eleven


def _average_ranks(values: list[float]) -> list[float]:
    """Ranks (1-based), with tied values sharing their average rank."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        average_rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = average_rank
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float:
    """Spearman rank correlation. Returns 0.0 when undefined.

    Undefined cases (fewer than two points, or zero variance in either
    series) return 0.0 rather than raising — a matchday where every player
    scored the same is not an error, it just carries no ranking signal.
    """
    n = len(xs)
    if n < 2 or n != len(ys):
        return 0.0

    rx, ry = _average_ranks(xs), _average_ranks(ys)
    mean_x, mean_y = sum(rx) / n, sum(ry) / n

    covariance = sum((a - mean_x) * (b - mean_y) for a, b in zip(rx, ry))
    var_x = sum((a - mean_x) ** 2 for a in rx)
    var_y = sum((b - mean_y) ** 2 for b in ry)

    if var_x == 0 or var_y == 0:
        return 0.0
    return covariance / (var_x * var_y) ** 0.5


def lineup_regret(
    squad: list[Any], chosen_ids: list[str], actual_points: dict[str, float]
) -> float:
    """Points left on the bench: best legal 11 minus the 11 actually chosen.

    The hindsight-optimal 11 is computed by handing *actual* points to the
    existing ``select_best_eleven``, which already enforces formation
    legality — so the benchmark is always a lineup we could really have
    fielded, not an illegal all-forwards fantasy.

    Returns:
        Non-negative points. 0.0 means the choice was optimal.
    """
    best = select_best_eleven(squad, actual_points)
    best_total = sum(actual_points.get(p.id, 0.0) for p in best)
    chosen_total = sum(actual_points.get(pid, 0.0) for pid in chosen_ids)
    return best_total - chosen_total
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_backtest/test_metrics.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add rehoboam/backtest/metrics.py tests/test_backtest/test_metrics.py
git commit -m "feat(backtest): spearman + lineup regret metrics"
```

______________________________________________________________________

## Task 10: Season-average baseline

**Files:**

- Create: `rehoboam/backtest/baselines.py`
- Create: `tests/test_backtest/test_baselines.py`

**Interfaces:**

- Produces: `season_average_baseline(matches: list[dict]) -> float`

**This is the model the v2 scorer must beat.** If weeks 2–3 cannot beat it on replay, the spec's safety valve says we ship this instead.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for rehoboam.backtest.baselines."""

from __future__ import annotations

import pytest

from rehoboam.backtest.baselines import season_average_baseline


def _m(day: int, points: int, minutes: int = 90) -> dict:
    return {
        "season": "2025/2026",
        "day_number": day,
        "points": points,
        "minutes": minutes,
    }


def test_average_over_played_matches():
    assert season_average_baseline([_m(1, 60), _m(2, 80), _m(3, 100)]) == pytest.approx(
        80.0
    )


def test_did_not_play_matches_are_excluded():
    """A 0-point, 0-minute row is an absence, not a bad performance. Counting
    it would drag every rotation player's estimate toward zero."""
    assert season_average_baseline([_m(1, 90), _m(2, 0, minutes=0)]) == pytest.approx(
        90.0
    )


def test_played_but_scored_zero_counts():
    result = season_average_baseline([_m(1, 90), _m(2, 0, minutes=45)])
    assert result == pytest.approx(45.0)


def test_negative_points_count():
    assert season_average_baseline([_m(1, -4), _m(2, 4)]) == pytest.approx(0.0)


def test_empty_history_returns_zero():
    assert season_average_baseline([]) == 0.0


def test_all_absences_returns_zero():
    assert season_average_baseline([_m(1, 0, minutes=0)]) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_backtest/test_baselines.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
"""Baseline models the v2 scorer has to beat.

The season-average baseline is one line of arithmetic and is a genuinely
strong predictor, because a player's own scoring rate is most of the signal.
The 2025/26 scorer would very likely have lost to it — which is exactly why
it exists here as the shipping fallback under the spec's safety valve.
"""

from __future__ import annotations

from typing import Any


def season_average_baseline(matches: list[dict[str, Any]]) -> float:
    """Mean points over matches the player actually appeared in.

    Absences (0 points *and* 0 minutes) are excluded rather than averaged in
    as zeros: an absence says nothing about how well the player performs, and
    counting it conflates "rotation risk" with "plays badly". Availability is
    modelled separately by the v2 scorer.

    Args:
        matches: rows from ``TrainingCorpus.matches_for_player``, already
            truncated by ``snapshot.matches_before``.

    Returns:
        Mean points per appearance; 0.0 when there are no appearances.
    """
    played = [m for m in matches if m["points"] != 0 or m["minutes"] > 0]
    if not played:
        return 0.0
    return sum(m["points"] for m in played) / len(played)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_backtest/test_baselines.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add rehoboam/backtest/baselines.py tests/test_backtest/test_baselines.py
git commit -m "feat(backtest): season-average baseline model"
```

______________________________________________________________________

## Task 11: The harness

**Files:**

- Create: `rehoboam/backtest/harness.py`
- Create: `tests/test_backtest/test_harness.py`

**Interfaces:**

- Consumes: `matches_before` (Task 7), `spearman` / `lineup_regret` (Task 9), `TrainingCorpus` (Task 3)
- Produces:
  - `MatchdayResult` dataclass: `day_number, chosen_points, best_points, regret, rank_correlation, players_scored`
  - `BacktestReport` dataclass: `results, mean_regret, mean_rank_correlation, total_chosen_points, total_best_points`
  - `run_backtest(corpus, scorer_fn, *, season, matchdays) -> BacktestReport`

`scorer_fn` signature: `(player_id: str, history: list[dict]) -> float`. Weeks 2–3 pass the v2 scorer; week 1 passes `season_average_baseline`.

`matchdays` is a list of `MatchdayInput` dataclasses: `day_number, squad (list[Player]), actual_points (dict[str, float])`.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for rehoboam.backtest.harness."""

from __future__ import annotations

import pytest

from rehoboam.backtest.harness import MatchdayInput, run_backtest
from rehoboam.enrichment.corpus import TrainingCorpus
from rehoboam.kickbase_client import Player


def _player(pid: str, position: str) -> Player:
    return Player(
        id=pid,
        first_name="F",
        last_name=f"P{pid}",
        position=position,
        team_id="1",
        team_name="T",
        market_value=1_000_000,
        points=0,
        average_points=0.0,
    )


def _legal_squad() -> list[Player]:
    return (
        [_player("1", "Goalkeeper")]
        + [_player(str(i), "Defender") for i in range(2, 7)]
        + [_player(str(i), "Midfielder") for i in range(7, 12)]
        + [_player(str(i), "Forward") for i in range(12, 15)]
    )


def _corpus_with_history(
    tmp_path, squad, points_by_day: dict[int, int]
) -> TrainingCorpus:
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    for p in squad:
        perf = {
            "it": [
                {
                    "ti": "2025/2026",
                    "ph": [
                        {"day": d, "p": pts, "mp": "90'", "t1": "1", "t2": "2"}
                        for d, pts in points_by_day.items()
                    ],
                }
            ]
        }
        corpus.record_match_history(p.id, "1", perf)
    return corpus


def test_backtest_produces_one_result_per_matchday(tmp_path):
    squad = _legal_squad()
    corpus = _corpus_with_history(tmp_path, squad, {1: 50, 2: 50, 3: 50})
    matchdays = [
        MatchdayInput(
            day_number=d, squad=squad, actual_points={p.id: 50.0 for p in squad}
        )
        for d in (2, 3)
    ]

    report = run_backtest(
        corpus, lambda pid, hist: 1.0, season="2025/2026", matchdays=matchdays
    )

    assert [r.day_number for r in report.results] == [2, 3]
    assert report.mean_regret >= 0.0


def test_scorer_never_sees_the_matchday_being_predicted(tmp_path):
    """The leak guard at harness level: whatever history the scorer receives
    must contain no match at or beyond the cutoff."""
    squad = _legal_squad()
    corpus = _corpus_with_history(tmp_path, squad, {d: 50 for d in range(1, 11)})
    seen: list[int] = []

    def spy_scorer(player_id: str, history: list[dict]) -> float:
        seen.extend(m["day_number"] for m in history)
        return 1.0

    matchdays = [
        MatchdayInput(
            day_number=5, squad=squad, actual_points={p.id: 50.0 for p in squad}
        )
    ]
    run_backtest(corpus, spy_scorer, season="2025/2026", matchdays=matchdays)

    assert seen, "scorer was never called"
    assert max(seen) < 5


def test_perfect_scorer_has_zero_regret(tmp_path):
    """A scorer that knows the actual points must field the optimal 11."""
    squad = _legal_squad()
    corpus = _corpus_with_history(tmp_path, squad, {1: 50})
    actual = {p.id: float(50 + int(p.id)) for p in squad}
    matchdays = [MatchdayInput(day_number=2, squad=squad, actual_points=actual)]

    report = run_backtest(
        corpus, lambda pid, hist: actual[pid], season="2025/2026", matchdays=matchdays
    )

    assert report.mean_regret == pytest.approx(0.0)
    assert report.results[0].rank_correlation == pytest.approx(1.0)


def test_empty_matchdays_returns_empty_report(tmp_path):
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    report = run_backtest(
        corpus, lambda pid, hist: 1.0, season="2025/2026", matchdays=[]
    )

    assert report.results == []
    assert report.mean_regret == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_backtest/test_harness.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
"""Backtest replay harness.

Runs a scorer function across a sequence of matchdays and reports how well it
ranked players, using only data that existed before each matchday.

This is the *tuning* instrument: cheap, repeatable, safe to run hundreds of
times because it evaluates ranking on held-out data. It is deliberately
separate from the full-bot season replay (week 4), which is a *verdict*
instrument whose credibility decays every time it is tuned against.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from rehoboam.backtest.metrics import lineup_regret, spearman
from rehoboam.backtest.snapshot import matches_before
from rehoboam.enrichment.corpus import TrainingCorpus

ScorerFn = Callable[[str, list[dict[str, Any]]], float]


@dataclass
class MatchdayInput:
    """One matchday to replay."""

    day_number: int
    squad: list[Any]
    actual_points: dict[str, float]


@dataclass
class MatchdayResult:
    day_number: int
    chosen_points: float
    best_points: float
    regret: float
    rank_correlation: float
    players_scored: int


@dataclass
class BacktestReport:
    results: list[MatchdayResult] = field(default_factory=list)
    mean_regret: float = 0.0
    mean_rank_correlation: float = 0.0
    total_chosen_points: float = 0.0
    total_best_points: float = 0.0


def run_backtest(
    corpus: TrainingCorpus,
    scorer_fn: ScorerFn,
    *,
    season: str,
    matchdays: list[MatchdayInput],
) -> BacktestReport:
    """Replay ``matchdays``, scoring each squad with ``scorer_fn``.

    For every player the scorer receives only ``matches_before`` the matchday
    under evaluation — the harness never hands it the answer.

    Args:
        corpus: source of per-player match history
        scorer_fn: ``(player_id, truncated_history) -> predicted_points``
        season: season being replayed, e.g. ``"2025/2026"``
        matchdays: matchdays to evaluate, in order

    Returns:
        A report with per-matchday detail and season aggregates.
    """
    from rehoboam.formation import select_best_eleven

    report = BacktestReport()
    if not matchdays:
        return report

    history_cache: dict[str, list[dict[str, Any]]] = {}

    for matchday in matchdays:
        predictions: dict[str, float] = {}
        for player in matchday.squad:
            if player.id not in history_cache:
                history_cache[player.id] = corpus.matches_for_player(player.id)
            visible = matches_before(
                history_cache[player.id], season=season, day_number=matchday.day_number
            )
            predictions[player.id] = scorer_fn(player.id, visible)

        chosen = select_best_eleven(matchday.squad, predictions)
        chosen_ids = [p.id for p in chosen]

        chosen_points = sum(matchday.actual_points.get(pid, 0.0) for pid in chosen_ids)
        regret = lineup_regret(matchday.squad, chosen_ids, matchday.actual_points)

        ids = [p.id for p in matchday.squad]
        correlation = spearman(
            [predictions[pid] for pid in ids],
            [matchday.actual_points.get(pid, 0.0) for pid in ids],
        )

        report.results.append(
            MatchdayResult(
                day_number=matchday.day_number,
                chosen_points=chosen_points,
                best_points=chosen_points + regret,
                regret=regret,
                rank_correlation=correlation,
                players_scored=len(predictions),
            )
        )

    n = len(report.results)
    report.mean_regret = sum(r.regret for r in report.results) / n
    report.mean_rank_correlation = sum(r.rank_correlation for r in report.results) / n
    report.total_chosen_points = sum(r.chosen_points for r in report.results)
    report.total_best_points = sum(r.best_points for r in report.results)
    return report
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_backtest/test_harness.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the whole backtest suite together**

Run: `uv run pytest tests/test_backtest/ -v`
Expected: PASS (25 tests across the five modules)

- [ ] **Step 6: Commit**

```bash
git add rehoboam/backtest/harness.py tests/test_backtest/test_harness.py
git commit -m "feat(backtest): matchday replay harness"
```

______________________________________________________________________

## Task 12: Guardrail — squad floor and formation fillability

**Files:**

- Modify: `rehoboam/config.py:89-92`
- Modify: `rehoboam/formation.py` (append `can_fill_starting_eleven`)
- Create: `tests/test_squad_floor_guardrail.py`

**Interfaces:**

- Produces: `formation.can_fill_starting_eleven(available: list) -> dict` with keys `ok: bool`, `reason: str`, `counts: dict[str, int]`

**Why:** matchdays 6, 17 and 21 fielded only 10 players, costing −100 each. `min_squad_size = 10` cannot fill 11 slots. Squad size alone is insufficient, though — a 13-man squad with only two fit defenders still cannot field a legal formation, so the position-aware check is the real fix.

- [ ] **Step 1: Write the failing test**

```python
"""Guardrail regression tests for the 2025/26 10-man lineup failures.

Matchdays 6, 17 and 21 fielded only 10 players, costing -100 points each.
Root cause: min_squad_size defaulted to 10, which cannot fill 11 slots.
"""

from __future__ import annotations

from rehoboam.config import Settings
from rehoboam.formation import can_fill_starting_eleven
from rehoboam.kickbase_client import Player


def _player(pid: str, position: str) -> Player:
    return Player(
        id=pid,
        first_name="F",
        last_name=f"P{pid}",
        position=position,
        team_id="1",
        team_name="T",
        market_value=1_000_000,
        points=0,
        average_points=0.0,
    )


def test_squad_floor_can_fill_eleven_slots():
    """The 2025/26 bug in one assertion: the floor must exceed the 11 a
    lineup needs, with room for injury cover."""
    assert Settings().min_squad_size >= 13


def test_can_fill_with_a_legal_squad():
    available = (
        [_player("1", "Goalkeeper")]
        + [_player(str(i), "Defender") for i in range(2, 7)]
        + [_player(str(i), "Midfielder") for i in range(7, 12)]
        + [_player(str(i), "Forward") for i in range(12, 15)]
    )
    result = can_fill_starting_eleven(available)
    assert result["ok"] is True


def test_cannot_fill_with_only_ten_available():
    available = (
        [_player("1", "Goalkeeper")]
        + [_player(str(i), "Defender") for i in range(2, 5)]
        + [_player(str(i), "Midfielder") for i in range(5, 10)]
        + [_player("10", "Forward")]
    )
    result = can_fill_starting_eleven(available)
    assert result["ok"] is False
    assert "10" in result["reason"] or "11" in result["reason"]


def test_cannot_fill_without_a_goalkeeper():
    """13 outfielders and no keeper is a -100 penalty waiting to happen."""
    available = (
        [_player(str(i), "Defender") for i in range(2, 8)]
        + [_player(str(i), "Midfielder") for i in range(8, 13)]
        + [_player(str(i), "Forward") for i in range(13, 16)]
    )
    result = can_fill_starting_eleven(available)
    assert result["ok"] is False
    assert "Goalkeeper" in result["reason"]


def test_cannot_fill_with_too_few_defenders():
    available = (
        [_player("1", "Goalkeeper")]
        + [_player("2", "Defender"), _player("3", "Defender")]
        + [_player(str(i), "Midfielder") for i in range(4, 12)]
        + [_player("12", "Forward")]
    )
    result = can_fill_starting_eleven(available)
    assert result["ok"] is False
    assert "Defender" in result["reason"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_squad_floor_guardrail.py -v`
Expected: FAIL — `test_squad_floor_can_fill_eleven_slots` fails (10 \< 13) and the others fail with `ImportError: cannot import name 'can_fill_starting_eleven'`

- [ ] **Step 3: Raise the squad floor**

In `rehoboam/config.py`, replace lines 89–92:

```python
min_squad_size: int = Field(
    default=13,
    description=(
        "Minimum squad size. Must exceed the 11 a lineup needs, with cover for "
        "injuries — a 10-player squad cannot fill 11 slots and eats the -100 "
        "empty-slot penalty (cost 3 matchdays in 2025/26)."
    ),
)
```

- [ ] **Step 4: Add the fillability check**

Append to `rehoboam/formation.py`:

```python
def can_fill_starting_eleven(available: list) -> dict[str, any]:
    """Can a legal starting 11 be built from these available players?

    Squad size alone does not answer this: a 13-man squad whose defenders are
    all injured still cannot field a legal formation. Called before a matchday
    so an emergency buy can be triggered while there is still time.

    Args:
        available: players who can actually play (exclude injured/suspended)

    Returns:
        ``{"ok": bool, "reason": str, "counts": dict[str, int]}``
    """
    requirements = FormationRequirements()
    counts = get_position_counts(available)

    if len(available) < requirements.starting_eleven_size:
        return {
            "ok": False,
            "reason": (
                f"Only {len(available)} available players, need "
                f"{requirements.starting_eleven_size}"
            ),
            "counts": counts,
        }

    minimums = {
        "Goalkeeper": requirements.min_goalkeepers,
        "Defender": requirements.min_defenders,
        "Midfielder": requirements.min_midfielders,
        "Forward": requirements.min_forwards,
    }
    for position, minimum in minimums.items():
        have = counts.get(POSITION_MAPPING[position], counts.get(position, 0))
        if have < minimum:
            return {
                "ok": False,
                "reason": f"{position}: have {have}, need {minimum}",
                "counts": counts,
            }

    return {"ok": True, "reason": "Legal starting 11 available", "counts": counts}
```

**Note:** `get_position_counts` returns keys per `POSITION_MAPPING` (`"GK"`, `"DEF"`, …). The lookup above tries the mapped key first and falls back to the full name; confirm against `formation.py:32` and simplify to whichever it actually returns.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_squad_floor_guardrail.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Check nothing else depended on the old floor**

Run: `uv run pytest -v`
Expected: PASS. If a test asserted `min_squad_size == 10`, update it — the old value was the defect.

- [ ] **Step 7: Commit**

```bash
git add rehoboam/config.py rehoboam/formation.py tests/test_squad_floor_guardrail.py
git commit -m "fix: raise squad floor to 13 + add formation fillability check"
```

______________________________________________________________________

## Task 13: Guardrail — budget-at-kickoff regression test

**Files:**

- Create: `tests/test_budget_kickoff_guardrail.py`

**Interfaces:**

- Consumes: the REH-11 budget block in `auto_trader.py`

**Why:** matchday 14 scored **0 official points** despite fielding 11 players worth 1,109 — the negative-budget-at-kickoff penalty, ~1,100 points in one day. REH-11's block landed *after* that failure and has never faced it.

- [ ] **Step 1: Locate the existing budget block**

Run: `grep -n "budget" rehoboam/auto_trader.py | grep -i "negative\|block\|kickoff\|< 0"`

Record the exact function name and line. The test targets that function directly; if no such guard exists, the test becomes a specification for one and Step 3 implements it.

- [ ] **Step 2: Write the failing test**

Adapt the guard's real name from Step 1 — the intent is fixed, the binding is not:

```python
"""Regression test for the matchday-14 zero-point failure.

On matchday 14 the bot fielded 11 players worth 1,109 points and officially
scored 0: budget was negative at kickoff, which zeroes the entire matchday.
REH-11 added a block after the fact. This test is the failure it never faced.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from rehoboam.auto_trader import AutoTrader


def _trader() -> AutoTrader:
    api = MagicMock()
    return AutoTrader(api=api)


def test_buy_blocked_when_it_would_leave_budget_negative_before_kickoff():
    """The matchday-14 scenario: a purchase that pushes budget below zero
    with a match imminent must be refused."""
    trader = _trader()
    allowed = trader._is_purchase_allowed(
        cost=5_000_000, budget=3_000_000, days_until_match=1
    )
    assert allowed is False


def test_buy_allowed_when_budget_covers_it():
    trader = _trader()
    assert (
        trader._is_purchase_allowed(
            cost=2_000_000, budget=3_000_000, days_until_match=1
        )
        is True
    )


def test_negative_budget_allowed_far_from_kickoff():
    """Going negative between matchdays is the intended aggressive strategy —
    only kickoff matters."""
    trader = _trader()
    assert (
        trader._is_purchase_allowed(
            cost=5_000_000, budget=3_000_000, days_until_match=6
        )
        is True
    )


def test_exactly_zero_budget_is_acceptable():
    """The penalty triggers on negative, not zero."""
    trader = _trader()
    assert (
        trader._is_purchase_allowed(
            cost=3_000_000, budget=3_000_000, days_until_match=1
        )
        is True
    )
```

- [ ] **Step 3: Run the test and reconcile with reality**

Run: `uv run pytest tests/test_budget_kickoff_guardrail.py -v`

Three possible outcomes, each with a different response:

1. **Passes as written** — REH-11 already covers this. Keep the test as the regression guard; it is now pinned.
1. **Fails because the method has a different name/signature** — rebind the test to the real API. Do not weaken the assertions.
1. **Fails because the guard genuinely permits the purchase** — this is the matchday-14 bug still live. Fix the guard so budget cannot go negative within the kickoff buffer, then rerun.

- [ ] **Step 4: Verify the whole suite still passes**

Run: `uv run pytest -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_budget_kickoff_guardrail.py rehoboam/auto_trader.py
git commit -m "test: pin budget-at-kickoff guard against the matchday-14 failure"
```

______________________________________________________________________

## Task 14: Delete provably-dead code

**Files:**

- Delete: `api/` (11 files, 1,977 lines), `rehoboam/roster_analyzer.py`, `railway.toml`, `deploy_lambda.sh`, `deploy/requirements-lambda.txt`, `.worktrees/ep-scoring/`
- Delete: 28 root/docs markdown files
- Modify: `pyproject.toml` (drop the `web` extra)

**Scope reminder:** `value_calculator.py`, `expected_points.py`, `profit_trader.py`, `bid_evaluator.py` and `league_compliance.py` are **live** and are *not* deleted here — see "Scope correction" above.

- [ ] **Step 1: Re-verify nothing imports the deletion targets**

```bash
grep -rn "roster_analyzer" rehoboam tests deploy --include="*.py" | grep -v "^rehoboam/roster_analyzer.py"
grep -rn "from api\|import api\." rehoboam tests deploy --include="*.py"
```

Expected: **no output**. If `roster_analyzer` shows a hit outside `api/`, stop and reassess — it moves to week 4.

- [ ] **Step 2: Delete the dead application code**

```bash
git rm -r api/
git rm rehoboam/roster_analyzer.py
git rm railway.toml deploy_lambda.sh deploy/requirements-lambda.txt
rm -rf .worktrees/ep-scoring
git worktree prune
```

- [ ] **Step 3: Drop the `web` extra from pyproject.toml**

Remove this block (`pyproject.toml:27-33`) — every one of these dependencies existed only for `api/`:

```toml
web = [
    "fastapi>=0.109.0",
    "uvicorn[standard]>=0.27.0",
    "python-jose[cryptography]>=3.3.0",
    "passlib[bcrypt]>=1.7.4",
    "python-multipart>=0.0.6",
]
```

Then refresh the lockfile:

```bash
uv lock
```

- [ ] **Step 4: Delete the stale documentation**

These describe strategies that no longer exist in the codebase:

```bash
git rm ANALYZE_CLEANUP_SUMMARY.md AUTO_TRADING_GUIDE.md BID_LOGIC_FIX_SUMMARY.md \
       BID_MANAGEMENT_FEATURES.md BUGFIXES.md CI_SETUP_SUMMARY.md \
       ENHANCED_ANALYSIS_GUIDE.md IMPROVEMENTS.md LEAGUE_COMPLIANCE.md \
       MY_BIDS_IMPLEMENTATION.md
git rm docs/AUCTION_MONITORING.md docs/AUTO_PURCHASE_PRICES.md \
       docs/DEBT_BASED_FLIPPING.md docs/DUAL_TRADING_STRATEGY.md \
       docs/LEARNING_SYSTEM_PROPOSAL.md docs/MATCHUP_ANALYSIS.md \
       docs/N_FOR_M_TRADING.md docs/SELL_ANALYSIS.md docs/SELL_STRATEGY.md \
       docs/SQUAD_ANALYSIS_GUIDE.md docs/STRENGTH_OF_SCHEDULE.md \
       docs/VALUE_BOUNDED_LEARNING.md
```

**Keep:** `CLAUDE.md`, `README.md`, `DEVELOPMENT.md`, `docs/KICKBASE_API_REFERENCE.md`, and everything under `docs/superpowers/`.

- [ ] **Step 5: Verify the suite still passes**

Run: `uv run pytest -v`
Expected: PASS. Any failure means something was still referenced — restore it with `git checkout HEAD -- <path>` and move it to week 4.

- [ ] **Step 6: Verify the CLI and the Azure entrypoint still import**

```bash
uv run rehoboam --help
uv run python -c "import deploy.azure_function.function_app" 2>/dev/null || \
  echo "(azure entrypoint not importable standalone — check deploy/azure_function/function_app.py imports by hand)"
```

Expected: the CLI help renders with all commands including `enrich-corpus`.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "chore: delete dead FastAPI app, Railway/Lambda config, and stale docs"
```

______________________________________________________________________

## Task 15: Prune merged branches and open the PR

**Files:** none (repository hygiene)

- [ ] **Step 1: Confirm the sweep from Task 6 finished**

```bash
tail -5 logs/enrich-corpus.log
sqlite3 logs/training_corpus.db \
  "SELECT (SELECT COUNT(*) FROM player_universe) AS players,
          (SELECT COUNT(*) FROM player_match_history) AS matches,
          (SELECT COUNT(*) FROM mv_series) AS mv_points;"
```

Record these three numbers for the PR description — they are the headline result of week 1. For comparison, the old corpus held 353 players and 27,374 match records.

- [ ] **Step 2: Run the baseline through the harness for a first real number**

Write a throwaway script in the scratchpad (not committed) that loads the 2025/26 fielded lineups from `logs/bid_learning.db`, reconstructs squads via Task 8, and runs `run_backtest` with `season_average_baseline`. Record `mean_regret` and `mean_rank_correlation`.

This is the number weeks 2–3 must beat. If the harness cannot run for lack of corpus coverage, say so explicitly in the PR rather than reporting a partial figure as if it were complete.

- [ ] **Step 3: Delete merged remote branches**

```bash
git fetch --prune
for b in $(git branch -r --merged origin/main | grep -v "origin/main\|origin/HEAD" | sed 's|origin/||'); do
  echo "would delete: $b"
done
```

Review the list, then delete with `git push origin --delete <branch>` for each confirmed one.

- [ ] **Step 4: Full verification before the PR**

```bash
uv run pytest -v
uv run ruff check rehoboam/
uv run mypy rehoboam/ --ignore-missing-imports
```

Expected: tests PASS, ruff clean on new files. Pre-existing mypy findings in untouched modules are acceptable; new ones are not.

- [ ] **Step 5: Open the PR**

```bash
git push -u origin feat/week1-enrichment-harness
gh pr create --title "Week 1: training corpus, backtest harness, penalty guardrails" --body "$(cat <<'EOF'
Implements week 1 of `docs/superpowers/specs/2026-07-29-rehoboam-v2-design.md`.

## Enrichment
- `TrainingCorpus` — durable, non-expiring store (deliberately not `performance_cache`, which has a 6h TTL and a 7-day cleanup path)
- Resumable league-wide sweep, throttled, tolerant of per-player failure
- `rehoboam enrich-corpus` CLI

Corpus after the sweep: **N players / N match records / N MV points** (was 353 / 27,374).

## Backtest harness
- `matches_before` — the anti-leakage primitive, with a deliberate-cheat test across all 34 cutoffs
- Squad reconstruction from flip windows + fielded lineups (medium fidelity — documented)
- `spearman` + `lineup_regret`, no new dependencies
- `season_average_baseline` — the model v2 must beat, and the shipping fallback under the spec's safety valve

Baseline on 2025/26: mean regret **N**, mean rank correlation **N**.

## Guardrails
- `min_squad_size` 10 → 13. A 10-player squad cannot fill 11 slots; this cost -100 on matchdays 6, 17 and 21
- `can_fill_starting_eleven` — position-aware check, because squad size alone doesn't guarantee a legal formation
- Budget-at-kickoff guard pinned against the matchday-14 zero-point failure (~1,100 points)

## Deletions
Dead FastAPI app (1,977 lines), `roster_analyzer`, Railway/Lambda config, the `web` extra, 22 stale docs.

**Scope correction to the spec:** `value_calculator` and `expected_points` are *not* dead — `auto_trader.py:1294` reaches them via the lineup fallback. They carry the same saturation defect and are removed in weeks 2-3 with the scorer rewrite.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Fill in the recorded numbers before submitting.

______________________________________________________________________

## Self-Review

**Spec coverage (§ by §):**

| Spec section                               | Task          | Notes                                                                                                                                                                                                      |
| ------------------------------------------ | ------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| §5.1 Tier-1 Kickbase enrichment            | 1, 3, 4, 5, 6 | Universe, performance, MV covered. **Gap: `transferHistory` and `managers/{id}/squad` are not swept in week 1** — they serve the week-4 buy-side simulation, not the week-2 scorer. Deferred deliberately. |
| §5.2 Tier-2 external (ClubElo, OpenLigaDB) | —             | **Deferred to week 3** with the context/fixture model. No week-1 consumer.                                                                                                                                 |
| §5.4 API-Football excluded                 | —             | No task; correctly absent.                                                                                                                                                                                 |
| §6.1 Scorer harness                        | 7–11          | Regret, rank correlation, baseline all covered. Buy-quality metric deferred to week 4 with the decision layer.                                                                                             |
| §6.3 Leakage                               | 7             | Deliberate-cheat test across all 34 cutoffs.                                                                                                                                                               |
| §6.3 Survivorship                          | 5, 6          | Full-league sweep.                                                                                                                                                                                         |
| §6.3 Overfit / train-test split            | —             | **Enforced in week 2** when a model is actually fitted; nothing to split in week 1.                                                                                                                        |
| §4.3 Guardrails                            | 12, 13        | Squad floor, fillability, budget block. Matchday 1–3 trade cap is week 4 (decision layer).                                                                                                                 |
| §7 Deletions                               | 14, 15        | Scope-corrected — live modules deferred.                                                                                                                                                                   |

**Placeholder scan:** no TBDs. Task 13 Step 3 branches on a real unknown (the guard's actual name) with a defined response per outcome, rather than hand-waving. Task 12 Step 4 flags the `get_position_counts` key format for verification against the source.

**Type consistency:** `parse_minutes` (Task 2) is consumed by Task 3. `TrainingCorpus.matches_for_player` returns dicts keyed `season, day_number, match_date, points, minutes, team_id, opponent_team_id, is_home` — matching what `matches_before` (Task 7), `season_average_baseline` (Task 10) and `run_backtest` (Task 11) index into. `SweepStats` fields match the CLI table in Task 6. `MatchdayInput` / `MatchdayResult` / `BacktestReport` fields align between Task 11's implementation and its tests.
