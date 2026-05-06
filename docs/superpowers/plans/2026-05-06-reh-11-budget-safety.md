# REH-11: Budget-at-Kickoff Safety Guard — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a defense-in-depth assertion in `ExecutionService.buy()` that refuses any trade pushing budget \< 0 when days-to-kickoff ≤ 1.

**Architecture:** Single boundary guard inside `ExecutionService.buy()`. Phase logic upstream remains unchanged as the structural prevention; this guard is the final assertion. Live mode raises `BudgetSafetyError`; dry-run returns a failure `AutoTradeResult`. Both modes log at ERROR.

**Tech Stack:** Python 3.10+, pytest, unittest.mock, existing `logging` module pattern (`logger = logging.getLogger(__name__)`).

**Spec:** [`docs/superpowers/specs/2026-05-06-reh-11-budget-safety-design.md`](../specs/2026-05-06-reh-11-budget-safety-design.md)

______________________________________________________________________

## Task 1: Add `BudgetSafetyError`, extend `buy()` signature, implement guard (TDD)

**Files:**

- Create: `tests/test_execution_safety.py`

- Modify: `rehoboam/services/execution.py`

- [ ] **Step 1.1: Create the test file with the first failing test**

Write `tests/test_execution_safety.py`:

```python
"""Tests for ExecutionService budget-at-kickoff safety guard (REH-11)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from rehoboam.services.execution import (
    AutoTradeResult,
    BudgetSafetyError,
    ExecutionService,
)


@pytest.fixture
def player():
    p = MagicMock()
    p.first_name = "Test"
    p.last_name = "Player"
    p.id = "p1"
    return p


@pytest.fixture
def api():
    a = MagicMock()
    a.buy_player = MagicMock(return_value=None)
    return a


@pytest.fixture
def tracker():
    t = MagicMock()
    t.record_bid_placed = MagicMock(return_value=None)
    return t


@pytest.fixture
def service(api, tracker):
    return ExecutionService(api=api, tracker=tracker, dry_run=False)


@pytest.fixture
def dry_service(api, tracker):
    return ExecutionService(api=api, tracker=tracker, dry_run=True)


def test_live_buy_raises_when_insolvent_in_lockout_window(service, player, api):
    """days=0 + budget < price → BudgetSafetyError, no API call."""
    with pytest.raises(BudgetSafetyError) as exc_info:
        service.buy(
            league=MagicMock(),
            player=player,
            price=2_000_000,
            reason="test",
            current_budget=1_000_000,
            days_until_match=0,
        )
    assert "Test Player" in str(exc_info.value)
    assert api.buy_player.call_count == 0
```

- [ ] **Step 1.2: Run the test to verify it fails**

```bash
pytest tests/test_execution_safety.py::test_live_buy_raises_when_insolvent_in_lockout_window -v
```

