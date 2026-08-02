# REH-51 Full-Bot Season Replay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replay the entire bot across the 2025/26 season — buys, sells, budget, lineups — from the verified 2025-08-08 starting state, and report where it would have finished against the real 14-manager table.

**Architecture:** A new `rehoboam/replay/` package. `market.py` reconstructs what was buyable each day from real `player_transfers` records. `state.py` holds squad + budget and reconstructs the season-opening assignment. `rules.py` enforces Kickbase legality. `engine.py` runs the matchday loop (score → sell → buy → field 11 → award real points). `attribution.py` decomposes the result. All scoring is truncated to pre-matchday data via the existing `matches_before` leak boundary.

**Tech Stack:** Python 3.12, stdlib only, SQLite via `TrainingCorpus`, existing `select_best_eleven` / `score_player_v2` / `matches_before`.

## Global Constraints

- **No new runtime dependencies.** Standard library only.
- **`rehoboam/scoring/v2/coefficients.json` must be untouched.** Verify `git diff --quiet` on it before every commit.
- **TDD required.** Write the failing test, run it, watch it fail, then implement.
- **Never commit to `main`.** Work on branch `feat/reh-51-season-replay`.
- **Never run `black` on a whole file** — the repo is not black-clean and it causes massive collateral churn.
- **This is a verdict instrument, not a tuning loop.** Do not adjust thresholds, weights, or coefficients to improve the replay's output. If a result looks bad, report it.
- **Leak discipline:** every read of match data, market value, or market inventory at matchday N must be restricted to strictly before matchday N's kickoff. Task 4 has a dedicated cheat test that must fail if this is violated.
- All monetary values in output are exact euros with thousands separators (e.g. `EUR 80,204,837`), never abbreviated.

## Verified Starting State (do not re-derive)

| fact                            | value                                 |
| ------------------------------- | ------------------------------------- |
| league id                       | `1933872`                             |
| our manager id                  | `3616202` (`Brownie`)                 |
| league created / squad assigned | `2025-08-08T14:05:47Z`                |
| assigned squad                  | 12 players, total MV `EUR 80,204,837` |
| chosen starting budget          | `EUR 80,000,000`                      |
| season                          | `2025/2026`, 34 matchdays             |
| our real final total            | `26,170` points (10th of 14)          |
| league winner's total           | `37,857` points                       |

The assigned squad is the set of `player_transfers` rows with `transfer_type = 0`
and `counterparty_id = '3616202'` whose `transfer_at` falls on 2025-08-08.

## Kickbase Rules the Replay Must Enforce

- Max **15** players in a squad.
- Max **3** players from the same Bundesliga team.
- Budget may go **negative during the week**, with a credit line of **70% of current team value**.
- Budget must be **>= 0 at kickoff** or the entire matchday scores **zero**.
- Each empty lineup slot costs **-100** points.
- Starting 11 needs at least 1 GK, 3 DEF, 2 MID, 1 FWD (`POSITION_MINIMUMS`).
- No captain mechanic — all 11 starters score equally.

## Fidelity — must appear in the report output

| component         | fidelity                | basis                                                                                              |
| ----------------- | ----------------------- | -------------------------------------------------------------------------------------------------- |
| Points scoring    | **exact**               | real per-match points from the corpus                                                              |
| Penalty avoidance | **exact**               | deterministic                                                                                      |
| Lineup selection  | **high**                | real squad, real formation rules                                                                   |
| Sell decisions    | **high**                | instant sell = 95% of MV, no inventory needed                                                      |
| Buy prices        | **high**                | real transaction prices from `player_transfers`                                                    |
| Buy availability  | **medium**              | only the ~74/matchday players who actually traded are visible; players nobody traded are invisible |
| Bid competition   | **absent — optimistic** | if the bot wants a traded player it gets them; in reality it could have been outbid                |

## File Structure

- `rehoboam/replay/__init__.py` — package marker
- `rehoboam/replay/market.py` — `MarketListing`, `ReplayMarket` (what was buyable when, at what price)
- `rehoboam/replay/state.py` — `ReplayPlayer`, `ReplayState`, `initial_state`
- `rehoboam/replay/rules.py` — squad legality and budget constraints
- `rehoboam/replay/engine.py` — the matchday loop
- `rehoboam/replay/attribution.py` — decompose simulated vs actual
- `rehoboam/replay/driver.py` — composition root wiring corpus + models + CLI
- Modify `rehoboam/enrichment/corpus.py` — add `transfers_between`, `market_value_at`, `team_ids_for`
- Modify `rehoboam/cli.py` — add `replay-season` command

______________________________________________________________________

### Task 1: Corpus readers + market reconstruction

**Files:**

- Modify: `rehoboam/enrichment/corpus.py`
- Create: `rehoboam/replay/__init__.py`, `rehoboam/replay/market.py`
- Test: `tests/test_replay/__init__.py`, `tests/test_replay/test_market.py`, `tests/test_corpus_replay_readers.py`

**Interfaces:**

- Consumes: `TrainingCorpus(db_path)` from `rehoboam/enrichment/corpus.py`

- Produces:

  - `TrainingCorpus.transfers_between(lo: float, hi: float, *, transfer_type: int = 2) -> list[dict]`
  - `TrainingCorpus.market_value_at(player_id: str, at: float) -> int | None`
  - `TrainingCorpus.team_ids_for(player_ids: list[str]) -> dict[str, str]`
  - `MarketListing(player_id: str, price: int, transfer_at: float)` (frozen dataclass)
  - `ReplayMarket(corpus: TrainingCorpus, *, window_days: int = 7)` with
    `available_before(at: float) -> list[MarketListing]`

- [ ] **Step 1: Write the failing corpus-reader tests**

```python
# tests/test_corpus_replay_readers.py
import sqlite3
import pytest
from rehoboam.enrichment.corpus import TrainingCorpus


@pytest.fixture
def corpus(tmp_path):
    c = TrainingCorpus(tmp_path / "c.db")
    with sqlite3.connect(c.db_path) as conn:
        conn.executemany(
            "INSERT INTO player_transfers (player_id, transfer_at, price, transfer_type,"
            " counterparty_id, counterparty_name) VALUES (?,?,?,?,?,?)",
            [
                ("1", 100.0, 5_000_000, 2, "m1", "Alice"),
                ("2", 200.0, 7_000_000, 2, "m2", "Bob"),
                ("3", 300.0, 0, 0, "m1", "Alice"),
                ("4", 250.0, 9_000_000, 2, None, None),
            ],
        )
        conn.executemany(
            "INSERT INTO mv_series (player_id, snapshot_at, market_value) VALUES (?,?,?)",
            [("1", 50.0, 4_000_000), ("1", 150.0, 6_000_000), ("2", 50.0, 8_000_000)],
        )
        conn.executemany(
            "INSERT INTO player_universe (player_id, position, team_id) VALUES (?,?,?)",
            [("1", "Forward", "40"), ("2", "Defender", "7")],
        )
    return c


def test_transfers_between_filters_by_type_and_window(corpus):
    rows = corpus.transfers_between(150.0, 260.0)
    assert [r["player_id"] for r in rows] == ["2", "4"]


def test_transfers_between_excludes_assignments(corpus):
    assert all(r["transfer_type"] == 2 for r in corpus.transfers_between(0.0, 1000.0))


def test_market_value_at_takes_latest_before_cutoff(corpus):
    assert corpus.market_value_at("1", 149.0) == 4_000_000
    assert corpus.market_value_at("1", 151.0) == 6_000_000


def test_market_value_at_returns_none_when_no_history_before_cutoff(corpus):
    assert corpus.market_value_at("1", 10.0) is None
    assert corpus.market_value_at("999", 1000.0) is None


def test_team_ids_for_returns_mapping(corpus):
    assert corpus.team_ids_for(["1", "2", "999"]) == {"1": "40", "2": "7"}
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_corpus_replay_readers.py -v`
Expected: FAIL — `AttributeError: 'TrainingCorpus' object has no attribute 'transfers_between'`

