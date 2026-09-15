# PR E: League-Wide Predictions and Calibration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every trading session scores every live player from store rows into `predictions`; the ingestion app turns finished matchdays into `calibration_rows` and one `calibration_reports` row with a same-rows baseline and the gate verdict; `rehoboam calibrate --backfill-day N` produces three real reports before merge.

**Architecture:** One pure scorer over store rows (`scoring/store_scorer.py`) that composes through the same `compose_ep` the live path uses; one store module (`store/calibration_store.py`, one transaction per call, bulk queries); one pure metrics/gate module (`services/calibration.py`); one orchestration module (`enrichment/calibrate.py`) called from the ingestion Function and the CLI. The lineup decision does not change.

**Tech Stack:** Python 3.12, psycopg 3 (dict rows, no prepared statements), pytest with a real PostgreSQL (`store_dsn`), Typer, Azure Functions timer.

**Spec:** `docs/superpowers/specs/2026-09-15-pr-e-calibration-design.md` (parent: `docs/superpowers/specs/2026-09-11-data-foundation-design.md` §4, §6).

## Global Constraints

- Every SQL statement schema-qualifies `rehoboam.<table>`; store methods run one transaction per call through `connect(self.dsn)`; `prepare_threshold=None` is set by `connect`, never re-set.
- Line length 100 (black + ruff, CI's pinned versions). Do not run `black` on an existing file; format only the files you create or the hunks you add.
- New tables are granted to `rehoboam_bot` by `migrate`'s `refresh_grants`; migrations never `grant` themselves.
- Tests against the database use the `store_dsn` fixture (a fresh migrated database). Never read, print or edit `.env`; never connect to any database other than the test one; never run `rehoboam auto|status|ingest|calibrate|export|migrate` — the controller runs live evidence.
- No API calls in the store scorer, the metrics module or the store module. The only new HTTP call is one `get_competition_matchdays()` per ingestion run.
- Every new call in the trading session is best-effort (try/except, logged with `exc_info=True`); a failure there must never stop the lineup step.
- Commit trailers, exactly: `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01F4d1fpUbNh28evK1pNy4Le`. Never commit to `main`.
- Implementers never dispatch subagents.

______________________________________________________________________

### Task 1: Migration 004 and the `CalibrationStore` core

**Files:**

- Create: `rehoboam/store/migrations/004_calibration.sql`
- Create: `rehoboam/store/calibration_store.py`
- Create: `tests/store/test_calibration_store.py`

**Interfaces:**

- Consumes: `rehoboam.store.connect`, `rehoboam.store.corpus_store.CorpusStore` (`upsert_players`, `record_match_history`, `record_status_daily`, `mark_fetched`) for seeding tests.

- Produces: `CalibrationStore(dsn=None)` with `connection()`, `current_season() -> str | None`, `stored_players(*, since_iso, status_day, before_iso=None) -> list[StoredPlayer]`, `write_predictions(rows) -> int`; `StoredPlayer` lives in `rehoboam/scoring/store_scorer.py` (Task 2) — this task creates it there with the fields below and Task 2 adds the functions.

- [ ] **Step 1: Write the migration**

```sql
-- PR E: league-wide predictions, the rows that pair them with actuals, and one report per matchday.
create table if not exists rehoboam.predictions (
    session_id   text not null,
    player_id    text not null,
    season       text not null,
    day_number   integer not null,
    kickoff      double precision not null,     -- epoch of the next kickoff at prediction time
    predicted_at double precision not null,     -- epoch
    predicted_ep double precision not null,
    p_status     jsonb not null,                -- {"1": p, "3": p, "4": p, "5": p}
    rate         double precision not null,
    prev_status  integer,
    live_status  integer,
    position     text not null,
    team_id      text,
    owned        boolean not null default false,
    listed       boolean not null default false,
    in_best_11   boolean not null default false,
    live_ep      double precision,              -- the API-path PlayerScore for owned/listed players
    data_grade   text not null,
    app          text not null,                 -- function | cli
    dry_run      boolean not null default false,
    backfill     boolean not null default false,
    primary key (session_id, player_id)
);
create index if not exists idx_predictions_matchday
    on rehoboam.predictions (season, day_number, predicted_at);

create table if not exists rehoboam.calibration_rows (
    season        text not null,
    day_number    integer not null,
    player_id     text not null,
    backfill      boolean not null default false,
    session_id    text,
    predicted_ep  double precision,
    live_ep       double precision,
    baseline_ep   double precision not null,
    actual_points integer not null,
    minutes       integer not null,
    status        integer,
    position      text not null,
    team_id       text,
    owned         boolean not null default false,
    in_best_11    boolean not null default false,
    prev_status   integer,
    live_status   integer,
    primary key (season, day_number, player_id, backfill)
);

create table if not exists rehoboam.calibration_reports (
    season                text not null,
    day_number            integer not null,
    backfill              boolean not null default false,
    computed_at           double precision not null,
    n                     integer not null,
    n_unpredicted         integer not null,
    n_stale_rows          integer not null default 0,
    mae                   double precision,
    bias                  double precision,
    spearman              double precision,
    baseline_spearman     double precision,
    top11_regret          double precision,
    baseline_top11_regret double precision,
    squad_regret          double precision,
    live_spearman         double precision,
    live_n                integer not null default 0,
    by_position           jsonb not null,
    by_status             jsonb not null,
    worst                 jsonb not null,
    gate                  jsonb,
    telegram_sent         boolean not null default false,
    primary key (season, day_number, backfill)
);
```

- [ ] **Step 2: Create `rehoboam/scoring/store_scorer.py` with the dataclasses only**

```python
"""Score a player from store rows through the same `compose_ep` the live path uses (PR E §1).

The live scorer reads a performance payload and a details payload; this one
reads the rows PR C1's ingestion wrote from the same endpoints. Both compose
through `compose_ep`, so the two numbers differ only by their inputs — which
is exactly what `predictions.live_ep` versus `predicted_ep` measures.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StoredPlayer:
    player_id: str
    position: str  # Goalkeeper | Defender | Midfielder | Forward
    team_id: str | None
    market_value: int | None
    live_status: int | None  # player_status_daily.status, None when no row
    lineup_probability: int | None  # stored, not used by the model
    status_fetched_at: float | None  # epoch of the status row, None when no row
    matches: list[dict[str, Any]]  # player_match_history rows, oldest first


@dataclass(frozen=True)
class StoredPrediction:
    player_id: str
    predicted_ep: float
    p_status: dict[int, float]
    rate: float
    prev_status: int | None
    data_grade: str
```

- [ ] **Step 3: Write the failing store tests**

```python
"""`predictions` writes and the bulk reads the store scorer needs (PR E Task 1)."""

from __future__ import annotations

from datetime import date

from rehoboam.store.calibration_store import CalibrationStore
from rehoboam.store.corpus_store import CorpusStore


def _perf(matches):
    return {"it": [{"ti": "2026/2027", "ph": matches}]}


def _match(day, md, st, p, mp="90"):
    return {
        "day": day,
        "md": md,
        "st": st,
        "p": p,
        "mp": mp,
        "t1": "1",
        "t2": "2",
        "pt": "1",
    }


def _seed(dsn):
    corpus = CorpusStore(dsn=dsn)
    corpus.upsert_players(
        [
            {
                "player_id": "a",
                "first_name": None,
                "last_name": "A",
                "position": "Midfielder",
                "team_id": "1",
                "market_value": 5_000_000,
                "average_points": 40.0,
            },
            {
                "player_id": "b",
                "first_name": None,
                "last_name": "B",
                "position": "Forward",
                "team_id": "2",
                "market_value": 3_000_000,
                "average_points": 20.0,
            },
            {
                "player_id": "nopos",
                "first_name": None,
                "last_name": "N",
                "position": None,
                "team_id": "2",
                "market_value": 1,
                "average_points": 0.0,
            },
        ]
    )
    corpus.record_match_history(
        "a",
        "1",
        _perf(
            [
                _match(1, "2026-08-22T13:30:00Z", 5, 80),
                _match(2, "2026-08-29T13:30:00Z", 4, 0),
                _match(4, "2026-09-19T13:30:00Z", 0, 0),
            ]
        ),
    )
    corpus.record_match_history(
        "b", "2", _perf([_match(1, "2026-08-22T13:30:00Z", 3, 12)])
    )
    corpus.record_status_daily(
        "a",
        date(2026, 9, 15),
        {"st": 0, "prob": 1, "mv": 5_000_000, "tid": "1"},
        1_789_500_000.0,
    )
    corpus.record_status_daily(
        "a",
        date(2026, 9, 14),
        {"st": 2, "prob": 2, "mv": 5_000_000, "tid": "1"},
        1_789_400_000.0,
    )
    return corpus


def test_current_season_is_the_newest_title(store_dsn):
    _seed(store_dsn)
    assert CalibrationStore(dsn=store_dsn).current_season() == "2026/2027"


def test_stored_players_assembles_rows_per_player(store_dsn):
    _seed(store_dsn)
    players = {
        p.player_id: p
        for p in CalibrationStore(dsn=store_dsn).stored_players(
            since_iso="2026-08-01T00:00:00Z", status_day=date(2026, 9, 15)
        )
    }
    assert set(players) == {"a", "b"}  # no position → not a scorable player
    a = players["a"]
    assert (a.position, a.team_id, a.live_status, a.lineup_probability) == (
        "Midfielder",
        "1",
        0,
        1,
    )
    assert a.status_fetched_at == 1_789_500_000.0  # the newest of the two days
    assert [m["day_number"] for m in a.matches] == [1, 2, 4]
    assert a.matches[0]["status"] == 5 and a.matches[0]["points"] == 80
    b = players["b"]
    assert b.live_status is None and b.status_fetched_at is None


def test_stored_players_before_is_the_leak_boundary(store_dsn):
    _seed(store_dsn)
    players = {
        p.player_id: p
        for p in CalibrationStore(dsn=store_dsn).stored_players(
            since_iso="2026-08-01T00:00:00Z",
            status_day=date(2026, 9, 15),
            before_iso="2026-08-29T13:30:00Z",
        )
    }
    assert [m["day_number"] for m in players["a"].matches] == [1]


def test_write_predictions_upserts_on_session_and_player(store_dsn):
    store = CalibrationStore(dsn=store_dsn)
    row = {
        "session_id": "s1",
        "player_id": "a",
        "season": "2026/2027",
        "day_number": 4,
        "kickoff": 1_789_756_200.0,
        "predicted_at": 1_789_500_000.0,
        "predicted_ep": 41.5,
        "p_status": {1: 0.1, 3: 0.2, 4: 0.1, 5: 0.6},
        "rate": 60.0,
        "prev_status": 5,
        "live_status": 0,
        "position": "Midfielder",
        "team_id": "1",
        "owned": True,
        "listed": False,
        "in_best_11": True,
        "live_ep": 44.0,
        "data_grade": "A",
        "app": "cli",
        "dry_run": True,
        "backfill": False,
    }
    assert store.write_predictions([row]) == 1
    assert store.write_predictions([dict(row, predicted_ep=50.0)]) == 1
    with store.connection() as conn:
        got = conn.execute(
            "SELECT predicted_ep, p_status, owned FROM rehoboam.predictions "
            "WHERE session_id = 's1' AND player_id = 'a'"
        ).fetchone()
    assert got["predicted_ep"] == 50.0 and got["p_status"] == {
        "5": 0.6,
        "1": 0.1,
        "3": 0.2,
        "4": 0.1,
    }
    assert got["owned"] is True
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `uv run pytest tests/store/test_calibration_store.py -q`
Expected: FAIL with `ModuleNotFoundError: rehoboam.store.calibration_store`.

- [ ] **Step 5: Write `rehoboam/store/calibration_store.py`**

```python
"""Predictions, calibration rows and reports in the store (PR E).

One transaction per call, bulk queries only: the trading session scores
~460 players and must not pay one pooler round trip per player (PR C1
measured ~1 s each before it pinned a connection).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from psycopg.types.json import Jsonb

from rehoboam.scoring.store_scorer import StoredPlayer

_PREDICTION_COLUMNS = (
    "session_id",
    "player_id",
    "season",
    "day_number",
    "kickoff",
    "predicted_at",
    "predicted_ep",
    "p_status",
    "rate",
    "prev_status",
    "live_status",
    "position",
    "team_id",
    "owned",
    "listed",
    "in_best_11",
    "live_ep",
    "data_grade",
    "app",
    "dry_run",
    "backfill",
)


class CalibrationStore:
    def __init__(self, dsn: str | None = None):
        self.dsn = dsn

    def connection(self):
        from rehoboam.store import connect

        return connect(self.dsn)

    def current_season(self) -> str | None:
        """The newest season title in the corpus, e.g. `2026/2027`."""
        with self.connection() as conn:
            row = conn.execute(
                "SELECT MAX(season) AS season FROM rehoboam.player_match_history"
            ).fetchone()
        return row["season"] if row and row["season"] else None

    def stored_players(
        self, *, since_iso: str, status_day: date, before_iso: str | None = None
    ) -> list[StoredPlayer]:
        """Every player with a position, with his newest status row on or after
        `status_day - 1` and his match rows dated in `[since_iso, before_iso)`.

        `before_iso` is the leak boundary for a backfill: rows dated at or after
        it never reach the scorer. Match dates are ISO strings in the store
        (`2026-09-19T13:30:00Z`), so the comparison is textual and only valid
        for that exact format — which is what `rows.match_history_rows` writes.
        """
        with self.connection() as conn:
            universe = conn.execute(
                "SELECT player_id, position, team_id, market_value FROM rehoboam.player_universe "
                "WHERE position IS NOT NULL ORDER BY player_id"
            ).fetchall()
            status = conn.execute(
                "SELECT DISTINCT ON (player_id) player_id, status, lineup_probability, fetched_at "
                "FROM rehoboam.player_status_daily WHERE day >= %s "
                "ORDER BY player_id, day DESC",
                (status_day - timedelta(days=1),),
            ).fetchall()
            params: list[Any] = [since_iso]
            before_clause = ""
            if before_iso is not None:
                before_clause = " AND match_date < %s"
                params.append(before_iso)
            matches = conn.execute(
                "SELECT player_id, season, day_number, match_date, points, minutes, status "
                "FROM rehoboam.player_match_history WHERE match_date >= %s"
                + before_clause
                + " ORDER BY player_id, season, day_number",
                params,
            ).fetchall()
        status_by_id = {r["player_id"]: r for r in status}
        matches_by_id: dict[str, list[dict[str, Any]]] = {}
        for m in matches:
            matches_by_id.setdefault(m["player_id"], []).append(dict(m))
        out: list[StoredPlayer] = []
        for u in universe:
            s = status_by_id.get(u["player_id"])
            out.append(
                StoredPlayer(
                    player_id=u["player_id"],
                    position=u["position"],
                    team_id=u["team_id"],
                    market_value=u["market_value"],
                    live_status=s["status"] if s else None,
                    lineup_probability=s["lineup_probability"] if s else None,
                    status_fetched_at=float(s["fetched_at"]) if s else None,
                    matches=matches_by_id.get(u["player_id"], []),
                )
            )
        return out

    def write_predictions(self, rows: list[dict[str, Any]]) -> int:
        """Upsert on `(session_id, player_id)`; `p_status` keys become strings in jsonb."""
        if not rows:
            return 0
        cols = ", ".join(_PREDICTION_COLUMNS)
        placeholders = ", ".join(["%s"] * len(_PREDICTION_COLUMNS))
        updates = ", ".join(
            f"{c} = excluded.{c}"
            for c in _PREDICTION_COLUMNS
            if c not in ("session_id", "player_id")
        )
        values = []
        for r in rows:
            row = dict(r)
            row["p_status"] = Jsonb({str(k): v for k, v in row["p_status"].items()})
            values.append([row[c] for c in _PREDICTION_COLUMNS])
        with self.connection() as conn, conn.cursor() as cur:
            # nosec B608 -- identifiers come from the `_PREDICTION_COLUMNS` constant;
            # every value is a `%s` parameter.
            cur.executemany(
                f"INSERT INTO rehoboam.predictions ({cols}) VALUES ({placeholders}) "  # nosec B608
                f"ON CONFLICT (session_id, player_id) DO UPDATE SET {updates}",
                values,
            )
        return len(rows)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/store/test_calibration_store.py tests/store/test_migrate.py -q`
Expected: PASS. (`test_migrate.py` proves 004 applies from scratch and is recorded.)

- [ ] **Step 7: Commit**

```bash
git add rehoboam/store/migrations/004_calibration.sql rehoboam/store/calibration_store.py rehoboam/scoring/store_scorer.py tests/store/test_calibration_store.py
git commit -m "feat(store): predictions, calibration_rows, calibration_reports and their bulk reads"
```

______________________________________________________________________

### Task 2: The store scorer

**Files:**

- Modify: `rehoboam/scoring/store_scorer.py` (add the functions below the dataclasses)
- Create: `tests/test_scoring_v2/test_store_scorer.py`

**Interfaces:**

- Consumes: `rehoboam.scoring.v2.adapter` (`prev_status_from_history`, `availability_probs`, `compose_ep`, `score_player_v2`, `MIN_SEASON_MATCHDAYS`, `PLAYED_OR_ABSENT_STATUSES`, `PLAYED_STATUSES_ON_PITCH`), `rehoboam.scoring.v2.features.PLAYED_STATUSES`, `RateModel.quality` (a `dict[str, float]` of fitted player ids).

- Produces: `played_share_from_rows(matches) -> tuple[int, int] | None`, `score_stored(player, *, now, max_status_age_days, availability, rate) -> StoredPrediction`.

- [ ] **Step 1: Write the failing tests**

```python
"""`score_stored` must equal `score_player_v2` on the same matches (PR E §1, Task 2)."""

from __future__ import annotations

from datetime import datetime, timezone

from rehoboam.kickbase_client import MarketPlayer
from rehoboam.scoring.models import PlayerData
from rehoboam.scoring.store_scorer import (
    StoredPlayer,
    played_share_from_rows,
    score_stored,
)
from rehoboam.scoring.v2.adapter import score_player_v2
from rehoboam.scoring.v2.coefficients import load_coefficients

NOW = datetime(2026, 9, 15, 20, 0, tzinfo=timezone.utc)


def _payload_match(day, md, st, p, mp="90"):
    return {"day": day, "md": md, "st": st, "p": p, "mp": mp}


def _row(day, md, st, p, minutes=90, season="2026/2027"):
    return {
        "season": season,
        "day_number": day,
        "match_date": md,
        "status": st,
        "points": p,
        "minutes": minutes,
    }


HISTORY = [
    (1, "2026-08-22T13:30:00Z", 5, 80),
    (2, "2026-08-29T13:30:00Z", 4, 0),
    (3, "2026-09-12T13:30:00Z", 3, 25),
    (4, "2026-09-19T13:30:00Z", 0, 0),
]


def _market_player(pid="1", position="Midfielder"):
    # Copy the `_player` builder from tests/test_scoring_v2/test_adapter.py (line 27)
    # verbatim and add the `position` argument; do not guess `MarketPlayer`'s fields.
    raise NotImplementedError(
        "replace with test_adapter._player, extended with position"
    )


def _live(pid, position, history):
    # Build `PlayerData` exactly as tests/test_scoring_v2/test_adapter.py's `_data` does
    # (player_details=None, team_strength=None, opponent_strength=None, is_dgw=False).
    perf = {"it": [{"ti": "2026/2027", "ph": [_payload_match(*h) for h in history]}]}
    data = PlayerData(
        player=_market_player(pid, position),
        performance=perf,
        player_details=None,
        team_strength=None,
        opponent_strength=None,
        is_dgw=False,
    )
    return score_player_v2(data, now=NOW, max_status_age_days=60.0)


def _stored(pid, position, history, live_status=None):
    return StoredPlayer(
        player_id=pid,
        position=position,
        team_id="1",
        market_value=1_000_000,
        live_status=live_status,
        lineup_probability=None,
        status_fetched_at=None,
        matches=[_row(*h) for h in history],
    )


def test_matches_the_live_scorer_on_the_same_history():
    availability, rate, _ = load_coefficients()
    for pid in ("1", next(iter(rate.quality))):  # one unfitted id, one fitted id
        live = _live(pid, "Midfielder", HISTORY)
        stored = score_stored(
            _stored(pid, "Midfielder", HISTORY),
            now=NOW,
            max_status_age_days=60.0,
            availability=availability,
            rate=rate,
        )
        assert round(stored.predicted_ep, 2) == live.expected_points
        assert stored.data_grade == live.data_quality.grade
        assert stored.prev_status == 3


def test_unsorted_rows_are_ordered_before_the_status_is_read():
    availability, rate, _ = load_coefficients()
    shuffled = [HISTORY[2], HISTORY[0], HISTORY[3], HISTORY[1]]
    stored = score_stored(
        _stored("1", "Forward", shuffled),
        now=NOW,
        max_status_age_days=60.0,
        availability=availability,
        rate=rate,
    )
    assert stored.prev_status == 3


def test_stale_status_falls_back_to_the_played_share_prior():
    availability, rate, _ = load_coefficients()
    old = [
        (d, "2026-03-%02dT13:30:00Z" % (d + 1), st, p)
        for d, st, p in [(20, 5, 50), (21, 5, 40), (22, 4, 0), (23, 1, 0), (24, 5, 60)]
    ]
    stored = score_stored(
        _stored("1", "Midfielder", old),
        now=NOW,
        max_status_age_days=60.0,
        availability=availability,
        rate=rate,
    )
    assert stored.prev_status is None
    assert played_share_from_rows([_row(*h) for h in old]) == (3, 5)
    live = _live("1", "Midfielder", old)
    assert round(stored.predicted_ep, 2) == live.expected_points


def test_played_share_needs_five_recorded_matchdays_in_one_season():
    rows = [_row(d, "2026-08-%02dT13:30:00Z" % (d + 20), 5, 10) for d in range(1, 5)]
    assert played_share_from_rows(rows) is None
    rows.append(_row(5, "2026-09-05T13:30:00Z", 0, 0))  # future rows are not evidence
    assert played_share_from_rows(rows) is None
    rows.append(_row(6, "2026-09-12T13:30:00Z", 4, 0))
    assert played_share_from_rows(rows) == (4, 5)


def test_injured_live_status_moves_ep_toward_zero():
    availability, rate, _ = load_coefficients()
    healthy = score_stored(
        _stored("1", "Midfielder", HISTORY, live_status=0),
        now=NOW,
        max_status_age_days=60.0,
        availability=availability,
        rate=rate,
    )
    injured = score_stored(
        _stored("1", "Midfielder", HISTORY, live_status=4),
        now=NOW,
        max_status_age_days=60.0,
        availability=availability,
        rate=rate,
    )
    assert injured.predicted_ep < healthy.predicted_ep
    assert abs(sum(injured.p_status.values()) - 1.0) < 1e-9


def test_no_matches_scores_from_the_prior():
    availability, rate, _ = load_coefficients()
    stored = score_stored(
        _stored("1", "Goalkeeper", []),
        now=NOW,
        max_status_age_days=60.0,
        availability=availability,
        rate=rate,
    )
    assert stored.prev_status is None and stored.predicted_ep > 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_scoring_v2/test_store_scorer.py -q`
Expected: FAIL with `ImportError: cannot import name 'score_stored'`.

- [ ] **Step 3: Add the functions to `rehoboam/scoring/store_scorer.py`**

Append after the dataclasses (add the imports at the top of the file):

```python
from datetime import datetime

from rehoboam.scoring.v2.adapter import (
    MIN_SEASON_MATCHDAYS,
    PLAYED_OR_ABSENT_STATUSES,
    PLAYED_STATUSES_ON_PITCH,
    availability_probs,
    compose_ep,
    prev_status_from_history,
)
from rehoboam.scoring.v2.availability import AvailabilityModel
from rehoboam.scoring.v2.features import PLAYED_STATUSES
from rehoboam.scoring.v2.rate import RateModel


def played_share_from_rows(matches: list[dict[str, Any]]) -> tuple[int, int] | None:
    """`adapter.recent_played_share` over store rows.

    Same rule: the most recent season with at least `MIN_SEASON_MATCHDAYS`
    recorded matchdays (statuses 1, 3, 4, 5 — status 0 is a fixture not yet
    played, not evidence), returning `(on the pitch, recorded)`. Season titles
    are `YYYY/YYYY`, so their string order is their time order.
    """
    by_season: dict[str, list[dict[str, Any]]] = {}
    for m in matches:
        by_season.setdefault(str(m["season"]), []).append(m)
    for season in sorted(by_season, reverse=True):
        recorded = [
            m for m in by_season[season] if m.get("status") in PLAYED_OR_ABSENT_STATUSES
        ]
        if len(recorded) < MIN_SEASON_MATCHDAYS:
            continue
        played = sum(1 for m in recorded if m["status"] in PLAYED_STATUSES_ON_PITCH)
        return played, len(recorded)
    return None


def score_stored(
    player: StoredPlayer,
    *,
    now: datetime,
    max_status_age_days: float,
    availability: AvailabilityModel,
    rate: RateModel,
) -> StoredPrediction:
    """The store-row twin of `score_player_v2`: same inputs, same composition.

    Mirrors `replay/driver.py`'s `_make_score_fn` and the adapter, in this
    order: previous played status from the rows (age-limited), the played-share
    prior only when that status is unusable, the live injury override, then
    `compose_ep`. No DGW multiplier: the Bundesliga has none, and a rescheduled
    double would show in the calibration report's bias, which is where it
    should be noticed.
    """
    ordered = sorted(
        player.matches, key=lambda m: (str(m["season"]), int(m["day_number"]))
    )
    prev_status = prev_status_from_history(
        [(m.get("match_date"), m.get("status")) for m in ordered],
        now=now,
        max_age_days=max_status_age_days,
    )
    played_history = played_share_from_rows(ordered) if prev_status is None else None
    probs = availability_probs(
        prev_status,
        availability,
        live_status=player.live_status,
        played_history=played_history,
    )
    ep = compose_ep(
        player.player_id,
        prev_status,
        player.position,
        availability,
        rate,
        live_status=player.live_status,
        played_history=played_history,
    )
    mass = sum(probs[s] for s in PLAYED_STATUSES)
    conditional_rate = (
        sum(
            probs[s] * rate.predict(player.player_id, s, player.position)
            for s in PLAYED_STATUSES
        )
        / mass
        if mass > 0
        else 0.0
    )
    return StoredPrediction(
        player_id=player.player_id,
        predicted_ep=round(ep, 2),
        p_status={int(s): float(probs[s]) for s in PLAYED_STATUSES},
        rate=round(conditional_rate, 2),
        prev_status=prev_status,
        data_grade="A" if player.player_id in rate.quality else "C",
    )
```

Before finishing, read `score_player_v2` (adapter.py lines 283–345) once and confirm two things: that it passes `played_history` only when `prev_status is None`, and that with `player_details=None` it uses the player id as the quality key. If either differs, mirror the adapter, not this snippet — the agreement test is the authority.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_scoring_v2/test_store_scorer.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add rehoboam/scoring/store_scorer.py tests/test_scoring_v2/test_store_scorer.py
git commit -m "feat(scoring): score a player from store rows through the live compose_ep"
```

______________________________________________________________________

### Task 3: The schedule knows matchday numbers and finished matchdays

**Files:**

- Modify: `rehoboam/kickoff.py`
- Modify: `rehoboam/trader.py:148-218` (`next_kickoff`)
- Test: `tests/test_kickoff.py` (append)

**Interfaces:**

- Produces: `NextFixture(day_number: int | None, at: datetime)`, `next_fixture_from_matchdays(payload, now) -> NextFixture | None`, `FinishedMatchday(day_number, first_kickoff, last_kickoff)`, `finished_matchdays(payload) -> list[FinishedMatchday]`, `NextKickoff.day_number: int | None = None` (last field, defaulted). `next_kickoff_from_matchdays` keeps its contract.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_kickoff.py`; it already defines `SCHEDULE` and `NOW`)

```python
from rehoboam.kickoff import (  # noqa: E402  (append next to the existing import)
    FinishedMatchday,
    NextFixture,
    finished_matchdays,
    next_fixture_from_matchdays,
)


class TestNextFixture:
    def test_carries_the_matchday_number(self):
        nf = next_fixture_from_matchdays(SCHEDULE, NOW)
        assert nf == NextFixture(
            day_number=4, at=datetime(2026, 9, 18, 18, 30, tzinfo=timezone.utc)
        )

    def test_nothing_upcoming_is_none(self):
        late = datetime(2026, 9, 30, tzinfo=timezone.utc)
        assert next_fixture_from_matchdays(SCHEDULE, late) is None

    def test_next_kickoff_wrapper_is_unchanged(self):
        assert (
            next_kickoff_from_matchdays(SCHEDULE, NOW)
            == next_fixture_from_matchdays(SCHEDULE, NOW).at
        )


class TestFinishedMatchdays:
    def test_only_groups_where_every_fixture_finished(self):
        assert finished_matchdays(SCHEDULE) == [
            FinishedMatchday(
                day_number=3,
                first_kickoff=datetime(2026, 9, 12, 13, 30, tzinfo=timezone.utc),
                last_kickoff=datetime(2026, 9, 12, 13, 30, tzinfo=timezone.utc),
            )
        ]

    def test_a_partly_played_matchday_is_not_finished(self):
        payload = {
            "it": [
                {
                    "day": 5,
                    "it": [
                        {"dt": "2026-10-09T18:30:00Z", "st": 2},
                        {"dt": "2026-10-10T13:30:00Z", "st": 0},
                    ],
                }
            ]
        }
        assert finished_matchdays(payload) == []

    def test_empty_groups_and_bad_dates_are_skipped(self):
        payload = {
            "it": [
                {"day": 1, "it": []},
                {"day": 2, "it": [{"dt": "garbage", "st": 2}]},
                {
                    "day": 3,
                    "it": [
                        {"dt": "2026-08-30T13:30:00Z", "st": 2},
                        {"dt": "2026-08-29T18:30:00Z", "st": 2},
                    ],
                },
                "not a dict",
            ]
        }
        got = finished_matchdays(payload)
        assert [m.day_number for m in got] == [3]
        assert got[0].first_kickoff.day == 29 and got[0].last_kickoff.day == 30

    def test_not_a_dict_is_empty(self):
        assert finished_matchdays(None) == []


class TestTraderDayNumber:
    def test_next_kickoff_carries_the_schedule_day(self):
        api = SimpleNamespace(
            get_competition_matchdays=lambda competition_id="1": SCHEDULE,
            get_starting_eleven=lambda league: {"lp": [], "nlp": []},
            client=SimpleNamespace(),
        )
        trader = Trader(api, Settings(kickbase_email="t@e.com", kickbase_password="x"))
        nk = trader.next_kickoff(SimpleNamespace(id="1"), now=NOW)
        assert nk.source == "schedule" and nk.day_number == 4

    def test_myeleven_fallback_has_no_day(self):
        soon = "2026-09-18T18:30:00Z"
        api = SimpleNamespace(
            get_competition_matchdays=lambda competition_id="1": {},
            get_starting_eleven=lambda league: {"lp": [{"md": soon}], "nlp": []},
            client=SimpleNamespace(),
        )
        trader = Trader(api, Settings(kickbase_email="t@e.com", kickbase_password="x"))
        nk = trader.next_kickoff(SimpleNamespace(id="1"), now=NOW)
        assert nk.source == "myeleven" and nk.day_number is None
```

Check how the existing tests in this file build a `Trader` (they may use a helper); reuse that helper if one exists instead of the `SimpleNamespace` above.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_kickoff.py -q`
Expected: FAIL with `ImportError: cannot import name 'FinishedMatchday'`.

- [ ] **Step 3: Implement in `rehoboam/kickoff.py`**

Add after `_parse_match_date`:

```python
@dataclass(frozen=True)
class NextFixture:
    day_number: int | None
    at: datetime


@dataclass(frozen=True)
class FinishedMatchday:
    day_number: int
    first_kickoff: datetime
    last_kickoff: datetime


def _day_groups(payload) -> list[tuple[int | None, list[dict]]]:
    """`(day, fixtures)` per matchday group; tolerant of every missing key."""
    if not isinstance(payload, dict):
        return []
    out: list[tuple[int | None, list[dict]]] = []
    for group in payload.get("it") or []:
        if not isinstance(group, dict):
            continue
        day = group.get("day")
        fixtures = [f for f in (group.get("it") or []) if isinstance(f, dict)]
        out.append((int(day) if isinstance(day, int) else None, fixtures))
    return out


def next_fixture_from_matchdays(payload: dict, now: datetime) -> NextFixture | None:
    """Earliest not-yet-started fixture, with the matchday number of its group."""
    best: NextFixture | None = None
    for day, fixtures in _day_groups(payload):
        for fixture in fixtures:
            if fixture.get("st") != 0:
                continue
            parsed = _parse_match_date(fixture.get("dt"))
            if parsed is None or parsed <= now:
                continue
            if best is None or parsed < best.at:
                best = NextFixture(day_number=day, at=parsed)
    return best


def finished_matchdays(payload) -> list[FinishedMatchday]:
    """Matchday groups whose every fixture has `st == 2`, oldest first.

    A group with no fixtures, an unparseable date or a missing day number is
    not finished — it is unknown, and unknown never triggers a report.
    """
    out: list[FinishedMatchday] = []
    for day, fixtures in _day_groups(payload):
        if day is None or not fixtures:
            continue
        if any(f.get("st") != 2 for f in fixtures):
            continue
        dates = [_parse_match_date(f.get("dt")) for f in fixtures]
        if any(d is None for d in dates):
            continue
        out.append(
            FinishedMatchday(
                day_number=day, first_kickoff=min(dates), last_kickoff=max(dates)
            )
        )
    return sorted(out, key=lambda m: m.day_number)
```

Rewrite `next_kickoff_from_matchdays` to delegate: its body becomes
`nf = next_fixture_from_matchdays(payload, now); return nf.at if nf else None`
(keep its docstring). Add `day_number: int | None = None` as the last field of `NextKickoff`.

In `Trader.next_kickoff`: replace `schedule_at = next_kickoff_from_matchdays(schedule_payload, now)` with

```python
            schedule_fixture = next_fixture_from_matchdays(schedule_payload, now)
            schedule_at = schedule_fixture.at if schedule_fixture else None
```

(initialise `schedule_fixture = None` next to `schedule_at`), import `next_fixture_from_matchdays` where `next_kickoff_from_matchdays` is imported (drop the old import if it becomes unused), and build the result as

```python
return NextKickoff(
    at=at,
    source=source,
    cross_check=cross_check,
    matchday_in_progress=in_progress,
    day_number=schedule_fixture.day_number if source == "schedule" else None,
)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_kickoff.py tests/test_session_facts_wiring.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add rehoboam/kickoff.py rehoboam/trader.py tests/test_kickoff.py
git commit -m "feat(kickoff): the schedule answers the matchday number and which matchdays finished"
```

______________________________________________________________________

### Task 4: The session writes league-wide predictions

**Files:**

- Modify: `rehoboam/auto_trader.py` (`__init__` near line 389, `_build_session_context` near line 520, step 2a near line 2456)
- Modify: `tests/test_session_facts_wiring.py` (one existing test, new tests)

**Interfaces:**

- Consumes: `CalibrationStore.stored_players/write_predictions/current_season` (Task 1), `score_stored` (Task 2), `NextKickoff.day_number` (Task 3), `formation.select_best_eleven`, `rehoboam.scoring.v2.coefficients.load_coefficients`.

- Produces: `AutoTrader(..., calibration_store: CalibrationStore | None = None)`, `AutoTrader._write_league_predictions(ctx, nk) -> int`, `self._next_kickoff: NextKickoff | None`, module constant `PREDICTION_STATUS_MAX_AGE_S = 48 * 3600`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_session_facts_wiring.py`)

```python
from datetime import date  # noqa: E402  (add to the existing imports)

from rehoboam.store.calibration_store import CalibrationStore  # noqa: E402
from rehoboam.store.corpus_store import CorpusStore  # noqa: E402


def _seed_league(dsn, *, fetched_at):
    """Three scorable players (one owned, one listed, one neither) and one stale."""
    corpus = CorpusStore(dsn=dsn)
    corpus.upsert_players(
        [
            {
                "player_id": pid,
                "first_name": None,
                "last_name": pid,
                "position": pos,
                "team_id": "1",
                "market_value": 1_000_000,
                "average_points": 30.0,
            }
            for pid, pos in (
                ("gk", "Goalkeeper"),
                ("lst", "Forward"),
                ("x", "Midfielder"),
                ("stale", "Defender"),
            )
        ]
    )
    perf = {
        "it": [
            {
                "ti": "2026/2027",
                "ph": [
                    {
                        "day": 1,
                        "md": "2026-08-22T13:30:00Z",
                        "st": 5,
                        "p": 40,
                        "mp": "90",
                    }
                ],
            }
        ]
    }
    for pid in ("gk", "lst", "x", "stale"):
        corpus.record_match_history(pid, "1", perf)
        corpus.record_status_daily(
            pid,
            date.today(),
            {"st": 0, "prob": 1},
            fetched_at - (10 * 86400 if pid == "stale" else 0),
        )
    return corpus


def test_a_session_writes_one_prediction_per_fresh_player(store_dsn, ctx_factory):
    _seed_league(store_dsn, fetched_at=time.time())
    trader = _trader(store_dsn, _legal_squad())
    ctx = ctx_factory()
    ctx.ep_result["market_players"] = {"lst": object()}
    ctx.ep_result["lineup_map"] = {p.id: 10.0 for p in ctx.squad}
    ctx.ep_result["squad_scores"] = [
        SimpleNamespace(player_id="gk", expected_points=33.3)
    ]
    nk = NextKickoff(
        at=datetime.now(tz=timezone.utc) + timedelta(days=3),
        source="schedule",
        cross_check=None,
        matchday_in_progress=False,
        day_number=4,
    )

    def _build(league):
        trader._next_kickoff = nk
        trader._facts_from_context(ctx, nk)
        return ctx

    with patch.object(AutoTrader, "_build_session_context", side_effect=_build):
        session = trader.run_full_session(LEAGUE)
    with CalibrationStore(dsn=store_dsn).connection() as conn:
        rows = {
            r["player_id"]: r
            for r in conn.execute(
                "SELECT * FROM rehoboam.predictions WHERE session_id = %s",
                (session.session_id,),
            ).fetchall()
        }
    assert set(rows) == {"gk", "lst", "x"}  # `stale` has a 10-day-old status row
    assert (
        rows["gk"]["owned"]
        and rows["gk"]["in_best_11"]
        and rows["gk"]["live_ep"] == 33.3
    )
    assert (
        rows["lst"]["listed"]
        and not rows["lst"]["owned"]
        and rows["lst"]["live_ep"] is None
    )
    assert (
        rows["x"]["day_number"] == 4
        and rows["x"]["app"] == "cli"
        and rows["x"]["dry_run"]
    )
    assert rows["x"]["backfill"] is False and rows["x"]["season"] == "2026/2027"
    assert (
        SessionStore(dsn=store_dsn).facts(session.session_id)["predictions_written"]
        == 3
    )


def test_no_matchday_number_means_no_predictions_and_i5_fails(store_dsn, ctx_factory):
    _seed_league(store_dsn, fetched_at=time.time())
    trader = _trader(store_dsn, _legal_squad())
    nk = NextKickoff(
        at=datetime.now(tz=timezone.utc) + timedelta(days=3),
        source="myeleven",
        cross_check=None,
        matchday_in_progress=False,
    )

    def _build(league):
        trader._next_kickoff = nk
        ctx = ctx_factory()
        trader._facts_from_context(ctx, nk)
        return ctx

    with patch.object(AutoTrader, "_build_session_context", side_effect=_build):
        session = trader.run_full_session(LEAGUE)
    assert "I5" in [f.rule for f in session.integrity_failures]
    assert (
        SessionStore(dsn=store_dsn).facts(session.session_id)["predictions_written"]
        == 0
    )


def test_a_store_failure_while_predicting_never_stops_the_session(
    store_dsn, ctx_factory
):
    trader = _trader(store_dsn, _legal_squad())
    trader._calibration_store = CalibrationStore(
        dsn="postgresql://nobody@127.0.0.1:1/nope"
    )
    nk = NextKickoff(
        at=datetime.now(tz=timezone.utc) + timedelta(days=3),
        source="schedule",
        cross_check=None,
        matchday_in_progress=False,
        day_number=4,
    )

    def _build(league):
        trader._next_kickoff = nk
        ctx = ctx_factory()
        trader._facts_from_context(ctx, nk)
        return ctx

    with patch.object(AutoTrader, "_build_session_context", side_effect=_build):
        session = trader.run_full_session(LEAGUE)
    assert (
        session.lineup_result == "dry_run"
        if hasattr(session, "lineup_result")
        else True
    )
    assert (
        SessionStore(dsn=store_dsn).facts(session.session_id)["predictions_written"]
        == 0
    )
```

In the last test, drop the `lineup_result` line if `AutoTradeSession` has no such attribute; the assertion that matters is that `run_full_session` returned and left a facts row.

Update `test_no_failures_means_no_message`: replace `trader.tracker.snapshot_predictions = lambda *a, **k: 1` with `trader._write_league_predictions = lambda *a, **k: 1` and adjust its comment (the league write, not the legacy snapshot, now feeds `predictions_written`).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_session_facts_wiring.py -q`
Expected: the three new tests FAIL (`AttributeError: _calibration_store` / `NextKickoff() got an unexpected keyword argument` if Task 3 is missing); `test_no_failures_means_no_message` FAILS on I5.

- [ ] **Step 3: Implement in `rehoboam/auto_trader.py`**

Constructor: add a keyword `calibration_store=None` after `session_store`, and next to `self._session_store = ...`:

```python
        from rehoboam.store.calibration_store import CalibrationStore

        self._calibration_store = calibration_store or CalibrationStore()
        self._next_kickoff: NextKickoff | None = None
```

(import `NextKickoff` from `rehoboam.kickoff` at module level if not already imported; keep the `CalibrationStore` import local if `auto_trader` avoids importing store modules at import time — follow whatever `SessionStore` does.)

Module constant near the other constants:

```python
#: PR E: a league prediction needs a live status younger than this; older rows
#: are skipped and counted rather than scored as if they were fresh.
PREDICTION_STATUS_MAX_AGE_S = 48 * 3600
```

In `_build_session_context`, right after `nk = trader.next_kickoff(league, now=now)`: `self._next_kickoff = nk`. Add `timedelta` to `auto_trader.py`'s `from datetime import ...` line (the method below uses it).

Step 2a (around line 2456): keep the legacy `snapshot_predictions` block but stop it from setting `predictions_written`; then add

```python
try:
    league_written = self._write_league_predictions(ctx, self._next_kickoff)
except Exception:
    logger.exception("league predictions failed (non-fatal)")
    league_written = 0
self._facts.predictions_written = int(league_written)
```

The method:

```python
def _write_league_predictions(
    self, ctx: EPSessionContext, nk: NextKickoff | None
) -> int:
    """PR E §1: score every live player from store rows and write `predictions`.

    Returns the number of rows written; 0 with a logged reason when the
    matchday is unknown, so rule I5 fails for the right cause. Everything
    here reads the store and the already-built context — no API call.
    """
    from rehoboam.formation import select_best_eleven
    from rehoboam.scoring.store_scorer import score_stored
    from rehoboam.scoring.v2.coefficients import load_coefficients

    if nk is None or nk.at is None or nk.day_number is None:
        logger.info(
            "predictions: skipped, matchday unknown (source=%s)",
            getattr(nk, "source", "none"),
        )
        return 0
    now = datetime.now(tz=timezone.utc)
    store = self._calibration_store
    season = store.current_season()
    if season is None:
        logger.info("predictions: skipped, corpus has no season")
        return 0
    availability, rate, _meta = load_coefficients()
    since_iso = (now - timedelta(days=400)).strftime("%Y-%m-%dT%H:%M:%SZ")
    players = store.stored_players(since_iso=since_iso, status_day=now.date())
    fresh_after = now.timestamp() - PREDICTION_STATUS_MAX_AGE_S

    owned = {p.id for p in ctx.squad}
    listed = set((ctx.ep_result.get("market_players") or {}).keys())
    lineup_map = ctx.ep_result.get("lineup_map") or {}
    best_11 = (
        {p.id for p in select_best_eleven(ctx.squad, lineup_map)}
        if lineup_map
        else set()
    )
    live_ep = {
        s.player_id: float(s.expected_points)
        for s in ctx.ep_result.get("squad_scores") or []
    }
    live_ep.update(
        {
            pid: float(s.expected_points)
            for pid, s in (ctx.ep_result.get("market_scores") or {}).items()
        }
    )

    rows: list[dict] = []
    skipped_stale = 0
    for p in players:
        if p.status_fetched_at is None or p.status_fetched_at < fresh_after:
            skipped_stale += 1
            continue
        pred = score_stored(
            p,
            now=now,
            max_status_age_days=self.settings.max_status_age_days,
            availability=availability,
            rate=rate,
        )
        rows.append(
            {
                "session_id": self._session_batch_id,
                "player_id": p.player_id,
                "season": season,
                "day_number": nk.day_number,
                "kickoff": nk.at.timestamp(),
                "predicted_at": now.timestamp(),
                "predicted_ep": pred.predicted_ep,
                "p_status": pred.p_status,
                "rate": pred.rate,
                "prev_status": pred.prev_status,
                "live_status": p.live_status,
                "position": p.position,
                "team_id": p.team_id,
                "owned": p.player_id in owned,
                "listed": p.player_id in listed,
                "in_best_11": p.player_id in best_11,
                "live_ep": live_ep.get(p.player_id),
                "data_grade": pred.data_grade,
                "app": self.app_name,
                "dry_run": bool(self.dry_run),
                "backfill": False,
            }
        )
    written = store.write_predictions(rows)
    logger.info(
        "predictions written=%d skipped_stale=%d matchday=%s",
        written,
        skipped_stale,
        nk.day_number,
    )
    return written
```

Check the real names before writing: `self._session_batch_id` (used by `SessionFacts(session_id=...)` at line 573), `self.settings.max_status_age_days`, and the type of `ep_result["market_players"]` in `trader.py` line 790 (`market_player_map` — a dict keyed by player id; if it is not, take `.id` from each element instead of `.keys()`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_session_facts_wiring.py tests/test_trading_mode.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add rehoboam/auto_trader.py tests/test_session_facts_wiring.py
git commit -m "feat(session): every session writes one prediction per live player from store rows"
```

______________________________________________________________________

### Task 5: Pure calibration metrics, the gate, the message

**Files:**

- Create: `rehoboam/services/calibration.py`
- Create: `tests/test_calibration.py`

**Interfaces:**

- Consumes: `rehoboam.backtest.metrics.spearman`, `rehoboam.formation.select_best_eleven`.

- Produces: `CalRow`, `CalibrationReport`, `build_report(rows, *, n_stale_rows=0) -> CalibrationReport`, `gate_verdict(reports, *, last_failure_at, now) -> dict`, `render_calibration_message(report, *, season, day_number, names, gate) -> str`, constants `GATE_REQUIRED_REPORTS = 3`, `GATE_REQUIRED_CLEAN_DAYS = 7`.

- [ ] **Step 1: Write the failing tests**

```python
"""Calibration metrics, the gate and the message are pure (PR E §2, Task 5)."""

from __future__ import annotations

import pytest

from rehoboam.services.calibration import (
    GATE_REQUIRED_CLEAN_DAYS,
    CalibrationReport,
    CalRow,
    build_report,
    gate_verdict,
    render_calibration_message,
)


def _row(
    pid,
    actual,
    predicted,
    *,
    position="Midfielder",
    baseline=None,
    live=None,
    owned=False,
    in_best_11=False,
    live_status=0,
):
    return CalRow(
        player_id=pid,
        position=position,
        actual=float(actual),
        predicted=predicted,
        baseline=float(baseline if baseline is not None else actual),
        live=live,
        owned=owned,
        in_best_11=in_best_11,
        live_status=live_status,
    )


def _league():
    """Eleven-plus per position so a legal eleven exists by every key."""
    rows = []
    pid = 0
    for pos, n in (
        ("Goalkeeper", 3),
        ("Defender", 8),
        ("Midfielder", 8),
        ("Forward", 6),
    ):
        for i in range(n):
            pid += 1
            actual = 100 - pid * 3
            rows.append(
                _row(
                    str(pid),
                    actual,
                    actual + (5 if i % 2 else -5),
                    position=pos,
                    baseline=actual - 10,
                )
            )
    return rows


class TestBuildReport:
    def test_counts_and_errors(self):
        rows = [_row("a", 10, 14), _row("b", 20, 18), _row("c", 30, None)]
        r = build_report(rows)
        assert (r.n, r.n_unpredicted) == (2, 1)
        assert r.mae == pytest.approx(3.0) and r.bias == pytest.approx(1.0)

    def test_perfect_ranking_is_spearman_one_and_zero_regret(self):
        rows = _league()
        for i, row in enumerate(rows):
            rows[i] = _row(
                row.player_id,
                row.actual,
                row.actual,
                position=row.position,
                baseline=row.baseline,
            )
        r = build_report(rows)
        assert r.spearman == pytest.approx(1.0) and r.top11_regret == pytest.approx(0.0)
        assert r.baseline_spearman == pytest.approx(
            1.0
        )  # a constant shift keeps the order

    def test_regret_is_best_eleven_by_actual_minus_best_eleven_by_prediction(self):
        rows = _league()
        # Push the true best forward to the bottom of the predicted order.
        top_fw = max(
            (r for r in rows if r.position == "Forward"), key=lambda r: r.actual
        )
        rows = [
            (
                _row(
                    r.player_id,
                    r.actual,
                    -1.0,
                    position=r.position,
                    baseline=r.baseline,
                )
                if r is top_fw
                else r
            )
            for r in rows
        ]
        r = build_report(rows)
        assert r.top11_regret > 0

    def test_by_position_and_status_buckets(self):
        rows = [
            _row("a", 10, 12, position="Forward", live_status=0),
            _row("b", 20, 18, position="Forward", live_status=0),
            _row("c", 5, 30, position="Goalkeeper", live_status=4),
            _row("d", 0, None, position="Goalkeeper", live_status=None),
        ]
        r = build_report(rows)
        assert (
            r.by_position["Forward"]["n"] == 2
            and r.by_position["Forward"]["mae"] == 2.0
        )
        assert r.by_position["Goalkeeper"]["n"] == 1
        assert set(r.by_status) == {"0", "4"} and r.by_status["4"]["bias"] == 25.0

    def test_worst_three_by_absolute_miss(self):
        rows = [_row(str(i), 0, float(i)) for i in range(1, 6)] + [_row("u", 0, None)]
        r = build_report(rows)
        assert [w["player_id"] for w in r.worst] == ["5", "4", "3"]
        assert r.worst[0] == {
            "player_id": "5",
            "predicted": 5.0,
            "actual": 0.0,
            "position": "Midfielder",
        }

    def test_squad_regret_needs_a_fielded_eleven(self):
        rows = _league()
        assert build_report(rows).squad_regret is None
        by_pos = {}
        for r in rows:
            by_pos.setdefault(r.position, []).append(r)
        # A legal 15: 2 GK, 5 DEF, 5 MID, 3 FW; the fielded eleven is a 4-4-2 that
        # benches the best defender, so the regret is exactly his lead over the
        # fifth defender.
        owned = (
            by_pos["Goalkeeper"][:2]
            + by_pos["Defender"][:5]
            + by_pos["Midfielder"][:5]
            + by_pos["Forward"][:3]
        )
        fielded = (
            [by_pos["Goalkeeper"][0]]
            + by_pos["Defender"][1:5]
            + by_pos["Midfielder"][:4]
            + by_pos["Forward"][:2]
        )
        chosen = {r.player_id for r in fielded}
        owned_ids = {r.player_id for r in owned}
        rows = [
            (
                _row(
                    r.player_id,
                    r.actual,
                    r.predicted,
                    position=r.position,
                    baseline=r.baseline,
                    owned=True,
                    in_best_11=r.player_id in chosen,
                )
                if r.player_id in owned_ids
                else r
            )
            for r in rows
        ]
        r = build_report(rows)
        expected = by_pos["Defender"][0].actual - by_pos["Defender"][4].actual
        assert r.squad_regret == pytest.approx(expected)

    def test_live_spearman_over_rows_with_a_live_ep(self):
        rows = [
            _row("a", 10, 12, live=11.0),
            _row("b", 20, 18, live=19.0),
            _row("c", 30, 33),
        ]
        r = build_report(rows)
        assert r.live_n == 2 and r.live_spearman == pytest.approx(1.0)

    def test_empty_rows(self):
        r = build_report([])
        assert r.n == 0 and r.mae is None and r.spearman is None and r.worst == []


def _report(**over) -> CalibrationReport:
    base = build_report(_league())
    return CalibrationReport(**{**base.__dict__, **over})


class TestGate:
    NOW = 1_800_000_000.0

    def test_three_consecutive_wins_and_a_clean_week_pass(self):
        reports = [
            _report(
                spearman=0.5,
                baseline_spearman=0.4,
                top11_regret=100.0,
                baseline_top11_regret=150.0,
            )
        ] * 3
        g = gate_verdict(reports, last_failure_at=self.NOW - 8 * 86400, now=self.NOW)
        assert g["consecutive_ok"] == 3 and g["passes"] is True
        assert g["integrity_clean_days"] == pytest.approx(8.0)

    def test_a_loss_in_the_middle_resets_the_run(self):
        win = _report(
            spearman=0.5,
            baseline_spearman=0.4,
            top11_regret=100.0,
            baseline_top11_regret=150.0,
        )
        loss = _report(
            spearman=0.3,
            baseline_spearman=0.4,
            top11_regret=100.0,
            baseline_top11_regret=150.0,
        )
        g = gate_verdict([win, win, loss, win], last_failure_at=None, now=self.NOW)
        assert g["consecutive_ok"] == 1 and g["passes"] is False

    def test_regret_must_be_strictly_better(self):
        tie = _report(
            spearman=0.5,
            baseline_spearman=0.5,
            top11_regret=100.0,
            baseline_top11_regret=100.0,
        )
        g = gate_verdict([tie] * 3, last_failure_at=None, now=self.NOW)
        assert (
            g["spearman_ok"] is True
            and g["regret_ok"] is False
            and g["passes"] is False
        )

    def test_a_recent_integrity_failure_blocks(self):
        win = _report(
            spearman=0.5,
            baseline_spearman=0.4,
            top11_regret=100.0,
            baseline_top11_regret=150.0,
        )
        g = gate_verdict([win] * 3, last_failure_at=self.NOW - 3600, now=self.NOW)
        assert g["passes"] is False and g["integrity_clean_days"] < 1

    def test_no_failure_ever_counts_as_clean(self):
        win = _report(
            spearman=0.5,
            baseline_spearman=0.4,
            top11_regret=100.0,
            baseline_top11_regret=150.0,
        )
        g = gate_verdict([win] * 3, last_failure_at=None, now=self.NOW)
        assert (
            g["integrity_clean_days"] == GATE_REQUIRED_CLEAN_DAYS
            and g["passes"] is True
        )

    def test_no_reports(self):
        g = gate_verdict([], last_failure_at=None, now=self.NOW)
        assert g["consecutive_ok"] == 0 and g["passes"] is False
        assert g["spearman_ok"] is None and g["regret_ok"] is None


class TestMessage:
    def test_headline_numbers_and_worst_misses(self):
        rows = _league()
        r = build_report(rows)
        gate = gate_verdict([r], last_failure_at=None, now=1_800_000_000.0)
        text = render_calibration_message(
            r, season="2026/2027", day_number=4, names={"1": "Neuer"}, gate=gate
        )
        assert text.startswith("Rehoboam calibration MD4 2026/2027")
        assert f"n={r.n}" in text and "spearman" in text and "baseline" in text
        assert "Neuer" in text or "1 " in text
        assert "gate:" in text and ("closed" in text or "open" in text)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_calibration.py -q`
Expected: FAIL with `ModuleNotFoundError: rehoboam.services.calibration`.

- [ ] **Step 3: Write `rehoboam/services/calibration.py`**

```python
"""Calibration metrics, the gate and the Telegram message — pure (PR E §2).

Rows in, numbers out. `spearman` comes from the backtest so the live report
and the offline harness cannot disagree about what a rank correlation is;
the two elevens in `top11_regret` come from `select_best_eleven` so they are
always lineups Kickbase would accept.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from types import SimpleNamespace
from typing import Any

from rehoboam.backtest.metrics import spearman
from rehoboam.formation import select_best_eleven

GATE_REQUIRED_REPORTS = 3
GATE_REQUIRED_CLEAN_DAYS = 7


@dataclass(frozen=True)
class CalRow:
    player_id: str
    position: str
    actual: float
    predicted: float | None
    baseline: float
    live: float | None
    owned: bool
    in_best_11: bool
    live_status: int | None


@dataclass(frozen=True)
class CalibrationReport:
    n: int
    n_unpredicted: int
    n_stale_rows: int
    mae: float | None
    bias: float | None
    spearman: float | None
    baseline_spearman: float | None
    top11_regret: float | None
    baseline_top11_regret: float | None
    squad_regret: float | None
    live_spearman: float | None
    live_n: int
    by_position: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_status: dict[str, dict[str, Any]] = field(default_factory=dict)
    worst: list[dict[str, Any]] = field(default_factory=list)

    def as_row(self) -> dict[str, Any]:
        return asdict(self)


def _predicted(rows: list[CalRow]) -> list[CalRow]:
    return [r for r in rows if r.predicted is not None]


def _mae(rows: list[CalRow]) -> float | None:
    if not rows:
        return None
    return sum(abs(r.predicted - r.actual) for r in rows) / len(rows)


def _bias(rows: list[CalRow]) -> float | None:
    if not rows:
        return None
    return sum(r.predicted - r.actual for r in rows) / len(rows)


def _spearman(rows: list[CalRow], key) -> float | None:
    pairs = [(key(r), r.actual) for r in rows if key(r) is not None]
    if len(pairs) < 2:
        return None
    return spearman([p for p, _ in pairs], [a for _, a in pairs])


def _best_total(rows: list[CalRow], key) -> float | None:
    """Actual points of the best legal eleven chosen by `key` (None = no eleven)."""
    candidates = [r for r in rows if key(r) is not None]
    squad = [SimpleNamespace(id=r.player_id, position=r.position) for r in candidates]
    eleven = select_best_eleven(squad, {r.player_id: key(r) for r in candidates})
    if len(eleven) < 11:
        return None
    actual = {r.player_id: r.actual for r in candidates}
    return sum(actual[p.id] for p in eleven)


def _league_regret(rows: list[CalRow], key) -> float | None:
    best = _best_total(rows, lambda r: r.actual)
    chosen = _best_total(rows, key)
    if best is None or chosen is None:
        return None
    return best - chosen


def _squad_regret(rows: list[CalRow]) -> float | None:
    owned = [r for r in rows if r.owned]
    fielded = [r for r in owned if r.in_best_11]
    if len(fielded) < 11:
        return None
    best = _best_total(owned, lambda r: r.actual)
    if best is None:
        return None
    return best - sum(r.actual for r in fielded)


def _bucket(rows: list[CalRow]) -> dict[str, Any]:
    p = _predicted(rows)
    return {
        "n": len(p),
        "mae": _mae(p),
        "bias": _bias(p),
        "spearman": _spearman(p, lambda r: r.predicted),
    }


def build_report(rows: list[CalRow], *, n_stale_rows: int = 0) -> CalibrationReport:
    predicted = _predicted(rows)
    by_position: dict[str, dict[str, Any]] = {}
    for pos in sorted({r.position for r in rows}):
        by_position[pos] = _bucket([r for r in rows if r.position == pos])
    by_status: dict[str, dict[str, Any]] = {}
    for status in sorted(
        {r.live_status for r in predicted}, key=lambda s: (s is None, s)
    ):
        by_status[str(status)] = _bucket(
            [r for r in predicted if r.live_status == status]
        )
    worst = sorted(predicted, key=lambda r: abs(r.predicted - r.actual), reverse=True)[
        :3
    ]
    live_rows = [r for r in predicted if r.live is not None]
    return CalibrationReport(
        n=len(predicted),
        n_unpredicted=len(rows) - len(predicted),
        n_stale_rows=n_stale_rows,
        mae=_mae(predicted),
        bias=_bias(predicted),
        spearman=_spearman(predicted, lambda r: r.predicted),
        baseline_spearman=_spearman(predicted, lambda r: r.baseline),
        top11_regret=_league_regret(rows, lambda r: r.predicted),
        baseline_top11_regret=_league_regret(rows, lambda r: r.baseline),
        squad_regret=_squad_regret(rows),
        live_spearman=_spearman(live_rows, lambda r: r.live),
        live_n=len(live_rows),
        by_position=by_position,
        by_status=by_status,
        worst=[
            {
                "player_id": r.player_id,
                "predicted": float(r.predicted),
                "actual": float(r.actual),
                "position": r.position,
            }
            for r in worst
        ],
    )


def _report_ok(r: CalibrationReport) -> tuple[bool | None, bool | None]:
    spearman_ok = (
        None
        if r.spearman is None or r.baseline_spearman is None
        else r.spearman >= r.baseline_spearman
    )
    regret_ok = (
        None
        if r.top11_regret is None or r.baseline_top11_regret is None
        else r.top11_regret < r.baseline_top11_regret
    )
    return spearman_ok, regret_ok


def gate_verdict(
    reports: list[CalibrationReport], *, last_failure_at: float | None, now: float
) -> dict[str, Any]:
    """The verdict the parent spec §4 describes, from real reports oldest first.

    `spearman_ok`/`regret_ok` describe the newest report. `consecutive_ok` is
    the trailing run where both held. No integrity failure on record counts
    as clean for the full window: the three-report requirement already
    guarantees weeks of sessions behind a pass.
    """
    consecutive = 0
    for r in reversed(reports):
        s, g = _report_ok(r)
        if s and g:
            consecutive += 1
        else:
            break
    newest = reports[-1] if reports else None
    spearman_ok, regret_ok = _report_ok(newest) if newest else (None, None)
    clean_days = (
        float(GATE_REQUIRED_CLEAN_DAYS)
        if last_failure_at is None
        else max(0.0, (now - last_failure_at) / 86400.0)
    )
    return {
        "spearman_ok": spearman_ok,
        "regret_ok": regret_ok,
        "consecutive_ok": consecutive,
        "required": GATE_REQUIRED_REPORTS,
        "integrity_clean_days": round(clean_days, 2),
        "required_clean_days": GATE_REQUIRED_CLEAN_DAYS,
        "passes": consecutive >= GATE_REQUIRED_REPORTS
        and clean_days >= GATE_REQUIRED_CLEAN_DAYS,
    }


def _fmt(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def render_calibration_message(
    report: CalibrationReport,
    *,
    season: str,
    day_number: int,
    names: dict[str, str],
    gate: dict[str, Any],
) -> str:
    worst = " · ".join(
        f"{names.get(w['player_id'], w['player_id'])} pred {w['predicted']:.0f} act {w['actual']:.0f}"
        for w in report.worst
    )
    verdict = "open" if gate.get("passes") else "closed"
    lines = [
        f"Rehoboam calibration MD{day_number} {season}",
        f"n={report.n} (unpredicted {report.n_unpredicted})  mae {_fmt(report.mae, 1)}  "
        f"bias {_fmt(report.bias, 1)}",
        f"spearman {_fmt(report.spearman)} (baseline {_fmt(report.baseline_spearman)})  "
        f"top11 regret {_fmt(report.top11_regret, 0)} "
        f"(baseline {_fmt(report.baseline_top11_regret, 0)})",
        f"squad regret {_fmt(report.squad_regret, 0)}  "
        f"live-path spearman {_fmt(report.live_spearman)} (n {report.live_n})",
        f"worst: {worst or 'none'}",
        f"gate: {gate.get('consecutive_ok', 0)}/{gate.get('required', GATE_REQUIRED_REPORTS)} "
        f"matchdays ok, integrity clean {gate.get('integrity_clean_days', 0)}/"
        f"{gate.get('required_clean_days', GATE_REQUIRED_CLEAN_DAYS)} d — {verdict}",
    ]
    return "\n".join(lines)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_calibration.py -q`
Expected: PASS. If `test_by_position_and_status_buckets` fails on the `"4"` bias, check the bucket uses predicted rows only (row `d` has no prediction and must not appear in `by_status`).

- [ ] **Step 5: Commit**

```bash
git add rehoboam/services/calibration.py tests/test_calibration.py
git commit -m "feat(calibration): metrics, the gate verdict and the matchday message, pure"
```

______________________________________________________________________

### Task 6: Store readers and writers for rows and reports

**Files:**

- Modify: `rehoboam/store/calibration_store.py`
- Modify: `tests/store/test_calibration_store.py` (append)

**Interfaces:**

- Produces on `CalibrationStore`: `last_predictions_before(*, season, day_number, kickoff, backfill) -> dict[str, dict]`, `fielded_eleven_before(*, season, day_number, kickoff) -> set[str]`, `actuals_for(*, season, day_number) -> list[dict]` (keys `player_id, points, minutes, status, team_id, position, name`), `history_before(*, before_iso) -> dict[str, list[dict]]`, `players_needing_final_rows(*, season, day_number, whistle) -> list[str]`, `write_calibration(*, season, day_number, backfill, rows, report, gate, computed_at) -> None`, `report_for(season, day_number, *, backfill=False) -> dict | None`, `recent_reports(season, *, backfill=False) -> list[dict]` (oldest first), `mark_telegram_sent(season, day_number)`, `last_integrity_failure_at() -> float | None`.

- [ ] **Step 1: Write the failing tests** (append)

```python
from rehoboam.services.calibration import build_report, CalRow  # noqa: E402
from rehoboam.services.session_facts import IntegrityFailure, SessionFacts  # noqa: E402
from rehoboam.store.session_store import SessionStore  # noqa: E402

KICKOFF = 1_789_756_200.0  # 2026-09-18 18:30 UTC


def _pred(session, pid, at, **over):
    row = {
        "session_id": session,
        "player_id": pid,
        "season": "2026/2027",
        "day_number": 4,
        "kickoff": KICKOFF,
        "predicted_at": at,
        "predicted_ep": 40.0,
        "p_status": {1: 0.1, 3: 0.2, 4: 0.1, 5: 0.6},
        "rate": 60.0,
        "prev_status": 5,
        "live_status": 0,
        "position": "Midfielder",
        "team_id": "1",
        "owned": False,
        "listed": False,
        "in_best_11": False,
        "live_ep": None,
        "data_grade": "A",
        "app": "function",
        "dry_run": False,
        "backfill": False,
    }
    row.update(over)
    return row


def test_last_prediction_before_kickoff_per_player(store_dsn):
    store = CalibrationStore(dsn=store_dsn)
    store.write_predictions(
        [
            _pred("s1", "a", KICKOFF - 7200, predicted_ep=30.0),
            _pred("s2", "a", KICKOFF - 60, predicted_ep=35.0),
            _pred("s3", "a", KICKOFF + 60, predicted_ep=99.0),  # after kickoff: ignored
            _pred("bf", "a", KICKOFF - 1, predicted_ep=11.0, backfill=True),
            _pred(
                "s2", "b", KICKOFF - 60, predicted_ep=20.0, day_number=5
            ),  # other matchday
        ]
    )
    got = store.last_predictions_before(
        season="2026/2027", day_number=4, kickoff=KICKOFF, backfill=False
    )
    assert (
        set(got) == {"a"}
        and got["a"]["predicted_ep"] == 35.0
        and got["a"]["session_id"] == "s2"
    )
    bf = store.last_predictions_before(
        season="2026/2027", day_number=4, kickoff=KICKOFF, backfill=True
    )
    assert bf["a"]["predicted_ep"] == 11.0


def test_fielded_eleven_comes_from_the_last_real_session(store_dsn):
    store = CalibrationStore(dsn=store_dsn)
    store.write_predictions(
        [
            _pred("dry", "a", KICKOFF - 30, dry_run=True, owned=True, in_best_11=True),
            _pred("real", "a", KICKOFF - 60, owned=True, in_best_11=True),
            _pred("real", "b", KICKOFF - 60, owned=True, in_best_11=False),
            _pred("older", "c", KICKOFF - 7200, owned=True, in_best_11=True),
        ]
    )
    assert store.fielded_eleven_before(
        season="2026/2027", day_number=4, kickoff=KICKOFF
    ) == {"a"}


def test_actuals_join_the_universe(store_dsn):
    _seed(store_dsn)
    store = CalibrationStore(dsn=store_dsn)
    rows = store.actuals_for(season="2026/2027", day_number=1)
    assert {r["player_id"] for r in rows} == {"a", "b"}
    a = next(r for r in rows if r["player_id"] == "a")
    assert (a["points"], a["status"], a["position"], a["name"]) == (
        80,
        5,
        "Midfielder",
        "A",
    )


def test_history_before_is_strict(store_dsn):
    _seed(store_dsn)
    hist = CalibrationStore(dsn=store_dsn).history_before(
        before_iso="2026-08-29T13:30:00Z"
    )
    assert [m["day_number"] for m in hist["a"]] == [1] and hist["b"][0]["points"] == 12


def test_players_needing_final_rows(store_dsn):
    corpus = _seed(store_dsn)
    corpus.mark_fetched("a", at=1_000.0, performance=True)  # before the whistle
    corpus.mark_fetched("b", at=9_999.0, performance=True)  # after
    store = CalibrationStore(dsn=store_dsn)
    assert store.players_needing_final_rows(
        season="2026/2027", day_number=1, whistle=5_000.0
    ) == ["a"]
    assert (
        store.players_needing_final_rows(
            season="2026/2027", day_number=1, whistle=500.0
        )
        == []
    )


def test_write_and_read_a_report(store_dsn):
    store = CalibrationStore(dsn=store_dsn)
    rows = [
        CalRow("a", "Midfielder", 50.0, 40.0, 30.0, 44.0, True, True, 0),
        CalRow("b", "Forward", 0.0, None, 10.0, None, False, False, None),
    ]
    report = build_report(rows, n_stale_rows=2)
    gate = {"passes": False, "consecutive_ok": 0}
    store.write_calibration(
        season="2026/2027",
        day_number=4,
        backfill=False,
        rows=[
            {
                "player_id": "a",
                "session_id": "s2",
                "predicted_ep": 40.0,
                "live_ep": 44.0,
                "baseline_ep": 30.0,
                "actual_points": 50,
                "minutes": 90,
                "status": 5,
                "position": "Midfielder",
                "team_id": "1",
                "owned": True,
                "in_best_11": True,
                "prev_status": 5,
                "live_status": 0,
            },
            {
                "player_id": "b",
                "session_id": None,
                "predicted_ep": None,
                "live_ep": None,
                "baseline_ep": 10.0,
                "actual_points": 0,
                "minutes": 0,
                "status": 1,
                "position": "Forward",
                "team_id": "2",
                "owned": False,
                "in_best_11": False,
                "prev_status": None,
                "live_status": None,
            },
        ],
        report=report,
        gate=gate,
        computed_at=123.0,
    )
    got = store.report_for("2026/2027", 4)
    assert got["n"] == 1 and got["n_unpredicted"] == 1 and got["n_stale_rows"] == 2
    assert got["gate"] == gate and got["telegram_sent"] is False
    assert got["by_position"]["Midfielder"]["n"] == 1
    assert store.report_for("2026/2027", 4, backfill=True) is None
    store.mark_telegram_sent("2026/2027", 4)
    assert store.report_for("2026/2027", 4)["telegram_sent"] is True
    # Re-writing the same matchday replaces, never duplicates.
    store.write_calibration(
        season="2026/2027",
        day_number=4,
        backfill=False,
        rows=[],
        report=build_report([]),
        gate=gate,
        computed_at=124.0,
    )
    assert store.report_for("2026/2027", 4)["n"] == 0
    with store.connection() as conn:
        n = conn.execute(
            "SELECT count(*) AS n FROM rehoboam.calibration_rows"
        ).fetchone()["n"]
    assert n == 0


def test_recent_reports_are_oldest_first_and_real_only(store_dsn):
    store = CalibrationStore(dsn=store_dsn)
    for day, bf in ((5, False), (4, False), (3, True)):
        store.write_calibration(
            season="2026/2027",
            day_number=day,
            backfill=bf,
            rows=[],
            report=build_report([]),
            gate=None,
            computed_at=1.0,
        )
    assert [r["day_number"] for r in store.recent_reports("2026/2027")] == [4, 5]


def test_last_integrity_failure_ignores_dry_runs(store_dsn):
    sessions = SessionStore(dsn=store_dsn)
    sessions.record(
        SessionFacts(
            session_id="d",
            app="cli",
            mode="full",
            dry_run=True,
            started_at=1.0,
            duration_s=1.0,
        )
    )
    sessions.record(
        SessionFacts(
            session_id="r",
            app="function",
            mode="lineup_only",
            dry_run=False,
            started_at=1.0,
            duration_s=1.0,
        )
    )
    sessions.record_failures("d", [IntegrityFailure("I2", "x")], at=900.0)
    store = CalibrationStore(dsn=store_dsn)
    assert store.last_integrity_failure_at() is None
    sessions.record_failures("r", [IntegrityFailure("I2", "x")], at=800.0)
    assert store.last_integrity_failure_at() == 800.0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/store/test_calibration_store.py -q`
Expected: the new tests FAIL with `AttributeError`.

- [ ] **Step 3: Add the methods to `CalibrationStore`**

```python
def last_predictions_before(
    self, *, season: str, day_number: int, kickoff: float, backfill: bool
) -> dict[str, dict[str, Any]]:
    """Per player, the newest prediction for this matchday made before `kickoff`."""
    with self.connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT ON (player_id) * FROM rehoboam.predictions "
            "WHERE season = %s AND day_number = %s AND predicted_at < %s AND backfill = %s "
            "ORDER BY player_id, predicted_at DESC",
            (season, day_number, kickoff, backfill),
        ).fetchall()
    return {r["player_id"]: dict(r) for r in rows}


def fielded_eleven_before(
    self, *, season: str, day_number: int, kickoff: float
) -> set[str]:
    """The `in_best_11` owned players of the newest non-dry-run session before kickoff."""
    with self.connection() as conn:
        session = conn.execute(
            "SELECT session_id FROM rehoboam.predictions "
            "WHERE season = %s AND day_number = %s AND predicted_at < %s "
            "AND dry_run = false AND backfill = false "
            "ORDER BY predicted_at DESC LIMIT 1",
            (season, day_number, kickoff),
        ).fetchone()
        if not session:
            return set()
        rows = conn.execute(
            "SELECT player_id FROM rehoboam.predictions "
            "WHERE session_id = %s AND owned AND in_best_11",
            (session["session_id"],),
        ).fetchall()
    return {r["player_id"] for r in rows}


def actuals_for(self, *, season: str, day_number: int) -> list[dict[str, Any]]:
    with self.connection() as conn:
        rows = conn.execute(
            "SELECT h.player_id, h.points, h.minutes, h.status, h.team_id, u.position, "
            "COALESCE(u.last_name, h.player_id) AS name "
            "FROM rehoboam.player_match_history h "
            "JOIN rehoboam.player_universe u ON u.player_id = h.player_id "
            "WHERE h.season = %s AND h.day_number = %s AND u.position IS NOT NULL "
            "ORDER BY h.player_id",
            (season, day_number),
        ).fetchall()
    return [dict(r) for r in rows]


def history_before(self, *, before_iso: str) -> dict[str, list[dict[str, Any]]]:
    """Every match row dated strictly before `before_iso`, per player, oldest first."""
    with self.connection() as conn:
        rows = conn.execute(
            "SELECT player_id, season, day_number, match_date, points, minutes, status "
            "FROM rehoboam.player_match_history WHERE match_date < %s "
            "ORDER BY player_id, season, day_number",
            (before_iso,),
        ).fetchall()
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(r["player_id"], []).append(dict(r))
    return out


def players_needing_final_rows(
    self, *, season: str, day_number: int, whistle: float
) -> list[str]:
    """Players with a row for this matchday whose performance was last fetched before `whistle`."""
    with self.connection() as conn:
        rows = conn.execute(
            "SELECT h.player_id FROM rehoboam.player_match_history h "
            "LEFT JOIN rehoboam.sweep_progress p ON p.player_id = h.player_id "
            "WHERE h.season = %s AND h.day_number = %s "
            "AND (p.performance_fetched_at IS NULL OR p.performance_fetched_at < %s) "
            "ORDER BY h.player_id",
            (season, day_number, whistle),
        ).fetchall()
    return [r["player_id"] for r in rows]


def write_calibration(
    self,
    *,
    season: str,
    day_number: int,
    backfill: bool,
    rows: list[dict[str, Any]],
    report,
    gate: dict[str, Any] | None,
    computed_at: float,
) -> None:
    """Replace this matchday's rows and report in one transaction."""
    r = report.as_row()
    with self.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM rehoboam.calibration_rows "
            "WHERE season = %s AND day_number = %s AND backfill = %s",
            (season, day_number, backfill),
        )
        cur.executemany(
            "INSERT INTO rehoboam.calibration_rows (season, day_number, player_id, backfill, "
            "session_id, predicted_ep, live_ep, baseline_ep, actual_points, minutes, status, "
            "position, team_id, owned, in_best_11, prev_status, live_status) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [
                (
                    season,
                    day_number,
                    x["player_id"],
                    backfill,
                    x["session_id"],
                    x["predicted_ep"],
                    x["live_ep"],
                    x["baseline_ep"],
                    x["actual_points"],
                    x["minutes"],
                    x["status"],
                    x["position"],
                    x["team_id"],
                    x["owned"],
                    x["in_best_11"],
                    x["prev_status"],
                    x["live_status"],
                )
                for x in rows
            ],
        )
        cur.execute(
            "INSERT INTO rehoboam.calibration_reports (season, day_number, backfill, "
            "computed_at, n, n_unpredicted, n_stale_rows, mae, bias, spearman, "
            "baseline_spearman, top11_regret, baseline_top11_regret, squad_regret, "
            "live_spearman, live_n, by_position, by_status, worst, gate, telegram_sent) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
            "%s, %s, false) "
            "ON CONFLICT (season, day_number, backfill) DO UPDATE SET "
            "computed_at = excluded.computed_at, n = excluded.n, "
            "n_unpredicted = excluded.n_unpredicted, n_stale_rows = excluded.n_stale_rows, "
            "mae = excluded.mae, bias = excluded.bias, spearman = excluded.spearman, "
            "baseline_spearman = excluded.baseline_spearman, "
            "top11_regret = excluded.top11_regret, "
            "baseline_top11_regret = excluded.baseline_top11_regret, "
            "squad_regret = excluded.squad_regret, live_spearman = excluded.live_spearman, "
            "live_n = excluded.live_n, by_position = excluded.by_position, "
            "by_status = excluded.by_status, worst = excluded.worst, gate = excluded.gate, "
            "telegram_sent = false",
            (
                season,
                day_number,
                backfill,
                computed_at,
                r["n"],
                r["n_unpredicted"],
                r["n_stale_rows"],
                r["mae"],
                r["bias"],
                r["spearman"],
                r["baseline_spearman"],
                r["top11_regret"],
                r["baseline_top11_regret"],
                r["squad_regret"],
                r["live_spearman"],
                r["live_n"],
                Jsonb(r["by_position"]),
                Jsonb(r["by_status"]),
                Jsonb(r["worst"]),
                Jsonb(gate) if gate is not None else None,
            ),
        )


