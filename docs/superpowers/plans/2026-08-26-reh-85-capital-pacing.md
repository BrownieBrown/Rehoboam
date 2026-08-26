# Capital Pacing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop one signing from consuming the season's capital, by reserving enough budget to make the moves the bot still needs.

**Architecture:** A new pure module `rehoboam/services/pacing.py` computes a euro *reserve* from the squad slots still to fill and a *measured* median move price. `SmartBidding.calculate_ep_bid` accepts an optional `PacingContext` and applies one extra `min()` immediately after the REH-99 ceiling. Nothing else in the bidding stack changes: because the existing code already turns a cap below the asking price into `recommended_bid = 0`, "cap, don't refuse" needs no new branch, and because trade pairs already pass a synthetic `SellPlan` carrying the sale proceeds, they get net-cost pacing with no special case.

**Tech Stack:** Python 3.12, Pydantic `Settings`, SQLite via `BidLearner`, pytest, `uv` for everything.

**Spec:** `docs/superpowers/specs/2026-08-26-reh-85-capital-pacing-design.md`

## Global Constraints

- Every tunable is a `Settings` field, re-tunable from `.env` without a deploy.
- Defaults: `pacing_enabled=True`, `pacing_in_season_min_moves=2`, `pacing_window_days=90`, `pacing_median_floor_eur=3_000_000`.
- The squad cap is **15**, and an open offer counts as a filled slot.
- Pacing **never** applies to emergency squad fill (an empty slot is -100) or to the compliance re-bid (mandatory).
- TDD throughout: write the failing test, watch it fail, minimal implementation, watch it pass, commit.
- Run `uv run pytest -q` before every commit. Formatting is enforced by pre-commit; do not run `black` on whole files manually.
- Baseline to beat: **26,391** on `replay-season --with-competition`; 26,960 on the plain configuration.

______________________________________________________________________

### Task 1: The pure pacing module

**Files:**

- Create: `rehoboam/services/pacing.py`
- Test: `tests/test_pacing.py`

**Interfaces:**

- Consumes: nothing.

- Produces: `SQUAD_CAP: int`, `available_squad_slots(squad_size: int, open_bid_count: int, cap: int = SQUAD_CAP) -> int`, `median_move_price(prices: Sequence[int], *, floor_eur: int) -> int`, `capital_reserve(*, slots_to_fill: int, in_season_min_moves: int, median_move: int) -> int`, and the frozen dataclass `PacingContext(reserve: int, open_offers: int)` with method `max_bid(self, budget_ceiling: int) -> int`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pacing.py`:

```python
"""Reserve the ability to keep buying (REH-85).

The bot committed EUR 71m of an EUR 80m ceiling to one player and then made
four more buys all season, finishing on EUR 500,000. The champions each made
one EUR 60-65m signing AND roughly 25 more purchases. The difference is not
the size of a bid; it is what the bid leaves behind.
"""

import pytest

from rehoboam.services.pacing import (
    PacingContext,
    available_squad_slots,
    capital_reserve,
    median_move_price,
)


class TestMedianMovePrice:
    def test_returns_the_median_of_the_observed_prices(self):
        assert (
            median_move_price([1_000_000, 5_000_000, 9_000_000], floor_eur=0)
            == 5_000_000
        )

    def test_even_count_takes_the_lower_of_the_two_middles(self):
        # Deliberately not an average: a reserve must be a price someone
        # actually paid, not an interpolation between two that nobody did.
        assert median_move_price([2_000_000, 4_000_000], floor_eur=0) == 2_000_000

    def test_empty_population_falls_back_to_the_floor(self):
        # A thin window must not collapse the reserve to zero, which would
        # silently disable pacing exactly when there is least evidence.
        assert median_move_price([], floor_eur=3_000_000) == 3_000_000

    def test_floor_wins_when_the_measured_median_is_below_it(self):
        assert median_move_price([500_000, 600_000], floor_eur=3_000_000) == 3_000_000


class TestAvailableSquadSlots:
    def test_open_bids_count_as_filled(self):
        # Kickbase counts a pending offer toward the 15-player cap.
        assert available_squad_slots(squad_size=11, open_bid_count=1) == 3

    def test_full_squad_has_no_slots(self):
        assert available_squad_slots(squad_size=15, open_bid_count=0) == 0

    def test_over_committed_squad_does_not_report_negative_room(self):
        assert available_squad_slots(squad_size=15, open_bid_count=2) == -2


class TestCapitalReserve:
    def test_reserves_one_median_move_per_unfilled_slot(self):
        assert (
            capital_reserve(
                slots_to_fill=3, in_season_min_moves=2, median_move=10_800_000
            )
            == 32_400_000
        )

    def test_full_squad_falls_back_to_the_in_season_minimum(self):
        # At 15/15 there are no slots to fill, but the bot must still be able
        # to replace a player mid-season.
        assert (
            capital_reserve(
                slots_to_fill=0, in_season_min_moves=2, median_move=10_800_000
            )
            == 21_600_000
        )

    def test_negative_slots_never_shrink_the_reserve_below_the_minimum(self):
        assert (
            capital_reserve(
                slots_to_fill=-2, in_season_min_moves=2, median_move=10_800_000
            )
            == 21_600_000
        )


class TestPacingContext:
    def test_max_bid_is_the_ceiling_less_open_offers_and_reserve(self):
        ctx = PacingContext(reserve=32_400_000, open_offers=0)
        assert ctx.max_bid(budget_ceiling=62_307_522) == 29_907_522

    def test_open_offers_are_already_spent(self):
        # Kickbase's reported budget does not deduct pending offers, so two
        # bids sized against the same nominal budget can both land.
        ctx = PacingContext(reserve=10_000_000, open_offers=5_000_000)
        assert ctx.max_bid(budget_ceiling=50_000_000) == 35_000_000

    def test_max_bid_never_goes_negative(self):
        ctx = PacingContext(reserve=50_000_000, open_offers=0)
        assert ctx.max_bid(budget_ceiling=10_000_000) == 0

    def test_the_unwind_sequence_from_the_spec(self):
        """Section 2 of the design doc, as executable arithmetic.

        The point of deriving the reserve from slots-to-fill rather than a
        constant N is that it unwinds. A constant 3 moves would leave the
        reserve at EUR 32.4m while the budget fell, capping the second buy
        near EUR 4.8m and freezing the bot one purchase later.
        """
        median = 10_800_000
        budget = 62_307_522
        caps = []
        for slots in (3, 2, 1):
            reserve = capital_reserve(
                slots_to_fill=slots, in_season_min_moves=2, median_move=median
            )
            cap = PacingContext(reserve=reserve, open_offers=0).max_bid(budget)
            caps.append(cap)
            budget -= cap  # spend the whole cap, the worst case for the next step
        assert caps[0] == 29_907_522
        assert all(c > 0 for c in caps), "the reserve must never freeze the next buy"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_pacing.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rehoboam.services.pacing'`