- [ ] **Step 3: Implement the readers**

Append these methods to the `TrainingCorpus` class in `rehoboam/enrichment/corpus.py`, following the existing `transfers_for_player` style:

```python
def transfers_between(
    self, lo: float, hi: float, *, transfer_type: int = 2
) -> list[dict[str, Any]]:
    """Transactions with ``lo <= transfer_at <= hi``, oldest first.

    Defaults to ``transfer_type=2`` — the only type carrying a real price.
    Types 0 (season assignment) and 3 (release to pool) always have price 0.
    """
    with sqlite3.connect(self.db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
                SELECT player_id, transfer_at, price, transfer_type,
                       counterparty_id, counterparty_name
                FROM player_transfers
                WHERE transfer_type = ? AND transfer_at >= ? AND transfer_at <= ?
                ORDER BY transfer_at
                """,
            (transfer_type, lo, hi),
        ).fetchall()
    return [dict(r) for r in rows]


def market_value_at(self, player_id: str, at: float) -> int | None:
    """Most recent market value at or before ``at``. None if no such snapshot."""
    with sqlite3.connect(self.db_path) as conn:
        row = conn.execute(
            "SELECT market_value FROM mv_series WHERE player_id = ? "
            "AND snapshot_at <= ? ORDER BY snapshot_at DESC LIMIT 1",
            (str(player_id), at),
        ).fetchone()
    return int(row[0]) if row else None


def team_ids_for(self, player_ids: list[str]) -> dict[str, str]:
    """Map player_id -> team_id for players that have one recorded."""
    if not player_ids:
        return {}
    placeholders = ",".join("?" * len(player_ids))
    with sqlite3.connect(self.db_path) as conn:
        rows = conn.execute(
            f"SELECT player_id, team_id FROM player_universe "  # noqa: S608
            f"WHERE player_id IN ({placeholders}) AND team_id IS NOT NULL",
            [str(p) for p in player_ids],
        ).fetchall()
    return {str(r[0]): str(r[1]) for r in rows}
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_corpus_replay_readers.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Write the failing market tests**

```python
# tests/test_replay/test_market.py
import sqlite3
import pytest
from rehoboam.enrichment.corpus import TrainingCorpus
from rehoboam.replay.market import MarketListing, ReplayMarket

DAY = 86400.0


@pytest.fixture
def market(tmp_path):
    c = TrainingCorpus(tmp_path / "c.db")
    with sqlite3.connect(c.db_path) as conn:
        conn.executemany(
            "INSERT INTO player_transfers (player_id, transfer_at, price, transfer_type,"
            " counterparty_id, counterparty_name) VALUES (?,?,?,?,?,?)",
            [
                ("old", 0.0, 1_000_000, 2, "m", "M"),  # 10 days before cutoff
                ("fresh", 8 * DAY, 5_000_000, 2, "m", "M"),  # 2 days before cutoff
                ("edge", 3 * DAY, 3_000_000, 2, "m", "M"),  # exactly 7 days before
                ("future", 11 * DAY, 9_000_000, 2, "m", "M"),  # after cutoff
                ("assigned", 8 * DAY, 0, 0, "m", "M"),  # not a real sale
            ],
        )
    return ReplayMarket(c)


def test_available_before_includes_only_the_trailing_window(market):
    ids = {lst.player_id for lst in market.available_before(10 * DAY)}
    assert ids == {"fresh", "edge"}


def test_available_before_excludes_future_transactions(market):
    assert "future" not in {lst.player_id for lst in market.available_before(10 * DAY)}


def test_available_before_excludes_non_sale_types(market):
    assert "assigned" not in {
        lst.player_id for lst in market.available_before(10 * DAY)
    }


def test_listing_carries_the_real_transaction_price(market):
    listing = next(
        x for x in market.available_before(10 * DAY) if x.player_id == "fresh"
    )
    assert listing == MarketListing(
        player_id="fresh", price=5_000_000, transfer_at=8 * DAY
    )


def test_most_recent_price_wins_for_repeat_transactions(tmp_path):
    c = TrainingCorpus(tmp_path / "d.db")
    with sqlite3.connect(c.db_path) as conn:
        conn.executemany(
            "INSERT INTO player_transfers (player_id, transfer_at, price, transfer_type,"
            " counterparty_id, counterparty_name) VALUES (?,?,?,?,?,?)",
            [
                ("p", 8 * DAY, 4_000_000, 2, "m", "M"),
                ("p", 9 * DAY, 6_000_000, 2, "m", "M"),
            ],
        )
    listings = ReplayMarket(c).available_before(10 * DAY)
    assert len(listings) == 1
    assert listings[0].price == 6_000_000
```

- [ ] **Step 6: Run to verify failure**

Run: `uv run pytest tests/test_replay/test_market.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rehoboam.replay'`

- [ ] **Step 7: Implement the market**

Create `rehoboam/replay/__init__.py` as an empty file, then `rehoboam/replay/market.py`:

```python
"""Reconstructed market inventory for the season replay.

A player is treated as buyable at time T if a real manager-to-manager
transaction (``transfer_type = 2``) for that player settled within the
trailing window ending at T, at that transaction's actual price.

This is a *lower bound* on what was really available — players nobody traded
are invisible to the replay — but every price in it is a price someone really
paid. It models no bid competition: if the bot wants a listed player it gets
them, which is optimistic and must be reported as such.
"""

from __future__ import annotations

from dataclasses import dataclass

from rehoboam.enrichment.corpus import TrainingCorpus

SECONDS_PER_DAY = 86400.0


@dataclass(frozen=True)
class MarketListing:
    """A player who was buyable, and what they really cost."""

    player_id: str
    price: int
    transfer_at: float


class ReplayMarket:
    """What was on the market, reconstructed from real transactions."""

    def __init__(self, corpus: TrainingCorpus, *, window_days: int = 7) -> None:
        self.corpus = corpus
        self.window_days = window_days

    def available_before(self, at: float) -> list[MarketListing]:
        """Listings visible at ``at``, most recent price per player.

        Strictly excludes transactions at or after ``at`` — this is a leak
        boundary, not a convenience filter.
        """
        lo = at - self.window_days * SECONDS_PER_DAY
        rows = self.corpus.transfers_between(lo, at)
        latest: dict[str, MarketListing] = {}
        for row in rows:
            if row["transfer_at"] >= at:
                continue
            pid = str(row["player_id"])
            listing = MarketListing(
                player_id=pid,
                price=int(row["price"]),
                transfer_at=float(row["transfer_at"]),
            )
            existing = latest.get(pid)
            if existing is None or listing.transfer_at > existing.transfer_at:
                latest[pid] = listing
        return sorted(latest.values(), key=lambda x: x.player_id)
```

- [ ] **Step 8: Run to verify pass**

Run: `uv run pytest tests/test_replay/ tests/test_corpus_replay_readers.py -v`
Expected: PASS (10 tests)

- [ ] **Step 9: Commit**

```bash
git add rehoboam/enrichment/corpus.py rehoboam/replay/ tests/test_replay/ tests/test_corpus_replay_readers.py
git commit -m "feat(replay): corpus readers and market reconstruction (REH-51)"
```

______________________________________________________________________

### Task 2: Replay state and season-opening reconstruction

**Files:**