def report_for(
    self, season: str, day_number: int, *, backfill: bool = False
) -> dict[str, Any] | None:
    with self.connection() as conn:
        row = conn.execute(
            "SELECT * FROM rehoboam.calibration_reports "
            "WHERE season = %s AND day_number = %s AND backfill = %s",
            (season, day_number, backfill),
        ).fetchone()
    return dict(row) if row else None


def recent_reports(
    self, season: str, *, backfill: bool = False
) -> list[dict[str, Any]]:
    with self.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM rehoboam.calibration_reports "
            "WHERE season = %s AND backfill = %s ORDER BY day_number",
            (season, backfill),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_telegram_sent(self, season: str, day_number: int) -> None:
    with self.connection() as conn:
        conn.execute(
            "UPDATE rehoboam.calibration_reports SET telegram_sent = true "
            "WHERE season = %s AND day_number = %s AND backfill = false",
            (season, day_number),
        )


def last_integrity_failure_at(self) -> float | None:
    """Newest integrity failure from a non-dry-run session (the gate's clean window)."""
    with self.connection() as conn:
        row = conn.execute(
            "SELECT MAX(f.created_at) AS at FROM rehoboam.integrity_failures f "
            "JOIN rehoboam.session_facts s ON s.session_id = f.session_id "
            "WHERE s.dry_run = 0"
        ).fetchone()
    return float(row["at"]) if row and row["at"] is not None else None
