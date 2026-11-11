# rehoboam/desk — the v3 decision layer

The spec is `docs/superpowers/specs/2026-11-11-rehoboam-v3-design.md`; the decisions are
`docs/adr/0001`–`0006`. This file is a map, not a rule log. A rule changes by amending the
spec or an ADR, with a `desk-backtest` run attached (spec §9).

- Pure: frozen dataclasses and functions, no I/O, no clock. Adapters (`rehoboam/sim/` today,
  the live session later) build the inputs.
- One home per concept. Solvency: `solvency.py`. Bid ceiling: `bids.py`. The best legal
  eleven: `eleven.py`. The euro-to-points rate μ: `exchange.py`. The objective and search:
  `planner.py`. Drift: `drift.py`.
- `tests/test_desk/test_architecture.py` fails the build if anything here (or in `sim/`)
  imports a legacy decision module (I-A1).
- No stored labels (ADR-0002): a player's role is recomputed from his valuation every
  session; nothing here reads a cost basis.
