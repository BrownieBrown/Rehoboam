# REH-75 Flip Loss Diagnosis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a committed, tested instrument that decomposes the −€55,256,064 realised across 151 completed round trips into Selection, Exit and Entry-premium terms over a horizon sweep, labels each trip with the `ProfitTrader` branch that would have accepted it, and produces the written diagnosis REH-75 asks for.

**Architecture:** Pure functions over rows, no API calls and no login. Market values come from `training_corpus.mv_series` via direct `sqlite3` reads (the pattern `replay/flip_buys.py:history_at` established); round trips come from `bid_learning.flip_outcomes`. The decomposition is an arithmetic identity, so aggregates are exact and carry no residual bucket. Branch labels are reconstructed from `TrendService.analyze` output and validated row-by-row against the real `ProfitTrader.find_profit_opportunities` verdict, so the reconstruction never becomes a second implementation of a shipped heuristic.

**Tech Stack:** Python 3.12, `uv`, pytest, Typer + Rich (CLI), sqlite3 stdlib. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-19-reh-75-flip-diagnosis-design.md`

## Global Constraints

- Market values come from `training_corpus.mv_series` — the **source of record**. `player_mv_history` is a cross-check only; a disagreement is a finding to report, never a number to average away.
- The decomposition is the identity `π = [mv(H) − mv_buy] + [s − mv(H)] − [b − mv_buy]`. **No residual bucket may be added**, at any point, for any reason.
- Horizons are `H ∈ {14, 21, 30, 45, 60}` days. **H = 30 is the headline.**
- Pre-registered dominance rule, fixed before any number is produced: the dominant mechanism is the term with the largest magnitude of its **signed** population sum, with entry premium entering as `−Σ(b − mv_buy)`. If the two largest land within **20%** of each other, report *no single dominant mechanism*.
- Branch labels mean **"flip-eligible at buy time"**, never "bought by the flip path". Every per-branch number in output and prose must carry that wording.
- Truncation for trend reconstruction is **strictly before** `buy_date` — no future leak.
- Censoring is explicit. A missing or too-distant snapshot yields `None`, never a silent zero.
- Read-only. No API calls, no login, no writes to `bid_learning.db` or `training_corpus.db`.
- Ticket-tag every commit `(REH-75)`.

______________________________________________________________________

### Task 1: Round trips and the decomposition identity

**Files:**

- Create: `rehoboam/diagnostics/__init__.py`
- Create: `rehoboam/diagnostics/flip_diagnosis.py`
- Test: `tests/test_diagnostics/__init__.py`
- Test: `tests/test_diagnostics/test_flip_decomposition.py`

**Interfaces:**

- Consumes: nothing.

- Produces: `RoundTrip` (frozen dataclass: `trip_id: int`, `player_id: str`, `player_name: str`, `buy_price: int`, `sell_price: int`, `buy_date: float`, `sell_date: float`, `hold_days: int`; property `realised: int`), `Decomposition` (frozen dataclass: `selection: int`, `exit_timing: int`, `entry_premium: int`; property `total: int`), `decompose(trip: RoundTrip, *, mv_buy: int, mv_h: int) -> Decomposition`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_diagnostics/__init__.py` as an empty file, then `tests/test_diagnostics/test_flip_decomposition.py`:

```python
"""REH-75: the loss decomposition is an identity, not a model.

The three terms cancel to `sell_price - buy_price` by construction. REH-71's
attribution table carried an `other = delta - explained` term, which is where a
wrong model hides; an identity cannot have one. These tests exist to keep it
that way.
"""

from __future__ import annotations

import pytest

from rehoboam.diagnostics.flip_diagnosis import Decomposition, RoundTrip, decompose


def _trip(**kw) -> RoundTrip:
    base = dict(
        trip_id=1,
        player_id="p1",
        player_name="Tester",
        buy_price=1_000_000,
        sell_price=1_100_000,
        buy_date=1_700_000_000.0,
        sell_date=1_700_000_000.0 + 30 * 86400,
        hold_days=30,
    )
    base.update(kw)
    return RoundTrip(**base)


@pytest.mark.parametrize(
    ("buy_price", "sell_price", "mv_buy", "mv_h"),
    [
        (1_000_000, 1_100_000, 1_000_000, 1_050_000),  # bought at MV, market rose
        (
            1_117_000,
            1_000_000,
            1_000_000,
            1_200_000,
        ),  # overpaid, market rose, sold anyway
        (900_000, 800_000, 1_000_000, 700_000),  # bought below MV, market fell
        (500_000, 500_000, 500_000, 500_000),  # the EUR 500k floor case
        (2_000_000, 0, 2_000_000, 1_000_000),  # sold for nothing
    ],
)
def test_the_three_terms_sum_to_the_realised_profit(
    buy_price, sell_price, mv_buy, mv_h
):
    trip = _trip(buy_price=buy_price, sell_price=sell_price)
    d = decompose(trip, mv_buy=mv_buy, mv_h=mv_h)
    assert d.total == trip.realised


def test_entry_premium_is_stored_unnegated_and_negated_in_the_total():
    """The sign convention is load-bearing: the pre-registered dominance rule
    compares `-entry_premium` against the other two, so storing it already
    negated would silently flip which mechanism wins."""
    trip = _trip(buy_price=1_100_000, sell_price=1_000_000)
    d = decompose(trip, mv_buy=1_000_000, mv_h=1_000_000)
    assert d.entry_premium == 100_000
    assert d.total == -100_000


def test_decompositions_add_across_trips():
    """Population totals are per-term sums; that only means anything if
    Decomposition adds componentwise."""
    a = Decomposition(selection=10, exit_timing=-4, entry_premium=3)
    b = Decomposition(selection=-2, exit_timing=7, entry_premium=1)
    assert a + b == Decomposition(selection=8, exit_timing=3, entry_premium=4)
    assert (a + b).total == a.total + b.total
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_diagnostics/test_flip_decomposition.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rehoboam.diagnostics'`