Expected: FAIL with `ImportError: cannot import name 'BudgetSafetyError'` (or similar — exception type doesn't exist yet, signature has no `current_budget`/`days_until_match` kwargs).

- [ ] **Step 1.3: Add `BudgetSafetyError`, signature change, guard logic**

Edit `rehoboam/services/execution.py`. At the top of the file, after the existing imports, add the logger:

```python
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from rich.console import Console

from ..learning import LearningTracker

console = Console()
logger = logging.getLogger(__name__)


class BudgetSafetyError(Exception):
    """Raised when a buy would push budget < 0 within the kickoff lockout window.

    Defense-in-depth assertion. If this fires in prod, an upstream bug in
    MatchdayPhase / flip_budget logic let an unsafe candidate reach execution.
    """


LOCKOUT_DAYS = (
    1  # Within ~24h of kickoff: any trade that would go into debt is refused.
)
```

Replace the existing `buy()` method with:

```python
def buy(
    self,
    league,
    player,
    price: int,
    reason: str,
    sell_plan_player_ids: list[str] | None = None,
    *,
    current_budget: int,
    days_until_match: int | None,
) -> AutoTradeResult:
    """Place a buy offer at the given price.

    If the buy has a paired sell_plan (bench players to sell after winning
    the auction to recover budget), pass their IDs here. They'll be
    persisted alongside the pending bid and executed when resolve_auctions
    detects we won. This ensures buy-first-sell-after semantics: we never
    sell the old player before securing the new one.

    ``current_budget`` and ``days_until_match`` are required keyword args
    — they drive the kickoff-lockout safety guard (REH-11). Negative
    budget at kickoff = 0 points for the entire matchday, so within
    ``LOCKOUT_DAYS`` of kickoff any trade that would push budget < 0
    is refused.
    """
    if (
        days_until_match is not None
        and days_until_match <= LOCKOUT_DAYS
        and (current_budget - price) < 0
    ):
        msg = (
            f"BLOCK: buy {player.first_name} {player.last_name} "
            f"€{price:,} would leave budget €{current_budget - price:,} "
            f"with {days_until_match}d to kickoff"
        )
        logger.error(msg)
        if self.dry_run:
            return AutoTradeResult(
                success=False,
                player_name=f"{player.first_name} {player.last_name}",
                action="BUY",
                price=price,
                reason=reason,
                timestamp=time.time(),
                error=msg,
            )
        raise BudgetSafetyError(msg)

    return self._do(
        action="BUY",
        player=player,
        price=price,
        reason=reason,
        announce=f"Buying {player.first_name} {player.last_name} for €{price:,}",
        success_msg=f"Buy order placed for {player.first_name} {player.last_name}",
        api_call=lambda: self.api.buy_player(league, player, price),
        on_success=lambda: self.tracker.record_bid_placed(
            player, price, sell_plan_player_ids=sell_plan_player_ids
        ),
    )
```

- [ ] **Step 1.4: Run the test to verify it passes**

```bash
pytest tests/test_execution_safety.py::test_live_buy_raises_when_insolvent_in_lockout_window -v
```

Expected: PASS.

- [ ] **Step 1.5: Commit**

```bash
git add tests/test_execution_safety.py rehoboam/services/execution.py
git commit -m "feat(REH-11): add BudgetSafetyError guard in ExecutionService.buy()"
```

______________________________________________________________________

## Task 2: Pin guard boundaries with the remaining 5 tests

**Files:**

- Modify: `tests/test_execution_safety.py`

- [ ] **Step 2.1: Add the dry-run, out-of-window, and boundary tests**

Append to `tests/test_execution_safety.py`:

```python
def test_dry_run_returns_failure_result_no_raise(dry_service, player, api, caplog):
    """days=0 + budget < price + dry_run → failure result, no API call, ERROR logged."""
    with caplog.at_level("ERROR"):
        result = dry_service.buy(
            league=MagicMock(),
            player=player,
            price=2_000_000,
            reason="test",
            current_budget=1_000_000,
            days_until_match=0,
        )
    assert isinstance(result, AutoTradeResult)
    assert result.success is False
    assert result.error is not None and "BLOCK" in result.error
    assert api.buy_player.call_count == 0
    assert any("BLOCK" in rec.message for rec in caplog.records)


def test_solvent_buy_in_lockout_window_proceeds(service, player, api, tracker):
    """days=0 + budget > price → guard passes, API called normally."""
    result = service.buy(
        league=MagicMock(),
        player=player,
        price=2_000_000,
        reason="test",
        current_budget=10_000_000,
        days_until_match=0,
    )
    assert result.success is True
    assert api.buy_player.call_count == 1
    assert tracker.record_bid_placed.call_count == 1


def test_insolvent_buy_outside_lockout_window_proceeds(service, player, api):
    """days=2 (out of window) + budget < price → guard skipped, API called.

    The guard is matchday-locked logic only — debt-tolerant trading days
    are gated by the upstream phase's flip_budget, not this guard.
    """
    result = service.buy(
        league=MagicMock(),
        player=player,
        price=2_000_000,
        reason="test",
        current_budget=1_000_000,
        days_until_match=2,
    )
    assert result.success is True
    assert api.buy_player.call_count == 1


def test_insolvent_buy_unknown_schedule_proceeds(service, player, api):
    """days=None (unknown schedule) → guard skipped, API called.

    Unknown-schedule sessions default to phase=moderate upstream, which
    has its own flip_budget gate. The guard only fires on a positive days
    signal.
    """
    result = service.buy(
        league=MagicMock(),
        player=player,
        price=2_000_000,
        reason="test",
        current_budget=1_000_000,
        days_until_match=None,
    )
    assert result.success is True
    assert api.buy_player.call_count == 1


def test_buy_at_zero_post_budget_proceeds(service, player, api):
    """days=1 + budget == price → post-budget exactly 0, guard passes.

    Pins the boundary: '< 0' is strict, '== 0' is allowed.
    """
    result = service.buy(
        league=MagicMock(),
        player=player,
        price=2_000_000,
        reason="test",
        current_budget=2_000_000,
        days_until_match=1,
    )
    assert result.success is True
    assert api.buy_player.call_count == 1
```

- [ ] **Step 2.2: Run the full test file to verify all 6 tests pass**

```bash
pytest tests/test_execution_safety.py -v
```

Expected: 6 passed.

- [ ] **Step 2.3: Commit**

```bash
git add tests/test_execution_safety.py
git commit -m "test(REH-11): pin guard boundaries (dry-run, out-of-window, == 0 edge)"
```

______________________________________________________________________

## Task 3: Wire up the four call sites in `auto_trader.py`

**Files:**

- Modify: `rehoboam/auto_trader.py` (4 call sites)

- [ ] **Step 3.1: Update line 423 — plain buy in `run_unified_trade_phase`**

Find the existing call:

```python
result = self.execution.buy(
    league,
    obj.player,
    obj.recommended_bid,
    obj.reason,
    sell_plan_player_ids=sp_ids,
)
```

Replace with:

```python
result = self.execution.buy(
    league,
    obj.player,
    obj.recommended_bid,
    obj.reason,
    sell_plan_player_ids=sp_ids,
    current_budget=ctx.current_budget,
    days_until_match=ctx.matchday_phase.days_until_match,
)
```

- [ ] **Step 3.2: Update line 472 — pair buy after instant_sell**

Find the existing call:

```python
buy_result = self.execution.buy(
    league,
    obj.buy_player,
    obj.recommended_bid,
    f"Trade pair: EP +{obj.ep_gain:.1f}",
)
```

Replace with:

```python
buy_result = self.execution.buy(
    league,
    obj.buy_player,
    obj.recommended_bid,
    f"Trade pair: EP +{obj.ep_gain:.1f}",
    current_budget=ctx.current_budget,
    days_until_match=ctx.matchday_phase.days_until_match,
)
```

- [ ] **Step 3.3: Update line 520 — profit flip buy**

Find the existing call:

```python
result = self.execution.buy(
    league,
    opp.player,
    opp.buy_price,
    f"Flip: +{opp.expected_appreciation:.0f}% in {opp.hold_days}d",
)
```

Replace with:

```python
result = self.execution.buy(
    league,
    opp.player,
    opp.buy_price,
    f"Flip: +{opp.expected_appreciation:.0f}% in {opp.hold_days}d",
    current_budget=ctx.current_budget,
    days_until_match=ctx.matchday_phase.days_until_match,
)
```

- [ ] **Step 3.4: Update line 610 — emergency lineup buy**

Find the existing call:

```python
result = self.execution.buy(
    league,
    rec.player,
    rec.recommended_bid,
    f"Emergency lineup fill (squad short by {slots_short})",
)
```

Replace with:

```python
result = self.execution.buy(
    league,
    rec.player,
    rec.recommended_bid,
    f"Emergency lineup fill (squad short by {slots_short})",
    current_budget=budget_remaining,
    days_until_match=ctx.matchday_phase.days_until_match,
)
```

(Note: this site uses local `budget_remaining`, not `ctx.current_budget`. That local is decremented at line 619 after each successful buy in this loop — already correct.)

- [ ] **Step 3.5: Run the full test suite to confirm no regressions**

```bash
pytest -x
```

Expected: all tests pass. Any failure here means a call site was missed or the kwargs are wrong.

- [ ] **Step 3.6: Commit**

```bash
git add rehoboam/auto_trader.py
git commit -m "refactor(REH-11): pass current_budget + days_until_match at all buy sites"
```

______________________________________________________________________

## Task 4: Decrement `ctx.current_budget` after successful buys

**Files:**

- Modify: `rehoboam/auto_trader.py` (3 decrement sites)

This is the correctness backbone of the guard — without it, `ctx.current_budget` stays at session-start value and the guard reports false negatives in long sessions.

- [ ] **Step 4.1: Add decrement at line 434 (plain buy success block)**

Find:

```python
                results.append(result)
                if result.success:
                    ctx.executed_trade_count += 1
                    self.daily_spend += obj.recommended_bid
                    ctx.flip_budget -= obj.recommended_bid
                    available_slots -= 1
```

Replace with:

```python
                results.append(result)
                if result.success:
                    ctx.executed_trade_count += 1
                    self.daily_spend += obj.recommended_bid
                    ctx.flip_budget -= obj.recommended_bid
                    ctx.current_budget -= obj.recommended_bid
                    available_slots -= 1
```

- [ ] **Step 4.2: Add decrement at line 485 (pair buy success block)**

Find:

```python
if buy_result.success:
    ctx.executed_trade_count += 1
    self.daily_spend += obj.recommended_bid
    # Use the actual sell proceeds (from sell_result.price) rather
    # than the estimated market value, to avoid budget drift.
    actual_net_cost = obj.recommended_bid - sell_result.price
    ctx.flip_budget -= actual_net_cost
    # Trade pair: slot freed by sell, consumed by buy = net zero
```

Replace with:

```python
if buy_result.success:
    ctx.executed_trade_count += 1
    self.daily_spend += obj.recommended_bid
    # Use the actual sell proceeds (from sell_result.price) rather
    # than the estimated market value, to avoid budget drift.
    actual_net_cost = obj.recommended_bid - sell_result.price
    ctx.flip_budget -= actual_net_cost
    ctx.current_budget -= actual_net_cost
    # Trade pair: slot freed by sell, consumed by buy = net zero
```

- [ ] **Step 4.3: Add decrement at line 530 (profit flip success block)**

Find:

```python
                results.append(result)
                if result.success:
                    ctx.executed_trade_count += 1
                    self.daily_spend += opp.buy_price
                    ctx.flip_budget -= opp.buy_price
                    available_slots -= 1
```

Replace with:

```python
                results.append(result)
                if result.success:
                    ctx.executed_trade_count += 1
                    self.daily_spend += opp.buy_price
                    ctx.flip_budget -= opp.buy_price
                    ctx.current_budget -= opp.buy_price
                    available_slots -= 1
```

- [ ] **Step 4.4: Run the full test suite to confirm no regressions**

```bash
pytest -x
```

Expected: all tests pass.

- [ ] **Step 4.5: Commit**

```bash
git add rehoboam/auto_trader.py
git commit -m "fix(REH-11): decrement ctx.current_budget after successful buys"
```

______________________________________________________________________

## Task 5: Final verification

- [ ] **Step 5.1: Run the full test suite with coverage on the changed file**

```bash
pytest tests/test_execution_safety.py --cov=rehoboam.services.execution --cov-report=term-missing
```

Expected: 6 passed. Coverage on the guard branches shows all four cases hit (live raise, dry-run failure, days-out-of-window skip, None skip, post-budget==0 pass).

- [ ] **Step 5.2: Run lint + format checks**

```bash
ruff check rehoboam/services/execution.py rehoboam/auto_trader.py tests/test_execution_safety.py
mypy rehoboam/services/execution.py --ignore-missing-imports
```

Expected: no errors. Note: do NOT run `black <file>` manually — the repo is not black-clean and a whole-file format would cause collateral churn. Pre-commit hooks will format on commit if needed.

- [ ] **Step 5.3: Smoke test in dry-run (if Kickbase credentials are configured)**

```bash
rehoboam status
```

Expected: status command runs to completion. If the matchday is currently locked AND a candidate would breach the guard, you'll see a `BLOCK:` log line in stderr (with `-v`) and a failure row in any trade output. In normal phase=aggressive sessions, no guard hits should appear.

If credentials are not configured, skip this step — the unit tests cover the boundary correctness.

- [ ] **Step 5.4: Push the branch and open the PR**

```bash
git push -u origin marcobraun2013/reh-11-hard-block-on-negative-budget-at-kickoff
gh pr create --title "feat: hard block on negative budget at kickoff (REH-11)" --body "$(cat <<'EOF'
## Summary

- Add `BudgetSafetyError` + defense-in-depth guard inside `ExecutionService.buy()` that refuses any trade pushing budget < 0 when ≤ 1 day from kickoff. Live mode raises; dry-run returns a failure `AutoTradeResult`. Both modes log at ERROR.
- Decrement `ctx.current_budget` after successful buys (was only decrementing `flip_budget`) so the guard sees post-trade budget on subsequent buys in the same session.
- Six unit tests pin live raise, dry-run failure, out-of-window-by-budget, out-of-window-by-days, unknown-schedule, and `== 0` boundary cases.

Closes REH-11. Spec: [`docs/superpowers/specs/2026-05-06-reh-11-budget-safety-design.md`](docs/superpowers/specs/2026-05-06-reh-11-budget-safety-design.md).

## Test plan

- [x] `pytest tests/test_execution_safety.py -v` — 6 passed
- [x] `pytest -x` — full suite green
- [x] `ruff check` — clean
- [ ] First Azure run after merge: confirm no `BudgetSafetyError` fires (would indicate an upstream phase-logic bug, not a problem with this guard)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: PR opens with all CI checks queued.

______________________________________________________________________

## Summary of changes

| File                                                               | Change                                                                                                                                     |
| ------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `rehoboam/services/execution.py`                                   | Add `BudgetSafetyError`, `logger`, `LOCKOUT_DAYS`; extend `buy()` signature with kw-only `current_budget`/`days_until_match`; insert guard |
| `rehoboam/auto_trader.py`                                          | Pass new kwargs at 4 buy sites; decrement `ctx.current_budget` at 3 success blocks                                                         |
| `tests/test_execution_safety.py`                                   | New file, 6 tests (1 live raise + 5 boundary)                                                                                              |
| `docs/superpowers/specs/2026-05-06-reh-11-budget-safety-design.md` | Already committed (`c1db058`)                                                                                                              |

Approximate diff: ~85 lines production + ~155 lines tests.