- Create: `rehoboam/replay/state.py`
- Test: `tests/test_replay/test_state.py`

**Interfaces:**

- Consumes: `TrainingCorpus` readers from Task 1

- Produces:

  - `ReplayPlayer(id: str, position: str, team_id: str | None)` — frozen dataclass with `.id` and `.position`, the two attributes `select_best_eleven` requires
  - `ReplayState(budget: int, squad: dict[str, ReplayPlayer])` with `squad_size`, `player_ids`, `buy(player, price)`, `sell(player_id, proceeds)`, `team_counts()`
  - `initial_state(corpus, *, manager_id: str, assigned_on: float, starting_budget: int) -> ReplayState`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_replay/test_state.py
import sqlite3
import pytest
from rehoboam.enrichment.corpus import TrainingCorpus
from rehoboam.replay.state import ReplayPlayer, ReplayState, initial_state

ASSIGNED_AT = 1_754_661_947.0  # 2025-08-08T14:05:47Z


def _player(pid, pos="Forward", team="40"):
    return ReplayPlayer(id=pid, position=pos, team_id=team)


def test_buy_debits_budget_and_adds_player():
    s = ReplayState(budget=10_000_000, squad={})
    s.buy(_player("1"), 4_000_000)
    assert s.budget == 6_000_000
    assert s.player_ids == ["1"]


def test_sell_credits_budget_and_removes_player():
    s = ReplayState(budget=0, squad={"1": _player("1")})
    s.sell("1", 3_000_000)
    assert s.budget == 3_000_000
    assert s.player_ids == []


def test_sell_unknown_player_raises():
    s = ReplayState(budget=0, squad={})
    with pytest.raises(KeyError):
        s.sell("nope", 1)


def test_budget_may_go_negative_on_buy():
    s = ReplayState(budget=1_000_000, squad={})
    s.buy(_player("1"), 5_000_000)
    assert s.budget == -4_000_000


def test_team_counts_groups_by_team():
    s = ReplayState(
        budget=0,
        squad={
            "1": _player("1", team="40"),
            "2": _player("2", team="40"),
            "3": _player("3", team="7"),
        },
    )
    assert s.team_counts() == {"40": 2, "7": 1}


def test_initial_state_reconstructs_the_assigned_squad(tmp_path):
    c = TrainingCorpus(tmp_path / "c.db")
    with sqlite3.connect(c.db_path) as conn:
        conn.executemany(
            "INSERT INTO player_transfers (player_id, transfer_at, price, transfer_type,"
            " counterparty_id, counterparty_name) VALUES (?,?,?,?,?,?)",
            [
                ("1", ASSIGNED_AT, 0, 0, "3616202", "Brownie"),
                ("2", ASSIGNED_AT, 0, 0, "3616202", "Brownie"),
                ("3", ASSIGNED_AT, 0, 0, "9999", "Someone"),  # another manager
                (
                    "4",
                    ASSIGNED_AT - 400 * 86400,
                    0,
                    0,
                    "3616202",
                    "Brownie",
                ),  # prior season
            ],
        )
        conn.executemany(
            "INSERT INTO player_universe (player_id, position, team_id) VALUES (?,?,?)",
            [("1", "Forward", "40"), ("2", "Defender", "7")],
        )
    state = initial_state(
        c, manager_id="3616202", assigned_on=ASSIGNED_AT, starting_budget=80_000_000
    )
    assert sorted(state.player_ids) == ["1", "2"]
    assert state.budget == 80_000_000
    assert state.squad["1"].position == "Forward"
    assert state.squad["1"].team_id == "40"


def test_initial_state_ignores_prior_season_assignments(tmp_path):
    c = TrainingCorpus(tmp_path / "d.db")
    with sqlite3.connect(c.db_path) as conn:
        conn.execute(
            "INSERT INTO player_transfers (player_id, transfer_at, price, transfer_type,"
            " counterparty_id, counterparty_name) VALUES (?,?,?,?,?,?)",
            ("old", ASSIGNED_AT - 365 * 86400, 0, 0, "3616202", "Brownie"),
        )
    state = initial_state(
        c, manager_id="3616202", assigned_on=ASSIGNED_AT, starting_budget=80_000_000
    )
    assert state.player_ids == []
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_replay/test_state.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rehoboam.replay.state'`

- [ ] **Step 3: Implement state**

```python
"""Squad and budget state through a replayed season."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from rehoboam.enrichment.corpus import TrainingCorpus

# Assignments are recorded within seconds of league creation; a one-day window
# separates this season's assignment from prior seasons' in the same table.
ASSIGNMENT_WINDOW_SECONDS = 86400.0


@dataclass(frozen=True)
class ReplayPlayer:
    """A squad member. ``id`` and ``position`` satisfy ``select_best_eleven``."""

    id: str
    position: str
    team_id: str | None = None


@dataclass
class ReplayState:
    """Mutable squad + budget. Budget may go negative between kickoffs."""

    budget: int
    squad: dict[str, ReplayPlayer] = field(default_factory=dict)

    @property
    def squad_size(self) -> int:
        return len(self.squad)

    @property
    def player_ids(self) -> list[str]:
        return list(self.squad)

    @property
    def players(self) -> list[ReplayPlayer]:
        return list(self.squad.values())

    def buy(self, player: ReplayPlayer, price: int) -> None:
        self.squad[player.id] = player
        self.budget -= int(price)

    def sell(self, player_id: str, proceeds: int) -> None:
        del self.squad[player_id]
        self.budget += int(proceeds)

    def team_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for p in self.squad.values():
            if p.team_id:
                counts[p.team_id] = counts.get(p.team_id, 0) + 1
        return counts


def initial_state(
    corpus: TrainingCorpus,
    *,
    manager_id: str,
    assigned_on: float,
    starting_budget: int,
) -> ReplayState:
    """The squad Kickbase randomly assigned at season start, plus chosen budget.

    Assignments are ``transfer_type = 0`` rows naming this manager. The table
    spans multiple seasons, so only rows within one day of ``assigned_on``
    count as this season's assignment.
    """
    lo = assigned_on - ASSIGNMENT_WINDOW_SECONDS
    hi = assigned_on + ASSIGNMENT_WINDOW_SECONDS
    with sqlite3.connect(corpus.db_path) as conn:
        rows = conn.execute(
            "SELECT player_id FROM player_transfers WHERE transfer_type = 0 "
            "AND counterparty_id = ? AND transfer_at >= ? AND transfer_at <= ?",
            (str(manager_id), lo, hi),
        ).fetchall()
    pids = [str(r[0]) for r in rows]
    positions = corpus.positions_for(pids)
    teams = corpus.team_ids_for(pids)
    squad = {
        pid: ReplayPlayer(id=pid, position=positions[pid], team_id=teams.get(pid))
        for pid in pids
        if pid in positions
    }
    return ReplayState(budget=int(starting_budget), squad=squad)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_replay/test_state.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add rehoboam/replay/state.py tests/test_replay/test_state.py
git commit -m "feat(replay): squad state and season-opening reconstruction (REH-51)"
```

______________________________________________________________________

### Task 3: Kickbase legality rules

**Files:**

- Create: `rehoboam/replay/rules.py`
- Test: `tests/test_replay/test_rules.py`

**Interfaces:**

- Consumes: `ReplayState`, `ReplayPlayer` from Task 2; `POSITION_MINIMUMS` from `rehoboam.config`

- Produces:

  - `MAX_SQUAD_SIZE = 15`, `MAX_PER_TEAM = 3`, `CREDIT_LINE_PCT = 0.70`, `EMPTY_SLOT_PENALTY = -100`, `STARTING_ELEVEN = 11`
  - `can_buy(state, player, price, *, team_value) -> tuple[bool, str]` — `(allowed, reason)`; reason is `""` when allowed. `team_value` is keyword-only.
  - `can_field_eleven(state) -> bool`
  - `empty_slot_penalty(chosen_count: int) -> int`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_replay/test_rules.py
