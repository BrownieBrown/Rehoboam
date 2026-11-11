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
- Next: executing a `Decision` behind `TRADING_MODE=v3` (instant sales, bids through
  `safety_gate`), hooked before the legacy locked stop, with `_run_debt_recovery` and the
  emergency fill skipped (they would fight `flipbook.repay` and `core.bids`).
