# rehoboam/live — v3 in the live league

The live adapter translates; it decides nothing. `v3.to_world_and_state` turns a `Snapshot` of
the real league (squad, wallet, Kickbase's open listings with exact expiries from the market
payload's `exs`, our open bids, each player's book) into the simulator's `World` and `SimState`,
and `v3.plan` calls `sim.engine.decide` with the frozen configuration (`v3.POLICY`, tag
`v3.FROZEN`). The live session and the replay run the same code, so the backtest keeps describing
what the bot does.

- `rehoboam v3-status`: read-only. GET calls and a read-only store transaction; it skips
  `_ensure_store()` because that applies pending migrations. Never run the legacy `status` to
  preview v3: a dry-run Top-5 writes `forced_sales(executed=0)` and suppresses the live sale.
- Inside Kickbase's nightly update (22:00–22:30 Berlin) a session decides as of 21:59 Berlin.
- A flip is a player whose bid or purchase is marked `intent='flip'`; everything else is core
  (ADR-0008). A bid placed by hand is core.
- `TRADING_MODE=v3` executes the `Decision` (`AutoTrader._run_v3_session`, hooked before the
  legacy locked stop): `v3.orders` sells first, `v3.execute` runs each order and reports a
  refusal without stopping the rest; bids go through `safety_gate` at the solid tier. The
  legacy `_run_debt_recovery` and emergency fill are skipped (they would fight
  `flipbook.repay` and `core.bids`). Every plan line and order result is logged (`v3 plan`,
  `v3 order`) so App Insights has them.
- The gate's budget is `v3.Headroom`, the engine's own debt rule: the wallet net of every
  open offer, plus a third of the unsold squad's value. It reproduces all 15 offers Kickbase
  accepted or refused with err 5050; `services/budget.max_debt` (a third of worth) does not.
