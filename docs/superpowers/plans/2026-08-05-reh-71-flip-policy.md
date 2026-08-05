# REH-71 Flip Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Model the live bot's profit-flip *buys* in the season replay, then run a 2×2 factorial over flip buys × profit sells and encode the verdict in two `Settings` switches.

**Architecture:** A new `rehoboam/replay/flip_buys.py` builds corpus-derived inputs (truncated market-value history, average points) and feeds them to the *real shipped* `TrendService.analyze` and `ProfitTrader.find_profit_opportunities` — the same "call the real object" seam `driver.make_ep_bid_fn` uses for `SmartBidding`. The engine gains a `flip_buy_fn` hook that runs after the EP buy loop with whatever squad slots remain. Flip P&L is tracked in a cash ledger reported separately from the points attribution table, because euros cannot be subtracted from points.

**Tech Stack:** Python 3.12, `uv`, pytest, SQLite (`logs/training_corpus.db`, `logs/bid_learning.db`), Typer CLI, Pydantic `Settings`.

**Spec:** `docs/superpowers/specs/2026-08-05-reh-71-flip-policy-design.md`

## Global Constraints

- **Read-only against real data.** Both DB SHA-256s unchanged; `rehoboam/scoring/v2/coefficients.json` byte-identical. Verify before and after Task 12.
- **Deterministic.** Two consecutive runs of the same arm must be byte-identical. No `Date.now()`-style nondeterminism, no dict-iteration-order dependence.
- **Shipped path unchanged by default.** New behaviour arrives as explicit parameters defaulting to off, exactly as `profit_take_pct` / `loss_cut_pct` did in REH-68.
- **No heuristic reimplementation.** `TrendService.analyze` and `ProfitTrader.find_profit_opportunities` are called, never copied.
- **Leak boundary.** Every input to a matchday-N decision is truncated strictly before N's kickoff, using `decide_at = kickoff - DECISION_LEAD_SECONDS`.
- **Run once per arm** in Task 12. Credibility decays with every tuning iteration.
- **Positions** are the strings `"Goalkeeper"`, `"Defender"`, `"Midfielder"`, `"Forward"`.
- **Style:** `uv run black rehoboam/ tests/` and `uv run ruff check rehoboam/ tests/ --fix` before each commit. Do NOT run `black` on unrelated files — the repo is not black-clean and whole-file formatting causes massive collateral churn.
- **Branch:** `feat/reh-71-flip-policy`, already created. Never commit to `main`.

______________________________________________________________________

### Task 1: The `CorpusMarketPlayer` adapter

`ProfitTrader` reads a handful of attributes off each market player. The corpus cannot build a real `kickbase_client.MarketPlayer` (dozens of live-API fields), so we supply a stand-in — and a contract test that fails loudly if `ProfitTrader` ever starts reading something new.

**Files:**

- Create: `rehoboam/replay/flip_buys.py`
- Test: `tests/test_replay/test_flip_buys.py`

**Interfaces:**

- Consumes: nothing.

- Produces: `CorpusMarketPlayer(id: str, price: int, market_value: int, average_points: float, position: str, status: int = 0, first_name: str = "", last_name: str = "")` — a frozen dataclass.

- [ ] **Step 1: Write the failing contract test**

```python
"""REH-71: model the live bot's profit-flip BUYS inside the replay.

The replay already models profit-taking sells (`engine._flip_sells`). The live
bot also buys purely for expected appreciation (`auto_trader.py:342-392` ->
`Trader.find_profit_opportunities` -> `ProfitTrader`), and the real
-EUR 55.3M came from both halves. Deciding the flip policy from the sell half
alone answers a question nobody asked.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

from rehoboam.replay.flip_buys import CorpusMarketPlayer


def _attributes_read_off(name: str, *functions) -> set[str]:
    """Every `<name>.<attr>` read anywhere in the given functions."""
    found: set[str] = set()
    for fn in functions:
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        found |= {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == name
        }
    return found


def test_the_adapter_satisfies_every_attribute_profit_trader_reads():
    """A contract test, not a formality. ProfitTrader is shipped code this
    module does not own. If it grows a new attribute read, the replay would
    raise AttributeError deep inside a season run, most likely on one matchday
    of thirty-four. Fail here instead.
    """
    from rehoboam.profit_trader import ProfitTrader

    read = _attributes_read_off(
        "player",
        ProfitTrader.find_profit_opportunities,
        ProfitTrader._calculate_risk,
    )

    missing = read - set(CorpusMarketPlayer.__dataclass_fields__)
    assert not missing, f"ProfitTrader reads attributes the adapter lacks: {missing}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_replay/test_flip_buys.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rehoboam.replay.flip_buys'`

- [ ] **Step 3: Write minimal implementation**

```python
"""Model the live bot's profit-flip BUYS inside the replay (REH-71).

Nothing here reimplements a heuristic. `TrendService.analyze` and
`ProfitTrader.find_profit_opportunities` are called for real, exactly as
`driver.make_ep_bid_fn` calls the real `SmartBidding` -- so a change to either
shipped rule shows up in the replay instead of silently drifting from it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CorpusMarketPlayer:
    """The attribute surface `ProfitTrader.find_profit_opportunities` reads.

    Deliberately a stand-in for `kickbase_client.MarketPlayer` rather than the
    real thing: the real one is built from a live API payload carrying dozens of
    fields the corpus cannot supply, and constructing it would mean inventing
    values that then look authoritative.

    `price == market_value` at every construction site is not an oversight --
    see `make_flip_buy_fn` for why feeding a real transaction price here
    silently disables the entire pass.
    """

    id: str
    price: int
    market_value: int
    average_points: float
    position: str
    status: int = 0
    first_name: str = ""
    last_name: str = ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_replay/test_flip_buys.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
uv run black rehoboam/replay/flip_buys.py tests/test_replay/test_flip_buys.py
uv run ruff check rehoboam/replay/flip_buys.py tests/test_replay/test_flip_buys.py --fix
git add rehoboam/replay/flip_buys.py tests/test_replay/test_flip_buys.py
git commit -m "feat(replay): adapter for the attribute surface ProfitTrader reads (REH-71)"
```

______________________________________________________________________

### Task 2: Leak-free truncated market-value history

`TrendService.analyze` wants the raw API history shape. We rebuild it from `mv_series`, truncated at the decision timestamp.

The subtle part: `analyze` computes `peak_value = max(api_peak, data_peak, current)`. Passing `hmv` at all risks leaking the season-wide peak into `ProfitTrader`'s mean-reversion branch, which gates on `current_vs_peak_pct < -25`. Omitting `hmv`/`lmv` makes the peak fall out of the truncated series alone — leak-free by construction rather than by care.

**Files:**

- Modify: `rehoboam/replay/flip_buys.py`
- Test: `tests/test_replay/test_flip_buys.py`

**Interfaces:**

- Consumes: `TrainingCorpus` (`rehoboam/enrichment/corpus.py`), whose `mv_series.snapshot_at` is exactly `dt × 86400`.

- Produces: `history_at(corpus: TrainingCorpus, player_id: str, at: float) -> dict` returning `{"it": [{"dt": int, "mv": int}, ...]}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_replay/test_flip_buys.py`:

```python
import sqlite3

from rehoboam.enrichment.corpus import TrainingCorpus
from rehoboam.replay.flip_buys import history_at

DAY = 86400.0


def _corpus_with_mv(tmp_path, series: list[tuple[float, int]]) -> TrainingCorpus:
    corpus = TrainingCorpus(tmp_path / "corpus.db")
    with sqlite3.connect(corpus.db_path) as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO mv_series (player_id, snapshot_at, market_value) "
            "VALUES ('p1', ?, ?)",
            series,
        )
        conn.commit()
    return corpus


def test_history_stops_strictly_before_the_decision_time(tmp_path):
    corpus = _corpus_with_mv(tmp_path, [(1 * DAY, 100), (2 * DAY, 200), (3 * DAY, 300)])

    history = history_at(corpus, "p1", 3 * DAY)

    assert [item["mv"] for item in history["it"]] == [100, 200]


def test_a_future_spike_cannot_reach_the_peak(tmp_path):
    """The leak that matters. ProfitTrader's mean-reversion branch gates on
    `current_vs_peak_pct < -25` (profit_trader.py:172-175). A season-wide peak
    would let the bot know in August what a player is worth in March.
    """
    from rehoboam.services.trend_service import TrendService

    corpus = _corpus_with_mv(
        tmp_path, [(1 * DAY, 100), (2 * DAY, 110), (9 * DAY, 10_000)]
    )

    analysis = TrendService.analyze(history_at(corpus, "p1", 3 * DAY), 110)

    assert analysis.peak_value == 110


def test_days_since_epoch_round_trips_exactly(tmp_path):
    """`record_mv_series` stores `dt * 86400`; `analyze` sorts on `dt`. A lossy
    round trip would silently reorder the series."""
    corpus = _corpus_with_mv(tmp_path, [(7 * DAY, 500)])

    assert history_at(corpus, "p1", 8 * DAY)["it"] == [{"dt": 7, "mv": 500}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_replay/test_flip_buys.py -v -k history or spike or round_trips`