```

`session_facts.dry_run` is an `integer` column (see migration 003), hence `= 0`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/store/test_calibration_store.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add rehoboam/store/calibration_store.py tests/store/test_calibration_store.py
git commit -m "feat(store): calibration rows and reports, the joins the report needs"
```

______________________________________________________________________

### Task 7: The calibration run — readiness, rows, report, backfill

**Files:**

- Create: `rehoboam/enrichment/calibrate.py`
- Create: `tests/test_calibrate.py`

**Interfaces:**

- Consumes: Tasks 1–6; `rehoboam.backtest.baselines.season_average_baseline`; `rehoboam.backtest.snapshot.matches_before`; `rehoboam.notify.telegram.send_message(token, chat_id, text) -> bool`.

- Produces: `CalibrationOutcome(reported: list[int], waiting: dict[int, int], error: str | None)`, `prepare_refresh(store, corpus, schedule, *, season, now) -> dict[int, int]`, `run_calibration(store, schedule, *, season, now, backfill=False, only_day=None, telegram=None, max_wait_s=72*3600) -> CalibrationOutcome`, `backfill_predictions(store, schedule, *, season, day_number, now, max_status_age_days) -> int`, constants `WHISTLE_AFTER_KICKOFF_S = 3 * 3600`, `MAX_WAIT_FOR_ROWS_S = 72 * 3600`.

