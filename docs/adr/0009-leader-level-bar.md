# ADR-0009 — Judge against the leaders, on 15 days; go live before the reset if MD10 passes

**Status:** Accepted, 2026-11-11 (Marco: "go with your recommendation but when we are done the
bot needs to be able to beat the leaders or at least be on the same level since there is also
luck involved") · **Amends:** ADR-0006 (timing), ADR-0007 (procedure)

## Context

ADR-0007 judges a candidate against our own real record, before the reset. That bar is now easy
to pass (frozen-3: +€60.0m and +20 points a matchday over it) and says nothing about the goal
Marco set: the bot must reach the leaders' level, allowing for luck. Eight paths started two days
apart left a 40-point difference in points a coin flip (M2-14). ADR-0006 put live trading at the
reset; the simulator's largest untested assumption, that rivals bid what they really bid while we
bid too, can only be tested live.

## Decision

1. **The leader-level bar.** Over the same windows, against the league's top three by profit:
   - **Same level:** mean total profit at or above the third leader's, and mean trading profit
     (each buy's exit or end value minus its price) at or above the median of the three. The
     spread among three skilled managers in the same weeks is the luck band.
   - **Beats the leaders:** both above the best of the three.
   - **Points** against the leaders are judged only from the reset, when every squad starts from
     zero, and only live. Before the reset, ADR-0007's points bar (our own real record) stays.
1. **Fifteen daily starts** (09-22 → 10-06) judge every candidate from now on, and a holdout
   starts a path every day. A freeze needs ADR-0007 passed and a gain over the frozen baseline
   on one axis without a loss on the other, paired by path:
   - **money:** a higher mean against our record and higher on at least 10 of 15 paths, with
     points no more than 10 a matchday below; or
   - **points:** at least 10 a matchday higher on the mean and higher on at least 10 of 15
     paths, with money no more than €3m below.
     Earlier freezes are never re-judged under the new procedure.
1. **Live before the reset, if MD10 passes.** If `v3-frozen-3` (or a later freeze) beats our
   real record on MD10 (about 11-24), the flip book goes live for MD11–MD14 behind the safety
   gate, every new sell path dry-run on the real squad first. If it fails on MD10, nothing goes
   live, whatever W1 said.

## Consequences

- The bar can be failed honestly: frozen-3 is at the same level on money (+€47.4m total against
  LOREY's +€39.7m; +€38.6m trading against the median's +€26.4m) and does not yet beat the leaders.
- Money earned before the reset is wiped by it, so the weeks before the reset are the cheapest
  time to learn what live auctions do to the simulator's assumptions.
- The daily starts share most of their weeks, so they are not independent draws. The gain in
  precision is smaller than √15 against √8 suggests, but it is free: two minutes a run.