Expected: FAIL with `ImportError: cannot import name 'history_at'`

- [ ] **Step 3: Write minimal implementation**

Append to `rehoboam/replay/flip_buys.py` (add `import sqlite3` and the `TrainingCorpus` import at the top):

```python
SECONDS_PER_DAY = 86400.0


def history_at(corpus: TrainingCorpus, player_id: str, at: float) -> dict:
    """A `TrendService.analyze`-shaped history, truncated strictly before ``at``.

    ``hmv``/``lmv`` are deliberately omitted rather than computed. ``analyze``
    derives ``peak_value = max(api_peak, data_peak, current)`` and
    ``low_value = min(v for v in [api_low, data_low, current] if v > 0)``, so an
    absent key drops out of both and the extremes come from the truncated series
    alone. Supplying the season-wide peak would leak the future into
    ``ProfitTrader``'s mean-reversion branch.

    ``snapshot_at`` is exactly ``dt * 86400`` (``corpus.record_mv_series``), so
    the round trip back to ``dt`` is lossless.
    """
    with sqlite3.connect(corpus.db_path) as conn:
        rows = conn.execute(
            "SELECT snapshot_at, market_value FROM mv_series "
            "WHERE player_id = ? AND snapshot_at < ? ORDER BY snapshot_at",
            (str(player_id), float(at)),
        ).fetchall()
    return {
        "it": [
            {"dt": int(snapshot / SECONDS_PER_DAY), "mv": int(mv)}
            for snapshot, mv in rows
        ]
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_replay/test_flip_buys.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
uv run black rehoboam/replay/flip_buys.py tests/test_replay/test_flip_buys.py
uv run ruff check rehoboam/replay/flip_buys.py tests/test_replay/test_flip_buys.py --fix
git add rehoboam/replay/flip_buys.py tests/test_replay/test_flip_buys.py
git commit -m "feat(replay): leak-free truncated MV history for the trend model (REH-71)"
```

______________________________________________________________________

### Task 3: Leak-free average points

`ProfitTrader` gates hard on `average_points` — `MIN_AVG_POINTS = 20.0` at `profit_trader.py:129`, plus thresholds of 30/40/50 across its appreciation branches. We derive it from `player_match_history` using the same `matches_before` boundary the v2 scorer uses, so the flip path and the EP path cannot disagree about what was knowable.

**Files:**

- Modify: `rehoboam/replay/flip_buys.py`
- Test: `tests/test_replay/test_flip_buys.py`

**Interfaces:**

- Consumes: `matches_before(matches, *, season, day_number)` from `rehoboam/backtest/snapshot.py`; `corpus.matches_for_player(player_id)` returning dicts with `season`, `day_number`, `points`, `status`.

- Produces: `average_points_at(corpus, player_id, *, season: str, day_number: int) -> float`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_replay/test_flip_buys.py`:

```python
from rehoboam.replay.flip_buys import average_points_at

SEASON = "2025/2026"


def _corpus_with_matches(tmp_path, rows: list[tuple[int, int, int]]) -> TrainingCorpus:
    """rows: (day_number, points, status)."""
    corpus = TrainingCorpus(tmp_path / "corpus.db")
    with sqlite3.connect(corpus.db_path) as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO player_match_history "
            "(player_id, season, day_number, points, minutes, is_home, status) "
            "VALUES ('p1', ?, ?, ?, 90, 1, ?)",
            [(SEASON, day, points, status) for day, points, status in rows],
        )
        conn.commit()
    return corpus


def test_average_points_ignores_matchdays_at_or_after_the_cutoff(tmp_path):
    corpus = _corpus_with_matches(tmp_path, [(1, 100, 5), (2, 200, 5), (3, 900, 5)])

    assert average_points_at(corpus, "p1", season=SEASON, day_number=3) == 150.0


def test_only_appearances_count_towards_the_average(tmp_path):
    """Kickbase's own average is per appearance. Averaging in matchdays the
    player was not in the squad (status 1) or sat unused (status 4) drags a fit
    player under ProfitTrader's MIN_AVG_POINTS gate of 20 and silently removes
    him from every flip branch.
    """
    corpus = _corpus_with_matches(tmp_path, [(1, 60, 5), (2, 0, 1), (3, 0, 4)])

    assert average_points_at(corpus, "p1", season=SEASON, day_number=4) == 60.0


def test_a_player_with_no_appearances_averages_zero(tmp_path):
    corpus = _corpus_with_matches(tmp_path, [(1, 0, 1)])

    assert average_points_at(corpus, "p1", season=SEASON, day_number=2) == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_replay/test_flip_buys.py -v -k average`
Expected: FAIL with `ImportError: cannot import name 'average_points_at'`

- [ ] **Step 3: Write minimal implementation**

Append to `rehoboam/replay/flip_buys.py`:

```python
# Statuses in which the player actually took the pitch: 3 = came on as a sub,
# 5 = started. Deliberately NARROWER than `driver.PLAYED_STATUSES`, which is
# (1, 3, 4, 5) because the availability model needs a fitted rate for every
# state including "not in squad". Kickbase's own average points is per
# APPEARANCE, so counting non-appearances here would understate every player.
APPEARANCE_STATUSES = (3, 5)


def average_points_at(
    corpus: TrainingCorpus, player_id: str, *, season: str, day_number: int
) -> float:
    """Mean points per appearance over matches strictly before ``day_number``.

    Reuses the v2 scorer's ``matches_before`` boundary rather than introducing a
    second truncation rule, so the flip path and the EP path cannot disagree
    about what was knowable at the decision instant.
    """
    from rehoboam.backtest.snapshot import matches_before

    history = matches_before(
        corpus.matches_for_player(player_id), season=season, day_number=day_number
    )
    played = [m for m in history if m.get("status") in APPEARANCE_STATUSES]
    if not played:
        return 0.0
    return sum(float(m["points"] or 0) for m in played) / len(played)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_replay/test_flip_buys.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
uv run black rehoboam/replay/flip_buys.py tests/test_replay/test_flip_buys.py
uv run ruff check rehoboam/replay/flip_buys.py tests/test_replay/test_flip_buys.py --fix
git add rehoboam/replay/flip_buys.py tests/test_replay/test_flip_buys.py
git commit -m "feat(replay): leak-free average points per appearance (REH-71)"
```

______________________________________________________________________

### Task 4: The economic bid ceiling for a flip

Rivals can target the same flip candidate, so flips face the competition model too. But `make_ep_bid_fn` is the wrong bidder: it sizes from `marginal_ep_gain`, which is ~0 for a flip by construction, so every flip bid would land in the bottom tier and lose — collapsing arms C and D into A and B as a pure artifact.

A flip bids on its own economics: pay at most what still leaves the margin `ProfitTrader` demands, given the exit returns `MV × 1.00`.

**Files:**

- Modify: `rehoboam/replay/flip_buys.py`
- Test: `tests/test_replay/test_flip_buys.py`

**Interfaces:**

- Consumes: `INSTANT_SELL_PCT` from `rehoboam/replay/engine.py` (currently `1.0`).

- Produces: `flip_bid_ceiling(market_value: int, expected_appreciation: float, *, min_profit_pct: float) -> int`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_replay/test_flip_buys.py`:

```python
from rehoboam.replay.flip_buys import flip_bid_ceiling


def test_the_ceiling_leaves_the_required_margin_intact():
    """Buy at the ceiling, sell at the appreciated value, and the round trip
    still returns exactly min_profit_pct."""
    ceiling = flip_bid_ceiling(10_000_000, 20.0, min_profit_pct=8.0)

    exit_value = 10_000_000 * 1.20
    realised_pct = (exit_value - ceiling) / ceiling * 100

    assert abs(realised_pct - 8.0) < 0.01


def test_a_bigger_expected_move_justifies_a_bigger_bid():
    """The gradient must discriminate across the operating range -- the missing
    test class that let REH-69 ship a saturated bid function."""
    low = flip_bid_ceiling(10_000_000, 10.0, min_profit_pct=8.0)
    mid = flip_bid_ceiling(10_000_000, 20.0, min_profit_pct=8.0)
    high = flip_bid_ceiling(10_000_000, 40.0, min_profit_pct=8.0)

    assert low < mid < high


def test_a_flip_with_no_expected_upside_bids_below_market_value():
    """Otherwise the bot pays full price for a player it expects to stagnate."""
    assert flip_bid_ceiling(10_000_000, 0.0, min_profit_pct=8.0) < 10_000_000
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_replay/test_flip_buys.py -v -k ceiling or bigger or upside`
Expected: FAIL with `ImportError: cannot import name 'flip_bid_ceiling'`

- [ ] **Step 3: Write minimal implementation**

Append to `rehoboam/replay/flip_buys.py`:

```python
def flip_bid_ceiling(
    market_value: int, expected_appreciation: float, *, min_profit_pct: float
) -> int:
    """The most a flip can rationally cost and still clear its own margin.

    A flip bought at ``P`` exits at ``MV x (1 + a/100) x INSTANT_SELL_PCT``,
    where ``INSTANT_SELL_PCT`` is 1.00 as measured in REH-67. Requiring the
    round trip to still return ``min_profit_pct`` gives::

        P <= MV x (1 + a/100) / (1 + m/100)

    Bidding above this guarantees a loss even when the expected appreciation
    fully materialises, so losing a listing to a rival who paid more is the
    correct outcome, not a modelling failure.

    Deliberately NOT `SmartBidding.calculate_ep_bid`: that sizes an overbid from
    the marginal-gain tier, and a flip's marginal EP gain is ~0 by construction,
    so every flip would bid into the bottom tier and lose every contested
    listing -- reporting "flip buys do nothing" as an artifact of the bidder
    rather than a fact about flipping.
    """
    from rehoboam.replay.engine import INSTANT_SELL_PCT

    exit_value = market_value * (1.0 + expected_appreciation / 100.0) * INSTANT_SELL_PCT
    return int(exit_value / (1.0 + min_profit_pct / 100.0))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_replay/test_flip_buys.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
uv run black rehoboam/replay/flip_buys.py tests/test_replay/test_flip_buys.py
uv run ruff check rehoboam/replay/flip_buys.py tests/test_replay/test_flip_buys.py --fix
git add rehoboam/replay/flip_buys.py tests/test_replay/test_flip_buys.py
git commit -m "feat(replay): bid flips on their own economics, not on EP gain (REH-71)"
```

______________________________________________________________________

### Task 5: Wire the real `ProfitTrader` into a candidate function

Composes Tasks 1-4 into the callable the engine will invoke once per matchday.

**Files:**

- Modify: `rehoboam/replay/flip_buys.py`
- Test: `tests/test_replay/test_flip_buys.py`

**Interfaces:**

- Consumes: `CorpusMarketPlayer`, `history_at`, `average_points_at`, `flip_bid_ceiling`; `TrendService.analyze`; `ProfitTrader.find_profit_opportunities`.

- Produces:

  - `FlipCandidate(player_id: str, market_value: int, expected_appreciation: float, max_bid: int)` — frozen dataclass.
  - `make_flip_buy_fn(corpus, *, season: str, day_fn: Callable[[float], int], position_fn: Callable[[str], str | None]) -> Callable[[list, float, int, int], list[FlipCandidate]]`. The returned callable takes `(listings, at, budget, team_value)` and returns candidates in `ProfitTrader`'s own preference order.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_replay/test_flip_buys.py`:

```python
from rehoboam.replay.flip_buys import FlipCandidate, make_flip_buy_fn
from rehoboam.replay.market import MarketListing


def _rising_corpus(tmp_path) -> TrainingCorpus:
    """A player on a steady climb who also scores well -- the shape
    ProfitTrader's `rising` branch is looking for."""
    corpus = TrainingCorpus(tmp_path / "corpus.db")
    with sqlite3.connect(corpus.db_path) as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO mv_series (player_id, snapshot_at, market_value) "
            "VALUES ('p1', ?, ?)",
            [(day * DAY, 10_000_000 + day * 400_000) for day in range(1, 31)],
        )
        conn.executemany(
            "INSERT OR REPLACE INTO player_match_history "
            "(player_id, season, day_number, points, minutes, is_home, status) "
            "VALUES ('p1', ?, ?, ?, 90, 1, 5)",
            [(SEASON, day, 80) for day in range(1, 5)],
        )
        conn.commit()
    return corpus


def _candidates(corpus, at: float):
    fn = make_flip_buy_fn(
        corpus,
        season=SEASON,
        day_fn=lambda _at: 10,
        position_fn=lambda _pid: "Forward",
    )
    listings = [MarketListing(player_id="p1", price=11_000_000, transfer_at=at - DAY)]
    return fn(listings, at, 50_000_000, 100_000_000)


def test_a_rising_high_scorer_is_offered_as_a_flip_candidate(tmp_path):
    """The regression test that matters most. If the adapter fed ProfitTrader a
    real transaction price instead of market value, EVERY candidate would take
    the non-Kickbase branch, `value_gap` would be negative, and the whole pass
    would return an empty list on every matchday while appearing to work.
    """
    found = _candidates(_rising_corpus(tmp_path), 31 * DAY)

    assert [c.player_id for c in found] == ["p1"]


def test_the_candidate_carries_an_economically_sized_max_bid(tmp_path):
    found = _candidates(_rising_corpus(tmp_path), 31 * DAY)

    assert found[0].max_bid == flip_bid_ceiling(
        found[0].market_value, found[0].expected_appreciation, min_profit_pct=8.0
    )


def test_a_player_with_no_market_value_is_skipped(tmp_path):
    """`market_value_at` returns None outside the recorded series; a zero-value
    adapter would divide by zero inside the trend model."""
    corpus = TrainingCorpus(tmp_path / "corpus.db")

    assert _candidates(corpus, 31 * DAY) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_replay/test_flip_buys.py -v -k candidate or max_bid or no_market_value`
Expected: FAIL with `ImportError: cannot import name 'FlipCandidate'`

- [ ] **Step 3: Write minimal implementation**

Append to `rehoboam/replay/flip_buys.py` (add `from collections.abc import Callable` at the top):