- [ ] **Step 1: Write the failing tests**

```python
"""Finished matchdays become rows and a report; the run waits for final rows (PR E §2, Task 7)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import patch

from rehoboam.enrichment.calibrate import (
    CalibrationOutcome,
    backfill_predictions,
    prepare_refresh,
    run_calibration,
)
from rehoboam.store.calibration_store import CalibrationStore
from rehoboam.store.corpus_store import CorpusStore

SEASON = "2026/2027"
MD1_FIRST = "2026-08-22T18:30:00Z"
MD1_LAST = "2026-08-23T15:30:00Z"
KICK1 = datetime(2026, 8, 22, 18, 30, tzinfo=timezone.utc).timestamp()
LAST1 = datetime(2026, 8, 23, 15, 30, tzinfo=timezone.utc).timestamp()
SCHEDULE = {
    "it": [
        {"day": 1, "it": [{"dt": MD1_FIRST, "st": 2}, {"dt": MD1_LAST, "st": 2}]},
        {"day": 2, "it": [{"dt": "2026-08-29T13:30:00Z", "st": 0}]},
    ]
}


def _perf(matches):
    return {"it": [{"ti": SEASON, "ph": matches}]}


def _m(day, md, st, p, mp="90"):
    return {
        "day": day,
        "md": md,
        "st": st,
        "p": p,
        "mp": mp,
        "t1": "1",
        "t2": "2",
        "pt": "1",
    }


def _seed(dsn, *, fetched_at):
    corpus = CorpusStore(dsn=dsn)
    players = []
    pid = 0
    for pos, n in (
        ("Goalkeeper", 2),
        ("Defender", 5),
        ("Midfielder", 5),
        ("Forward", 3),
    ):
        for _ in range(n):
            pid += 1
            players.append(
                {
                    "player_id": str(pid),
                    "first_name": None,
                    "last_name": f"P{pid}",
                    "position": pos,
                    "team_id": "1",
                    "market_value": 1,
                    "average_points": 1,
                }
            )
    corpus.upsert_players(players)
    for p in players:
        i = int(p["player_id"])
        corpus.record_match_history(
            p["player_id"],
            "1",
            _perf(
                [
                    _m(1, MD1_FIRST, 5 if i % 3 else 1, 10 * i if i % 3 else 0),
                    _m(2, "2026-08-29T13:30:00Z", 0, 0),
                ]
            ),
        )
        corpus.mark_fetched(p["player_id"], at=fetched_at, performance=True)
    return corpus, [p["player_id"] for p in players]


def _predict(dsn, ids, *, at, session="s1", dry_run=False):
    store = CalibrationStore(dsn=dsn)
    store.write_predictions(
        [
            {
                "session_id": session,
                "player_id": pid,
                "season": SEASON,
                "day_number": 1,
                "kickoff": KICK1,
                "predicted_at": at,
                "predicted_ep": 5.0 * int(pid),
                "p_status": {1: 0.1, 3: 0.2, 4: 0.1, 5: 0.6},
                "rate": 60.0,
                "prev_status": 5,
                "live_status": 0,
                "position": "Midfielder",
                "team_id": "1",
                "owned": int(pid) <= 11,
                "listed": False,
                "in_best_11": int(pid) <= 11,
                "live_ep": None,
                "data_grade": "A",
                "app": "function",
                "dry_run": dry_run,
                "backfill": False,
            }
            for pid in ids
        ]
    )
    return store


def test_a_finished_matchday_with_final_rows_is_reported(store_dsn):
    corpus, ids = _seed(store_dsn, fetched_at=LAST1 + 4 * 3600)
    store = _predict(store_dsn, ids[:-1], at=KICK1 - 60)  # one player unpredicted
    now = LAST1 + 5 * 3600
    outcome = run_calibration(store, SCHEDULE, season=SEASON, now=now)
    assert outcome == CalibrationOutcome(reported=[1], waiting={}, error=None)
    report = store.report_for(SEASON, 1)
    assert report["n"] == len(ids) - 1 and report["n_unpredicted"] == 1
    assert report["gate"]["consecutive_ok"] in (0, 1) and report["spearman"] is not None
    assert report["baseline_spearman"] is not None
    assert (
        run_calibration(store, SCHEDULE, season=SEASON, now=now).reported == []
    )  # idempotent


def test_waits_for_rows_fetched_before_the_whistle(store_dsn):
    corpus, ids = _seed(store_dsn, fetched_at=LAST1 - 3600)
    store = _predict(store_dsn, ids, at=KICK1 - 60)
    now = LAST1 + 5 * 3600
    outcome = run_calibration(store, SCHEDULE, season=SEASON, now=now)
    assert outcome.reported == [] and outcome.waiting == {1: len(ids)}
    assert store.report_for(SEASON, 1) is None


def test_prepare_refresh_clears_the_stale_players(store_dsn):
    corpus, ids = _seed(store_dsn, fetched_at=LAST1 - 3600)
    corpus.mark_fetched(ids[0], at=LAST1 + 4 * 3600, performance=True)
    store = CalibrationStore(dsn=store_dsn)
    cleared = prepare_refresh(
        store, corpus, SCHEDULE, season=SEASON, now=LAST1 + 5 * 3600
    )
    assert cleared == {1: len(ids) - 1}
    assert set(corpus.players_needing_fetch("performance")) == set(ids[1:])


def test_reports_anyway_after_the_wait_cap(store_dsn):
    corpus, ids = _seed(store_dsn, fetched_at=LAST1 - 3600)
    store = _predict(store_dsn, ids, at=KICK1 - 60)
    now = LAST1 + 3 * 3600 + 72 * 3600 + 1
    outcome = run_calibration(store, SCHEDULE, season=SEASON, now=now)
    assert outcome.reported == [1] and store.report_for(SEASON, 1)[
        "n_stale_rows"
    ] == len(ids)


def test_not_finished_or_before_the_whistle_is_skipped(store_dsn):
    corpus, ids = _seed(store_dsn, fetched_at=LAST1 + 4 * 3600)
    store = _predict(store_dsn, ids, at=KICK1 - 60)
    assert (
        run_calibration(store, SCHEDULE, season=SEASON, now=LAST1 + 60).reported == []
    )


def test_telegram_is_sent_once_and_recorded(store_dsn):
    corpus, ids = _seed(store_dsn, fetched_at=LAST1 + 4 * 3600)
    store = _predict(store_dsn, ids, at=KICK1 - 60)
    with patch("rehoboam.enrichment.calibrate.send_message", return_value=True) as send:
        run_calibration(
            store,
            SCHEDULE,
            season=SEASON,
            now=LAST1 + 5 * 3600,
            telegram=("tok", "chat"),
        )
    assert send.call_count == 1 and send.call_args.args[2].startswith(
        "Rehoboam calibration MD1"
    )
    assert store.report_for(SEASON, 1)["telegram_sent"] is True


def test_a_failed_send_is_retried_next_run(store_dsn):
    corpus, ids = _seed(store_dsn, fetched_at=LAST1 + 4 * 3600)
    store = _predict(store_dsn, ids, at=KICK1 - 60)
    with patch("rehoboam.enrichment.calibrate.send_message", return_value=False):
        run_calibration(
            store,
            SCHEDULE,
            season=SEASON,
            now=LAST1 + 5 * 3600,
            telegram=("tok", "chat"),
        )
    assert store.report_for(SEASON, 1)["telegram_sent"] is False
    with patch("rehoboam.enrichment.calibrate.send_message", return_value=True) as send:
        outcome = run_calibration(
            store,
            SCHEDULE,
            season=SEASON,
            now=LAST1 + 6 * 3600,
            telegram=("tok", "chat"),
        )
    assert outcome.reported == [] and send.call_count == 1
    assert store.report_for(SEASON, 1)["telegram_sent"] is True


def test_backfill_scores_before_kickoff_and_reports_apart(store_dsn):
    corpus, ids = _seed(store_dsn, fetched_at=LAST1 + 4 * 3600)
    store = CalibrationStore(dsn=store_dsn)
    n = backfill_predictions(
        store,
        SCHEDULE,
        season=SEASON,
        day_number=1,
        now=LAST1 + 5 * 3600,
        max_status_age_days=60.0,
    )
    assert n == len(ids)
    preds = store.last_predictions_before(
        season=SEASON, day_number=1, kickoff=KICK1, backfill=True
    )
    assert set(preds) == set(ids) and preds[ids[0]]["session_id"] == "backfill-md1"
    assert preds[ids[0]]["live_status"] is None and preds[ids[0]]["prev_status"] is None
    with patch("rehoboam.enrichment.calibrate.send_message") as send:
        outcome = run_calibration(
            store,
            SCHEDULE,
            season=SEASON,
            now=LAST1 + 5 * 3600,
            backfill=True,
            only_day=1,
            telegram=("tok", "chat"),
        )
    assert outcome.reported == [1] and send.call_count == 0
    assert store.report_for(SEASON, 1, backfill=True)["n"] == len(ids)
    assert store.report_for(SEASON, 1) is None
```