from rehoboam.replay.rules import (
    EMPTY_SLOT_PENALTY,
    MAX_PER_TEAM,
    MAX_SQUAD_SIZE,
    can_buy,
    can_field_eleven,
    empty_slot_penalty,
)
from rehoboam.replay.state import ReplayPlayer, ReplayState


def _p(pid, pos="Forward", team="40"):
    return ReplayPlayer(id=pid, position=pos, team_id=team)


def _squad(spec):
    return {pid: _p(pid, pos, team) for pid, pos, team in spec}


def test_buy_allowed_within_all_limits():
    state = ReplayState(budget=10_000_000, squad={})
    assert can_buy(state, _p("x"), 5_000_000, team_value=50_000_000) == (True, "")


def test_buy_blocked_at_squad_cap():
    squad = _squad([(str(i), "Forward", str(i)) for i in range(MAX_SQUAD_SIZE)])
    state = ReplayState(budget=100_000_000, squad=squad)
    allowed, reason = can_buy(
        state, _p("new", team="99"), 1_000_000, team_value=50_000_000
    )
    assert allowed is False
    assert "squad full" in reason


def test_buy_blocked_at_team_limit():
    squad = _squad([(str(i), "Forward", "40") for i in range(MAX_PER_TEAM)])
    state = ReplayState(budget=100_000_000, squad=squad)
    allowed, reason = can_buy(
        state, _p("new", team="40"), 1_000_000, team_value=50_000_000
    )
    assert allowed is False
    assert "team limit" in reason


def test_buy_allowed_for_different_team_at_team_limit():
    squad = _squad([(str(i), "Forward", "40") for i in range(MAX_PER_TEAM)])
    state = ReplayState(budget=100_000_000, squad=squad)
    assert (
        can_buy(state, _p("new", team="7"), 1_000_000, team_value=50_000_000)[0] is True
    )


def test_buy_allowed_into_negative_budget_within_credit_line():
    # credit line = 70% of 50,000,000 = 35,000,000
    state = ReplayState(budget=0, squad={})
    assert can_buy(state, _p("x"), 30_000_000, team_value=50_000_000)[0] is True


def test_buy_blocked_beyond_credit_line():
    state = ReplayState(budget=0, squad={})
    allowed, reason = can_buy(state, _p("x"), 40_000_000, team_value=50_000_000)
    assert allowed is False
    assert "credit line" in reason


def test_credit_line_boundary_is_inclusive():
    state = ReplayState(budget=0, squad={})
    assert can_buy(state, _p("x"), 35_000_000, team_value=50_000_000)[0] is True


def test_can_field_eleven_requires_position_minimums():
    ok = _squad(
        [("g", "Goalkeeper", "1")]
        + [(f"d{i}", "Defender", str(i)) for i in range(3)]
        + [(f"m{i}", "Midfielder", str(i + 10)) for i in range(2)]
        + [("f", "Forward", "20")]
        + [(f"x{i}", "Midfielder", str(i + 30)) for i in range(4)]
    )
    assert can_field_eleven(ReplayState(budget=0, squad=ok)) is True


def test_can_field_eleven_false_without_goalkeeper():
    squad = _squad([(f"m{i}", "Midfielder", str(i)) for i in range(11)])
    assert can_field_eleven(ReplayState(budget=0, squad=squad)) is False


def test_can_field_eleven_false_with_too_few_players():
    squad = _squad([(f"m{i}", "Midfielder", str(i)) for i in range(10)])
    assert can_field_eleven(ReplayState(budget=0, squad=squad)) is False


def test_empty_slot_penalty_scales_with_missing_slots():
    assert empty_slot_penalty(11) == 0
    assert empty_slot_penalty(10) == EMPTY_SLOT_PENALTY
    assert empty_slot_penalty(9) == 2 * EMPTY_SLOT_PENALTY
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_replay/test_rules.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rehoboam.replay.rules'`

- [ ] **Step 3: Implement rules**

```python
"""Kickbase legality constraints the replay must respect.

Budget may go negative between kickoffs — the binding constraint is a credit
line of 70% of current team value, and a non-negative balance at kickoff.
A negative balance at kickoff zeroes the entire matchday.
"""

from __future__ import annotations

from rehoboam.config import POSITION_MINIMUMS
from rehoboam.replay.state import ReplayPlayer, ReplayState

MAX_SQUAD_SIZE = 15
MAX_PER_TEAM = 3
CREDIT_LINE_PCT = 0.70
EMPTY_SLOT_PENALTY = -100
STARTING_ELEVEN = 11


def can_buy(
    state: ReplayState, player: ReplayPlayer, price: int, *, team_value: int
) -> tuple[bool, str]:
    """Whether this purchase is legal. Returns ``(allowed, reason)``."""
    if state.squad_size >= MAX_SQUAD_SIZE:
        return False, f"squad full ({MAX_SQUAD_SIZE})"
    if player.team_id and state.team_counts().get(player.team_id, 0) >= MAX_PER_TEAM:
        return False, f"team limit ({MAX_PER_TEAM} from team {player.team_id})"
    floor = -int(team_value * CREDIT_LINE_PCT)
    if state.budget - int(price) < floor:
        return False, f"credit line (floor EUR {floor:,})"
    return True, ""


def can_field_eleven(state: ReplayState) -> bool:
    """Whether the squad can legally fill all 11 starting slots."""
    if state.squad_size < STARTING_ELEVEN:
        return False
    counts: dict[str, int] = {}
    for p in state.players:
        counts[p.position] = counts.get(p.position, 0) + 1
    return all(counts.get(pos, 0) >= need for pos, need in POSITION_MINIMUMS.items())


def empty_slot_penalty(chosen_count: int) -> int:
    """Penalty points for unfilled starting slots."""
    return max(0, STARTING_ELEVEN - chosen_count) * EMPTY_SLOT_PENALTY
```

