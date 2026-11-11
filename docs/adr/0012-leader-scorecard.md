# ADR-0012 — Every change is judged on the leader scorecard

**Status:** Accepted, 2026-11-11 (Marco: "test all changes against beating the leaders. I dont
want to repeat our mistakes in adding things but we are bleeding money, missing player and
points") · **Amends:** ADR-0009 (the freeze rule)

## Context

Between 2026-09-22 and 2026-11-11 the legacy bot grew by ~40 rules, each judged on its own
story, and lost money, fielded empty slots and fell behind on points. The v3 run log judged
changes against our own record and against the previous freeze, one axis at a time, so a change
could win points while bleeding money (M2-19, M2-23) and still look like progress.

## Decision

1. **One scorecard for every change** (`rehoboam desk-scorecard`, `backtest.scorecard`): over the
   15 daily windows, the configuration beside the league's top three by profit, on
   - **money:** mean total profit and mean trading profit (`backtest.trading_profit`);
   - **missing players:** kickoffs with an empty slot;
   - **points:** mean points a matchday.
1. **A change is kept only if no axis gets worse** (more empty slots, more than €3m less money,
   more than 10 points a matchday less) **and at least one gets better.** The scorecard says
   KEEP or REJECT with the reasons.
1. **Money against the leaders:** "beats" (above the best on total and trading), "same level"
   (total at least the third's, trading at least the median), else "below".
1. Configurations worth comparing are named in `backtest.NAMED_POLICIES` and run on the current
   code; frozen tags keep their own code for the holdout.

## Consequences

- No configuration on W1 is at the leaders' level on all three (M2-25). Money and empty slots can
  be at their level together (cheap fill: +€44.5m, 2/75); points cannot without spending money,
  because the leaders' squads are worth €60–80m more than ours.
- Rejected changes stay in the code only behind a flag that is off, with their run in §12.