`prev_status` is None in the backfill because every row before MD1's kickoff is excluded by the leak boundary — the fixture has no earlier season.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_calibrate.py -q`
Expected: FAIL with `ModuleNotFoundError: rehoboam.enrichment.calibrate`.

- [ ] **Step 3: Write `rehoboam/enrichment/calibrate.py`**

```python
"""Finished matchdays → calibration rows → one report, on the ingestion app (PR E §2).

Two entry points around the ingest loop: `prepare_refresh` before it (clear
the fetch stamps of players whose rows predate the final whistle, so the loop
re-reads them), `run_calibration` after it (report every finished matchday
whose rows are final). `backfill_predictions` writes leak-free predictions
for a matchday that finished before this code existed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from rehoboam.backtest.baselines import season_average_baseline
from rehoboam.kickoff import FinishedMatchday, finished_matchdays
from rehoboam.notify.telegram import send_message
from rehoboam.scoring.store_scorer import StoredPlayer, score_stored
from rehoboam.scoring.v2.coefficients import load_coefficients
from rehoboam.services.calibration import (
    CalRow,
    build_report,
    gate_verdict,
    render_calibration_message,
)
from rehoboam.store.calibration_store import CalibrationStore
from rehoboam.store.corpus_store import CorpusStore

logger = logging.getLogger(__name__)

WHISTLE_AFTER_KICKOFF_S = 3 * 3600
MAX_WAIT_FOR_ROWS_S = 72 * 3600


@dataclass
class CalibrationOutcome:
    reported: list[int] = field(default_factory=list)
    waiting: dict[int, int] = field(default_factory=dict)
    error: str | None = None


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _whistle(md: FinishedMatchday) -> float:
    return md.last_kickoff.timestamp() + WHISTLE_AFTER_KICKOFF_S


def _unreported(
    store: CalibrationStore,
    schedule,
    *,
    season: str,
    now: float,
    backfill: bool,
    only_day: int | None,
) -> list[FinishedMatchday]:
    out = []
    for md in finished_matchdays(schedule):
        if only_day is not None and md.day_number != only_day:
            continue
        if now < _whistle(md):
            continue
        if store.report_for(season, md.day_number, backfill=backfill) is not None:
            continue
        out.append(md)
    return out


def prepare_refresh(
    store: CalibrationStore, corpus: CorpusStore, schedule, *, season: str, now: float
) -> dict[int, int]:
    """Before the ingest loop: make the loop re-read every player whose rows
    for a finished, unreported matchday predate the whistle. Returns
    `{day_number: players cleared}`."""
    cleared: dict[int, int] = {}
    for md in _unreported(
        store, schedule, season=season, now=now, backfill=False, only_day=None
    ):
        stale = store.players_needing_final_rows(
            season=season, day_number=md.day_number, whistle=_whistle(md)
        )
        if stale:
            corpus.clear_performance_fetched(stale)
            cleared[md.day_number] = len(stale)
    return cleared


def _rows_for(
    store: CalibrationStore, md: FinishedMatchday, *, season: str, backfill: bool
) -> tuple[list[CalRow], list[dict], dict[str, str]]:
    kickoff = md.first_kickoff.timestamp()
    actuals = store.actuals_for(season=season, day_number=md.day_number)
    preds = store.last_predictions_before(
        season=season, day_number=md.day_number, kickoff=kickoff, backfill=backfill
    )
    fielded = (
        set()
        if backfill
        else store.fielded_eleven_before(
            season=season, day_number=md.day_number, kickoff=kickoff
        )
    )
    history = store.history_before(before_iso=_iso(kickoff))
    cal_rows: list[CalRow] = []
    db_rows: list[dict] = []
    names: dict[str, str] = {}
    for a in actuals:
        pid = a["player_id"]
        p = preds.get(pid)
        baseline = season_average_baseline(history.get(pid, []))
        names[pid] = a["name"]
        owned = bool(p and p["owned"])
        cal_rows.append(
            CalRow(
                player_id=pid,
                position=a["position"],
                actual=float(a["points"]),
                predicted=float(p["predicted_ep"]) if p else None,
                baseline=float(baseline),
                live=float(p["live_ep"]) if p and p["live_ep"] is not None else None,
                owned=owned,
                in_best_11=pid in fielded,
                live_status=p["live_status"] if p else None,
            )
        )
        db_rows.append(
            {
                "player_id": pid,
                "session_id": p["session_id"] if p else None,
                "predicted_ep": float(p["predicted_ep"]) if p else None,
                "live_ep": p["live_ep"] if p else None,
                "baseline_ep": float(baseline),
                "actual_points": int(a["points"]),
                "minutes": int(a["minutes"]),
                "status": a["status"],
                "position": a["position"],
                "team_id": a["team_id"],
                "owned": owned,
                "in_best_11": pid in fielded,
                "prev_status": p["prev_status"] if p else None,
                "live_status": p["live_status"] if p else None,
            }
        )
    return cal_rows, db_rows, names


def _send(
    store: CalibrationStore,
    *,
    season: str,
    day_number: int,
    text: str,
    telegram: tuple[str, str],
) -> None:
    token, chat_id = telegram
    try:
        if send_message(token, chat_id, text):
            store.mark_telegram_sent(season, day_number)
        else:
            logger.warning(
                "calibration: telegram send returned False for MD%d", day_number
            )
    except Exception:
        logger.warning(
            "calibration: telegram send failed for MD%d", day_number, exc_info=True
        )


def _resend_unsent(
    store: CalibrationStore, *, season: str, now: float, telegram: tuple[str, str]
) -> None:
    for r in store.recent_reports(season):
        if r["telegram_sent"]:
            continue
        from rehoboam.services.calibration import CalibrationReport

        keys = {f for f in CalibrationReport.__dataclass_fields__}
        report = CalibrationReport(**{k: r[k] for k in keys})
        names = {w["player_id"]: w["player_id"] for w in report.worst}
        text = render_calibration_message(
            report,
            season=season,
            day_number=r["day_number"],
            names=names,
            gate=r["gate"] or {},
        )
        _send(
            store,
            season=season,
            day_number=r["day_number"],
            text=text,
            telegram=telegram,
        )


def run_calibration(
    store: CalibrationStore,
    schedule,
    *,
    season: str,
    now: float,
    backfill: bool = False,
    only_day: int | None = None,
    telegram: tuple[str, str] | None = None,
    max_wait_s: float = MAX_WAIT_FOR_ROWS_S,
) -> CalibrationOutcome:
    """Report every finished matchday whose rows are final (or overdue)."""
    outcome = CalibrationOutcome()
    try:
        for md in _unreported(
            store,
            schedule,
            season=season,
            now=now,
            backfill=backfill,
            only_day=only_day,
        ):
            whistle = _whistle(md)
            stale = store.players_needing_final_rows(
                season=season, day_number=md.day_number, whistle=whistle
            )
            if stale and now < whistle + max_wait_s:
                outcome.waiting[md.day_number] = len(stale)
                logger.info(
                    "calibration: MD%d waiting for %d final rows",
                    md.day_number,
                    len(stale),
                )
                continue
            cal_rows, db_rows, names = _rows_for(
                store, md, season=season, backfill=backfill
            )
            report = build_report(cal_rows, n_stale_rows=len(stale))
            gate = None
            if not backfill:
                from rehoboam.services.calibration import CalibrationReport

                keys = set(CalibrationReport.__dataclass_fields__)
                earlier = [
                    CalibrationReport(**{k: r[k] for k in keys})
                    for r in store.recent_reports(season)
                    if r["day_number"] < md.day_number
                ]
                gate = gate_verdict(
                    earlier + [report],
                    last_failure_at=store.last_integrity_failure_at(),
                    now=now,
                )
            store.write_calibration(
                season=season,
                day_number=md.day_number,
                backfill=backfill,
                rows=db_rows,
                report=report,
                gate=gate,
                computed_at=now,
            )
            outcome.reported.append(md.day_number)
            logger.info(
                "calibration-report md=%d n=%d spearman=%s baseline=%s regret=%s backfill=%s",
                md.day_number,
                report.n,
                report.spearman,
                report.baseline_spearman,
                report.top11_regret,
                backfill,
            )
            if telegram and not backfill:
                text = render_calibration_message(
                    report,
                    season=season,
                    day_number=md.day_number,
                    names=names,
                    gate=gate or {},
                )
                _send(
                    store,
                    season=season,
                    day_number=md.day_number,
                    text=text,
                    telegram=telegram,
                )
        if telegram and not backfill:
            _resend_unsent(store, season=season, now=now, telegram=telegram)
    except Exception as e:
        logger.exception("calibration failed")
        outcome.error = str(e)[:500]
    return outcome


def backfill_predictions(
    store: CalibrationStore,
    schedule,
    *,
    season: str,
    day_number: int,
    now: float,
    max_status_age_days: float,
) -> int:
    """Leak-free predictions for a finished matchday, flagged `backfill`.

    Rows dated at or after the matchday's first kickoff never reach the
    scorer; there is no live status, because `player_status_daily` did not
    exist yet — the prediction says so with `live_status = NULL`.
    """
    md = next(
        (m for m in finished_matchdays(schedule) if m.day_number == day_number), None
    )
    if md is None:
        raise ValueError(f"matchday {day_number} is not finished in the schedule")
    kickoff = md.first_kickoff.timestamp()
    availability, rate, _meta = load_coefficients()
    since_iso = _iso(kickoff - 400 * 86400)
    players = store.stored_players(
        since_iso=since_iso,
        status_day=md.first_kickoff.date(),
        before_iso=_iso(kickoff),
    )
    actual_ids = {
        a["player_id"] for a in store.actuals_for(season=season, day_number=day_number)
    }
    rows = []
    for p in players:
        if p.player_id not in actual_ids:
            continue
        blind = StoredPlayer(
            player_id=p.player_id,
            position=p.position,
            team_id=p.team_id,
            market_value=p.market_value,
            live_status=None,
            lineup_probability=None,
            status_fetched_at=None,
            matches=p.matches,
        )
        pred = score_stored(
            blind,
            now=md.first_kickoff,
            max_status_age_days=max_status_age_days,
            availability=availability,
            rate=rate,
        )
        rows.append(
            {
                "session_id": f"backfill-md{day_number}",
                "player_id": p.player_id,
                "season": season,
                "day_number": day_number,
                "kickoff": kickoff,
                "predicted_at": kickoff - 1.0,
                "predicted_ep": pred.predicted_ep,
                "p_status": pred.p_status,
                "rate": pred.rate,
                "prev_status": pred.prev_status,
                "live_status": None,
                "position": p.position,
                "team_id": p.team_id,
                "owned": False,
                "listed": False,
                "in_best_11": False,
                "live_ep": None,
                "data_grade": pred.data_grade,
                "app": "cli",
                "dry_run": False,
                "backfill": True,
            }
        )
    return store.write_predictions(rows)
```