Note: `can_buy` takes `team_value` as a keyword-only argument; the tests above
call it positionally as `can_buy(state, player, price, team_value=...)`, which
matches.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_replay/test_rules.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add rehoboam/replay/rules.py tests/test_replay/test_rules.py
git commit -m "feat(replay): Kickbase legality rules (REH-51)"
```

______________________________________________________________________

### Task 4: The matchday engine

**Files:**

- Create: `rehoboam/replay/engine.py`
- Test: `tests/test_replay/test_engine.py`

**Interfaces:**

- Consumes: `ReplayMarket`/`MarketListing` (Task 1), `ReplayState`/`ReplayPlayer` (Task 2), `rules` (Task 3), `select_best_eleven` from `rehoboam.formation`
- Produces:
  - `Matchday(day_number: int, kickoff: float, points: dict[str, float])` — frozen dataclass
  - `MatchdayOutcome(day_number, points_scored, lineup_ids, penalty, budget_at_kickoff, zeroed, squad_size, buys, sells)` — frozen dataclass
  - `SeasonResult(outcomes: list[MatchdayOutcome], total_points: int, final_budget: int)`
  - `run_season(*, state, market, matchdays, score_fn, mv_fn, position_fn, team_fn, min_ep_gain=5.0) -> SeasonResult`
    - `score_fn: Callable[[str, float], float]` — `(player_id, kickoff) -> expected points`
    - `mv_fn: Callable[[str, float], int | None]` — `(player_id, at) -> market value`
    - `position_fn: Callable[[str], str | None]`, `team_fn: Callable[[str], str | None]`

The engine is injected with plain callables rather than the corpus so it can be
tested without a database and so the leak boundary is enforced at one place
(the driver, Task 6) rather than scattered through the loop.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_replay/test_engine.py
import pytest
from rehoboam.replay.engine import Matchday, run_season
from rehoboam.replay.market import MarketListing, ReplayMarket
from rehoboam.replay.state import ReplayPlayer, ReplayState

DAY = 86400.0
POS = ["Goalkeeper"] + ["Defender"] * 4 + ["Midfielder"] * 4 + ["Forward"] * 2


class FakeMarket:
    def __init__(self, listings):
        self.listings = listings

    def available_before(self, at):
        return [x for x in self.listings if x.transfer_at < at]


def _full_squad():
    return {
        str(i): ReplayPlayer(id=str(i), position=POS[i], team_id=str(i))
        for i in range(11)
    }


def _matchday(day, kickoff, points):
    return Matchday(day_number=day, kickoff=kickoff, points=points)


def test_scores_the_points_of_the_fielded_eleven():
    state = ReplayState(budget=0, squad=_full_squad())
    mds = [_matchday(1, 10 * DAY, {str(i): 50.0 for i in range(11)})]
    result = run_season(
        state=state,
        market=FakeMarket([]),
        matchdays=mds,
        score_fn=lambda pid, at: 10.0,
        mv_fn=lambda pid, at: 1_000_000,
        position_fn=lambda pid: "Forward",
        team_fn=lambda pid: "99",
    )
    assert result.total_points == 550
    assert result.outcomes[0].penalty == 0


def test_negative_budget_at_kickoff_zeroes_the_matchday():
    state = ReplayState(budget=-1, squad=_full_squad())
    mds = [_matchday(1, 10 * DAY, {str(i): 50.0 for i in range(11)})]
    result = run_season(
        state=state,
        market=FakeMarket([]),
        matchdays=mds,
        score_fn=lambda pid, at: 10.0,
        mv_fn=lambda pid, at: 1_000_000,
        position_fn=lambda pid: "Forward",
        team_fn=lambda pid: "99",
    )
    assert result.outcomes[0].zeroed is True
    assert result.total_points == 0


def test_short_squad_incurs_the_empty_slot_penalty():
    squad = {
        str(i): ReplayPlayer(id=str(i), position=POS[i], team_id=str(i))
        for i in range(9)
    }
    state = ReplayState(budget=0, squad=squad)
    mds = [_matchday(1, 10 * DAY, {str(i): 50.0 for i in range(9)})]
    result = run_season(
        state=state,
        market=FakeMarket([]),
        matchdays=mds,
        score_fn=lambda pid, at: 10.0,
        mv_fn=lambda pid, at: 1_000_000,
        position_fn=lambda pid: "Forward",
        team_fn=lambda pid: "99",
    )
    assert result.outcomes[0].penalty == -200
    assert result.outcomes[0].points_scored == 9 * 50 - 200


def test_buys_a_clearly_better_player_when_affordable():
    state = ReplayState(budget=50_000_000, squad=_full_squad())
    market = FakeMarket(
        [MarketListing(player_id="star", price=10_000_000, transfer_at=9 * DAY)]
    )
    mds = [_matchday(1, 10 * DAY, {**{str(i): 10.0 for i in range(11)}, "star": 100.0})]
    result = run_season(
        state=state,
        market=market,
        matchdays=mds,
        score_fn=lambda pid, at: 200.0 if pid == "star" else 1.0,
        mv_fn=lambda pid, at: 1_000_000,
        position_fn=lambda pid: "Forward",
        team_fn=lambda pid: "star-team" if pid == "star" else pid,
    )
    assert "star" in result.outcomes[0].lineup_ids
    assert result.outcomes[0].buys == 1


def test_does_not_buy_when_gain_is_below_threshold():
    state = ReplayState(budget=50_000_000, squad=_full_squad())
    market = FakeMarket(
        [MarketListing(player_id="meh", price=10_000_000, transfer_at=9 * DAY)]
    )
    mds = [_matchday(1, 10 * DAY, {str(i): 10.0 for i in range(11)})]
    result = run_season(
        state=state,
        market=market,
        matchdays=mds,
        score_fn=lambda pid, at: 10.0,  # identical to squad — no marginal gain
        mv_fn=lambda pid, at: 1_000_000,
        position_fn=lambda pid: "Forward",
        team_fn=lambda pid: "meh-team",
        min_ep_gain=5.0,
    )
    assert result.outcomes[0].buys == 0


def test_engine_never_reads_points_of_the_matchday_being_played():
    """Leak guard: score_fn must never be handed the matchday's own result."""
    seen = []
    state = ReplayState(budget=0, squad=_full_squad())
    mds = [_matchday(5, 10 * DAY, {str(i): 999.0 for i in range(11)})]

    def spy(pid, at):
        seen.append(at)
        return 10.0

    run_season(
        state=state,
        market=FakeMarket([]),
        matchdays=mds,
        score_fn=spy,
        mv_fn=lambda pid, at: 1_000_000,
        position_fn=lambda pid: "Forward",
        team_fn=lambda pid: "99",
    )
    assert seen, "score_fn was never called"
    assert all(at < 10 * DAY for at in seen), "scored at or after kickoff — leak"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_replay/test_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rehoboam.replay.engine'`

- [ ] **Step 3: Implement the engine**