- [ ] **Step 3: Write minimal implementation**

Create `rehoboam/diagnostics/__init__.py` as an empty file, then `rehoboam/diagnostics/flip_diagnosis.py`:

```python
"""REH-75: where the money went across every completed round trip.

`flip_outcomes` is a table of ROUND TRIPS, not of flips. `backfill.py`'s
`_pair_flips` FIFO-pairs every buy against every later sell per `player_id`,
and `LearningTracker.record_flip_outcome` fires on every instant sell. Neither
consults the motive for the buy, so an EP-driven squad buy that was later sold
is indistinguishable here from a `ProfitTrader` flip. Nothing in this module
may attribute a sum to the flip channel -- see the design doc's opening
section for why that claim is not available from this data.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RoundTrip:
    """One completed buy->sell pair, as `flip_outcomes` records it."""

    trip_id: int
    player_id: str
    player_name: str
    buy_price: int
    sell_price: int
    buy_date: float
    sell_date: float
    hold_days: int

    @property
    def realised(self) -> int:
        return self.sell_price - self.buy_price


@dataclass(frozen=True)
class Decomposition:
    """The identity's three terms, in euros.

    `entry_premium` is stored UNNEGATED -- what we paid above market value --
    and enters `total` negated, exactly as it enters the identity. The
    pre-registered dominance rule compares `-entry_premium` against the other
    two terms, so storing it pre-negated would silently flip the winner.
    """

    selection: int
    exit_timing: int
    entry_premium: int

    @property
    def total(self) -> int:
        return self.selection + self.exit_timing - self.entry_premium

    def __add__(self, other: Decomposition) -> Decomposition:
        return Decomposition(
            selection=self.selection + other.selection,
            exit_timing=self.exit_timing + other.exit_timing,
            entry_premium=self.entry_premium + other.entry_premium,
        )


def decompose(trip: RoundTrip, *, mv_buy: int, mv_h: int) -> Decomposition:
    """Split a round trip's realised P&L into SELECTION + EXIT - ENTRY PREMIUM.

    An identity, not an estimate: the terms cancel to `sell_price - buy_price`.
    """
    return Decomposition(
        selection=mv_h - mv_buy,
        exit_timing=trip.sell_price - mv_h,
        entry_premium=trip.buy_price - mv_buy,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_diagnostics/test_flip_decomposition.py -v`
Expected: PASS, 7 tests

- [ ] **Step 5: Commit**

```bash
git add rehoboam/diagnostics tests/test_diagnostics
git commit -m "feat(diagnostics): the loss decomposition as an identity (REH-75)"
```

______________________________________________________________________

### Task 2: Market-value lookups with explicit censoring

**Files:**

- Modify: `rehoboam/diagnostics/flip_diagnosis.py`
- Test: `tests/test_diagnostics/test_mv_lookups.py`

**Interfaces:**

- Consumes: nothing from Task 1 (independent functions in the same module).

- Produces: `SECONDS_PER_DAY: float`, `DEFAULT_MAX_GAP_DAYS: float = 3.0`, `mv_nearest(db_path: Path, player_id: str, at: float, *, max_gap_days: float = DEFAULT_MAX_GAP_DAYS) -> int | None`, `peak_between(db_path: Path, player_id: str, start: float, end: float) -> int | None`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_diagnostics/test_mv_lookups.py`:

```python
"""REH-75: market-value lookups, and what happens when the data is not there.

Censoring is explicit everywhere in this module. A missing snapshot returns
None so the caller must decide; returning 0 would put a fabricated -mv_buy into
the SELECTION term and quietly move the diagnosis.
"""

from __future__ import annotations

import sqlite3

import pytest

from rehoboam.diagnostics.flip_diagnosis import (
    SECONDS_PER_DAY,
    mv_nearest,
    peak_between,
)

DAY0 = 1_700_000_000.0


@pytest.fixture
def corpus_db(tmp_path):
    """A minimal `mv_series`, matching `enrichment/corpus.py`'s schema."""
    path = tmp_path / "corpus.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE mv_series ("
            "player_id TEXT NOT NULL, snapshot_at REAL NOT NULL, "
            "market_value INTEGER NOT NULL, PRIMARY KEY (player_id, snapshot_at))"
        )
    return path


def _insert(path, player_id, series):
    with sqlite3.connect(path) as conn:
        conn.executemany(
            "INSERT INTO mv_series (player_id, snapshot_at, market_value) VALUES (?, ?, ?)",
            [(player_id, at, mv) for at, mv in series],
        )


def test_nearest_snapshot_wins_even_when_it_is_after_the_target(corpus_db):
    """Daily snapshots straddle a target instant. `corpus.market_value_at`
    always looks backwards; here the closer snapshot may be the later one."""
    _insert(corpus_db, "p1", [(DAY0, 1_000_000), (DAY0 + SECONDS_PER_DAY, 1_200_000)])
    target = DAY0 + 0.8 * SECONDS_PER_DAY
    assert mv_nearest(corpus_db, "p1", target) == 1_200_000


def test_the_earlier_snapshot_wins_when_it_is_closer(corpus_db):
    _insert(corpus_db, "p1", [(DAY0, 1_000_000), (DAY0 + SECONDS_PER_DAY, 1_200_000)])
    target = DAY0 + 0.2 * SECONDS_PER_DAY
    assert mv_nearest(corpus_db, "p1", target) == 1_000_000


def test_a_snapshot_beyond_the_gap_limit_is_censored_not_used(corpus_db):
    _insert(corpus_db, "p1", [(DAY0, 1_000_000)])
    target = DAY0 + 10 * SECONDS_PER_DAY
    assert mv_nearest(corpus_db, "p1", target, max_gap_days=3.0) is None


