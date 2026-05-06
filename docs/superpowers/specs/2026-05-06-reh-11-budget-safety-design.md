# REH-11: Hard block on negative budget at kickoff

**Status:** approved 2026-05-06
**Linear:** [REH-11](https://linear.app/jovily/issue/REH-11/hard-block-on-negative-budget-at-kickoff)
**Branch:** `marcobraun2013/reh-11-hard-block-on-negative-budget-at-kickoff`

## Background

Negative budget at kickoff = 0 points for the entire matchday — one of the
two catastrophic Kickbase failure modes (the other being an empty lineup
slot at -100 pts). The bot already prevents this **structurally**:

- `MatchdayPhase.locked` (≤1 day to kickoff) sets `max_trades=0` and
  `flip_budget=0`, suppressing the candidate list entirely.
- `MatchdayPhase.moderate` (≤4 days) sets `flip_budget = current_budget − pending_bids`, excluding any debt capacity, so any buy that would push
  budget negative is filtered before reaching execution.
- `optimize_squad_for_gameday` (`trader.py:694`) actively sells bench /
  surplus GKs whenever current_budget \< 0.

What's missing is **defense-in-depth at the boundary**. If a future refactor
loosens the phase logic — or a code path like `_run_emergency_lineup_buys`
gains a corner case — there is no assertion at the actual buy execution
point that re-checks the safety property. A guard at the boundary survives
upstream changes; a guard upstream does not.

The original Linear issue spec also asked for a 48h warning + auto-suggested
sell. For an autonomous bot that runs unattended on Azure, "warn the
operator" is not a real output: warnings are useless if nobody reads them.
The 48-96h window is already structurally handled by `phase=moderate`'s
debt-free flip_budget, so no additional warning is needed. The 24h hard
block is the only real gap.

## Goal

Add a single defense-in-depth assertion inside `ExecutionService.buy()`
that refuses any trade which would push `current_budget − price < 0` when
`days_until_match ≤ 1`. Raise `BudgetSafetyError` in live mode; return a
failure `AutoTradeResult` in dry-run. Log at ERROR level in both modes so
an unexpected fire is visible in `logs/rehoboam.log`.

## Non-goals

- **Hour-level kickoff resolution.** The issue spec says `<24h`; we use the
  existing `days ≤ 1` primitive. Day resolution is precise enough — the
  Azure schedule runs 2x/day, so a finer granularity buys nothing.
- **48h warning / auto-suggest-sell.** The 48-96h window is already handled
  by `phase=moderate`'s flip_budget logic. A warning to a non-existent
  human operator is not a useful output for an autonomous bot.
- **Surfacing in a renamed `analyze` command.** `analyze` was deleted; the
  `status` command runs the full pipeline in dry-run and will surface the
  guard's failure result naturally.
- **Changes to phase logic.** Untouched. The guard is additive.

## Architecture

```
auto_trader.run_unified_trade_phase    auto_trader._run_emergency_lineup_buys
            │                                       │
            └──────────────┬────────────────────────┘
                           │ passes current_budget + days_until_match
                           ▼
            ExecutionService.buy(...)
                           │
                           ▼
                  ┌─────── guard ───────┐
                  │ days ≤ 1 AND        │
                  │ budget − price < 0  │
                  └─────────┬───────────┘
                            │
                ┌───────────┴───────────┐
                │                       │
            live mode               dry-run
                │                       │
                ▼                       ▼
       raise BudgetSafetyError    return AutoTradeResult(
       (logger.error)               success=False,
                                    error=msg)
                                    (logger.error)
```

## Components

### 1. `BudgetSafetyError` exception

Added to `rehoboam/services/execution.py`:

```python
class BudgetSafetyError(Exception):
    """Raised when a buy would push budget < 0 within the kickoff lockout window.

    This is a should-never-fire defense-in-depth assertion. If it fires in
    prod, that signals an upstream bug in MatchdayPhase / flip_budget logic
    that allowed an unsafe candidate to reach execution.
    """
```

### 2. `ExecutionService.buy()` signature

Two new keyword-only parameters:

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
```

Keyword-only to force every call site to pass them explicitly. The guard's
value disappears if a future caller forgets, so the signature should make
forgetting impossible.

### 3. Guard logic

Inserted at the top of `buy()`, before the existing `_do(...)` call:

```python
LOCKOUT_DAYS = 1  # ≤1 day == within the ~24h kickoff window

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
```

Guard returns immediately on `days_until_match is None` (unknown schedule)
without firing — that path is already conservatively gated upstream by
`phase=moderate`. No new behavior is needed for it here.

### 4. Call-site updates in `auto_trader.py`

Four buy call sites, each gains the two new keyword args:

| Line | Caller                          | Budget source            |
| ---- | ------------------------------- | ------------------------ |
| 423  | `run_unified_trade_phase` plain | `ctx.current_budget`     |
| 472  | `run_unified_trade_phase` pair  | `ctx.current_budget`     |
| 520  | profit flip buy                 | `ctx.current_budget`     |
| 610  | `_run_emergency_lineup_buys`    | local `budget_remaining` |

`days_until_match` source for all four: `ctx.matchday_phase.days_until_match`.

### 4a. Decrement `ctx.current_budget` after successful buys

**Bug to fix as part of this change.** Today, only `ctx.flip_budget` is
decremented after a successful buy (`auto_trader.py:434`, `:485`, `:530`).
`ctx.current_budget` stays at session-start value for the lifetime of the
session.

That works for the existing affordability gate (which compares against
`flip_budget`), but it breaks the new guard: a sequence of buys in a
single session could each individually pass `current_budget − price ≥ 0`
while cumulatively driving the real budget negative.

The fix: in addition to the existing `flip_budget` decrement, decrement
`ctx.current_budget` after each successful buy at lines 434, 485, 530.
The emergency path at line 619 already uses a local `budget_remaining`
that is decremented correctly — no change needed there.

For pair buys (line 485), the budget impact is `recommended_bid − sell_result.price` (the buy minus the actual sell proceeds), not the raw
`recommended_bid`. Mirror the existing `actual_net_cost` calculation.

This change is small (3 lines), but it's the correctness backbone of the
guard — without it, the guard reports false positives in long sessions.

## Data flow

1. `_build_session_context` populates `ctx.current_budget` and
   `ctx.matchday_phase.days_until_match` (existing behavior).
1. Trade phase iterates candidates and calls `self.execution.buy(..., current_budget=ctx.current_budget, days_until_match=ctx.matchday_phase.days_until_match)`.
1. After a successful buy, `ctx.current_budget` is decremented (existing
   behavior at line 434 — extend if not already wired) so subsequent calls
   see the post-trade budget.
1. `ExecutionService.buy()` evaluates the guard. If safe, falls through to
   the existing `_do(...)` scaffolding unchanged.

## Error handling

| Mode    | Guard outcome                                                            |
| ------- | ------------------------------------------------------------------------ |
| Live    | `raise BudgetSafetyError(msg)` + `logger.error(msg)`                     |
| Dry-run | `return AutoTradeResult(success=False, error=msg)` + `logger.error(msg)` |

The session loop in `auto_trader` does NOT catch `BudgetSafetyError`
specifically — it bubbles up and aborts the session. This is intentional:
the guard should never fire in steady state, and a session abort is the
right alarm channel. The Azure Function logs the traceback to
`logs/rehoboam.log`, which is already the bot's primary observability
surface.

If the session catches a generic `Exception` upstream of `execution.buy`,
verify during implementation that it doesn't silently swallow
`BudgetSafetyError`. If it does, narrow the catch.

## Testing

New file `tests/test_execution_safety.py`. Mock `api` and `tracker`; use a
minimal `Player` fixture.

| #   | Scenario                                            | Expected                                                            |
| --- | --------------------------------------------------- | ------------------------------------------------------------------- |
| 1   | `days=0, budget=€1M, price=€2M`, live               | `BudgetSafetyError` raised, no API call                             |
| 2   | `days=0, budget=€1M, price=€2M`, dry-run            | returns `AutoTradeResult(success=False)`, no API call, ERROR logged |
| 3   | `days=0, budget=€10M, price=€2M`                    | guard passes, API called normally                                   |
| 4   | `days=2, budget=€1M, price=€2M`                     | guard skipped (out of window), API called                           |
| 5   | `days=None, budget=€1M, price=€2M`                  | guard skipped (unknown schedule), API called                        |
| 6   | `days=1, budget=€2M, price=€2M` (exactly zero post) | guard passes (post-budget = 0, not \< 0)                            |

Test 6 pins the boundary explicitly — `< 0` is strict, `== 0` is allowed.

Plus a shallow integration check in the same file: instantiate
`ExecutionService` (dry_run=True) and confirm a guarded call returns a
failure `AutoTradeResult` rather than crashing the caller. No need to
exercise the full `AutoTrader.run_unified_trade_phase` — that's covered
by the unit tests at the boundary.

## Files touched

- `rehoboam/services/execution.py` — add `BudgetSafetyError`, extend
  `buy()` signature, insert guard, import `logger` from `logging_config`
- `rehoboam/auto_trader.py` — pass new kwargs at four call sites; decrement
  `ctx.current_budget` after successful buys at lines 434, 485, 530
- `tests/test_execution_safety.py` — new file, six unit tests

Approximate diff: ~85 lines (production) + ~150 lines (tests).

## Rollout

- No data migration. No schema change. No config change.
- Dry-run friendly: `rehoboam status` will surface any guard hit as a
  failure row in the trade table.
- First Azure run after merge: if any `BudgetSafetyError` fires, that's
  signal of a real upstream bug — investigate phase logic, do NOT widen
  the guard to silence it.

## Risks

- **Risk:** A legitimate sell-plan-backed buy in `phase=locked` (e.g.
  emergency lineup fill that pairs with an instant-sell) gets blocked.
  **Mitigation:** `_run_emergency_lineup_buys` already filters to plain
  in-budget candidates only (`auto_trader.py:590`), so this combination
  doesn't exist today. If it's ever introduced, the guard will catch it —
  and that's the correct outcome until the new path is reasoned through.

- **Risk:** Forgetting the new kwargs at a future buy call site. The
  keyword-only signature with no defaults makes this a `TypeError` at
  import time, not a silent failure.

- **Risk:** The guard's `current_budget` reflects ctx state, not the
  authoritative Kickbase view. If ctx drifts (e.g. an external trade
  executed by the user between the session-context fetch and the buy),
  the guard could incorrectly allow OR block. Acceptable: the upstream
  `flip_budget` check uses the same value, so the guard is no worse than
  existing logic.
