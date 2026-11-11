# ADR-0006 — Keep the foundation, rebuild the decision layer, live at the reset

**Status:** Accepted, 2026-11-11

## Context

Not everything failed. The scorer ranks the league (Spearman 0.63–0.68 against a 0.23–0.42
baseline on MD4–MD9). The lineup is set well, with no penalty this season. The store, the
ingestion and the calibration gate made the evaluation possible in one afternoon. What failed is
the decision layer built on top of them (evaluation §3, §4).

The winter reset clears squads, budgets, points and the table. In 2025/26 the league liquidated on
2025-12-20/21. Whatever the bot does in the first half is capped in cost by the reset, and the
second half starts from zero for everyone.

## Decision

**Keep**, unchanged except at their interfaces:

- the store, ingestion, scorer v2 and calibration;
- `kickoff.py`, `formation.py` and the lineup setter;
- `services/safety_gate`, `services/execution`, `services/win_curve` and `services/mv_forecast`;
- the Top-5 forced sale, the learning tracker's auction resolution, integrity, and notify.

**Build new**: `rehoboam/desk/` (pure decisions) and `rehoboam/sim/` (backtest and paper). The
live adapter is a `TRADING_MODE=v3` session that replaces `auto_trader`'s sell, trade and
debt-recovery phases.

**Delete** what the planner replaces, once v3 runs live. The full list is in spec §7: about
`bidding_strategy`, `profit_trader`, `bid_evaluator`, `squad_optimizer`, `league_compliance`, the
recommendation path in `trader`, and fourteen `services/` modules, plus the `Settings` fields
only they read.

**Timing.** Prod stays `lineup_only` until the reset. The new code backtests (M0–M2), then paper
trades (M3) through the remaining first-half matchdays, and goes live at the reset (M4) only on
the paper verdict (ADR-0005). The reset gets a mode of its own: supply pressure across the days to
the first kickoff, and a drift table fitted on the 2025/26 reset window.

## Consequences

- The deletion happens in one PR, after the replacement has run live. Until then the old code is
  dormant behind `lineup_only`, not half-removed.
- During the first half, the debt recovery and the last-day fill still run in `lineup_only`
  (evaluation §0). Marco repays the 11-19 debt by hand.
- If the paper verdict fails, the reset starts in `lineup_only` with Marco trading by hand. That
  is the same position as today, and no worse.

## Alternatives considered

- **Rewrite everything** (scorer included). The scorer is the part with a verdict, and it
  passes it.
- **Go live before the reset** on the backtest alone. That would mean three or four matchdays of
  live risk, on a squad that will be liquidated, to learn what paper trading teaches for free.