def test_an_unknown_player_is_censored(corpus_db):
    assert mv_nearest(corpus_db, "nobody", DAY0) is None


def test_peak_between_includes_both_endpoints(corpus_db):
    """The hold window is closed: a player bought at their peak and sold at it
    must not report a higher peak than either endpoint."""
    _insert(
        corpus_db,
        "p1",
        [
            (DAY0, 900_000),
            (DAY0 + SECONDS_PER_DAY, 1_500_000),
            (DAY0 + 2 * SECONDS_PER_DAY, 1_100_000),
        ],
    )
    assert peak_between(corpus_db, "p1", DAY0, DAY0 + 2 * SECONDS_PER_DAY) == 1_500_000
    assert peak_between(corpus_db, "p1", DAY0, DAY0) == 900_000


def test_peak_between_is_censored_when_the_window_holds_no_snapshot(corpus_db):
    _insert(corpus_db, "p1", [(DAY0, 900_000)])
    assert (
        peak_between(
            corpus_db, "p1", DAY0 + 5 * SECONDS_PER_DAY, DAY0 + 6 * SECONDS_PER_DAY
        )
        is None
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_diagnostics/test_mv_lookups.py -v`
Expected: FAIL — `ImportError: cannot import name 'mv_nearest'`

- [ ] **Step 3: Write minimal implementation**

Append to `rehoboam/diagnostics/flip_diagnosis.py` (and add `import sqlite3` plus `from pathlib import Path` to the imports at the top):

```python
SECONDS_PER_DAY = 86400.0

# Corpus snapshots are daily, and every round trip in scope resolves to within
# 0.99 days of every horizon (measured during design). Three days is therefore
# a guard against a future rerun over sparser data, not a threshold this run
# relies on.
DEFAULT_MAX_GAP_DAYS = 3.0


def mv_nearest(
    db_path: Path,
    player_id: str,
    at: float,
    *,
    max_gap_days: float = DEFAULT_MAX_GAP_DAYS,
) -> int | None:
    """Market value at the snapshot nearest ``at``, or None if too far away.

    Deliberately NOT `TrainingCorpus.market_value_at`, which takes the most
    recent snapshot at or before ``at``. For a horizon endpoint the nearest
    snapshot may be the following day's, and a backwards-only lookup would
    silently substitute a value up to a day stale in one direction only.

    Returns None rather than 0 when nothing is close enough: a fabricated zero
    would enter the SELECTION term as a full loss of market value.
    """
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT snapshot_at, market_value FROM mv_series WHERE player_id = ? "
            "AND snapshot_at BETWEEN ? AND ? ORDER BY ABS(snapshot_at - ?) LIMIT 1",
            (
                str(player_id),
                at - max_gap_days * SECONDS_PER_DAY,
                at + max_gap_days * SECONDS_PER_DAY,
                at,
            ),
        ).fetchall()
    return int(rows[0][1]) if rows else None


def peak_between(db_path: Path, player_id: str, start: float, end: float) -> int | None:
    """Highest market value over the CLOSED interval ``[start, end]``.

    Feeds the `peak_during_hold - sell_price` sub-measure (REH-33's angle):
    how much of the appreciation we did capture was given back before selling.
    """
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT MAX(market_value) FROM mv_series "
            "WHERE player_id = ? AND snapshot_at BETWEEN ? AND ?",
            (str(player_id), float(start), float(end)),
        ).fetchone()
    return int(row[0]) if row and row[0] is not None else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_diagnostics/test_mv_lookups.py -v`
Expected: PASS, 6 tests

- [ ] **Step 5: Commit**

```bash
git add rehoboam/diagnostics/flip_diagnosis.py tests/test_diagnostics/test_mv_lookups.py
git commit -m "feat(diagnostics): nearest-snapshot market values with explicit censoring (REH-75)"
```

______________________________________________________________________

### Task 3: Branch reconstruction, validated against the shipped ladder

**Files:**

- Create: `rehoboam/diagnostics/flip_branches.py`
- Test: `tests/test_diagnostics/test_branch_reconstruction.py`

**Interfaces:**

- Consumes: `rehoboam.replay.flip_buys.FLIP_MIN_PROFIT_PCT`, `CorpusMarketPlayer`; `rehoboam.services.trend_service.TrendService`; `rehoboam.profit_trader.ProfitTrader`.
- Produces: `BRANCHES: tuple[str, ...]`, `reconstruct_branch(trend: dict, average_points: float, *, min_profit_pct: float = FLIP_MIN_PROFIT_PCT) -> tuple[str, float]` returning `(branch_name, expected_appreciation)`, and `profit_trader_accepts(trend: dict, average_points: float, market_value: int) -> bool`.

Branch names, mirroring `profit_trader.py:126-190` in ladder order: `"low_points"`, `"small_sample"`, `"rising"`, `"recovery"`, `"dip_in_uptrend"`, `"stable"`, `"falling_mean_reversion"`, `"secular_decline"`, `"shallow_dip"`, `"no_pattern"`, `"below_min_profit"`. Only `rising`, `recovery`, `dip_in_uptrend`, `stable` and `falling_mean_reversion` are eligible outcomes; the rest are rejections named by their cause.

- [ ] **Step 1: Write the failing test**

Create `tests/test_diagnostics/test_branch_reconstruction.py`:

```python
"""REH-75: name the ProfitTrader branch that would have accepted each buy.

`flip_outcomes.trend_at_buy` is NULL on all 151 rows -- declared in the schema,
never written -- so the branch cannot be looked up and has to be reconstructed
from market-value history at the buy instant.

Naming a branch means re-stating the ladder's conditions, and this repo's rule
is that nothing reimplements a heuristic. The reconciliation test below is what
makes that safe: the reconstruction supplies the LABEL, and the shipped
`ProfitTrader` remains the AUTHORITY on the accept/reject decision. If they
ever disagree, this fails.
"""