```python
"""The matchday loop: score, sell, buy, field eleven, award real points.

Every decision at matchday N is made with data strictly before N's kickoff.
The engine takes callables rather than a database so that boundary lives in
one place (the driver) and can be tested without I/O.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from rehoboam.formation import select_best_eleven
from rehoboam.replay.rules import (
    MAX_SQUAD_SIZE,
    can_buy,
    empty_slot_penalty,
)
from rehoboam.replay.state import ReplayPlayer, ReplayState

# Selling instantly to Kickbase returns 95% of market value.
INSTANT_SELL_PCT = 0.95
# Decisions are made this long before kickoff, mirroring the live bot's
# pre-matchday session rather than pretending to trade at the whistle.
DECISION_LEAD_SECONDS = 3600.0


@dataclass(frozen=True)
class Matchday:
    day_number: int
    kickoff: float
    points: dict[str, float]


@dataclass(frozen=True)
class MatchdayOutcome:
    day_number: int
    points_scored: int
    lineup_ids: list[str]
    penalty: int
    budget_at_kickoff: int
    zeroed: bool
    squad_size: int
    buys: int
    sells: int


@dataclass
class SeasonResult:
    outcomes: list[MatchdayOutcome] = field(default_factory=list)
    total_points: int = 0
    final_budget: int = 0


def _team_value(
    state: ReplayState, mv_fn: Callable[[str, float], int | None], at: float
) -> int:
    return sum(mv_fn(pid, at) or 0 for pid in state.player_ids)


def run_season(
    *,
    state: ReplayState,
    market,
    matchdays: list[Matchday],
    score_fn: Callable[[str, float], float],
    mv_fn: Callable[[str, float], int | None],
    position_fn: Callable[[str], str | None],
    team_fn: Callable[[str], str | None],
    min_ep_gain: float = 5.0,
) -> SeasonResult:
    """Replay every matchday in order, mutating ``state`` as the bot would."""
    result = SeasonResult()

    for md in matchdays:
        decide_at = md.kickoff - DECISION_LEAD_SECONDS
        scores = {pid: score_fn(pid, decide_at) for pid in state.player_ids}

        buys = sells = 0
        listings = sorted(
            market.available_before(decide_at),
            key=lambda x: score_fn(x.player_id, decide_at),
            reverse=True,
        )
        for listing in listings:
            if listing.player_id in state.squad:
                continue
            cand_ep = score_fn(listing.player_id, decide_at)
            position = position_fn(listing.player_id)
            if not position:
                continue

            candidate = ReplayPlayer(
                id=listing.player_id,
                position=position,
                team_id=team_fn(listing.player_id),
            )
            team_value = _team_value(state, mv_fn, decide_at)

            # Marginal gain: how much this player improves the weakest slot.
            weakest = min(scores.values()) if scores else 0.0
            if cand_ep - weakest < min_ep_gain and state.squad_size >= 11:
                continue

            # Check every constraint that a sale would NOT relieve before
            # selling anyone — otherwise a blocked buy leaves us a player down.
            allowed, _reason = can_buy(
                state, candidate, listing.price, team_value=team_value
            )
            if not allowed and "squad full" not in _reason:
                continue

            sold_id: str | None = None
            if state.squad_size >= MAX_SQUAD_SIZE:
                sold_id = min(scores, key=lambda p: scores[p])
                proceeds = int((mv_fn(sold_id, decide_at) or 0) * INSTANT_SELL_PCT)
                state.sell(sold_id, proceeds)
                del scores[sold_id]
                sells += 1
                allowed, _reason = can_buy(
                    state, candidate, listing.price, team_value=team_value
                )
                if not allowed:
                    continue

            state.buy(candidate, listing.price)
            scores[candidate.id] = cand_ep
            buys += 1

        # Budget must be non-negative at kickoff — sell the weakest until it is.
        while state.budget < 0 and state.squad_size > 11:
            worst_id = min(scores, key=lambda p: scores[p])
            proceeds = int((mv_fn(worst_id, decide_at) or 0) * INSTANT_SELL_PCT)
            state.sell(worst_id, proceeds)
            del scores[worst_id]
            sells += 1

        eleven = select_best_eleven(state.players, scores)
        lineup_ids = [p.id for p in eleven]
        penalty = empty_slot_penalty(len(lineup_ids))
        zeroed = state.budget < 0
        raw = sum(md.points.get(pid, 0.0) for pid in lineup_ids)
        scored = 0 if zeroed else int(raw + penalty)

        result.outcomes.append(
            MatchdayOutcome(
                day_number=md.day_number,
                points_scored=scored,
                lineup_ids=lineup_ids,
                penalty=0 if zeroed else penalty,
                budget_at_kickoff=state.budget,
                zeroed=zeroed,
                squad_size=state.squad_size,
                buys=buys,
                sells=sells,
            )
        )
        result.total_points += scored

    result.final_budget = state.budget
    return result
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_replay/test_engine.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add rehoboam/replay/engine.py tests/test_replay/test_engine.py
git commit -m "feat(replay): matchday engine with leak-guarded decisions (REH-51)"
```

______________________________________________________________________

### Task 5: Attribution and fidelity reporting

**Files:**

- Create: `rehoboam/replay/attribution.py`
- Test: `tests/test_replay/test_attribution.py`

**Interfaces:**

- Consumes: `SeasonResult`, `MatchdayOutcome` from Task 4

- Produces:

  - `LeagueStanding(manager_id: str, name: str, total_points: int)` — frozen dataclass
  - `place_in_league(simulated_total: int, standings: list[LeagueStanding]) -> int` — 1-indexed finishing position
  - `attribution_rows(result, *, actual_total, actual_per_matchday) -> list[tuple[str, int, str]]` — `(source, points, fidelity)`
  - `format_report(result, *, actual_total, actual_per_matchday, standings) -> str`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_replay/test_attribution.py
from rehoboam.replay.attribution import (
    LeagueStanding,
    attribution_rows,
    format_report,
    place_in_league,
)
from rehoboam.replay.engine import MatchdayOutcome, SeasonResult


def _outcome(day, pts, zeroed=False, penalty=0):
    return MatchdayOutcome(
        day_number=day,
        points_scored=pts,
        lineup_ids=[],
        penalty=penalty,
        budget_at_kickoff=0,
        zeroed=zeroed,
        squad_size=15,
        buys=0,
        sells=0,
    )


STANDINGS = [
    LeagueStanding(manager_id="a", name="Alice", total_points=37_857),
    LeagueStanding(manager_id="b", name="Bob", total_points=30_000),
    LeagueStanding(manager_id="c", name="Cara", total_points=26_170),
]


def test_place_in_league_top():
    assert place_in_league(40_000, STANDINGS) == 1


def test_place_in_league_middle():
    assert place_in_league(31_000, STANDINGS) == 2


def test_place_in_league_last():
    assert place_in_league(1_000, STANDINGS) == 4


def test_place_in_league_ties_lose_to_incumbent():
    """A tie does not overtake — the real manager keeps the higher place."""
    assert place_in_league(30_000, STANDINGS) == 3


def test_attribution_rows_report_penalties_avoided():
    result = SeasonResult(
        outcomes=[_outcome(1, 800), _outcome(2, 900)], total_points=1_700
    )
    rows = attribution_rows(
        result, actual_total=1_000, actual_per_matchday={1: 0, 2: 1_000}
    )
    labels = {r[0] for r in rows}
    assert "Zero-point matchdays avoided" in labels
    assert any(r[2] == "exact" for r in rows)


def test_attribution_total_matches_simulated_minus_actual():
    result = SeasonResult(
        outcomes=[_outcome(1, 800), _outcome(2, 900)], total_points=1_700
    )
    rows = attribution_rows(
        result, actual_total=1_000, actual_per_matchday={1: 0, 2: 1_000}
    )
    total_row = next(r for r in rows if r[0] == "TOTAL vs actual")
    assert total_row[1] == 700


def test_format_report_states_finishing_position_and_fidelity():
    result = SeasonResult(outcomes=[_outcome(1, 800)], total_points=800)
    text = format_report(
        result, actual_total=700, actual_per_matchday={1: 700}, standings=STANDINGS
    )
    assert "FINISHING POSITION" in text
    assert "Bid competition" in text  # fidelity caveat must be printed
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_replay/test_attribution.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rehoboam.replay.attribution'`

- [ ] **Step 3: Implement attribution**

