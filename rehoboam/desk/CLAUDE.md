# rehoboam/desk — the v3 decision layer

The spec is `docs/superpowers/specs/2026-11-11-rehoboam-v3-design.md`; the decisions are
`docs/adr/0001`–`0010`. This file is a map, not a rule log. A rule changes by amending the
spec or an ADR, with a `desk-backtest` run attached (spec §9).

- Pure: frozen dataclasses and functions, no I/O, no clock. Adapters (`rehoboam/sim/` today,
  the live session later) build the inputs.
- Two books (ADR-0008, ADR-0010). The flip book, `flipbook.py`: exit at the turn, enter on
  the forecast within slots and the 33% debt allowance, repay before the kickoff. The core,
  `core.py`: one capital-neutral points upgrade a session. The single planner (`planner.py`,
  ADR-0003) is measured but not the frozen configuration; its churn is spec §12, M2-13/14.
- One home per concept. Solvency: `solvency.py`. Bid ceiling and value bid: `bids.py`. The best
  legal eleven (ties break by player id): `eleven.py`. The euro-to-points rate μ:
  `exchange.py`. Drift and its optional refinements: `drift.py`. The price blend: `points.py`.
- `tests/test_desk/test_architecture.py` fails the build if anything here (or in `sim/`)
  imports a legacy decision module (I-A1).
- No stored labels (ADR-0002): a player's role is recomputed from his valuation every
  session; nothing here reads a cost basis.