from __future__ import annotations

import pytest

from rehoboam.diagnostics.flip_branches import (
    profit_trader_accepts,
    reconstruct_branch,
)


def _trend(**kw) -> dict:
    base = dict(
        has_data=True,
        trend="stable",
        trend_pct=0.0,
        current_value=1_000_000,
        peak_value=1_000_000,
        is_dip_in_uptrend=False,
        is_secular_decline=False,
        is_recovery=False,
    )
    base.update(kw)
    return base


@pytest.mark.parametrize(
    ("trend", "average_points", "expected_branch"),
    [
        (_trend(), 10.0, "low_points"),
        (_trend(trend="rising", trend_pct=50.0), 90.0, "small_sample"),
        (_trend(trend="rising", trend_pct=12.0), 45.0, "rising"),
        (_trend(is_recovery=True), 35.0, "recovery"),
        (_trend(is_dip_in_uptrend=True), 35.0, "dip_in_uptrend"),
        (_trend(trend="stable"), 45.0, "stable"),
        (
            _trend(trend="falling", current_value=700_000, peak_value=1_000_000),
            45.0,
            "falling_mean_reversion",
        ),
        (
            _trend(trend="falling", is_secular_decline=True, current_value=700_000),
            45.0,
            "secular_decline",
        ),
        (
            _trend(trend="falling", current_value=950_000, peak_value=1_000_000),
            45.0,
            "shallow_dip",
        ),
        (_trend(trend="rising", trend_pct=2.0), 25.0, "no_pattern"),
    ],
)
def test_each_ladder_rung_is_named(trend, average_points, expected_branch):
    branch, _ = reconstruct_branch(trend, average_points)
    assert branch == expected_branch


def test_the_ladder_is_ordered_points_gate_before_pattern():
    """A player under the points floor is rejected for THAT reason even when a
    pattern would otherwise fire -- the order is what makes the label causal."""
    branch, _ = reconstruct_branch(_trend(trend="rising", trend_pct=12.0), 5.0)
    assert branch == "low_points"


def test_expected_appreciation_below_the_profit_floor_is_a_rejection():
    """`ProfitTrader` drops candidates whose expected appreciation is under
    `min_profit_pct`, so an eligible-looking pattern can still be rejected."""
    branch, appreciation = reconstruct_branch(
        _trend(trend="stable"), 45.0, min_profit_pct=20.0
    )
    assert appreciation == 8.0
    assert branch == "below_min_profit"