```python
# Thresholds read from `Trader.find_profit_opportunities`'s call site
# (trader.py:715-721), NOT from `ProfitTrader.__init__`'s defaults, which no
# caller in the codebase actually uses.
FLIP_MIN_PROFIT_PCT = 8.0
FLIP_MAX_HOLD_DAYS = 7
FLIP_MAX_RISK_SCORE = 60.0
# The live path scores only the first 50 market entries (trader.py:711).
FLIP_MARKET_SCAN_LIMIT = 50


@dataclass(frozen=True)
class FlipCandidate:
    """A player worth buying for appreciation, with what he is worth paying."""

    player_id: str
    market_value: int
    expected_appreciation: float
    max_bid: int


def make_flip_buy_fn(
    corpus: TrainingCorpus,
    *,
    season: str,
    day_fn: Callable[[float], int],
    position_fn: Callable[[str], str | None],
) -> Callable[[list, float, int, int], list[FlipCandidate]]:
    """Rank flip candidates with the bot's own profit-trading logic (REH-71).

    DECISION PRICE vs EXECUTION PRICE. The adapter reports
    ``price == market_value`` while the engine pays a bid derived from
    ``flip_bid_ceiling``. This is not a fudge. The live bot only ever flips
    ``is_kickbase_seller()`` listings (trader.py:685), where the two are equal by
    construction, and ``ProfitTrader`` *branches* on that equality
    (profit_trader.py:121). Feeding it a real transaction price -- which averages
    1.117x market value -- sends every candidate down the non-Kickbase branch,
    where ``value_gap`` is negative and the candidate is dropped at
    profit_trader.py:194. The pass would look modelled and never fire once.

    ``status`` is pinned to 0 (available) because the corpus's per-match status
    is participation, not injury. Nothing is therefore skipped as injured, so
    this buys MORE flips than the live bot would -- an upper bound on flip
    activity, and hence on flip harm.
    """
    from rehoboam.profit_trader import ProfitTrader
    from rehoboam.services.trend_service import TrendService

    trader = ProfitTrader(
        min_profit_pct=FLIP_MIN_PROFIT_PCT,
        max_hold_days=FLIP_MAX_HOLD_DAYS,
        max_risk_score=FLIP_MAX_RISK_SCORE,
    )

    def candidates(
        listings: list, at: float, budget: int, team_value: int
    ) -> list[FlipCandidate]:
        day = day_fn(at)
        players: list[CorpusMarketPlayer] = []
        trends: dict[str, dict] = {}

        for listing in listings[:FLIP_MARKET_SCAN_LIMIT]:
            pid = listing.player_id
            market_value = corpus.market_value_at(pid, at)
            position = position_fn(pid)
            if not market_value or not position:
                continue
            players.append(
                CorpusMarketPlayer(
                    id=pid,
                    price=market_value,
                    market_value=market_value,
                    average_points=average_points_at(
                        corpus, pid, season=season, day_number=day
                    ),
                    position=position,
                )
            )
            trends[pid] = TrendService.analyze(
                history_at(corpus, pid, at), market_value
            ).to_dict()

        opportunities = trader.find_profit_opportunities(
            market_players=players,
            current_budget=budget,
            player_trends=trends,
            team_value=team_value,
        )
        return [
            FlipCandidate(
                player_id=o.player.id,
                market_value=o.market_value,
                expected_appreciation=o.expected_appreciation,
                max_bid=flip_bid_ceiling(
                    o.market_value,
                    o.expected_appreciation,
                    min_profit_pct=FLIP_MIN_PROFIT_PCT,
                ),
            )
            for o in opportunities
        ]

    return candidates
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_replay/test_flip_buys.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: Commit**

```bash
uv run black rehoboam/replay/flip_buys.py tests/test_replay/test_flip_buys.py
uv run ruff check rehoboam/replay/flip_buys.py tests/test_replay/test_flip_buys.py --fix
git add rehoboam/replay/flip_buys.py tests/test_replay/test_flip_buys.py
git commit -m "feat(replay): call the real ProfitTrader to rank flip buys (REH-71)"
```

______________________________________________________________________

### Task 6: Engine integration — the flip-buy pass

Runs after the EP buy loop with whatever slots remain, mirroring `auto_trader.py:533`.

**Files:**

- Modify: `rehoboam/replay/engine.py`
- Test: `tests/test_replay/test_flip_buy_pass.py`

**Interfaces:**

- Consumes: objects with `.player_id`, `.market_value`, `.expected_appreciation`, `.max_bid` (duck-typed, so the engine need not import `flip_buys` and risk a cycle).

- Produces: `run_season(..., flip_buy_fn: Callable[[list, float, int, int], list] | None = None)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_replay/test_flip_buy_pass.py`:

```python
"""REH-71: the replay buys for appreciation, not only for points.

The live bot buys flip candidates with whatever squad slots the EP pass leaves
(auto_trader.py:533). A flip never displaces a squad member -- the live bot does
not sell to make room for one -- so the pass simply stops at MAX_SQUAD_SIZE.
"""

from __future__ import annotations

from dataclasses import dataclass

from rehoboam.replay.engine import Matchday, run_season
from rehoboam.replay.market import MarketListing
from rehoboam.replay.state import ReplayPlayer, ReplayState

DAY = 86400.0
# Fifteen slots, ordered so that _squad(12) is a legal eleven-fieldable squad
# (1 GK / 5 DEF / 4 MID / 2 FW) with midfield still below the formation ceiling
# of 5 -- otherwise `_would_create_dead_weight` refuses the candidate and every
# assertion below fails for a reason unrelated to what it is testing.
POS = (
    ["Goalkeeper"]
    + ["Defender"] * 5
    + ["Midfielder"] * 4
    + ["Forward"] * 3
    + ["Goalkeeper"]
    + ["Midfielder"]
)


@dataclass(frozen=True)
class FakeCandidate:
    player_id: str
    market_value: int
    expected_appreciation: float
    max_bid: int


class OneListing:
    def __init__(self, price: int) -> None:
        self.price = price

    def available_before(self, at):
        return [MarketListing(player_id="new", price=self.price, transfer_at=at - DAY)]


def _squad(n: int, basis: int = 10_000_000):
    return {
        str(i): ReplayPlayer(
            id=str(i), position=POS[i], team_id=str(i), buy_price=basis, bought_at=0.0
        )
        for i in range(n)
    }


def _run(
    *, squad_size: int, listing_price: int, max_bid: int, budget: int = 50_000_000
):
    state = ReplayState(budget=budget, squad=_squad(squad_size))
    result = run_season(
        state=state,
        market=OneListing(listing_price),
        matchdays=[Matchday(day_number=1, kickoff=10 * DAY, points={})],
        # High floor so the EP pass never buys; the flip pass is what we measure.
        min_ep_gain=1_000_000.0,
        score_fn=lambda pid, at: 10.0,
        mv_fn=lambda pid, at: 10_000_000,
        # Midfield sits at 4 of a maximum 5, so the candidate is a real upgrade
        # slot rather than permanent dead weight.
        position_fn=lambda pid: "Midfielder",
        team_fn=lambda pid: pid,
        flip_buy_fn=lambda listings, at, budget, tv: [
            FakeCandidate(
                player_id="new",
                market_value=10_000_000,
                expected_appreciation=20.0,
                max_bid=max_bid,
            )
        ],
    )
    return result, state


def test_a_flip_candidate_within_the_ceiling_is_bought():
    _result, state = _run(squad_size=12, listing_price=9_000_000, max_bid=11_000_000)

    assert "new" in state.squad


def test_a_flip_is_not_bought_above_its_economic_ceiling():
    """Paying more than the flip can ever return is a guaranteed loss, so losing
    the listing to a rival is the correct outcome."""
    _result, state = _run(squad_size=12, listing_price=12_000_000, max_bid=11_000_000)

    assert "new" not in state.squad


def test_a_flip_never_displaces_a_squad_member():
    """At 15/15 the live bot does not sell to make room for a flip."""
    _result, state = _run(squad_size=15, listing_price=9_000_000, max_bid=11_000_000)

    assert "new" not in state.squad
    assert len(state.squad) == 15


def test_a_flip_is_skipped_when_it_would_leave_the_budget_negative():
    _result, state = _run(
        squad_size=12, listing_price=9_000_000, max_bid=11_000_000, budget=1_000
    )

    assert "new" not in state.squad


def test_flip_buying_is_off_by_default():
    """The shipped replay path must be unchanged until the run that enables it."""
    state = ReplayState(budget=50_000_000, squad=_squad(12))
    run_season(
        state=state,
        market=OneListing(9_000_000),
        matchdays=[Matchday(day_number=1, kickoff=10 * DAY, points={})],
        min_ep_gain=1_000_000.0,
        score_fn=lambda pid, at: 10.0,
        mv_fn=lambda pid, at: 10_000_000,
        position_fn=lambda pid: "Midfielder",
        team_fn=lambda pid: pid,
    )

    assert "new" not in state.squad
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_replay/test_flip_buy_pass.py -v`
Expected: FAIL with `TypeError: run_season() got an unexpected keyword argument 'flip_buy_fn'`

- [ ] **Step 3: Write minimal implementation**

Add to `rehoboam/replay/engine.py`, after `_flip_sells`:

```python
def _flip_buys(
    state: ReplayState,
    scores: dict[str, float],
    listings: list,
    at: float,
    *,
    flip_buy_fn: Callable[[list, float, int, int], list],
    score_fn: Callable[[str, float], float],
    mv_fn: Callable[[str, float], int | None],
    position_fn: Callable[[str], str | None],
    team_fn: Callable[[str], str | None],
    with_competition: bool,
) -> int:
    """Buy for expected appreciation with the slots the EP pass left (REH-71).

    Mirrors the live ordering: EP candidates execute first and flips take
    "remaining slots" (auto_trader.py:533). A flip never displaces a squad
    member, so this stops at MAX_SQUAD_SIZE rather than calling
    ``_fieldable_sale_victim``.

    Candidates carry their own ``max_bid`` from ``flip_buys.flip_bid_ceiling``
    rather than going through ``bid_fn``: a flip's marginal EP gain is ~0 by
    construction, so the EP bidder would put every flip in its bottom tier and
    lose every contested listing, reporting a bidder artifact as a fact about
    flipping.
    """
    from rehoboam.scoring.decision import _would_create_dead_weight

    by_id = {listing.player_id: listing for listing in listings}
    team_value = _team_value(state, mv_fn, at)
    buys = 0

    for candidate in flip_buy_fn(listings, at, state.budget, team_value):
        if state.squad_size >= MAX_SQUAD_SIZE:
            break
        pid = candidate.player_id
        listing = by_id.get(pid)
        position = position_fn(pid)
        if listing is None or not position or pid in state.squad:
            continue

        # Under competition we must outbid what the real buyer paid, and we then
        # pay our own bid. Without it the listing is ours at its asking price --
        # but never above the ceiling, which is an economic limit either way.
        if with_competition:
            if candidate.max_bid <= listing.price:
                continue
            cost = int(candidate.max_bid)
        else:
            if listing.price > candidate.max_bid:
                continue
            cost = int(listing.price)

        player = ReplayPlayer(id=pid, position=position, team_id=team_fn(pid))
        if _would_create_dead_weight(player, state.players):
            continue
        allowed, _reason = can_buy(state, player, cost, team_value=team_value)
        if not allowed or not _solvent_after(state, cost):
            continue

        state.buy(player, cost, at=at)
        scores[pid] = score_fn(pid, at)
        team_value = _team_value(state, mv_fn, at)
        buys += 1

    return buys