Tidy the two local `CalibrationReport` imports into the module-level import (they are written inline above only to keep the snippet readable); the reconstruction from a report row must use exactly the dataclass field names, which the `SELECT *` row carries plus extra columns the dict comprehension ignores.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_calibrate.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add rehoboam/enrichment/calibrate.py tests/test_calibrate.py
git commit -m "feat(calibrate): finished matchdays become rows and a report; backfill for evidence"
```

______________________________________________________________________

### Task 8: The ingestion Function and `rehoboam calibrate`

**Files:**

- Modify: `deploy/azure_function_external/function_app.py` (`ingest`)
- Modify: `rehoboam/cli.py` (new command after `export_cmd`)
- Modify: `rehoboam/enrichment/ingest.py:74-99` (`facts_for_ingest` gains `calibration=None`)
- Test: `tests/test_ingest.py` or wherever `facts_for_ingest` is tested (append one test); `tests/store/test_cli.py` (append)

**Interfaces:**

- Consumes: Task 7.

- Produces: `facts_for_ingest(stats, *, app, session_id, calibration: dict | None = None)` — `extra["calibration"]` when given; CLI `rehoboam calibrate [--day N] [--backfill-day N] [--dry-run] [--league I]`.

- [ ] **Step 1: Write the failing tests**

Append to the file that tests `facts_for_ingest`:

```python
def test_facts_for_ingest_carries_the_calibration_outcome():
    from rehoboam.enrichment.ingest import IngestStats, facts_for_ingest

    facts = facts_for_ingest(
        IngestStats(status_written=1),
        app="external",
        session_id="x",
        calibration={"reported": [4], "waiting": {}, "error": None},
    )
    assert facts.extra["calibration"] == {"reported": [4], "waiting": {}, "error": None}
    assert facts.errors == 0
