# ADR-0010 — The core is capital-neutral

**Status:** Accepted, 2026-11-11 (under Marco's "go with your recommendation") · **Amends:**
ADR-0008 decision 2 ("the core has first claim on the wallet and the debt allowance")

## Context

With first claim on the wallet and the allowance, the core bought about 15 upgrades a path and
gained +81 points a matchday over frozen-3, but lost €26.2m (spec §12, M2-19). Its own buys
roughly broke even (+€1.7m). The loss was crowd-out: each upgrade bought a dearer player than the
starter it displaced, the difference came out of the flip book's capital, and the repayment
before the kickoff sold flips to cover it (flip profit +€39.8m → +€17.0m, M2-20). The planner's
exchange rate μ treats cash as static wealth, but capital has a yield here: the flip book turned
the debt allowance into about +€40m in seven weeks. Requiring upgrades whose drift covers their
premium did not help (−€29.9m, M2-20): the cost is the capital, not the price path.

## Decision

A core upgrade may bid at most what the starters it displaces sell for. The core improves by
buying points cheaper than it sells them; the flip book keeps its capital. One upgrade a
session, at least 10 expected points a matchday, priced by the planner's utility, as ADR-0008.

## Consequences

- Over 15 daily starts (M2-21): +45.2 points a matchday over frozen-3, higher on 15 of 15 paths,
  and +€1.1m of money, higher on 13 of 15. Frozen as `v3-frozen-4`.
- The core never turns profit into a bigger squad. Before the reset that costs nothing (the reset
  wipes the money); after it, converting profit into squad value at the right pace is how the
  leaders reached €190–216m squads, and it is the open problem of the reset mode.
- Pricing capital explicitly (a yield on the flip book) would be the general form. It needs a
  yield measured on the same weeks it would be judged on, so it waits for out-of-sample data.