```python
"""Turn a replayed season into an honest, self-caveating report.

The output is an attribution table, not a verdict. Buy-side gains carry an
explicit optimism warning: the replay models no bid competition.
"""

from __future__ import annotations

from dataclasses import dataclass

from rehoboam.replay.engine import SeasonResult

FIDELITY_NOTES = [
    ("Points scoring", "exact", "real per-match points from the corpus"),
    ("Penalty avoidance", "exact", "deterministic"),
    ("Lineup selection", "high", "real squad, real formation rules"),
    ("Sell decisions", "high", "instant sell = 95% of market value"),
    ("Buy prices", "high", "real transaction prices"),
    ("Buy availability", "medium", "only players who actually traded are visible"),
    ("Bid competition", "ABSENT - optimistic", "wanted players are always won"),
]


@dataclass(frozen=True)
class LeagueStanding:
    manager_id: str
    name: str
    total_points: int


def place_in_league(simulated_total: int, standings: list[LeagueStanding]) -> int:
    """1-indexed finishing position. Ties do not overtake the real manager."""
    ahead = sum(1 for s in standings if s.total_points >= simulated_total)
    return ahead + 1


def attribution_rows(
    result: SeasonResult,
    *,
    actual_total: int,
    actual_per_matchday: dict[int, int],
) -> list[tuple[str, int, str]]:
    """Decompose simulated minus actual into labelled sources."""
    zero_recovered = sum(
        o.points_scored
        for o in result.outcomes
        if actual_per_matchday.get(o.day_number, 0) == 0 and not o.zeroed
    )
    penalties = sum(o.penalty for o in result.outcomes)
    delta = result.total_points - actual_total
    other = delta - zero_recovered

    return [
        ("Zero-point matchdays avoided", zero_recovered, "exact"),
        ("Empty-slot penalties incurred", penalties, "exact"),
        ("Better squad and lineup", other, "medium - buy side is optimistic"),
        ("TOTAL vs actual", delta, "mixed"),
    ]


def format_report(
    result: SeasonResult,
    *,
    actual_total: int,
    actual_per_matchday: dict[int, int],
    standings: list[LeagueStanding],
) -> str:
    """Human-readable replay report with fidelity caveats attached."""
    place = place_in_league(result.total_points, standings)
    total_managers = len(standings) + 1
    lines = [
        "=" * 68,
        "FULL-BOT SEASON REPLAY - 2025/2026",
        "=" * 68,
        "",
        f"Simulated total:  {result.total_points:>8,} points",
        f"Actual total:     {actual_total:>8,} points",
        f"Difference:       {result.total_points - actual_total:>+8,} points",
        "",
        f"FINISHING POSITION: {place} of {total_managers}",
        "",
        "Attribution",
        "-" * 68,
    ]
    for label, points, fidelity in attribution_rows(
        result, actual_total=actual_total, actual_per_matchday=actual_per_matchday
    ):
        lines.append(f"  {label:<34}{points:>+9,}   {fidelity}")

    lines += ["", "Fidelity", "-" * 68]
    for component, level, basis in FIDELITY_NOTES:
        lines.append(f"  {component:<20}{level:<22}{basis}")

    zeroed = [o.day_number for o in result.outcomes if o.zeroed]
    lines += [
        "",
        f"Matchdays zeroed by negative budget: {zeroed or 'none'}",
        f"Total buys: {sum(o.buys for o in result.outcomes)}   "
        f"Total sells: {sum(o.sells for o in result.outcomes)}",
        f"Final budget: EUR {result.final_budget:,}",
        "",
        "This models no bid competition: any listed player the bot wanted, it got.",
        "Treat the buy-side contribution as an upper bound.",
        "=" * 68,
    ]
    return "\n".join(lines)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_replay/test_attribution.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add rehoboam/replay/attribution.py tests/test_replay/test_attribution.py
git commit -m "feat(replay): attribution and fidelity reporting (REH-51)"
```

______________________________________________________________________

### Task 6: Driver, CLI, and the real run

**Files:**

- Create: `rehoboam/replay/driver.py`
- Modify: `rehoboam/cli.py`
- Test: `tests/test_replay/test_driver.py`

**Interfaces:**

- Consumes: everything from Tasks 1-5, `score_player_v2` from `rehoboam.scoring.v2.adapter`, `matches_before` from `rehoboam.backtest.snapshot`

- Produces:

  - `SEASON = "2025/2026"`, `LEAGUE_ID = "1933872"`, `MANAGER_ID = "3616202"`, `ASSIGNED_ON = 1754661947.0`, `STARTING_BUDGET = 80_000_000`
  - `build_matchdays(corpus, *, season) -> list[Matchday]`
  - `load_standings(learning_db_path, *, league_id, exclude_manager_id) -> list[LeagueStanding]`
  - `run_replay(*, corpus_path, learning_db_path) -> tuple[SeasonResult, str]`

- [ ] **Step 1: Write the failing driver tests**

```python
# tests/test_replay/test_driver.py
import sqlite3
import pytest
from rehoboam.enrichment.corpus import TrainingCorpus
from rehoboam.replay.driver import build_matchdays, load_standings


@pytest.fixture
def corpus(tmp_path):
    c = TrainingCorpus(tmp_path / "c.db")
    with sqlite3.connect(c.db_path) as conn:
        conn.executemany(
            "INSERT INTO player_match_history (player_id, season, day_number, match_date,"
            " points, minutes, team_id, opponent_team_id, is_home, status)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                ("1", "2025/2026", 1, "2025-08-23T13:30:00Z", 80, 90, "40", "7", 1, 5),
                ("2", "2025/2026", 1, "2025-08-23T15:30:00Z", 40, 45, "7", "40", 0, 3),
                ("1", "2025/2026", 2, "2025-08-30T13:30:00Z", 60, 90, "40", "9", 1, 5),
                ("9", "2024/2025", 1, "2024-08-23T13:30:00Z", 10, 90, "40", "7", 1, 5),
            ],
        )
    return c


def test_build_matchdays_returns_one_entry_per_matchday(corpus):
    mds = build_matchdays(corpus, season="2025/2026")
    assert [m.day_number for m in mds] == [1, 2]


def test_build_matchdays_collects_points_per_player(corpus):
    md1 = build_matchdays(corpus, season="2025/2026")[0]
    assert md1.points == {"1": 80.0, "2": 40.0}


def test_build_matchdays_excludes_other_seasons(corpus):
    assert all("9" not in m.points for m in build_matchdays(corpus, season="2025/2026"))


def test_build_matchdays_kickoff_is_the_earliest_match(corpus):
    md1 = build_matchdays(corpus, season="2025/2026")[0]
    from datetime import datetime, timezone

    assert datetime.fromtimestamp(md1.kickoff, timezone.utc) == datetime(
        2025, 8, 23, 13, 30, tzinfo=timezone.utc
    )


def test_load_standings_excludes_our_own_manager(tmp_path):
    db = tmp_path / "learn.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE league_rank_history (snapshot_at REAL, league_id TEXT,"
            " manager_id TEXT, day_number INTEGER, rank_overall INTEGER,"
            " rank_matchday INTEGER, total_points INTEGER, matchday_points INTEGER,"
            " team_value INTEGER, is_self INTEGER)"
        )
        conn.executemany(
            "INSERT INTO league_rank_history VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                (1.0, "L", "a", 34, 1, 1, 37_857, 900, 0, 0),
                (1.0, "L", "us", 34, 10, 10, 26_170, 700, 0, 1),
                (0.5, "L", "a", 33, 1, 1, 36_428, 800, 0, 0),
            ],
        )
    standings = load_standings(db, league_id="L", exclude_manager_id="us")
    assert [(s.manager_id, s.total_points) for s in standings] == [("a", 37_857)]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_replay/test_driver.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rehoboam.replay.driver'`

- [ ] **Step 3: Implement the driver**