- [ ] **Step 3: Write the minimal implementation**

Create `rehoboam/services/pacing.py`:

```python
"""How much budget a buy must leave behind (REH-85).

`bidding_strategy.max_bid_fraction` asks what fraction of the *current* budget
a signing justifies — a single-decision question with a defensible answer.
Nothing asked the sequential one: what does this signing leave the bot able to
do for the next thirty matchdays? A competition-modelled replay answered it —
EUR 71m on one player, then five buys all season and EUR 500,000 left, with
every declined candidate rated `must_have` by the bot's own tiering.

Measured against `manager_transfers`, the champions each made ONE signing of
EUR 60-65m *and* roughly 25 further purchases. So the rule here constrains what
a buy leaves behind, never how large it is. A hard per-transaction cap near
their EUR 11m mean — the ticket's first suggestion — would have banned both of
their biggest signings; that mean is an artefact of a heavily skewed
distribution.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

#: Kickbase's hard squad-size limit, including open (unresolved) bids.
SQUAD_CAP = 15


def available_squad_slots(
    squad_size: int, open_bid_count: int, cap: int = SQUAD_CAP
) -> int:
    """Slots left under the squad cap, counting open bids as committed.

    Kickbase counts pending offers toward the 15-player cap before they
    resolve — a squad at 13 with 2 open bids has zero room for a further bid,
    not two. May return a negative number when over-committed; callers that
    need a floor apply their own.
    """
    return cap - squad_size - open_bid_count


def median_move_price(prices: Sequence[int], *, floor_eur: int) -> int:
    """What one further purchase costs in this league, in euros.

    The median rather than the mean, because the population is heavily skewed:
    the champions' buys have a median near EUR 10m and a maximum of EUR 65m, and
    a mean would price a "move" at something nobody routinely pays.

    On an even count this takes the lower of the two middle values rather than
    averaging them, so the result is always a price someone actually paid.

    `floor_eur` guards the thin-window case. A near-empty window would otherwise
    produce a reserve of nearly zero, which disables pacing silently — exactly
    when there is least evidence to justify spending freely.
    """
    if not prices:
        return floor_eur
    ordered = sorted(int(p) for p in prices)
    median = ordered[(len(ordered) - 1) // 2]
    return max(median, floor_eur)


def capital_reserve(
    *, slots_to_fill: int, in_season_min_moves: int, median_move: int
) -> int:
    """Euros that must survive this buy, so the bot can keep operating.

    `slots_to_fill` rather than a constant is the load-bearing choice. A
    constant N keeps the reserve fixed while the budget falls, so the bot
    freezes one purchase later than it does today rather than not at all.
    Deriving it from unfilled slots makes the reserve unwind as the squad
    completes, and `in_season_min_moves` is what remains at 15/15 so a full
    squad can still replace a player.
    """
    moves = slots_to_fill if slots_to_fill > 0 else in_season_min_moves
    return max(0, moves) * max(0, median_move)


@dataclass(frozen=True)
class PacingContext:
    """What pacing needs that the bidder itself cannot know.

    Built once per session by the caller, which is the only layer that sees the
    squad, the open offers and the learning DB. `SmartBidding` stays ignorant of
    all three and just applies the number.
    """

    reserve: int
    open_offers: int

    def max_bid(self, budget_ceiling: int) -> int:
        """The largest bid that still leaves the reserve intact.

        `budget_ceiling` already includes any sell-plan recovery, which is why
        trade pairs need no special case: `trader.py` gives a pair a synthetic
        sell plan whose `total_recovery` is the sale proceeds, so subtracting
        the reserve from the ceiling paces the pair on its NET cost. A pair
        recycles capital rather than consuming it, and pacing it on the gross
        bid would freeze pair trading at 15/15 — the one mechanism a full squad
        has to improve itself.
        """
        return max(0, int(budget_ceiling) - int(self.open_offers) - int(self.reserve))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_pacing.py -q`
Expected: PASS, 14 tests.

- [ ] **Step 5: Commit**

```bash
git add rehoboam/services/pacing.py tests/test_pacing.py
git commit -m "feat(pacing): reserve arithmetic for season capital (REH-85)"
```

______________________________________________________________________

### Task 2: Remove the duplicate slot definition

**Files:**

