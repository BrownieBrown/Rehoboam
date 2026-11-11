# ADR-0005 — No live trading without a money-and-points verdict

**Status:** Accepted, 2026-11-11 · **Bar (B1, B2) amended by ADR-0007**

## Context

The scorer has a verdict: the calibration gate scores it against a baseline every matchday. The
trading rules never had one. From go-live on 2026-09-22, rule after rule shipped with unit tests
and one dry run on the day's squad. CLAUDE.md records the season replay as "inert by
construction" for each of them (evaluation §8). Nothing ever measured the rules together
against money and points, so their sum (−23.1m in seven weeks) was discovered in the wallet.

Trading can be backtested honestly here. Market value is set across all Kickbase leagues, so one
league's trades don't move the price paths (§7.4). Instant sales pay exactly market value. The
league's real transfers say what it took to win each listing we could have bid on.

## Decision

1. **A simulator that reproduces history first.** `sim/validate.py` replays every manager's real
   trades. Each manager's transfer P&L and net worth must match the store within 5% or €2m before
   any counterfactual is trusted.
1. **A backtest of the planner** on window W1 (2026-09-22 → 2026-11-11, from our real go-live
   squad). Auctions are resolved against the league's real buyer of each listing, or against the
   value at expiry when nobody bought it.
1. **A bar written down before the first run** (spec §8.4):
   - B1: net-worth change ≥ the league median manager's;
   - B2: points per matchday ≥ the league median;
   - B3: zero invariant violations;
   - B4: B1 and B2 robust to μ ×0.5 and ×2 and to an earlier drift fit;
   - B5: the simulator validates.
1. **Paper trading** on live data from MD10 to the reset, with the same bar, before
   `TRADING_MODE=v3` goes live.
1. **Every run is stored** (`sim_runs`: commit, parameters, cutoff, results), so a claim in a PR
   can be checked.

## Consequences

- The bar is the median, not the leaders. That is honest for six to ten matchdays of evidence,
  and the median on both points and money would still be the best this bot has done (12th of 14
  today).
- **The same planner code runs in backtest, paper and live** behind different adapters (ports and
  adapters). A green backtest is evidence about the code that will trade, not about a model of it.
- The backtest is the inner loop for every rule change from now on. A parameter change without
  a run attached is not reviewed.
- The verdict can say no. If paper trading misses the bar, the bot does not go live at the reset,
  and the spec says what to re-examine first: the drift table, then μ, then the ceiling.

## Alternatives considered

- **Ship and watch** (go live, measure in production). That is the method that produced −66.6m:
  each lesson was paid for with a real trade.
- **Scorer-style metrics** (Spearman for drift). Necessary for M1, but not sufficient. A model
  can rank well and still lose money after premiums, which is exactly what happened to the
  scorer's picks.
