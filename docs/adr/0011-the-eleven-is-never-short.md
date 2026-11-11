# ADR-0011 — The eleven is never short

**Status:** Accepted, 2026-11-11 (Marco: "i think it should behave now like it does after reset?
There might allways be a slot short that we need to fill?") · **Amends:** ADR-0009 (the freeze
rule does not apply to this invariant), ADR-0010 (capital neutrality yields to an empty slot)

## Context

`v3-frozen-4` fielded fewer than eleven at 24 of 75 replayed kickoffs (spec §12, M2-23). An empty
slot scores −100. The repayment before a kickoff sold starters when the flips could not cover
the debt, and the capital-neutral core can never fill a slot, because nobody is displaced to pay
for it. A fill that was measured (−€12.9m, +70.7 points a matchday) fails ADR-0009's money
tolerance for a points gain, and the obvious response, a points-first rule until the reset,
would have been written after seeing the result.

Marco's answer removed the question: the bot behaves the same before and after the reset, and
after the reset the squad starts from zero, so filling slots is the core's first job, not an
optional strategy.

## Decision

1. **The eleven the whole squad can field is never short when an upgrade can fill it.** The core
   fills an empty slot (a hole the squad, flips included, cannot field) from the wallet and the
   debt allowance, outside capital neutrality (`core.upgrade(fill_holes=True, others=flips)`).
1. **The repayment before a kickoff is the smallest sufficient sacrifice** (`flipbook.repay`):
   the set of up to three sales that covers the debt for the fewest points of the best eleven.
1. **One set of rules before and after the reset.** No pre-reset exception to the freeze rule.
   Replacing flips that stand in for starters (`core_fills_holes="core"`) remains a strategy and
   is judged by ADR-0009; it failed on money (M2-23).

## Consequences

- `v3-frozen-5` = `v3-frozen-4` plus decisions 1–2: 2 short kickoffs of 75 (both on paths that
  start in our real debt), +70.7 points a matchday over frozen-4 on 11 of 15 paths, −€12.9m.
- Its total profit (+€37.5m) falls just below the third leader's (+€39.7m): on money it is below
  ADR-0009's "same level", on points it moves toward the leaders (about 862 against 1028–1205).
- Fills are expensive: the core picks the filler that maximises its utility, often a star at a
  premium, and the repayment can sell him again the next week. A cheaper fill is the next lever.