@pytest.mark.parametrize(
    ("trend", "average_points"),
    [
        (_trend(), 10.0),
        (_trend(trend="rising", trend_pct=12.0), 45.0),
        (_trend(is_dip_in_uptrend=True), 35.0),
        (_trend(trend="stable"), 45.0),
        (_trend(trend="falling", current_value=700_000, peak_value=1_000_000), 45.0),
        (_trend(trend="rising", trend_pct=2.0), 25.0),
        (_trend(has_data=False), 45.0),
    ],
)
def test_reconstruction_agrees_with_the_real_profit_trader(trend, average_points):
    """The reconciliation gate. `profit_trader_accepts` calls the shipped
    `ProfitTrader.find_profit_opportunities`; the reconstruction must reach the
    same verdict on every case."""
    eligible_branches = {
        "rising",
        "recovery",
        "dip_in_uptrend",
        "stable",
        "falling_mean_reversion",
    }
    branch, _ = reconstruct_branch(trend, average_points)
    assert (branch in eligible_branches) == profit_trader_accepts(
        trend, average_points, market_value=1_000_000
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_diagnostics/test_branch_reconstruction.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rehoboam.diagnostics.flip_branches'`

- [ ] **Step 3: Write minimal implementation**

Create `rehoboam/diagnostics/flip_branches.py`:

```python
"""REH-75: which `ProfitTrader` branch would have accepted a given buy.

MIRROR, NOT SOURCE. `profit_trader.py:126-190` is the authority on whether a
candidate is accepted; this module exists only to NAME the rung that decided
it, because `flip_outcomes.trend_at_buy` was never populated.
`test_branch_reconstruction.py` reconciles the two on every rung, so a change
to the shipped ladder breaks this loudly instead of relabelling silently.
"""

from __future__ import annotations

from rehoboam.replay.flip_buys import FLIP_MIN_PROFIT_PCT, CorpusMarketPlayer

BRANCHES = (
    "low_points",
    "small_sample",
    "rising",
    "recovery",
    "dip_in_uptrend",
    "stable",
    "falling_mean_reversion",
    "secular_decline",
    "shallow_dip",
    "no_pattern",
    "below_min_profit",
    "no_trend_data",
)

ELIGIBLE_BRANCHES = frozenset(
    {"rising", "recovery", "dip_in_uptrend", "stable", "falling_mean_reversion"}
)

MIN_AVG_POINTS = 20.0


def reconstruct_branch(
    trend: dict,
    average_points: float,
    *,
    min_profit_pct: float = FLIP_MIN_PROFIT_PCT,
) -> tuple[str, float]:
    """Name the rung that decides this candidate, and its expected appreciation.

    Rung order is the shipped order; it is what makes the label causal rather
    than merely descriptive.
    """
    if not trend.get("has_data", False):
        return "no_trend_data", 0.0

    trend_direction = trend.get("trend", "unknown")
    trend_pct = trend.get("trend_pct", 0)
    current_value = trend.get("current_value", 0)
    peak_value = trend.get("peak_value", 0)

    if average_points < MIN_AVG_POINTS:
        return "low_points", 0.0
    if average_points > 80 and trend_pct > 40:
        return "small_sample", 0.0

    if trend_direction == "rising" and trend_pct > 5:
        branch, appreciation = "rising", min(trend_pct, 20)
    elif trend.get("is_recovery", False) and average_points >= 30:
        branch, appreciation = "recovery", 12.0
    elif trend.get("is_dip_in_uptrend", False) and average_points >= 30:
        branch, appreciation = "dip_in_uptrend", 10.0
    elif trend_direction == "stable" and average_points >= 40:
        branch, appreciation = "stable", 8.0
    elif trend_direction == "falling" and peak_value > 0:
        if trend.get("is_secular_decline", False):
            return "secular_decline", 0.0
        current_vs_peak_pct = ((current_value - peak_value) / peak_value) * 100
        if current_vs_peak_pct < -25 and average_points >= 40:
            branch = "falling_mean_reversion"
            appreciation = min(abs(current_vs_peak_pct) * 0.3, 15)
        else:
            return "shallow_dip", 0.0
    else:
        return "no_pattern", 0.0

    if appreciation < min_profit_pct:
        return "below_min_profit", float(appreciation)
    return branch, float(appreciation)


def profit_trader_accepts(
    trend: dict, average_points: float, market_value: int
) -> bool:
    """The shipped verdict, for reconciling against `reconstruct_branch`.

    `price == market_value` because `ProfitTrader` BRANCHES on that equality
    (profit_trader.py:121) -- feeding anything else sends the candidate down
    the non-Kickbase path where `value_gap` is negative and it is dropped, and
    the reconciliation would pass vacuously with everything rejected.
    """
    from rehoboam.profit_trader import ProfitTrader

    trader = ProfitTrader(min_profit_pct=FLIP_MIN_PROFIT_PCT)
    player = CorpusMarketPlayer(
        id="reconcile",
        price=market_value,
        market_value=market_value,
        average_points=average_points,
        position="Midfielder",
    )
    opportunities = trader.find_profit_opportunities(
        market_players=[player],
        current_budget=market_value * 10,
        player_trends={"reconcile": trend},
        team_value=market_value * 10,
    )
    return bool(opportunities)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_diagnostics/test_branch_reconstruction.py -v`
Expected: PASS, 19 tests

If a reconciliation case fails, the mirror is wrong and **the mirror gets fixed** — never the assertion, and never `profit_trader.py`.

- [ ] **Step 5: Commit**

```bash
git add rehoboam/diagnostics/flip_branches.py tests/test_diagnostics/test_branch_reconstruction.py
git commit -m "feat(diagnostics): name the ProfitTrader rung, reconciled against the shipped ladder (REH-75)"
```

______________________________________________________________________

### Task 4: Assemble the diagnosis and apply the pre-registered rule

**Files:**

- Modify: `rehoboam/diagnostics/flip_diagnosis.py`
- Test: `tests/test_diagnostics/test_diagnosis_run.py`

**Interfaces:**

- Consumes: `RoundTrip`, `Decomposition`, `decompose`, `mv_nearest`, `peak_between` (Tasks 1–2); `reconstruct_branch` (Task 3).

- Produces: `HORIZONS: tuple[int, ...] = (14, 21, 30, 45, 60)`, `HEADLINE_HORIZON: int = 30`, `FLOOR_PRICE: int = 500_000`, `TEMPORAL_BOUNDARY_ISO: str = "2026-01-03"`, `TripRow` (frozen dataclass: `trip: RoundTrip`, `mv_buy: int | None`, `branch: str`, `expected_appreciation: float`, `by_horizon: dict[int, Decomposition]`, `peak_during_hold: int | None`, `is_floor_trip: bool`), `DiagnosisResult` (frozen dataclass: `rows: list[TripRow]`, `horizons: tuple[int, ...]`, `censored: dict[int, int]`), `load_round_trips(learner_db: Path) -> list[RoundTrip]`, `totals_by_horizon(result: DiagnosisResult) -> dict[int, Decomposition]`, `totals_by_branch(result: DiagnosisResult, horizon: int) -> dict[str, Decomposition]`, `temporal_split(result: DiagnosisResult, horizon: int, boundary: float) -> dict[str, Decomposition]`, `dominant_mechanism(totals: Decomposition, *, tie_band: float = 0.20) -> str`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_diagnostics/test_diagnosis_run.py`:

```python
"""REH-75: population aggregates, and the rule that reads them.

The dominance rule is pre-registered in the design doc and fixed BEFORE any
real number is produced. These tests pin it so it cannot drift toward whatever
the data happens to say.
"""

from __future__ import annotations

from rehoboam.diagnostics.flip_diagnosis import (
    Decomposition,
    DiagnosisResult,
    RoundTrip,
    TripRow,
    dominant_mechanism,
    temporal_split,
    totals_by_branch,
    totals_by_horizon,
)

DAY0 = 1_700_000_000.0


def _row(
    *,
    branch="rising",
    buy_date=DAY0,
    selection=0,
    exit_timing=0,
    entry_premium=0,
    floor=False,
):
    trip = RoundTrip(
        trip_id=1,
        player_id="p1",
        player_name="Tester",
        buy_price=1_000_000,
        sell_price=1_000_000,
        buy_date=buy_date,
        sell_date=buy_date + 30 * 86400,
        hold_days=30,
    )
    return TripRow(
        trip=trip,
        mv_buy=1_000_000,
        branch=branch,
        expected_appreciation=10.0,
        by_horizon={
            30: Decomposition(
                selection=selection,
                exit_timing=exit_timing,
                entry_premium=entry_premium,
            )
        },
        peak_during_hold=1_000_000,
        is_floor_trip=floor,
    )


def test_horizon_totals_sum_the_terms_componentwise():
    result = DiagnosisResult(
        rows=[
            _row(selection=100, exit_timing=-30, entry_premium=20),
            _row(selection=50),
        ],
        horizons=(30,),
        censored={30: 0},
    )
    assert totals_by_horizon(result)[30] == Decomposition(
        selection=150, exit_timing=-30, entry_premium=20
    )


def test_floor_trips_are_excluded_from_the_headline_totals():
    """EUR 500k floor round trips have MV pinned at the floor; including them
    dilutes every term with structural zeros. They are reported separately."""
    result = DiagnosisResult(
        rows=[_row(selection=100), _row(selection=999, floor=True)],
        horizons=(30,),
        censored={30: 0},
    )
    assert totals_by_horizon(result)[30].selection == 100


def test_branch_totals_are_keyed_by_reconstructed_branch():
    result = DiagnosisResult(
        rows=[_row(branch="rising", selection=10), _row(branch="stable", selection=7)],
        horizons=(30,),
        censored={30: 0},
    )
    totals = totals_by_branch(result, horizon=30)
    assert totals["rising"].selection == 10
    assert totals["stable"].selection == 7


def test_temporal_split_partitions_on_the_buy_date():
    result = DiagnosisResult(
        rows=[
            _row(buy_date=DAY0 - 86400, selection=5),
            _row(buy_date=DAY0 + 86400, selection=-9),
        ],
        horizons=(30,),
        censored={30: 0},
    )
    split = temporal_split(result, horizon=30, boundary=DAY0)
    assert split["before"].selection == 5
    assert split["after"].selection == -9


def test_the_dominant_mechanism_is_the_largest_signed_magnitude():
    """Entry premium enters negated, exactly as in the identity."""
    assert (
        dominant_mechanism(
            Decomposition(selection=-1_000, exit_timing=-100, entry_premium=100)
        )
        == "selection"
    )
    assert (
        dominant_mechanism(
            Decomposition(selection=-100, exit_timing=-200, entry_premium=5_000)
        )
        == "entry_premium"
    )


def test_a_near_tie_reports_no_single_dominant_mechanism():
    """The 20% band exists so a photo-finish is not narrated as a winner."""
    assert (
        dominant_mechanism(
            Decomposition(selection=-1_000, exit_timing=-950, entry_premium=0)
        )
        == "no single dominant mechanism"
    )


def test_a_clear_win_outside_the_band_is_named():
    assert (
        dominant_mechanism(
            Decomposition(selection=-1_000, exit_timing=-500, entry_premium=0)
        )
        == "selection"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_diagnostics/test_diagnosis_run.py -v`
Expected: FAIL — `ImportError: cannot import name 'DiagnosisResult'`

- [ ] **Step 3: Write minimal implementation**

Append to `rehoboam/diagnostics/flip_diagnosis.py`:

```python
HORIZONS = (14, 21, 30, 45, 60)
HEADLINE_HORIZON = 30

# Kickbase's price floor. Round trips at buy == sell == this value have market
# value pinned, so all three terms are structurally zero -- and during design
# that exact pattern produced a false "15 flips at market value, EUR 0 P&L"
# reading. They are separated, never silently mixed in.
FLOOR_PRICE = 500_000

TEMPORAL_BOUNDARY_ISO = "2026-01-03"


@dataclass(frozen=True)
class TripRow:
    """One round trip with everything the diagnosis needs about it."""

    trip: RoundTrip
    mv_buy: int | None
    branch: str
    expected_appreciation: float
    by_horizon: dict[int, Decomposition]
    peak_during_hold: int | None
    is_floor_trip: bool


@dataclass(frozen=True)
class DiagnosisResult:
    rows: list[TripRow]
    horizons: tuple[int, ...]
    censored: dict[int, int]

    def scored(self) -> list[TripRow]:
        """Rows carried in the headline totals: everything but the floor group."""
        return [r for r in self.rows if not r.is_floor_trip]


def load_round_trips(learner_db: Path) -> list[RoundTrip]:
    """Every completed round trip in `flip_outcomes`, oldest first.

    NOT "every flip" -- see this module's docstring.
    """
    with sqlite3.connect(learner_db) as conn:
        rows = conn.execute(
            "SELECT id, player_id, player_name, buy_price, sell_price, "
            "buy_date, sell_date, hold_days FROM flip_outcomes ORDER BY buy_date"
        ).fetchall()
    return [
        RoundTrip(
            trip_id=int(r[0]),
            player_id=str(r[1]),
            player_name=str(r[2]),
            buy_price=int(r[3]),
            sell_price=int(r[4]),
            buy_date=float(r[5]),
            sell_date=float(r[6]),
            hold_days=int(r[7]),
        )
        for r in rows
    ]


def _sum(decompositions: list[Decomposition]) -> Decomposition:
    total = Decomposition(selection=0, exit_timing=0, entry_premium=0)
    for d in decompositions:
        total = total + d
    return total


def totals_by_horizon(result: DiagnosisResult) -> dict[int, Decomposition]:
    return {
        h: _sum([r.by_horizon[h] for r in result.scored() if h in r.by_horizon])
        for h in result.horizons
    }


def totals_by_branch(result: DiagnosisResult, horizon: int) -> dict[str, Decomposition]:
    totals: dict[str, Decomposition] = {}
    for row in result.scored():
        if horizon not in row.by_horizon:
            continue
        current = totals.get(row.branch)
        totals[row.branch] = (
            row.by_horizon[horizon]
            if current is None
            else current + row.by_horizon[horizon]
        )
    return totals


def temporal_split(
    result: DiagnosisResult, horizon: int, boundary: float
) -> dict[str, Decomposition]:
    before = [
        r.by_horizon[horizon]
        for r in result.scored()
        if r.trip.buy_date < boundary and horizon in r.by_horizon
    ]
    after = [
        r.by_horizon[horizon]
        for r in result.scored()
        if r.trip.buy_date >= boundary and horizon in r.by_horizon
    ]
    return {"before": _sum(before), "after": _sum(after)}


def dominant_mechanism(totals: Decomposition, *, tie_band: float = 0.20) -> str:
    """Apply REH-75's pre-registered rule. Fixed before any real number existed.

    Contributions are compared as the magnitude of each term's SIGNED sum, with
    entry premium entering negated exactly as it does in the identity.
    """
    contributions = {
        "selection": totals.selection,
        "exit_timing": totals.exit_timing,
        "entry_premium": -totals.entry_premium,
    }
    ranked = sorted(contributions.items(), key=lambda kv: abs(kv[1]), reverse=True)
    (winner, top), (_, second) = ranked[0], ranked[1]
    if abs(top) == 0:
        return "no single dominant mechanism"
    if (abs(top) - abs(second)) <= tie_band * abs(top):
        return "no single dominant mechanism"
    return winner
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_diagnostics/ -v`
Expected: PASS, all tests across the four files

- [ ] **Step 5: Commit**

```bash
git add rehoboam/diagnostics/flip_diagnosis.py tests/test_diagnostics/test_diagnosis_run.py
git commit -m "feat(diagnostics): population aggregates and the pre-registered dominance rule (REH-75)"
```

______________________________________________________________________

### Task 5: The `diagnose-flips` command and its report

**Files:**

- Create: `rehoboam/diagnostics/flip_report.py`
- Modify: `rehoboam/diagnostics/flip_diagnosis.py` (add `run_diagnosis`)
- Modify: `rehoboam/cli.py` (new command, after `backtest-baseline`)
- Test: `tests/test_diagnostics/test_flip_report.py`

**Interfaces:**

- Consumes: everything from Tasks 1–4; `rehoboam.enrichment.corpus.TrainingCorpus`; `rehoboam.replay.flip_buys.average_points_at`, `history_at`; `rehoboam.replay.driver.SEASON`, `day_for_kickoff`, `load_calendar`, `LEAGUE_ID`; `rehoboam.services.trend_service.TrendService`.

- Produces: `run_diagnosis(learner_db: Path, corpus_db: Path, *, horizons: tuple[int, ...] = HORIZONS) -> DiagnosisResult`; `format_report(result: DiagnosisResult) -> str`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_diagnostics/test_flip_report.py`:

```python
"""REH-75: what the report must say, including what it must refuse to say."""

from __future__ import annotations

from rehoboam.diagnostics.flip_diagnosis import (
    Decomposition,
    DiagnosisResult,
    RoundTrip,
    TripRow,
)
from rehoboam.diagnostics.flip_report import format_report

DAY0 = 1_700_000_000.0


def _result():
    trip = RoundTrip(
        trip_id=1,
        player_id="p1",
        player_name="Tester",
        buy_price=1_100_000,
        sell_price=1_000_000,
        buy_date=DAY0,
        sell_date=DAY0 + 30 * 86400,
        hold_days=30,
    )
    row = TripRow(
        trip=trip,
        mv_buy=1_000_000,
        branch="rising",
        expected_appreciation=10.0,
        by_horizon={
            h: Decomposition(selection=10 * h, exit_timing=-50, entry_premium=100_000)
            for h in (14, 21, 30, 45, 60)
        },
        peak_during_hold=1_200_000,
        is_floor_trip=False,
    )
    return DiagnosisResult(
        rows=[row],
        horizons=(14, 21, 30, 45, 60),
        censored=dict.fromkeys((14, 21, 30, 45, 60), 0),
    )


def test_every_horizon_appears_in_the_sweep():
    report = format_report(_result())
    for h in (14, 21, 30, 45, 60):
        assert f"{h}d" in report


def test_the_report_states_the_label_semantics():
    """Per-branch numbers without this wording invite exactly the causal
    reading the data cannot support."""
    report = format_report(_result())
    assert "flip-eligible" in report
    assert "not" in report.lower()


def test_the_report_names_the_population_correctly():
    """It is round trips, not flips. The wrong noun here is what sent REH-71
    to a withdrawn conclusion."""
    report = format_report(_result())
    assert "round trip" in report.lower()


def test_censored_counts_are_shown_even_when_zero():
    """A silent absence of censoring is indistinguishable from unhandled
    censoring; the report says which."""
    assert "censored" in format_report(_result()).lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_diagnostics/test_flip_report.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rehoboam.diagnostics.flip_report'`

- [ ] **Step 3: Write minimal implementation**

Create `rehoboam/diagnostics/flip_report.py` with a `format_report(result)` that returns a plain-text report containing, in order:

1. A header naming the population: `"151 completed ROUND TRIPS (not flips — see REH-75 design §1)"`, built from `len(result.rows)`.
1. The horizon sweep table, one row per `h` in `result.horizons` labelled `f"{h}d"`, with columns Selection / Exit / Entry premium / Total, plus the censored count for that horizon.
1. The headline line at `HEADLINE_HORIZON` naming `dominant_mechanism(...)`.
1. The per-branch table at `HEADLINE_HORIZON`, preceded verbatim by: `"Branch labels mean flip-eligible at buy time. They do not mean the flip path bought the player — provenance is unrecorded before 2026-01-03."`
1. The temporal split at `TEMPORAL_BOUNDARY_ISO`.
1. The floor group, reported separately with its count and P&L.

Then add `run_diagnosis` to `flip_diagnosis.py`: load round trips, and for each one resolve `mv_buy = mv_nearest(corpus_db, pid, buy_date)`, `mv_h = mv_nearest(corpus_db, pid, buy_date + h * SECONDS_PER_DAY)` per horizon (absent `mv_h` increments `censored[h]` and omits that horizon for the row), `peak_between(corpus_db, pid, buy_date, sell_date)`, and the branch via `TrendService.analyze(history_at(corpus, pid, buy_date), mv_buy).to_dict()` plus `average_points_at(corpus, pid, season=SEASON, day_number=day_for_kickoff(kickoffs, buy_date))` fed to `reconstruct_branch`. Mark `is_floor_trip` when `buy_price == sell_price == FLOOR_PRICE`.

Finally register the command in `rehoboam/cli.py`, following `backtest-baseline`'s shape exactly:

```python
@app.command("diagnose-flips")
def diagnose_flips(
    learner_db: Path = typer.Option(
        Path("logs") / "bid_learning.db",
        "--learner-db",
        help="Path to bid_learning.db (flip_outcomes).",
    ),
    corpus_db: Path = typer.Option(
        Path("logs") / "training_corpus.db",
        "--corpus-db",
        help="Path to training_corpus.db (mv_series + player_match_history).",
    ),
):
    """Decompose every completed round trip's P&L into selection, exit and entry premium (REH-75).

    Read-only, no API calls and no login. See
    docs/superpowers/specs/2026-08-19-reh-75-flip-diagnosis-design.md for the
    identity, the horizon sweep, and the pre-registered dominance rule.
    """
    from rehoboam.diagnostics.flip_diagnosis import run_diagnosis
    from rehoboam.diagnostics.flip_report import format_report

    console.print(format_report(run_diagnosis(learner_db, corpus_db)))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_diagnostics/ -v && uv run rehoboam diagnose-flips --help`
Expected: all tests PASS; the help text lists `--learner-db` and `--corpus-db`

- [ ] **Step 5: Commit**

```bash
git add rehoboam/diagnostics/flip_report.py rehoboam/diagnostics/flip_diagnosis.py rehoboam/cli.py tests/test_diagnostics/test_flip_report.py
git commit -m "feat(diagnostics): diagnose-flips runs the sweep and reports it (REH-75)"
```

______________________________________________________________________

### Task 6: Run it once, gate it, and write the diagnosis

**Files:**

- Create: `docs/superpowers/specs/2026-08-19-reh-75-flip-diagnosis-results.md`

**Interfaces:**

- Consumes: the `diagnose-flips` command from Task 5.

- Produces: the written diagnosis. No code.

- [ ] **Step 1: Verify the whole suite and the quality gates are green**

```bash
uv run pytest -q
uv run ruff check rehoboam/ tests/
uv run mypy rehoboam/ --ignore-missing-imports
uv run bandit -r rehoboam/ -c pyproject.toml
```

Expected: all pass. Fix anything that does not **before** producing a number — a reported figure from an unverified tree cannot be un-reported.

- [ ] **Step 2: Record the input hashes**

```bash
shasum -a 256 logs/bid_learning.db logs/training_corpus.db
```

Copy both digests into the results document. They pin exactly which data produced the numbers, as REH-71 did.

- [ ] **Step 3: Run the determinism gate**

```bash
uv run rehoboam diagnose-flips > /tmp/reh75-run1.txt
uv run rehoboam diagnose-flips > /tmp/reh75-run2.txt
diff /tmp/reh75-run1.txt /tmp/reh75-run2.txt && echo DETERMINISTIC
```

Expected: `diff` exits 0. **If it does not, stop and do not record any number** — a non-deterministic instrument has no findings, only outputs.

- [ ] **Step 4: Sanity-check the identity against ground truth**

```bash
sqlite3 logs/bid_learning.db "select sum(profit) from flip_outcomes;"
```

The floor group's P&L plus the scored group's total across any horizon must equal **−55,256,064** exactly. If it does not, the identity has been broken somewhere and the arithmetic is wrong — investigate before writing prose.

- [ ] **Step 5: Write the results document**

Create `docs/superpowers/specs/2026-08-19-reh-75-flip-diagnosis-results.md` containing:

- **The population correction first**, before any number: `flip_outcomes` counts all completed round trips, not flips; provenance is unrecorded before 2026-01-03; the €500k-floor test that looked like a channel split and was not. This is what the follow-up tickets need most.

- Input hashes and the determinism-gate result.

- The horizon sweep table, and what the shape of Selection across H says about REH-43's "median hold ≥ 21 days" premise — confirming or refuting it explicitly.

- The dominant mechanism per the pre-registered rule, quoted as the rule, including a "no single dominant mechanism" outcome if that is what the numbers give. **Do not narrate a winner the rule did not name.**

- The per-branch table, with the flip-eligible wording.

- The temporal split, and whether the +€5.4M / −€60.7M pattern survives the decomposition.

- The design doc's three caveats, restated: the squad-cap/budget upper bound, the conditioning on having sold, and up-to-one-day snapshot staleness in the entry premium.

- Follow-ups worth filing, in particular populating `trend_at_buy` going forward so next season's version of this is a lookup.

- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/specs/2026-08-19-reh-75-flip-diagnosis-results.md
git commit -m "docs(diagnostics): the flip-loss diagnosis, and the population correction it rests on (REH-75)"
```

______________________________________________________________________

## Self-Review

**Spec coverage:** decomposition identity → Task 1; MV source of record, nearest-snapshot rule, censoring → Task 2; branch labelling with reconciliation against shipped `ProfitTrader` → Task 3; horizon sweep, floor group, temporal split, pre-registered dominance rule → Task 4; module + CLI command + label-semantics wording → Task 5; hashes, determinism gate, results document, caveats → Task 6. The `player_mv_history` cross-check named in the spec's Global Constraints is **not** implemented as code — it is a manual verification noted in Task 6 Step 4's sanity check, deliberately, because it covers only 150 of 151 trips and would add a second MV path to every lookup for a check that runs once.

**Placeholder scan:** no TBD/TODO; every code step carries real code except Task 5 Step 3's report body, which is specified as an ordered content contract with the two verbatim strings its tests assert.

**Type consistency:** `Decomposition` fields (`selection`, `exit_timing`, `entry_premium`) are used identically in Tasks 1, 4 and 5. `reconstruct_branch` returns `tuple[str, float]` in Task 3 and is unpacked as such in Task 5. `mv_nearest`/`peak_between` take `db_path: Path` in Task 2 and are called with `corpus_db` in Task 5. `TripRow.by_horizon` is `dict[int, Decomposition]` throughout.