```

Append to `tests/store/test_cli.py` (follow its existing `CliRunner` pattern for the store commands):

```python
def test_calibrate_dry_run_reports_nothing_written(store_dsn, monkeypatch):
    from unittest.mock import patch

    from typer.testing import CliRunner

    from rehoboam.cli import app

    schedule = {"it": [{"day": 1, "it": [{"dt": "2026-08-22T18:30:00Z", "st": 2}]}]}
    api = type(
        "Api",
        (),
        {"get_competition_matchdays": lambda self, competition_id="1": schedule},
    )()
    with patch("rehoboam.cli._login_and_get_league", return_value=(api, None, None)):
        result = CliRunner().invoke(app, ["calibrate", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "no season" in result.output or "dry run" in result.output
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/store/test_cli.py -q -k calibrate` and the `facts_for_ingest` test.
Expected: FAIL (`No such command 'calibrate'`; `TypeError: unexpected keyword 'calibration'`).

- [ ] **Step 3: Implement**

`facts_for_ingest`: add `calibration: dict | None = None`; build `extra = asdict(stats)` and, when `calibration is not None`, `extra["calibration"] = calibration`; pass `extra=extra`.

`function_app.ingest`, inside the `try` after `league = ...`:

```python
        from rehoboam.enrichment.calibrate import prepare_refresh, run_calibration
        from rehoboam.store.calibration_store import CalibrationStore

        corpus = CorpusStore()
        calibration_store = CalibrationStore()
        schedule = None
        season = None
        try:
            schedule = api.get_competition_matchdays()
            season = calibration_store.current_season()
            if season:
                cleared = prepare_refresh(
                    calibration_store, corpus, schedule, season=season, now=time.time()
                )
                if cleared:
                    logging.info("calibration: cleared fetch stamps %s", cleared)
        except Exception:
            logging.warning("calibration: pre-ingest step failed", exc_info=True)
```

then `stats = run_ingestion(api.client, corpus, ...)` (pass the same `corpus` instance), then

```python
        calibration = None
        if schedule is not None and season:
            telegram = (
                (settings.telegram_bot_token, settings.telegram_chat_id)
                if settings.telegram_bot_token and settings.telegram_chat_id
                else None
            )
            outcome = run_calibration(
                calibration_store, schedule, season=season, now=time.time(), telegram=telegram
            )
            calibration = asdict(outcome)
            logging.info("calibration-end %s", calibration)
```

(`from dataclasses import asdict` with the other imports) and `facts_for_ingest(stats, app="external", session_id=session_id, calibration=calibration)`. `run_calibration` already catches its own exceptions, so the ingest row keeps `errors = 0` when only the report fails.

CLI, after `export_cmd`:

```python
@app.command("calibrate")
def calibrate_cmd(
    day: int | None = typer.Option(None, "--day", help="Only this matchday."),
    backfill_day: int | None = typer.Option(
        None,
        "--backfill-day",
        help="Write leak-free predictions for a finished matchday, then report it apart.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Compute, write nothing, send nothing."
    ),
    league_index: int = typer.Option(
        0, "--league", "-l", help="League index (0 for first league)"
    ),
):
    """Turn finished matchdays into calibration rows and a report — what the ingestion app does after each run."""
    import time

    from .enrichment.calibrate import backfill_predictions, run_calibration
    from .store.calibration_store import CalibrationStore

    _ensure_store()
    api, settings, _league = _login_and_get_league(league_index)
    schedule = api.get_competition_matchdays()
    store = CalibrationStore()
    season = store.current_season()
    if not season:
        console.print(
            "[yellow]no season in the corpus yet — nothing to calibrate[/yellow]"
        )
        return
    now = time.time()
    if dry_run:
        from .kickoff import finished_matchdays

        for md in finished_matchdays(schedule):
            stale = store.players_needing_final_rows(
                season=season,
                day_number=md.day_number,
                whistle=md.last_kickoff.timestamp() + 3 * 3600,
            )
            existing = store.report_for(season, md.day_number)
            console.print(
                f"MD{md.day_number}: finished, stale rows {len(stale)}, "
                f"report {'exists' if existing else 'missing'} (dry run, nothing written)"
            )
        return
    if backfill_day is not None:
        n = backfill_predictions(
            store,
            schedule,
            season=season,
            day_number=backfill_day,
            now=now,
            max_status_age_days=get_settings().max_status_age_days,
        )
        console.print(f"backfill MD{backfill_day}: {n} predictions written")
        outcome = run_calibration(
            store,
            schedule,
            season=season,
            now=now,
            backfill=True,
            only_day=backfill_day,
        )
        _print_report(store.report_for(season, backfill_day, backfill=True), outcome)
        return
    telegram = (
        (settings.telegram_bot_token, settings.telegram_chat_id)
        if settings and settings.telegram_bot_token and settings.telegram_chat_id
        else None
    )
    outcome = run_calibration(
        store, schedule, season=season, now=now, only_day=day, telegram=telegram
    )
    for d in outcome.reported:
        _print_report(store.report_for(season, d), outcome)
    if outcome.waiting:
        console.print(f"waiting for final rows: {outcome.waiting}")
    if outcome.error:
        console.print(f"[red]{outcome.error}[/red]")
        raise typer.Exit(code=1)


def _print_report(report, outcome) -> None:
    if not report:
        console.print(f"no report written ({outcome})")
        return
    table = Table(title=f"calibration MD{report['day_number']} {report['season']}")
    table.add_column("metric")
    table.add_column("value", justify="right")
    for key in (
        "n",
        "n_unpredicted",
        "n_stale_rows",
        "mae",
        "bias",
        "spearman",
        "baseline_spearman",
        "top11_regret",
        "baseline_top11_regret",
        "squad_regret",
        "live_spearman",
        "live_n",
    ):
        v = report[key]
        table.add_row(
            key,
            "n/a" if v is None else (f"{v:.3f}" if isinstance(v, float) else str(v)),
        )
    console.print(table)
    console.print(f"worst: {report['worst']}")
    console.print(f"gate: {report['gate']}")
```

Check `Table` is already imported in `cli.py` (Rich) and that `_login_and_get_league` returns `(api, settings, league)`; the dry-run test passes `settings=None`, which the command tolerates as written.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/store/test_cli.py tests/test_ingest*.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add deploy/azure_function_external/function_app.py rehoboam/cli.py rehoboam/enrichment/ingest.py tests/
git commit -m "feat(external): the ingest run refreshes final rows and reports finished matchdays; rehoboam calibrate"
```

______________________________________________________________________

### Task 9: Docs and the deploy note

**Files:**

- Modify: `CLAUDE.md` (Current state bullet, Common Commands, Store workflow, the store section, Data Flow step 2a)

- Modify: `docs/superpowers/specs/2026-09-11-data-foundation-design.md` (Rollout table row E: mark shipped with the PR date)

- [ ] **Step 1: Edit `CLAUDE.md`**

- Common Commands: add `uv run rehoboam calibrate            # Report finished matchdays (what func-rehoboam-external does after each ingest)` and `uv run rehoboam calibrate --backfill-day 3   # Leak-free predictions + report for a finished matchday, kept apart from the gate`.

- Store section: add a bullet **League-wide calibration (PR E, 2026-09-15)**: every session writes `rehoboam.predictions` for every live player from store rows (`scoring/store_scorer.py`, the same `compose_ep` as the live path; squad/market keep the API path and record `live_ep` beside it); the ingestion run clears fetch stamps for finished matchdays, re-reads them, and writes `calibration_rows` + one `calibration_reports` row per matchday (`enrichment/calibrate.py`, metrics in `services/calibration.py`, baseline scored on the same rows) with the gate verdict in `gate` and one Telegram message; the gate is a verdict — trading resumes by changing `TRADING_MODE`. `predicted_eps`/`matchday_outcomes`/`reconcile_finished_matchdays` stay until PR F.

- Data Flow step 2a: "`snapshot_predictions` (REH-20, until PR F) and `_write_league_predictions` (PR E; feeds `predictions_written` and rule I5)".

- Testing Notes: "`tests/test_scoring_v2/test_store_scorer.py` asserts the store scorer equals `score_player_v2` on the same history — keep it green when touching either."

- [ ] **Step 2: Run the whole suite and the CI checks**

Run: `uv run pytest -q` then `uv run black --check rehoboam/ tests/ deploy/` (only the files this branch created or touched must be clean; report pre-existing failures without fixing them), `uv run ruff check rehoboam/ tests/ deploy/`, `uv run bandit -r rehoboam/ -c pyproject.toml`.
Expected: suite green; no new lint or bandit findings.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md docs/superpowers/specs/2026-09-11-data-foundation-design.md
git commit -m "docs: league-wide predictions, calibration reports and the gate"
```

______________________________________________________________________

## Controller-run steps after the plan (not tasks for implementers)

1. `uv run rehoboam migrate` as admin (applies 004, refreshes grants).
1. Live dry-run `rehoboam status` against prod: expect `predictions_written` ≈ live universe, seconds, in `session_facts`.
1. `uv run rehoboam calibrate --backfill-day 1`, `2`, `3`: three backfill reports with n ≥ 400; record the numbers in the PR description.
1. `uv run rehoboam calibrate --dry-run`: MD4 not finished, nothing written.
1. Open the PR, merge, CI deploys both apps; verify the first real report after MD4 (2026-09-21 05:00 or 17:00 UTC run) and its Telegram message.
