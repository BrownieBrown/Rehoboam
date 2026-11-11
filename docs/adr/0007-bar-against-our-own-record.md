# ADR-0007 — Before the reset, beat our own record; freeze on a rolling basis

**Status:** Accepted, 2026-11-11 (Marco: "Amend", "Rolling freeze") · **Amends:** ADR-0005's bar
(B1, B2)

## Context

ADR-0005's bar compared v3 with the rivals' median **level** of points per matchday. The
ensemble backtest (spec §12, M2-5) showed that no configuration can meet it from where we start.
We enter W1 with the league's lowest team value (~130m against ~200m), and points follow team
value (ρ 0.90, evaluation §7.3). Every path started later, from a weaker real squad, scored
lower across all settings. The flaw was knowable from the evaluation before the bar was written.
It is a design error in the bar, not a result.

A single replayed path is also one draw. Neighbouring settings swung ±150–250 points a
matchday (M2-4).

## Decision

1. **Before the reset** (unequal starts), v3 must beat **our own real record from the same
   start** on money **and** points, as an ensemble mean, with no invariant violated.
1. **From the reset** (everyone starts from zero), the level bar applies as written: beat the
   rivals' median on both.
1. **Ensembles, not paths.** A configuration is judged on paths started two days apart, by the
   mean, with the spread reported.
1. **Rolling freeze.** A configuration is frozen as a git tag, `v3-frozen-N`, which pins the
   code, not just the parameters. It is judged only on rounds that kick off after its freeze
   and have finished (`rehoboam desk-holdout`, run from a worktree of the tag). Later
   improvements get their own tags and their own, later rounds. Nothing is ever judged on data
   it was tuned on.

## Consequences

- The amendment is not a pass. Under it, the first frozen candidate still fails on points:
  +€22.3m of money against our record, but −27 points a matchday (M2-6).
- Before the reset the holdout compares v3 with Marco's hand management from 11-11. That is the
  question that matters for going live: would the bot have done better than him?
- Each tag consumes the rounds after it, so a configuration frozen later has fewer rounds before
  the reset. The reset's first rounds are the fair test for all of them.