```

Add the parameter to `run_season`'s signature, immediately after `loss_cut_pct`:

```
    # REH-71 flip buys. Given (listings, at, budget, team_value), returns
    # candidates carrying their own economic max_bid. None keeps the shipped
    # behaviour, in which every buy is justified by marginal expected points.
    flip_buy_fn: Callable[[list, float, int, int], list] | None = None,
```

And call it inside the matchday loop, immediately after the EP `for listing in listings:` loop ends and *before* `sells += _restore_budget(...)`:

```python
if flip_buy_fn is not None:
    buys += _flip_buys(
        state,
        scores,
        listings,
        decide_at,
        flip_buy_fn=flip_buy_fn,
        score_fn=score_fn,
        mv_fn=mv_fn,
        position_fn=position_fn,
        team_fn=team_fn,
        with_competition=bid_fn is not None,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_replay/ -v`
Expected: PASS — the 5 new tests plus every pre-existing replay test.

- [ ] **Step 5: Commit**

```bash
uv run black rehoboam/replay/engine.py tests/test_replay/test_flip_buy_pass.py
uv run ruff check rehoboam/replay/engine.py tests/test_replay/test_flip_buy_pass.py --fix
git add rehoboam/replay/engine.py tests/test_replay/test_flip_buy_pass.py
git commit -m "feat(replay): buy for appreciation with the slots the EP pass left (REH-71)"
```

______________________________________________________________________

### Task 7: The flip ledger

Round trips must be countable in euros and comparable to the real 151 / −€55.3M. The opening squad carries a cost basis (market value at assignment), so without an explicit marker its disposals would inflate the count and destroy the comparison.

**Files:**

- Modify: `rehoboam/replay/state.py`, `rehoboam/replay/engine.py`
- Test: `tests/test_replay/test_flip_ledger.py`

**Interfaces:**

- Consumes: `ReplayPlayer`, `ReplayState.sell`.

- Produces:

  - `ReplayPlayer.acquired: str = "assigned"` — `"assigned"` or `"bought"`.
  - `FlipRecord(player_id: str, buy_price: int, proceeds: int, bought_at: float | None, sold_at: float)` in `engine.py`.
  - `SeasonResult.flips: list[FlipRecord]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_replay/test_flip_ledger.py`:

```python
"""REH-71: flip P&L is cash, and cash is counted separately from points.

The attribution table decomposes a POINTS delta. Flip income is EUROS and
reaches the scoreboard only indirectly, through buys it funds. Mixing the two
would be a category error, so the ledger stands apart.
"""

from __future__ import annotations

from rehoboam.replay.engine import Matchday, run_season
from rehoboam.replay.state import ReplayPlayer, ReplayState

DAY = 86400.0
POS = ["Goalkeeper"] + ["Defender"] * 4 + ["Midfielder"] * 4 + ["Forward"] * 3


class NoMarket:
    def available_before(self, at):
        return []


def _squad_of_12(acquired: str = "bought"):
    return {
        str(i): ReplayPlayer(
            id=str(i),
            position=POS[i],
            team_id=str(i),
            buy_price=10_000_000,
            bought_at=0.0,
            acquired=acquired,
        )
        for i in range(12)
    }


def _run(*, current_mv: int, squad):
    state = ReplayState(budget=10_000_000, squad=squad)
    return run_season(
        state=state,
        market=NoMarket(),
        matchdays=[Matchday(day_number=1, kickoff=10 * DAY, points={})],
        score_fn=lambda pid, at: 0.0 if pid == "11" else 100.0,
        mv_fn=lambda pid, at: current_mv if pid == "11" else 10_000_000,
        position_fn=lambda pid: "Forward",
        team_fn=lambda pid: pid,
        profit_take_pct=15.0,
    )


def test_a_profitable_round_trip_is_recorded_with_its_pnl():
    result = _run(current_mv=12_000_000, squad=_squad_of_12())

    assert len(result.flips) == 1
    assert result.flips[0].proceeds - result.flips[0].buy_price == 2_000_000


def test_an_assigned_player_sold_is_not_a_round_trip():
    """The opening squad was assigned, not bought (state.py:96-100). Counting
    its disposals would inflate the count and make it incomparable to the real
    151 flips."""
    result = _run(current_mv=12_000_000, squad=_squad_of_12(acquired="assigned"))

    assert result.flips == []


def test_players_bought_during_the_season_are_marked_as_bought():
    state = ReplayState(budget=10_000_000, squad={})
    state.buy(ReplayPlayer(id="x", position="Forward"), 5_000_000, at=1.0)

    assert state.squad["x"].acquired == "bought"


def test_a_flip_bought_then_sold_closes_as_exactly_one_round_trip():
    """The end-to-end shape the ledger exists to count: the flip pass opens a
    position on matchday 1, the market moves, and the sell pass banks it on
    matchday 2. Anything other than a single record means the two halves
    disagree about what a round trip is.
    """
    from dataclasses import dataclass

    from rehoboam.replay.market import MarketListing

    @dataclass(frozen=True)
    class FakeCandidate:
        player_id: str
        market_value: int
        expected_appreciation: float
        max_bid: int

    class OneListing:
        def available_before(self, at):
            return [
                MarketListing(player_id="new", price=8_000_000, transfer_at=at - DAY)
            ]

    # "new" is worth 8M when bought and 12M by matchday 2 -- a 50% gain, well
    # clear of the 15% take-profit threshold. He scores nothing, so he is never
    # a best-eleven starter and starter protection does not hold him.
    def mv_fn(pid, at):
        if pid != "new":
            return 10_000_000
        return 8_000_000 if at < 15 * DAY else 12_000_000

    state = ReplayState(budget=50_000_000, squad=_squad_of_12(acquired="assigned"))
    result = run_season(
        state=state,
        market=OneListing(),
        matchdays=[
            Matchday(day_number=1, kickoff=10 * DAY, points={}),
            Matchday(day_number=2, kickoff=20 * DAY, points={}),
        ],
        min_ep_gain=1_000_000.0,
        score_fn=lambda pid, at: 0.0 if pid == "new" else 100.0,
        mv_fn=mv_fn,
        # The squad already holds 3 forwards, the formation ceiling, so a
        # forward candidate would be refused as dead weight. Midfield has 4 of 5.
        position_fn=lambda pid: "Midfielder" if pid == "new" else "Forward",
        team_fn=lambda pid: pid,
        profit_take_pct=15.0,
        flip_buy_fn=lambda listings, at, budget, tv: [
            FakeCandidate(
                player_id="new",
                market_value=8_000_000,
                expected_appreciation=20.0,
                max_bid=9_000_000,
            )
        ],
    )

    assert "new" not in state.squad
    assert len(result.flips) == 1
    assert result.flips[0].proceeds - result.flips[0].buy_price == 4_000_000
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_replay/test_flip_ledger.py -v`
Expected: FAIL with `TypeError: ReplayPlayer.__init__() got an unexpected keyword argument 'acquired'`

- [ ] **Step 3: Write minimal implementation**

In `rehoboam/replay/state.py`, add the field to `ReplayPlayer` (after `bought_at`):

```
    # "assigned" for the randomly-allocated opening squad, "bought" for anything
    # the replay actually purchased. Both carry a cost basis, so the basis alone
    # cannot distinguish them -- and counting assigned disposals as round trips
    # would make the ledger incomparable to the real 151 flips (REH-71).
    acquired: str = "assigned"
```

And mark purchases in `ReplayState.buy`:

```python
def buy(self, player: ReplayPlayer, price: int, at: float | None = None) -> None:
    """Add ``player`` to the squad, recording the cost basis (REH-68)."""
    self.squad[player.id] = replace(
        player, buy_price=int(price), bought_at=at, acquired="bought"
    )
    self.budget -= int(price)
```

In `rehoboam/replay/engine.py`, add the record type next to `MatchdayOutcome`:

```python
@dataclass(frozen=True)
class FlipRecord:
    """One completed round trip: a player the replay bought and later sold."""

    player_id: str
    buy_price: int
    proceeds: int
    bought_at: float | None
    sold_at: float
```

Add to `SeasonResult`:

```python
flips: list[FlipRecord] = field(default_factory=list)
```

Give `_flip_sells` a `ledger: list[FlipRecord]` keyword argument and append before each sale:

```python
if player.acquired == "bought":
    ledger.append(
        FlipRecord(
            player_id=pid,
            buy_price=int(player.buy_price),
            proceeds=_proceeds(pid, mv_fn, at),
            bought_at=player.bought_at,
            sold_at=at,
        )
    )
state.sell(pid, _proceeds(pid, mv_fn, at))
```

Pass `ledger=result.flips` at the `_flip_sells` call site in `run_season`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_replay/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
uv run black rehoboam/replay/state.py rehoboam/replay/engine.py tests/test_replay/test_flip_ledger.py
uv run ruff check rehoboam/replay/state.py rehoboam/replay/engine.py tests/test_replay/test_flip_ledger.py --fix
git add rehoboam/replay/state.py rehoboam/replay/engine.py tests/test_replay/test_flip_ledger.py
git commit -m "feat(replay): a cash ledger for completed round trips (REH-71)"
```

______________________________________________________________________

### Task 8: The Trading report block

**Files:**

- Modify: `rehoboam/replay/attribution.py`
- Test: `tests/test_replay/test_attribution.py`

**Interfaces:**

- Consumes: `SeasonResult.flips`.

- Produces: `trading_summary(result: SeasonResult) -> tuple[int, int, int]` returning `(realised_pnl, round_trips, wins)`; `format_report(..., with_flip_buys: bool = False)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_replay/test_attribution.py`:

```python
from rehoboam.replay.engine import FlipRecord, SeasonResult
from rehoboam.replay.attribution import format_report, trading_summary


def _result_with_flips() -> SeasonResult:
    return SeasonResult(
        flips=[
            FlipRecord("a", 10_000_000, 12_000_000, 0.0, 1.0),
            FlipRecord("b", 10_000_000, 7_000_000, 0.0, 1.0),
        ]
    )


def test_trading_summary_nets_wins_against_losses():
    assert trading_summary(_result_with_flips()) == (-1_000_000, 2, 1)


def test_the_report_keeps_cash_out_of_the_points_attribution():
    """Euros minus points is a category error. The Trading block must say so on
    its face, so no reader ever adds it to the attribution table."""
    report = format_report(
        _result_with_flips(),
        actual_total=0,
        actual_per_matchday={},
        standings=[],
        min_ep_gain=40.0,
        with_flips=True,
    )

    assert "does not enter the points attribution" in report


def test_the_report_prints_the_real_season_for_comparison():
    """A replay P&L with nothing to compare it against is uninterpretable."""
    report = format_report(
        _result_with_flips(),
        actual_total=0,
        actual_per_matchday={},
        standings=[],
        min_ep_gain=40.0,
        with_flips=True,
    )

    assert "55,256,064" in report
    assert "151" in report
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_replay/test_attribution.py -v -k trading or cash or comparison`
Expected: FAIL with `ImportError: cannot import name 'trading_summary'`

- [ ] **Step 3: Write minimal implementation**

In `rehoboam/replay/attribution.py`:

```python
# The real 2025/26 season, for comparison. Without a reference the replay's own
# P&L is uninterpretable.
REAL_FLIP_PNL = -55_256_064
REAL_FLIP_TRIPS = 151
REAL_FLIP_WIN_RATE = 27.8


def trading_summary(result: SeasonResult) -> tuple[int, int, int]:
    """``(realised_pnl, round_trips, wins)`` over completed round trips."""
    pnl = sum(f.proceeds - f.buy_price for f in result.flips)
    wins = sum(1 for f in result.flips if f.proceeds > f.buy_price)
    return pnl, len(result.flips), wins
```

Add `with_flip_buys: bool = False` to `format_report`'s signature, and insert this block after the `Configuration` section:

```python
if with_flips or with_flip_buys:
    pnl, trips, wins = trading_summary(result)
    rate = (100.0 * wins / trips) if trips else 0.0
    lines += [
        "",
        "Trading (cash - does not enter the points attribution above)",
        "-" * 68,
        f"  {'Realised flip P&L':<34}{'EUR ' + format(pnl, '+,'):>21}",
        f"  {'Round trips completed':<34}{trips:>21,}",
        f"  {'Win rate':<34}{rate:>20.1f}%   ({wins} of {trips})",
        f"  {'Real 2025/26, for comparison':<34}"
        f"{'EUR ' + format(REAL_FLIP_PNL, '+,'):>21}",
        f"  {'':<34}{REAL_FLIP_TRIPS:>21,} trips, {REAL_FLIP_WIN_RATE}%",
    ]
```

Update `FIDELITY_NOTES`'s sell entry so it no longer asserts flips are absent:

```python
("Sell decisions", "medium", "instant sell at MV; profit flips per --with-flips"),
```

Add a footer paragraph mirroring the existing `with_flips` one:

```python
if with_flip_buys:
    lines += [
        "Flip BUYING is modelled: candidates come from the real ProfitTrader,",
        "bid at an economic ceiling rather than by marginal EP gain.",
    ]
else:
    lines += [
        "Flip BUYING is NOT modelled: every buy here is justified by expected",
        "points, while the live bot also buys purely for appreciation.",
    ]
```

Finally, extend the INCOMPLETE guard so a run missing any of the three behaviours is still labelled diagnostic:

```python
if not (with_competition and with_flips and with_flip_buys):
    lines += ["INCOMPLETE - diagnostic only, not a season result."]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_replay/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
uv run black rehoboam/replay/attribution.py tests/test_replay/test_attribution.py
uv run ruff check rehoboam/replay/attribution.py tests/test_replay/test_attribution.py --fix
git add rehoboam/replay/attribution.py tests/test_replay/test_attribution.py
git commit -m "feat(replay): report trading P&L as cash, beside the points table (REH-71)"
```

______________________________________________________________________

### Task 9: Driver and CLI plumbing

**Files:**

- Modify: `rehoboam/replay/driver.py`, `rehoboam/cli.py`
- Test: `tests/test_replay/test_driver.py`

**Interfaces:**

- Consumes: `make_flip_buy_fn`, `day_for_kickoff`, `position_fn`.

- Produces: `run_replay(..., with_flip_buys: bool = False)`; CLI flag `--with-flip-buys` on `replay-season`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_replay/test_driver.py`:

```python
import inspect

from rehoboam.replay import driver


def test_run_replay_accepts_a_flip_buy_switch():
    assert "with_flip_buys" in inspect.signature(driver.run_replay).parameters


def test_run_replay_passes_the_flip_buy_fn_to_the_engine():
    """A switch that never reaches run_season changes nothing -- the exact
    defect REH-66 caught on the gain floor."""
    source = inspect.getsource(driver.run_replay)

    assert "flip_buy_fn=" in source


def test_the_flip_buy_fn_uses_the_same_matchday_resolver_as_the_scorer():
    """Two different cutoff rules in one run would let the flip path see a
    matchday the EP path cannot."""
    source = inspect.getsource(driver.run_replay)

    assert "day_fn=" in source
    assert "day_for_kickoff" in source
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_replay/test_driver.py -v -k flip`
Expected: FAIL — `with_flip_buys` not in signature.

- [ ] **Step 3: Write minimal implementation**

In `rehoboam/replay/driver.py`, add `with_flip_buys: bool = False` to `run_replay`'s keyword arguments, then before the `run_season` call:

```
    flip_buy_fn = None
    if with_flip_buys:
        from rehoboam.replay.flip_buys import make_flip_buy_fn

        flip_buy_fn = make_flip_buy_fn(
            corpus,
            season=SEASON,
            # The SAME resolver the scorer uses, so the flip path and the EP
            # path cannot disagree about which matchday is being predicted.
            day_fn=lambda at: day_for_kickoff(kickoffs, at),
            position_fn=position_fn,
        )
```

Pass `flip_buy_fn=flip_buy_fn` into `run_season`, and `with_flip_buys=with_flip_buys` into `format_report`.

In `rehoboam/cli.py`, add to `replay_season` after the `with_flips` option:

```python
with_flip_buys: bool = (
    typer.Option(
        False,
        "--with-flip-buys",
        help=(
            "Model profit-flip BUYING (REH-71): candidates from the real "
            "ProfitTrader, bid at an economic ceiling. The live bot does this; "
            "--with-flips alone only models the selling half."
        ),
    ),
)
```

and thread `with_flip_buys=with_flip_buys` into the `run_replay` call.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_replay/ -v && uv run rehoboam replay-season --help`
Expected: PASS, and the help text lists `--with-flip-buys`.

- [ ] **Step 5: Commit**

```bash
uv run black rehoboam/replay/driver.py rehoboam/cli.py tests/test_replay/test_driver.py
uv run ruff check rehoboam/replay/driver.py rehoboam/cli.py tests/test_replay/test_driver.py --fix
git add rehoboam/replay/driver.py rehoboam/cli.py tests/test_replay/test_driver.py
git commit -m "feat(replay): --with-flip-buys, wired through the driver (REH-71)"
```

______________________________________________________________________

### Task 10: The 2×2 factorial command

**Files:**

- Modify: `rehoboam/replay/driver.py`, `rehoboam/cli.py`
- Test: `tests/test_replay/test_flip_policy_report.py`

**Interfaces:**

- Consumes: `run_replay`, `trading_summary`.
- Produces: `format_flip_policy(arms: dict[str, SeasonResult], *, actual_total: int) -> str`; `run_flip_policy(*, corpus_path, learning_db_path) -> str`; CLI command `replay-flip-policy`.

Arm keys are exactly `"A"`, `"B"`, `"C"`, `"D"` per the spec table.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_replay/test_flip_policy_report.py`:

```python
"""REH-71: the 2x2 report, and the decision rule fixed in advance.

REH-68 measured a 6,162-point faithfulness swing. Against a ~27,000-point
season that will plausibly swallow every flip delta, so the inconclusive branch
is the LIKELY one and has to be stated before the numbers arrive.
"""

from __future__ import annotations

from rehoboam.replay.attribution import NOISE_FLOOR_POINTS, format_flip_policy
from rehoboam.replay.engine import SeasonResult


def _arms(a: int, b: int, c: int, d: int) -> dict[str, SeasonResult]:
    return {
        key: SeasonResult(total_points=points)
        for key, points in (("A", a), ("B", b), ("C", c), ("D", d))
    }


def test_the_report_names_all_four_arms():
    report = format_flip_policy(
        _arms(27_000, 27_100, 27_050, 27_150), actual_total=26_172
    )

    for label in ("A", "B", "C", "D"):
        assert label in report


def test_small_deltas_are_declared_inconclusive():
    """Every arm within the noise floor of every other."""
    report = format_flip_policy(
        _arms(27_000, 27_100, 27_050, 27_150), actual_total=26_172
    )

    assert "INCONCLUSIVE" in report


def test_a_delta_clearing_the_noise_floor_is_not_inconclusive():
    report = format_flip_policy(
        _arms(27_000, 27_000, 20_000, 20_000), actual_total=26_172
    )

    assert "INCONCLUSIVE" not in report


def test_the_noise_floor_is_the_one_reh_68_measured():
    assert NOISE_FLOOR_POINTS == 6_162
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_replay/test_flip_policy_report.py -v`
Expected: FAIL with `ImportError: cannot import name 'NOISE_FLOOR_POINTS'`

- [ ] **Step 3: Write minimal implementation**

In `rehoboam/replay/attribution.py`:

```python
# REH-68 measured a single faithfulness decision moving the season total by this
# much. Any flip delta smaller than it is modelling noise, not a finding. Fixed
# BEFORE the runs so the result cannot be rationalised after it is seen.
NOISE_FLOOR_POINTS = 6_162

ARM_LABELS = {
    "A": "flip buys off, profit sells off",
    "B": "flip buys off, profit sells ON",
    "C": "flip buys ON,  profit sells off",
    "D": "flip buys ON,  profit sells ON",
}


def format_flip_policy(arms: dict, *, actual_total: int) -> str:
    """The 2x2, its main effects, and the pre-committed decision rule."""
    points = {key: arms[key].total_points for key in ("A", "B", "C", "D")}
    buy_effect = (points["C"] + points["D"]) / 2 - (points["A"] + points["B"]) / 2
    sell_effect = (points["B"] + points["D"]) / 2 - (points["A"] + points["C"]) / 2
    interaction = (points["D"] - points["C"]) - (points["B"] - points["A"])

    lines = [
        "=" * 68,
        "FLIP POLICY 2x2 - REH-71",
        "=" * 68,
        "",
        f"Human actual: {actual_total:>8,}",
        "",
    ]
    for key in ("A", "B", "C", "D"):
        delta = points[key] - actual_total
        lines.append(f"  {key}  {ARM_LABELS[key]:<34}{points[key]:>9,}  ({delta:+,})")

    lines += [
        "",
        "Main effects (points)",
        "-" * 68,
        f"  {'Flip buying':<34}{buy_effect:>+9,.0f}",
        f"  {'Profit selling':<34}{sell_effect:>+9,.0f}",
        f"  {'Interaction':<34}{interaction:>+9,.0f}",
        "",
        f"Noise floor (REH-68): {NOISE_FLOOR_POINTS:,} points",
        "",
    ]

    effects = (abs(buy_effect), abs(sell_effect), abs(interaction))
    if max(effects) < NOISE_FLOOR_POINTS:
        lines += [
            "INCONCLUSIVE on points - every effect is inside the noise floor.",
            "Per the pre-committed rule, the decision falls to the cash evidence:",
            f"real flipping lost EUR {abs(REAL_FLIP_PNL):,} at a "
            f"{REAL_FLIP_WIN_RATE}% win rate over {REAL_FLIP_TRIPS} round trips,",
            "and every round trip pays a measured 11.7% toll. Both switches",
            "default OFF, decided on cash rather than on points.",
        ]
    else:
        lines += [
            "An effect clears the noise floor. Adopt that arm's verdict for its",
            "own switch; the other switch follows the same rule independently.",
        ]
    lines += ["", "A labelled control, not the counterfactual season result.", "=" * 68]
    return "\n".join(lines)
```

In `rehoboam/replay/driver.py`:

```python
def run_flip_policy(*, corpus_path: Path, learning_db_path: Path) -> str:
    """REH-71: the 2x2 over flip buys x profit sells.

    Every arm runs with bid competition on, because the whole question is what
    flipping is worth when rivals contest the same listings. Nothing else varies
    between arms.
    """
    from rehoboam.replay.attribution import format_flip_policy

    arms = {}
    for key, flip_buys, profit_sells in (
        ("A", False, False),
        ("B", False, True),
        ("C", True, False),
        ("D", True, True),
    ):
        arms[key], _report = run_replay(
            corpus_path=corpus_path,
            learning_db_path=learning_db_path,
            with_competition=True,
            with_flips=profit_sells,
            with_flip_buys=flip_buys,
        )

    with sqlite3.connect(learning_db_path) as conn:
        actual_total = conn.execute(
            "SELECT MAX(total_points) FROM league_rank_history WHERE is_self = 1"
        ).fetchone()[0]

    return format_flip_policy(arms, actual_total=int(actual_total or 0))
```

In `rehoboam/cli.py`, add a command modelled exactly on `replay_buy_control`:

```python
@app.command("replay-flip-policy")
def replay_flip_policy(
    corpus: Path = typer.Option(  # noqa: B008
        Path("logs/training_corpus.db"), help="Path to the training corpus DB"
    ),
    learning_db: Path = typer.Option(  # noqa: B008
        Path("logs/bid_learning.db"), help="Path to the learning DB with real standings"
    ),
) -> None:
    """REH-71: 2x2 over flip buys x profit sells, with competition on."""
    from rehoboam.replay.driver import run_flip_policy

    if not corpus.exists():
        console.print(f"[red]Corpus not found: {corpus}[/red]")
        raise typer.Exit(1)
    if not learning_db.exists():
        console.print(f"[red]Learning DB not found: {learning_db}[/red]")
        raise typer.Exit(1)

    console.print(run_flip_policy(corpus_path=corpus, learning_db_path=learning_db))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_replay/ -v && uv run rehoboam replay-flip-policy --help`
Expected: PASS, and the command exists.

- [ ] **Step 5: Commit**

```bash
uv run black rehoboam/replay/attribution.py rehoboam/replay/driver.py rehoboam/cli.py tests/test_replay/test_flip_policy_report.py
uv run ruff check rehoboam/replay/attribution.py rehoboam/replay/driver.py rehoboam/cli.py tests/test_replay/test_flip_policy_report.py --fix
git add rehoboam/replay/attribution.py rehoboam/replay/driver.py rehoboam/cli.py tests/test_replay/test_flip_policy_report.py
git commit -m "feat(replay): replay-flip-policy runs the 2x2 and applies the rule (REH-71)"
```

______________________________________________________________________

### Task 11: The `Settings` switches

**Files:**

- Modify: `rehoboam/config.py`, `rehoboam/auto_trader.py`, `.env.example`
- Test: `tests/test_replay/test_shipped_config.py`, `tests/test_auto_trader_flip_switches.py`

**Interfaces:**

- Produces: `Settings.enable_flip_buys: bool`, `Settings.enable_profit_sells: bool`.

Defaults are `False` per §1's inconclusive branch, revised in Task 12 only if an effect clears the noise floor.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_auto_trader_flip_switches.py`:

```python
"""REH-71: the flip verdict must be honoured from .env, without a deploy."""

from __future__ import annotations

import inspect

from rehoboam.auto_trader import AutoTrader
from rehoboam.config import Settings


def test_both_switches_exist():
    assert "enable_flip_buys" in Settings.model_fields
    assert "enable_profit_sells" in Settings.model_fields


def test_the_flip_buy_block_is_gated_on_its_switch():
    source = inspect.getsource(AutoTrader.run_unified_trade_phase)

    assert "enable_flip_buys" in source


def test_the_profit_sell_phase_is_gated_on_its_switch():
    source = inspect.getsource(AutoTrader.run_profit_sell_phase)

    assert "enable_profit_sells" in source
```

Both method names are verified: `run_unified_trade_phase` (`auto_trader.py:250`) encloses the flip-candidate block at line 344, and `run_profit_sell_phase` begins at line 751.

Append to `tests/test_replay/test_shipped_config.py`:

```python
def test_the_flip_switches_default_off():
    """REH-71's pre-committed rule: absent a points effect clearing the 6,162
    noise floor, the decision falls to cash evidence and both switches are off.
    """
    assert Settings.model_fields["enable_flip_buys"].default is False
    assert Settings.model_fields["enable_profit_sells"].default is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_auto_trader_flip_switches.py -v`
Expected: FAIL — fields absent.

- [ ] **Step 3: Write minimal implementation**

In `rehoboam/config.py`, after `max_loss_pct`:

```
    # REH-71. Real flipping lost EUR 55,256,064 over 151 round trips at a 27.8%
    # win rate, and every round trip pays a measured 11.7% toll. Split in two
    # because buying for appreciation and taking profit on the squad are
    # distinct behaviours that need not share a verdict.
    enable_flip_buys: bool = Field(
        default=False,
        description="Buy players for expected appreciation rather than expected points",
    )
    enable_profit_sells: bool = Field(
        default=False,
        description="Take profit / cut losses on squad players against their cost basis",
    )
```

In `rehoboam/auto_trader.py`, gate the flip-candidate block (`auto_trader.py:344`):

```
        if (
            self.settings.enable_flip_buys
            and ctx.matchday_phase.allow_flips
            and available_slots > 0
        ):
```

and return early from `run_profit_sell_phase`:

```
if not self.settings.enable_profit_sells:
    console.print("[dim]Profit selling disabled (REH-71)[/dim]")
    return []
```

Add both to `.env.example` with a one-line comment each.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -v`
Expected: PASS across the whole suite. Investigate any auto_trader test that assumed flips were on — update it to set the switch explicitly rather than weakening the assertion.

- [ ] **Step 5: Commit**

```bash
uv run black rehoboam/config.py rehoboam/auto_trader.py tests/test_auto_trader_flip_switches.py tests/test_replay/test_shipped_config.py
uv run ruff check rehoboam/config.py rehoboam/auto_trader.py tests/test_auto_trader_flip_switches.py tests/test_replay/test_shipped_config.py --fix
git add rehoboam/config.py rehoboam/auto_trader.py .env.example tests/test_auto_trader_flip_switches.py tests/test_replay/test_shipped_config.py
git commit -m "feat(config): enable_flip_buys / enable_profit_sells switches (REH-71)"
```

______________________________________________________________________

### Task 12: Run the 2×2 and record the verdict

Not a TDD task — this is the measurement the whole ticket exists for. Run once. Do not tune and re-run.

**Files:**

- Create: `docs/superpowers/specs/2026-08-05-reh-71-flip-policy-results.md`

- Modify: `rehoboam/config.py` (only if an effect clears the noise floor)

- [ ] **Step 1: Record the pre-run integrity baseline**

```bash
shasum -a 256 logs/training_corpus.db logs/bid_learning.db rehoboam/scoring/v2/coefficients.json | tee /tmp/reh71-before.txt
```

- [ ] **Step 2: Confirm determinism before trusting any number**

```bash
uv run rehoboam replay-season --with-competition --with-flips --with-flip-buys > /tmp/reh71-det-1.txt
uv run rehoboam replay-season --with-competition --with-flips --with-flip-buys > /tmp/reh71-det-2.txt
diff /tmp/reh71-det-1.txt /tmp/reh71-det-2.txt && echo "DETERMINISTIC"
```

Expected: `DETERMINISTIC`. If the files differ, STOP — a nondeterministic harness cannot support any verdict. Find the source (dict ordering, unsorted SQL, floating-point accumulation order) and fix it before continuing.

- [ ] **Step 3: Run the factorial once**

```bash
uv run rehoboam replay-flip-policy | tee /tmp/reh71-factorial.txt
```

- [ ] **Step 4: Verify nothing was mutated**

```bash
shasum -a 256 logs/training_corpus.db logs/bid_learning.db rehoboam/scoring/v2/coefficients.json > /tmp/reh71-after.txt
diff /tmp/reh71-before.txt /tmp/reh71-after.txt && echo "READ-ONLY CONFIRMED"
```

Expected: `READ-ONLY CONFIRMED`.

- [ ] **Step 5: Write the results document**

Create `docs/superpowers/specs/2026-08-05-reh-71-flip-policy-results.md` containing, verbatim: the four arm totals, the three main effects, the per-arm trading P&L / round trips / win rate, the tool's own INCONCLUSIVE verdict or the effect that cleared the floor, and the resulting switch values. State plainly which evidence decided it — points or cash. Do not editorialise the number upward.

- [ ] **Step 6: Set the defaults if — and only if — an effect cleared the floor**

If `INCONCLUSIVE`, the `False` defaults from Task 11 already encode the verdict; change nothing. Otherwise update the relevant `Settings` default and the assertion in `test_the_flip_switches_default_off`, renaming that test to match what it now asserts.

- [ ] **Step 7: Commit**

```bash
git add docs/superpowers/specs/2026-08-05-reh-71-flip-policy-results.md rehoboam/config.py tests/test_replay/test_shipped_config.py
git commit -m "docs(replay): the 2x2 flip-policy result and the verdict it supports (REH-71)"
```

- [ ] **Step 8: Full suite and lint before opening the PR**

```bash
uv run pytest
uv run ruff check rehoboam/ tests/
uv run mypy rehoboam/ --ignore-missing-imports
```

Expected: green. Then open the PR against `main` referencing REH-71, quoting the factorial table in the body.

______________________________________________________________________

## Notes for the implementer

- **`_would_create_dead_weight` duck-types.** It is annotated `MarketPlayer` but reads only `.position` (`decision.py:803`), so a `ReplayPlayer` works. Do not build a real `MarketPlayer` to satisfy the annotation.
- **`min_ep_gain=1_000_000.0` is the idiom** for silencing the EP buy pass in a test that wants to observe the flip pass alone.
- **Do not add flips to `attribution_rows`.** The whole point of the cash block is that euros never enter a points column.
- **`INSTANT_SELL_PCT` is 1.0, not 0.95.** REH-67 measured it. If you see 0.95 anywhere, that is a bug, not a convention.