- Modify: `rehoboam/auto_trader.py` (the `SQUAD_CAP` constant and `_available_squad_slots`, around lines 75-90 before Task 1's changes)
- Test: `tests/test_pacing.py` (append)

**Interfaces:**

- Consumes: `SQUAD_CAP`, `available_squad_slots` from Task 1.
- Produces: `auto_trader._available_squad_slots` keeps its name and signature so existing callers and tests are untouched.

`auto_trader` already defines this exact arithmetic. Two definitions of a safety-relevant number is how REH-99's 8%/20% split happened, so make `auto_trader` delegate rather than letting the copies drift.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pacing.py`:

```python
def test_auto_trader_slot_helper_delegates_to_the_pacing_module():
    """One definition of the squad cap, not two.

    REH-99 was caused by two constants for one concept living in different
    modules and drifting apart. This asserts the copies cannot.
    """
    from rehoboam import auto_trader
    from rehoboam.services import pacing

    # Identity on the FUNCTION, not on SQUAD_CAP: CPython interns small
    # integers, so `15 is 15` is True even for two separate definitions and
    # would pass before this task did anything.
    assert auto_trader.available_squad_slots is pacing.available_squad_slots
    assert auto_trader.SQUAD_CAP == pacing.SQUAD_CAP
    for squad, bids in ((11, 1), (15, 0), (13, 2), (15, 2)):
        assert auto_trader._available_squad_slots(
            squad, bids
        ) == pacing.available_squad_slots(squad, bids)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_pacing.py::test_auto_trader_slot_helper_delegates_to_the_pacing_module -q`
Expected: FAIL with `AttributeError: module 'rehoboam.auto_trader' has no attribute 'available_squad_slots'` — the name does not exist there until Step 3 imports it.

- [ ] **Step 3: Replace the duplicate with a delegation**

In `rehoboam/auto_trader.py`, delete the `SQUAD_CAP = 15` line and the whole body of `_available_squad_slots`, and replace both with:

```python
# One definition of the squad cap, in `services/pacing`. Two copies of one
# safety-relevant number in different modules is how REH-99's 8%/20% split
# happened; the name is kept here because callers and tests already use it.
SQUAD_CAP = pacing_squad_cap


def _available_squad_slots(
    squad_size: int, open_bid_count: int, cap: int = SQUAD_CAP
) -> int:
    """Slots left under Kickbase's squad cap, counting open bids as committed.

    Kickbase counts pending offers toward the 15-player cap before they even
    resolve — a squad at 13 with 2 open bids has zero room for a further
    bid, not two. Positive means room for another bid; zero or negative
    means none.
    """
    return available_squad_slots(squad_size, open_bid_count, cap)
```

Add to the imports at the top of `rehoboam/auto_trader.py`, beside the existing `from .services.safety_gate import BuyGate, club_counts`:

```python
from .services.pacing import SQUAD_CAP as pacing_squad_cap
from .services.pacing import available_squad_slots
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS. Existing callers of `_available_squad_slots` are unchanged.

- [ ] **Step 5: Commit**

```bash
git add rehoboam/auto_trader.py tests/test_pacing.py
git commit -m "refactor(pacing): one definition of the squad cap (REH-85)"
```

______________________________________________________________________

### Task 3: Read the measured move price from the learning DB

**Files:**

- Modify: `rehoboam/bid_learner.py` (add a reader beside the other read methods)
- Test: `tests/test_pacing_prices.py`

**Interfaces:**

- Consumes: nothing from earlier tasks.
- Produces: `BidLearner.recent_buy_prices(window_days: int) -> list[int]`.

`manager_transfers` stores `transfer_type = 1` for a buy and `2` for a sell — verified against our own manager `3616202`, whose type-1 total of EUR 69,831,333 and type-2 total of EUR 139,638,056 match REH-72 §5 exactly. `transfer_dt` is an ISO-8601 string such as `2026-08-25T14:02:11Z`, so a lexicographic comparison against an ISO cutoff is correct and needs no date parsing.

- [ ] **Step 1: Write the failing test**

Create `tests/test_pacing_prices.py`:

```python
"""The measured price of one further move (REH-85).

A "move" costs EUR 6.03m across the whole `manager_transfers` table but
EUR 10.8m in the 2026/27 pre-season. A hardcoded euro figure would be wrong
within one transfer window, which is the same lesson REH-99 learned about the
overbid cap: measure the population, and re-measure it.
"""

from __future__ import annotations

import time

import pytest

from rehoboam.bid_learner import BidLearner


@pytest.fixture
def learner(tmp_path):
    return BidLearner(db_path=tmp_path / "bid_learning.db")


def _iso(days_ago: float) -> str:
    return time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - days_ago * 86400)
    )


def _row(
    price: int, days_ago: float, transfer_type: int = 1, player_id: str = "p"
) -> dict:
    return {
        "league_id": "L",
        "manager_id": "m1",
        "transfer_dt": _iso(days_ago),
        "player_id": f"{player_id}{price}",
        "player_name": "Test",
        "transfer_type": transfer_type,
        "transfer_price": price,
    }


def test_returns_buy_prices_inside_the_window(learner):
    learner.record_manager_transfers([_row(5_000_000, 10), _row(9_000_000, 20)])
    assert sorted(learner.recent_buy_prices(window_days=90)) == [5_000_000, 9_000_000]


def test_excludes_sells(learner):
    """Type 2 is a sale. Pricing a move off sale proceeds would be wrong."""
    learner.record_manager_transfers(
        [_row(5_000_000, 10), _row(80_000_000, 10, transfer_type=2)]
    )
    assert learner.recent_buy_prices(window_days=90) == [5_000_000]


def test_excludes_rows_outside_the_window(learner):
    learner.record_manager_transfers([_row(5_000_000, 10), _row(9_000_000, 200)])
    assert learner.recent_buy_prices(window_days=90) == [5_000_000]


def test_prices_are_absolute(learner):
    """The feed signs a buy negative in some payloads; a reserve is a magnitude."""
    learner.record_manager_transfers([_row(-7_000_000, 5)])
    assert learner.recent_buy_prices(window_days=90) == [7_000_000]


def test_empty_table_returns_empty_list_not_an_error(learner):
    assert learner.recent_buy_prices(window_days=90) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_pacing_prices.py -q`
Expected: FAIL — `AttributeError: 'BidLearner' object has no attribute 'recent_buy_prices'`

- [ ] **Step 3: Add the reader**

In `rehoboam/bid_learner.py`, add this method to `BidLearner` immediately after `record_manager_transfers`:

```python
def recent_buy_prices(self, window_days: int) -> list[int]:
    """Purchase prices across the league in the trailing window, in euros.

    The population behind REH-85's reserve: what one further move actually
    costs here. `transfer_type = 1` is a buy and `2` is a sale — verified
    against manager 3616202, whose type-1 total of EUR 69,831,333 and
    type-2 total of EUR 139,638,056 match REH-72 §5.

    Prices are returned as magnitudes because the transfer feed signs a
    purchase negative in some payloads and a reserve is a size, not a
    direction. `transfer_dt` is ISO-8601, so the cutoff compares
    lexicographically without parsing.
    """
    cutoff = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - window_days * 86400)
    )
    with sqlite3.connect(self.db_path) as conn:
        rows = conn.execute(
            """
                SELECT ABS(transfer_price)
                FROM manager_transfers
                WHERE transfer_type = 1
                  AND transfer_price IS NOT NULL
                  AND transfer_dt >= ?
                """,
            (cutoff,),
        ).fetchall()
    return [int(r[0]) for r in rows]
```

`bid_learner.py` already imports both `sqlite3` and `time` at module level (lines 4-5), and every method in the file opens its own `with sqlite3.connect(self.db_path) as conn:` — there is no shared `_connect` helper. Match that pattern; do not introduce a second one.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_pacing_prices.py -q`
Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
git add rehoboam/bid_learner.py tests/test_pacing_prices.py
git commit -m "feat(pacing): read the measured move price from manager_transfers (REH-85)"
```

______________________________________________________________________

### Task 4: Settings fields

**Files:**

- Modify: `rehoboam/config.py` (add four fields to `Settings`, beside `overbid_floor_eur`)
- Modify: `.env.example`
- Test: `tests/test_pacing.py` (append)

**Interfaces:**

- Consumes: nothing.

- Produces: `Settings.pacing_enabled: bool`, `Settings.pacing_in_season_min_moves: int`, `Settings.pacing_window_days: int`, `Settings.pacing_median_floor_eur: int`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pacing.py`:

```python
class TestPacingSettings:
    def test_defaults_match_the_design(self, monkeypatch):
        monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
        monkeypatch.setenv("KICKBASE_PASSWORD", "test")
        from rehoboam.config import Settings

        s = Settings()
        assert s.pacing_enabled is True
        assert s.pacing_in_season_min_moves == 2
        assert s.pacing_window_days == 90
        assert s.pacing_median_floor_eur == 3_000_000

    def test_every_knob_is_overridable_from_the_environment(self, monkeypatch):
        """Re-tunable mid-season without a deploy — the REH-99 requirement."""
        monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
        monkeypatch.setenv("KICKBASE_PASSWORD", "test")
        monkeypatch.setenv("PACING_ENABLED", "false")
        monkeypatch.setenv("PACING_IN_SEASON_MIN_MOVES", "4")
        from rehoboam.config import Settings

        s = Settings()
        assert s.pacing_enabled is False
        assert s.pacing_in_season_min_moves == 4
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_pacing.py::TestPacingSettings -q`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'pacing_enabled'`

- [ ] **Step 3: Add the fields**

In `rehoboam/config.py`, inside `class Settings`, next to `overbid_floor_eur`:

```python
    pacing_enabled: bool = Field(
        default=True,
        description=(
            "REH-85: reserve enough budget after each buy to make the moves still "
            "needed. One switch, so the whole behaviour can be reverted from .env "
            "without a deploy."
        ),
    )
    pacing_in_season_min_moves: int = Field(
        default=2,
        description=(
            "Moves the reserve protects once the squad is full at 15/15, where "
            "there are no slots left to fill. A guess until the replay sweep "
            "settles it — see the REH-85 design doc."
        ),
    )
    pacing_window_days: int = Field(
        default=90,
        description=(
            "Trailing window over manager_transfers used to measure what one "
            "further move costs. A move cost EUR 6.03m league-wide but EUR 10.8m "
            "in the 2026/27 pre-season, so this is measured, not hardcoded."
        ),
    )
    pacing_median_floor_eur: int = Field(
        default=3_000_000,
        description=(
            "Floor under the measured median move price. A thin window would "
            "otherwise yield a near-zero reserve, silently disabling pacing "
            "exactly when there is least evidence."
        ),
    )
```

Add the same four keys to `.env.example` with their defaults and a one-line comment each.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_pacing.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add rehoboam/config.py .env.example tests/test_pacing.py
git commit -m "feat(pacing): settings knobs, re-tunable from .env (REH-85)"
```

______________________________________________________________________

### Task 5: Apply the cap in the bidder

**Files:**

- Modify: `rehoboam/bidding_strategy.py` (the `calculate_ep_bid` signature, and the block right after the REH-99 ceiling near line 445)
- Test: `tests/test_pacing_bidding.py`

**Interfaces:**

- Consumes: `PacingContext` from Task 1.
- Produces: `calculate_ep_bid(..., pacing: PacingContext | None = None)`. `None` means pacing is off, and every existing caller keeps working unchanged.

Placement matters and is not arbitrary. `bidding_strategy.py:434` reads `ep_max_bid = max(ep_max_bid, asking_price + overbid_amount)` — the Kimmich fix of 2026-08-24, which deliberately floors the EP cap at the asking price so a wanted player is not priced out of his own auction. A pacing cap applied *before* that line would be floored straight back off. Apply it immediately after the REH-99 ceiling, which is the last thing that lowers a bid.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pacing_bidding.py`:

```python
"""Pacing inside the live bidding path (REH-85).

These drive the real `SmartBidding.calculate_ep_bid`, because the whole defect
was that a cap existed in one place and the number that executed came from
another.
"""

from __future__ import annotations

import pytest

from rehoboam.bidding_strategy import SmartBidding
from rehoboam.services.bid_ceiling import BidCeilingPolicy, Tier
from rehoboam.services.pacing import PacingContext

CEILING = BidCeilingPolicy(
    floor_eur=250_000,
    tier_pcts={
        Tier.MARGINAL: 8.0,
        Tier.SOLID: 15.0,
        Tier.STRONG: 25.0,
        Tier.MUST_HAVE: 35.0,
    },
)


@pytest.fixture
def bidding():
    return SmartBidding(
        bid_learner=None, activity_feed_learner=None, ceiling_policy=CEILING
    )


def _bid(bidding, *, asking, mv, gain, budget, pacing=None):
    return bidding.calculate_ep_bid(
        asking_price=asking,
        market_value=mv,
        expected_points=80.0,
        marginal_ep_gain=gain,
        confidence=0.8,
        current_budget=budget,
        sell_plan=None,
        pacing=pacing,
    ).recommended_bid


def test_without_pacing_the_bid_is_unchanged(bidding):
    """None must be a true no-op, or every existing caller changes behaviour."""
    assert (
        _bid(bidding, asking=10_000_000, mv=10_000_000, gain=90.0, budget=62_307_522)
        > 0
    )


def test_a_signing_that_would_break_the_reserve_is_not_bid_on(bidding):
    """Tah: EUR 44.1m asking against a EUR 32.4m reserve on a EUR 62.3m budget.

    The cap lands below the asking price, and the existing
    `if recommended_bid < asking_price: recommended_bid = 0` turns that into a
    skip. Pacing therefore needs no refusal path of its own.
    """
    paced = _bid(
        bidding,
        asking=44_068_628,
        mv=37_028_628,
        gain=90.0,
        budget=62_307_522,
        pacing=PacingContext(reserve=32_400_000, open_offers=0),
    )
    assert paced == 0


def test_a_signing_that_fits_the_reserve_still_happens(bidding):
    """Asllani: EUR 25.1m leaves EUR 37.2m against a EUR 32.4m reserve."""
    paced = _bid(
        bidding,
        asking=25_058_860,
        mv=23_708_860,
        gain=90.0,
        budget=62_307_522,
        pacing=PacingContext(reserve=32_400_000, open_offers=0),
    )
    assert paced >= 25_058_860