```python
"""Composition root for the full-bot season replay.

Wires the corpus, the fitted v2 scorer, the reconstructed market and the real
standings into one run. This is the only place that knows about file paths and
the verified starting state, and the only place the leak boundary is applied.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from rehoboam.backtest.snapshot import matches_before
from rehoboam.enrichment.corpus import TrainingCorpus
from rehoboam.replay.attribution import LeagueStanding, format_report
from rehoboam.replay.engine import Matchday, SeasonResult, run_season
from rehoboam.replay.market import ReplayMarket
from rehoboam.replay.state import initial_state
from rehoboam.scoring.v2.coefficients import load_coefficients

SEASON = "2025/2026"
LEAGUE_ID = "1933872"
MANAGER_ID = "3616202"
ASSIGNED_ON = (
    1754661947.0  # 2025-08-08T14:05:47Z, verified from /v4/leagues/{id}/overview
)
STARTING_BUDGET = 80_000_000

PLAYED_STATUSES = (1, 3, 4, 5)


def _parse(dt: str) -> float:
    return (
        datetime.strptime(dt, "%Y-%m-%dT%H:%M:%SZ")
        .replace(tzinfo=timezone.utc)
        .timestamp()
    )


def build_matchdays(corpus: TrainingCorpus, *, season: str) -> list[Matchday]:
    """One ``Matchday`` per day_number, with real points and the earliest kickoff."""
    with sqlite3.connect(corpus.db_path) as conn:
        rows = conn.execute(
            "SELECT day_number, player_id, points, match_date FROM player_match_history "
            "WHERE season = ? AND match_date IS NOT NULL ORDER BY day_number",
            (season,),
        ).fetchall()

    by_day: dict[int, dict[str, float]] = {}
    kickoffs: dict[int, float] = {}
    for day, pid, points, match_date in rows:
        day = int(day)
        by_day.setdefault(day, {})[str(pid)] = float(points or 0)
        at = _parse(match_date)
        if day not in kickoffs or at < kickoffs[day]:
            kickoffs[day] = at

    return [
        Matchday(day_number=day, kickoff=kickoffs[day], points=by_day[day])
        for day in sorted(by_day)
    ]


def load_standings(
    learning_db_path: Path, *, league_id: str, exclude_manager_id: str
) -> list[LeagueStanding]:
    """Final season totals for every manager except ours."""
    with sqlite3.connect(learning_db_path) as conn:
        rows = conn.execute(
            "SELECT manager_id, MAX(day_number), total_points FROM league_rank_history "
            "WHERE league_id = ? AND manager_id != ? GROUP BY manager_id",
            (league_id, exclude_manager_id),
        ).fetchall()
    return [
        LeagueStanding(
            manager_id=str(r[0]), name=str(r[0]), total_points=int(r[2] or 0)
        )
        for r in rows
    ]


def _make_score_fn(
    corpus: TrainingCorpus, season: str
) -> Callable[[str, float], float]:
    """Score a player using only matches before the current matchday.

    The leak boundary lives here: ``matches_before`` truncates history to
    strictly earlier matchdays, and the cutoff is derived from the decision
    timestamp, never from the matchday being predicted.
    """
    availability, rate, _meta = load_coefficients()
    positions: dict[str, str] = {}

    @lru_cache(maxsize=None)
    def day_for(at: float) -> int:
        """The matchday being predicted, from the decision timestamp."""
        with sqlite3.connect(corpus.db_path) as conn:
            row = conn.execute(
                "SELECT MIN(day_number) FROM player_match_history "
                "WHERE season = ? AND match_date > ?",
                (
                    season,
                    datetime.fromtimestamp(at, timezone.utc).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    ),
                ),
            ).fetchone()
        return int(row[0]) if row and row[0] is not None else 99

    def score(player_id: str, at: float) -> float:
        day = day_for(at)
        history = matches_before(
            corpus.matches_for_player(player_id), season=season, day_number=day
        )
        prev_status = None
        for match in reversed(history):
            if match.get("status") in PLAYED_STATUSES:
                prev_status = int(match["status"])
                break
        if player_id not in positions:
            positions.update(corpus.positions_for([player_id]))
        position = positions.get(player_id)
        probs = availability.predict(prev_status)
        return sum(
            probs[s] * rate.predict(player_id, s, position) for s in PLAYED_STATUSES
        )

    return score


def run_replay(
    *, corpus_path: Path, learning_db_path: Path
) -> tuple[SeasonResult, str]:
    """Replay the whole season and return the result plus a formatted report."""
    corpus = TrainingCorpus(corpus_path)
    matchdays = build_matchdays(corpus, season=SEASON)
    state = initial_state(
        corpus,
        manager_id=MANAGER_ID,
        assigned_on=ASSIGNED_ON,
        starting_budget=STARTING_BUDGET,
    )
    market = ReplayMarket(corpus)
    score_fn = _make_score_fn(corpus, SEASON)
    positions_cache: dict[str, str] = {}
    teams_cache: dict[str, str] = {}

    def position_fn(pid: str) -> str | None:
        if pid not in positions_cache:
            positions_cache.update(corpus.positions_for([pid]))
        return positions_cache.get(pid)

    def team_fn(pid: str) -> str | None:
        if pid not in teams_cache:
            teams_cache.update(corpus.team_ids_for([pid]))
        return teams_cache.get(pid)

    result = run_season(
        state=state,
        market=market,
        matchdays=matchdays,
        score_fn=score_fn,
        mv_fn=corpus.market_value_at,
        position_fn=position_fn,
        team_fn=team_fn,
    )

    with sqlite3.connect(learning_db_path) as conn:
        actual_rows = conn.execute(
            "SELECT day_number, MAX(total_points), MAX(matchday_points) "
            "FROM league_rank_history WHERE is_self = 1 GROUP BY day_number",
            (),
        ).fetchall()
    actual_per_matchday = {int(r[0]): int(r[2] or 0) for r in actual_rows}
    actual_total = max((int(r[1] or 0) for r in actual_rows), default=0)

    standings = load_standings(
        learning_db_path, league_id=LEAGUE_ID, exclude_manager_id=MANAGER_ID
    )
    report = format_report(
        result,
        actual_total=actual_total,
        actual_per_matchday=actual_per_matchday,
        standings=standings,
    )
    return result, report
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_replay/test_driver.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Add the CLI command**

In `rehoboam/cli.py`, following the style of the existing `backtest-baseline`
command, add:

```python
@app.command("replay-season")
def replay_season(
    corpus: Path = typer.Option(  # noqa: B008
        Path("logs/training_corpus.db"), help="Path to the training corpus DB"
    ),
    learning_db: Path = typer.Option(  # noqa: B008
        Path("logs/bid_learning.db"), help="Path to the learning DB with real standings"
    ),
) -> None:
    """Replay the full bot across 2025/26 and report the counterfactual finish."""
    from rehoboam.replay.driver import run_replay

    if not corpus.exists():
        console.print(f"[red]Corpus not found: {corpus}[/red]")
        raise typer.Exit(1)
    if not learning_db.exists():
        console.print(f"[red]Learning DB not found: {learning_db}[/red]")
        raise typer.Exit(1)

    _result, report = run_replay(corpus_path=corpus, learning_db_path=learning_db)
    console.print(report)
```

- [ ] **Step 6: Verify the CLI is wired**

Run: `uv run rehoboam replay-season --help`
Expected: help text showing `--corpus` and `--learning-db`

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest -q`
Expected: all tests pass; the count should be the Task 5 total plus 5.

- [ ] **Step 8: Run the real replay and capture the output**

```bash
uv run rehoboam replay-season | tee .superpowers/sdd/2026-07-31-reh-51-full-season-replay/replay-output.txt
```

Report the finishing position, simulated total, attribution table, and any
matchday that was zeroed. **Do not tune anything to improve this number.**
If the result looks poor, that is the finding.

- [ ] **Step 9: Commit**

```bash
git add rehoboam/replay/driver.py rehoboam/cli.py tests/test_replay/test_driver.py
git commit -m "feat(replay): season replay driver and CLI (REH-51)"
```

______________________________________________________________________

## Self-Review Notes

**Spec coverage** (`docs/superpowers/specs/2026-07-29-rehoboam-v2-design.md` §6.2):

- "Replays the entire agent — buys, sells, budget, squad evolution, lineup" → Task 4
- "Fidelity is not uniform and the output must say so" → Task 5 `FIDELITY_NOTES`, printed in every report
- "Output is an attribution table, not a verdict" → Task 5 `attribution_rows`
- "Buy-side results are an upper bound and are labelled as such" → Task 5 report footer
- §6.3 leakage guard → Task 4 `test_engine_never_reads_points_of_the_matchday_being_played`, plus `matches_before` in Task 6

**Known limitation carried forward:** the engine sells only via instant sell
(95% of MV), never by listing on the market. This is deliberate and
conservative — market listing takes days and would require modelling rival
bidders, which the replay explicitly does not do.