def test_pacing_never_raises_a_bid(bidding):
    """It is a cap. It composes with the REH-99 ceiling; it never competes."""
    unpaced = _bid(
        bidding, asking=5_000_000, mv=5_000_000, gain=90.0, budget=62_307_522
    )
    paced = _bid(
        bidding,
        asking=5_000_000,
        mv=5_000_000,
        gain=90.0,
        budget=62_307_522,
        pacing=PacingContext(reserve=32_400_000, open_offers=0),
    )
    assert paced <= unpaced


def test_open_offers_reduce_what_may_be_committed(bidding):
    """A EUR 30m open offer plus a EUR 32.4m reserve leaves nothing spendable."""
    paced = _bid(
        bidding,
        asking=25_058_860,
        mv=23_708_860,
        gain=90.0,
        budget=62_307_522,
        pacing=PacingContext(reserve=32_400_000, open_offers=30_000_000),
    )
    assert paced == 0


def test_a_trade_pair_is_paced_on_its_net_cost(bidding):
    """The synthetic sell plan is what makes this work with no special case.

    `trader.py` gives a pair a SellPlan whose total_recovery is the sale
    proceeds, so budget_ceiling already includes them. A pair that looks
    unaffordable gross becomes affordable net — which is correct, because a
    pair recycles capital rather than consuming it.
    """
    from rehoboam.scoring.models import SellPlan

    plan = SellPlan(
        players_to_sell=[],
        total_recovery=20_000_000,
        net_budget_after=0,
        is_viable=True,
        ep_impact=0.0,
        reasoning="test",
    )
    gross_only = bidding.calculate_ep_bid(
        asking_price=25_000_000,
        market_value=25_000_000,
        expected_points=80.0,
        marginal_ep_gain=90.0,
        confidence=0.8,
        current_budget=40_000_000,
        sell_plan=None,
        pacing=PacingContext(reserve=32_400_000, open_offers=0),
    ).recommended_bid
    with_pair = bidding.calculate_ep_bid(
        asking_price=25_000_000,
        market_value=25_000_000,
        expected_points=80.0,
        marginal_ep_gain=90.0,
        confidence=0.8,
        current_budget=40_000_000,
        sell_plan=plan,
        pacing=PacingContext(reserve=32_400_000, open_offers=0),
    ).recommended_bid
    assert gross_only == 0
    assert with_pair >= 25_000_000
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_pacing_bidding.py -q`
Expected: FAIL — `TypeError: calculate_ep_bid() got an unexpected keyword argument 'pacing'`

- [ ] **Step 3: Add the parameter and the cap**

In `rehoboam/bidding_strategy.py`, add to the end of the `calculate_ep_bid` parameter list (after `is_dgw: bool = False`):

```text
pacing: PacingContext | None = None,
```

(Fenced as `text`, not `python`, deliberately: the repo's mdformat pre-commit hook runs black over python blocks, and black parses a bare `name: T = None,` fragment as a statement and rewrites it to `= (None,)` — a tuple default. That corruption is what the Task 5 implementer hit and correctly worked around. No quotes are needed on the annotation: `bidding_strategy.py` already has `from __future__ import annotations`, and ruff's UP037 removes them.)

Add the import at the top of the file:

```python
from rehoboam.services.pacing import PacingContext
```

Add to the docstring's `Args:` block:

```
            pacing: REH-85 capital pacing. When given, caps the bid so the
                reserve survives it — the budget needed to make the moves the
                squad still requires. None disables pacing entirely.
```

Then, immediately after the existing REH-99 ceiling block (the `if self.ceiling_policy is not None and market_value > 0:` block) and **before** the `if recommended_bid < asking_price:` line, insert:

```python
        # REH-85: leave enough behind to keep buying. Applied here, after the
        # REH-99 ceiling, because `ep_max_bid` is deliberately floored at the
        # asking price a few lines above (the Kimmich fix) — a cap applied
        # before that floor would simply be lifted back off.
        #
        # This caps rather than refuses. Where the cap falls below the asking
        # price the block below turns it into recommended_bid = 0, so pacing
        # needs no refusal path competing with the ceiling and the safety gate.
        if pacing is not None:
            pace_cap = pacing.max_bid(budget_ceiling)
            if recommended_bid > pace_cap:
                logger.info(
                    "ep-bid paced player=%s bid=%d -> %d (reserve=%d open_offers=%d)",
                    player_id,
                    recommended_bid,
                    pace_cap,
                    pacing.reserve,
                    pacing.open_offers,
                )
                recommended_bid = pace_cap
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_pacing_bidding.py -q && uv run pytest -q`
Expected: PASS both. Every existing caller passes `pacing=None` implicitly and is unaffected.

- [ ] **Step 5: Commit**

```bash
git add rehoboam/bidding_strategy.py tests/test_pacing_bidding.py
git commit -m "feat(pacing): cap the bid so the reserve survives it (REH-85)"
```

______________________________________________________________________

### Task 6: Build the context once per session and pass it

**Files:**

- Modify: `rehoboam/trader.py` (`get_ep_recommendations`, around lines 218-235 and both `calculate_ep_bid` call sites at 598 and 631)
- Test: `tests/test_pacing_session.py`

**Interfaces:**

- Consumes: `capital_reserve`, `median_move_price`, `available_squad_slots`, `PacingContext` from Task 1; `BidLearner.recent_buy_prices` from Task 3; the `Settings` fields from Task 4.
- Produces: `Trader._build_pacing_context(squad_size: int, my_bids: list) -> PacingContext | None`, returning `None` when `settings.pacing_enabled` is False.

`get_ep_recommendations` does not currently fetch open bids, so add one `self.api.get_my_bids(league)` call beside the existing `get_team_info` call. It is needed twice over: open offers are euros already committed, and open bids occupy squad slots.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pacing_session.py`:

```python
"""Building the pacing context from live session state (REH-85)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from rehoboam.config import Settings
from rehoboam.trader import Trader


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "test")
    return Settings()


@pytest.fixture
def trader(settings):
    learner = MagicMock()
    learner.recent_buy_prices.return_value = [10_800_000] * 9
    return Trader(api=MagicMock(), settings=settings, bid_learner=learner)


def _bid(amount):
    return SimpleNamespace(user_offer_price=amount)


def test_reserve_covers_every_unfilled_slot(trader):
    """11 players + 1 open bid = 12/15, so 3 slots at EUR 10.8m each."""
    ctx = trader._build_pacing_context(squad_size=11, my_bids=[_bid(40_717_295)])
    assert ctx is not None
    assert ctx.reserve == 32_400_000


def test_open_offers_are_carried_into_the_context(trader):
    ctx = trader._build_pacing_context(squad_size=11, my_bids=[_bid(40_717_295)])
    assert ctx.open_offers == 40_717_295


def test_full_squad_falls_back_to_the_in_season_minimum(trader):
    ctx = trader._build_pacing_context(squad_size=15, my_bids=[])
    assert ctx.reserve == 21_600_000


def test_disabled_setting_returns_no_context(trader, settings):
    settings.pacing_enabled = False
    assert trader._build_pacing_context(squad_size=11, my_bids=[]) is None


def test_a_learner_failure_disables_pacing_rather_than_breaking_the_session(settings):
    """Best-effort learning: a DB problem must never block the EP pipeline."""
    learner = MagicMock()
    learner.recent_buy_prices.side_effect = RuntimeError("db gone")
    t = Trader(api=MagicMock(), settings=settings, bid_learner=learner)
    assert t._build_pacing_context(squad_size=11, my_bids=[]) is None


def test_no_learner_at_all_disables_pacing(settings):
    t = Trader(api=MagicMock(), settings=settings, bid_learner=None)
    assert t._build_pacing_context(squad_size=11, my_bids=[]) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_pacing_session.py -q`
Expected: FAIL — `AttributeError: 'Trader' object has no attribute '_build_pacing_context'`

- [ ] **Step 3: Add the builder and wire both call sites**

Add this method to `Trader` in `rehoboam/trader.py`, immediately before `get_ep_recommendations`:

```python
def _build_pacing_context(self, squad_size: int, my_bids: list):
    """The REH-85 reserve for this session, or None when pacing is off.

    Built once per run rather than per candidate: the median move price is
    a league-level measurement, and recomputing it inside the candidate
    loop would hit the DB once per listing for an identical answer.

    Returns None — pacing disabled — rather than raising when the learning
    DB cannot be read. Pacing is a spending restraint, and the established
    rule in this codebase is that a learning-side failure never blocks the
    EP pipeline. A restraint that cannot be measured is not applied.
    """
    from .services.pacing import (
        PacingContext,
        available_squad_slots,
        capital_reserve,
        median_move_price,
    )

    if not getattr(self.settings, "pacing_enabled", True):
        return None
    if self.bid_learner is None:
        return None
    try:
        prices = self.bid_learner.recent_buy_prices(
            window_days=int(self.settings.pacing_window_days)
        )
    except Exception:
        logger.exception("pacing: could not read recent buy prices — pacing disabled")
        return None

    median_move = median_move_price(
        prices, floor_eur=int(self.settings.pacing_median_floor_eur)
    )
    open_offers = sum(int(getattr(b, "user_offer_price", 0) or 0) for b in my_bids)
    slots_to_fill = available_squad_slots(squad_size, len(my_bids))
    reserve = capital_reserve(
        slots_to_fill=slots_to_fill,
        in_season_min_moves=int(self.settings.pacing_in_season_min_moves),
        median_move=median_move,
    )
    logger.info(
        "pacing session median_move=%d slots_to_fill=%d reserve=%d open_offers=%d n_prices=%d",
        median_move,
        slots_to_fill,
        reserve,
        open_offers,
        len(prices),
    )
    return PacingContext(reserve=reserve, open_offers=open_offers)
```

In `get_ep_recommendations`, beside the existing `team_info = self.api.get_team_info(league)` line, add:

```python
        # Open bids are needed twice over for REH-85: they are euros already
        # committed, and Kickbase counts them toward the 15-player cap.
        try:
            my_bids = self.api.get_my_bids(league)
        except Exception:
            logger.exception("pacing: could not read open bids — pacing disabled this run")
            my_bids = None
        pacing = (
            None if my_bids is None else self._build_pacing_context(squad_size, my_bids)
        )
```

Then add `pacing=pacing,` to **both** `self.bidding.calculate_ep_bid(...)` calls — the plain-buy one and the trade-pair one.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_pacing_session.py -q && uv run pytest -q`
Expected: PASS both.

- [ ] **Step 5: Commit**

```bash
git add rehoboam/trader.py tests/test_pacing_session.py
git commit -m "feat(pacing): build the reserve once per session and apply it (REH-85)"
```

______________________________________________________________________

### Task 7: Prove emergency fill is exempt

**Files:**

- Test: `tests/test_pacing_session.py` (append)

**Interfaces:**

- Consumes: everything above. No production change is expected — this task exists to prove that, and to fail loudly if the exemption is ever lost.

Emergency squad fill reads `rec.recommended_bid` off recommendations produced by `get_ep_recommendations`, which are now paced. If pacing zeroed those bids, the fill loop's `if not rec.recommended_bid or rec.recommended_bid <= 0: continue` would skip every candidate and leave a slot empty at **-100**. Emergency fill runs when the squad cannot field eleven, so `slots_to_fill` is large and the reserve is correspondingly large — which is precisely when pacing bites hardest.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pacing_session.py`:

```python
def test_emergency_fill_is_not_starved_by_the_reserve(tmp_path, monkeypatch):
    """An empty lineup slot is -100; that outranks pacing.

    A short squad has many slots to fill, so its reserve is at its largest
    exactly when it most needs to buy. If pacing ever reaches this path it will
    zero the bids and the fill loop will skip every candidate.
    """
    from unittest.mock import MagicMock

    from rehoboam.auto_trader import AutoTrader
    from rehoboam.config import Settings

    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "test")
    monkeypatch.chdir(tmp_path)

    api = MagicMock()
    api.buy_player = MagicMock(return_value=None)
    trader = AutoTrader(api=api, settings=Settings(), dry_run=False)
    trader.learner = MagicMock()
    trader.learner.was_recently_sold.return_value = False

    squad = [
        SimpleNamespace(
            id=f"s{i}",
            first_name="X",
            last_name=f"P{i}",
            position="Defender",
            price=1_000_000,
            market_value=1_000_000,
            team_id=f"club{i}",
        )
        for i in range(9)
    ]
    target = SimpleNamespace(
        id="fill",
        first_name="Fill",
        last_name="Target",
        position="Forward",
        price=4_000_000,
        market_value=4_000_000,
        team_id="club99",
    )
    ctx = SimpleNamespace(
        ep_result={
            "buy_recs": [
                SimpleNamespace(
                    player=target,
                    recommended_bid=4_000_000,
                    marginal_ep_gain=10.0,
                    sell_plan=None,
                )
            ],
            "trade_pairs": [],
            "squad_scores": [],
            "market_players": {"fill": target},
        },
        my_bid_amounts={},
        my_bids=[],
        squad=squad,
        current_budget=50_000_000,
        flip_budget=50_000_000,
        executed_trade_count=0,
        matchday_phase=SimpleNamespace(days_until_match=None),
    )

    results = trader._run_emergency_squad_fill(
        league=SimpleNamespace(id="L"), ctx=ctx, fresh_squad=squad, slots_short=1
    )
    assert any(
        r.success for r in results
    ), "the slot must be filled despite the reserve"
    assert api.buy_player.call_count == 1
```

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/test_pacing_session.py::test_emergency_fill_is_not_starved_by_the_reserve -q`
Expected: PASS immediately. Emergency fill uses `rec.recommended_bid` as sized, and pacing is applied in `calculate_ep_bid`, not in the fill loop.

**If it FAILS**, pacing is reaching emergency fill and that is a real defect. Fix it by having `_run_emergency_squad_fill` fall back to `rec.player.price` when `rec.recommended_bid` is zero, and add a comment naming the -100 penalty as the reason. Do not weaken the reserve.

- [ ] **Step 3: Commit**

```bash
git add tests/test_pacing_session.py
git commit -m "test(pacing): emergency fill is exempt from the reserve (REH-85)"
```

______________________________________________________________________

### Task 8: Make pacing visible in the replay

**Files:**

- Modify: `rehoboam/replay/driver.py` (`make_ep_bid_fn`, lines 394-460)
- Modify: `rehoboam/replay/engine.py` (the `bid_fn(...)` call around line 494)
- Test: `tests/test_pacing_replay.py`

**Interfaces:**

- Consumes: `PacingContext`, `capital_reserve`, `median_move_price`, `available_squad_slots` from Task 1.
- Produces: the replay `bid_fn` signature gains a sixth positional argument — `bid(player_id: str, price: int, at: float, gain: float, budget: int, squad_size: int) -> int`.

The replay is where the failure is visible, so it must exercise pacing or the measurement is meaningless. The replay has no `manager_transfers` for the season it replays, but it does have the real transacted prices of every listing — which *is* the league's buy-price population for that season. Measure the median from those, with the same pure function, so the harness and live agree.

- [ ] **Step 1: Write the failing test**

Create `tests/test_pacing_replay.py`:

```python
"""The replay must exercise pacing, or the measurement means nothing (REH-85)."""

from __future__ import annotations

import inspect

from rehoboam.replay.driver import make_ep_bid_fn


def test_bid_fn_takes_squad_size():
    """Pacing needs slots-to-fill, which needs the squad size."""
    fn = make_ep_bid_fn(
        mv_fn=lambda pid, at: 10_000_000,
        score_fn=lambda pid, at: 80.0,
        median_move=10_800_000,
        in_season_min_moves=2,
    )
    assert len(inspect.signature(fn).parameters) == 6


def test_a_short_squad_is_capped_below_an_unaffordable_signing():
    """9 players = 6 slots to fill = a EUR 64.8m reserve on EUR 80m."""
    fn = make_ep_bid_fn(
        mv_fn=lambda pid, at: 40_000_000,
        score_fn=lambda pid, at: 80.0,
        median_move=10_800_000,
        in_season_min_moves=2,
    )
    assert fn("p1", 44_000_000, 0.0, 90.0, 80_000_000, 9) == 0


def test_an_affordable_signing_still_goes_through():
    fn = make_ep_bid_fn(
        mv_fn=lambda pid, at: 5_000_000,
        score_fn=lambda pid, at: 80.0,
        median_move=10_800_000,
        in_season_min_moves=2,
    )
    assert fn("p1", 5_000_000, 0.0, 90.0, 80_000_000, 9) >= 5_000_000
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_pacing_replay.py -q`
Expected: FAIL — `TypeError: make_ep_bid_fn() got an unexpected keyword argument 'median_move'`

- [ ] **Step 3: Wire pacing through the replay**

In `rehoboam/replay/driver.py`, change `make_ep_bid_fn`'s signature to:

```python
def make_ep_bid_fn(
    *,
    mv_fn: Callable[[str, float], int | None],
    score_fn: Callable[[str, float], float],
    median_move: int,
    in_season_min_moves: int,
) -> Callable[[str, int, float, float, int, int], int]:
```

and replace its inner `bid` function with:

```python
def bid(
    player_id: str, price: int, at: float, gain: float, budget: int, squad_size: int
) -> int:
    # REH-85: the reserve the live bot applies. Without it the harness
    # measures a bidder nobody deploys — the same reason the tiers and the
    # REH-99 ceiling are read from the shipped defaults above.
    reserve = capital_reserve(
        slots_to_fill=available_squad_slots(squad_size, 0),
        in_season_min_moves=in_season_min_moves,
        median_move=median_move,
    )
    rec = bidding.calculate_ep_bid(
        asking_price=price,
        market_value=mv_fn(player_id, at) or price,
        expected_points=score_fn(player_id, at),
        marginal_ep_gain=gain,
        confidence=0.8,
        current_budget=budget,
        sell_plan=None,
        trend_change_pct=0.0,
        pacing=PacingContext(reserve=reserve, open_offers=0),
    )
    return int(rec.recommended_bid)
```

Add to `driver.py`'s imports:

```python
from rehoboam.services.pacing import (
    PacingContext,
    available_squad_slots,
    capital_reserve,
    median_move_price,
)
```

At the `make_ep_bid_fn(...)` construction site near line 262, pass the two new arguments. Use the file's existing `_shipped_default` helper (line 173) rather than reaching into `Settings.model_fields` — `Settings` is only imported inside functions here, deliberately, so the replay stays runnable without KICKBASE credentials. It returns a float, so wrap the two integer knobs in `int(...)`:

```python
bid_fn = (
    (
        make_ep_bid_fn(
            mv_fn=corpus.market_value_at,
            score_fn=score_fn,
            median_move=median_move_price(
                [
                    int(row["price"])
                    for row in corpus.transfers_between(
                        0.0, matchdays[0].kickoff - DECISION_LEAD_SECONDS
                    )
                ],
                floor_eur=int(_shipped_default("pacing_median_floor_eur")),
            ),
            in_season_min_moves=int(_shipped_default("pacing_in_season_min_moves")),
        )
        if with_competition
        else None
    ),
)
```

**Two traps here, both easy to fall into.**

First, **leakage**. The upper bound is the first matchday's decision moment, not the end of the season. `matches_before` and `ReplayMarket.available_before` both treat this as a hard boundary — "a leak boundary, not a convenience filter" — and letting a season's later prices set an early-season constant would breach it even though the constant is only a scalar.

Second, **two different tables both called "transfers"**. `corpus.transfers_between` reads the corpus's `player_transfers`, where **`transfer_type=2` is the type carrying a real price** and is already the parameter's default. That is NOT the same encoding as `manager_transfers` in the learning DB, where **type 1 is a buy** (Task 3). Do not copy the type filter from one to the other. Take `transfers_between`'s default and pass no `transfer_type`.

`DECISION_LEAD_SECONDS` (engine.py:31, value 3600.0) is **not** currently imported by `driver.py`. Add it to the existing `from rehoboam.replay.engine import (...)` block at driver.py:18, which today lists `Matchday, SeasonResult, run_season, shipped_min_ep_gain` — do not create a second import line. `matchdays` is already in scope at the construction site, since it is passed to `run_season` a few lines above.

In `rehoboam/replay/engine.py`, update the single call site to pass the squad size:

```python
our_bid = bid_fn(
    listing.player_id, listing.price, decide_at, gain, state.budget, state.squad_size
)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_pacing_replay.py -q && uv run pytest -q`
Expected: PASS both.

- [ ] **Step 5: Commit**

```bash
git add rehoboam/replay/driver.py rehoboam/replay/engine.py tests/test_pacing_replay.py
git commit -m "feat(pacing): exercise the reserve in the season replay (REH-85)"
```

______________________________________________________________________

### Task 9: Measure it

**Files:**

- Create: `docs/superpowers/specs/2026-08-26-reh-85-capital-pacing-results.md`

**Interfaces:**

- Consumes: the finished feature.
- Produces: the results document, and a go/no-go recommendation.

Points alone would hide the failure being fixed. "Five buys and EUR 500,000 left" scores similarly to "several mid buys and EUR 500,000 left", and only one of those is fixed — so buy count and terminal budget are reported alongside the total.

- [ ] **Step 1: Record the baseline before any sweep**

```bash
uv run rehoboam replay-season --with-competition 2>&1 | tee /tmp/reh85-baseline.txt
```

Expected: a total near **26,391**. If it differs by more than a few points, stop and investigate before sweeping — the baseline has moved and every comparison below would be against the wrong number.

- [ ] **Step 2: Sweep `pacing_in_season_min_moves`**

Run the replay once per value, overriding via the environment:

```bash
for n in 0 1 2 3 4 6; do
  echo "=== in_season_min_moves=$n ==="
  PACING_IN_SEASON_MIN_MOVES=$n uv run rehoboam replay-season --with-competition
done 2>&1 | tee /tmp/reh85-sweep.txt
```

`0` is the control: the reserve then protects only unfilled slots. Compare against the disabled case too:

```bash
PACING_ENABLED=false uv run rehoboam replay-season --with-competition
```

- [ ] **Step 3: Write the results document**

Create `docs/superpowers/specs/2026-08-26-reh-85-capital-pacing-results.md` with:

- The sweep as a table: `in_season_min_moves`, total points, delta vs 26,391, **buy count**, **terminal budget**.

- An explicit statement of whether buy count rose. If it did not, the reserve converted one lockout into another and the sell side (the deferred half) is the next ticket — say so plainly rather than shipping on a points delta.

- The REH-71 caution restated: treat any delta below 6,162 points with suspicion unless it replicates.

- A recommended default for `pacing_in_season_min_moves`, with the reasoning.

- [ ] **Step 4: Live smoke test**

```bash
uv run rehoboam auto --dry-run
```

Confirm the `pacing session median_move=... reserve=... open_offers=...` line appears in the output and that its numbers match the live squad. Record them in the results document.

- [ ] **Step 5: Commit and open the PR**

```bash
git add docs/superpowers/specs/2026-08-26-reh-85-capital-pacing-results.md
git commit -m "docs(pacing): replay sweep results and recommended default (REH-85)"
git push -u origin marcobraun2013/reh-85-capital-pacing
gh pr create --title "feat(bidding): pace season capital — reserve the ability to keep buying (REH-85)"
```

The PR body must state the measured sweep, the buy-count outcome, and the live-smoke numbers. If buy count did not improve, say so in the PR body rather than in a follow-up.

______________________________________________________________________

## Self-Review

**Spec coverage.** Design §1 the rule → Tasks 1, 5. §2 the unwind → Task 1's `test_the_unwind_sequence_from_the_spec`. §3 where it applies: plain buys and pairs → Task 6; emergency fill exemption → Task 7; compliance re-bid → untouched by construction, since it never calls `calculate_ep_bid`. §4 open offers → Tasks 1, 6. §5 caps not refuses → Task 5. §6 no proposal that pacing would refuse → satisfied by construction: `_propose_buy` renders `rec.recommended_bid`, which is paced at source in Task 6. §7 configuration → Task 4. Verification §1-5 → Tasks 1-8 for units, Task 9 for the replay, sweep and live smoke.

**Placeholder scan.** No TBD/TODO. Every code step carries real code. Two steps name a conditional fallback (Task 3's connection helper, Task 8's listings accessor) — both are "match the file you are in", not deferred decisions, and both name the exact alternative.

**Type consistency.** `PacingContext(reserve, open_offers)` and `.max_bid(budget_ceiling)` are used identically in Tasks 1, 5, 6 and 8. `capital_reserve(slots_to_fill=, in_season_min_moves=, median_move=)` matches across Tasks 1, 6 and 8. `median_move_price(prices, floor_eur=)` matches across Tasks 1, 6 and 8. `available_squad_slots(squad_size, open_bid_count, cap=)` matches across Tasks 1, 2, 6 and 8. `recent_buy_prices(window_days=)` matches between Tasks 3 and 6.

**One risk the plan cannot remove.** Task 8 changes a function signature used by the replay engine, and the replay is the instrument this feature is judged by. If Task 8 is wrong, Task 9 measures the wrong thing while looking healthy. Task 8's tests assert the cap actually zeroes an unaffordable bid rather than merely that the argument was accepted.
