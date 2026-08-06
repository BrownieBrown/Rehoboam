# REH-71 — Flip policy 2×2 results

Ticket: https://linear.app/jovily/issue/REH-71
Date: 2026-08-06
Status: measurement complete, decision recorded

This document records the single, once-only run of `rehoboam replay-flip-policy`
against the 2025/26 season replay, per the design in
`2026-08-05-reh-71-flip-policy-design.md`. All four arms run with
`--with-competition`. Nothing else varies between arms.

## Determinism gate

`rehoboam replay-season --with-competition --with-flips --with-flip-buys`
(arm D's configuration) was run twice and diffed byte-for-byte. The two runs
produced identical output — `diff` exited 0. The harness is deterministic; its
result may be recorded.

## Integrity

SHA-256 of `logs/training_corpus.db`, `logs/bid_learning.db`, and
`rehoboam/scoring/v2/coefficients.json` was recorded before any replay command
ran and re-recorded after the full factorial (plus the two determinism-gate
runs and one confirmatory `replay-season` run per arm to recover each arm's
cash figures, all read-only). All three hashes are byte-identical before and
after. Nothing was mutated.

## The four arm totals

Human actual (real 2025/26 season): **26,172 points**

| Arm | flip buys | profit sells | points | delta vs actual |
| --- | --------- | ------------ | ------ | --------------- |
| A   | off       | off          | 26,391 | +219            |
| B   | off       | ON           | 27,714 | +1,542          |
| C   | ON        | off          | 26,391 | +219            |
| D   | ON        | ON           | 29,182 | +3,010          |

(Verbatim from `rehoboam replay-flip-policy`.)

## Three main effects (points)

| Effect         | value  |
| -------------- | ------ |
| Flip buying    | +734   |
| Profit selling | +2,057 |
| Interaction    | +1,468 |

Noise floor (REH-68, fixed before any run in this ticket): **6,162 points**.

All three effects (734, 2,057, 1,468) are smaller in magnitude than the
6,162-point noise floor.

## Per-arm trading P&L / round trips / win rate

Recovered from `rehoboam replay-season` run once per arm's exact flag
combination (each arm's cash section is not printed by `replay-flip-policy`
itself, only by the underlying per-arm `replay-season` report; arm D's figures
below are the ones already captured by the determinism-gate runs above, so no
arm was replayed more than the gate + one confirmatory pass required).

| Arm                                 | Realised flip P&L                                                            | Round trips completed | Win rate                                                                                                  |
| ----------------------------------- | ---------------------------------------------------------------------------- | --------------------- | --------------------------------------------------------------------------------------------------------- |
| A (flip buys off, profit sells off) | *(no Trading section — neither flip pass is enabled, so nothing is tracked)* | —                     | —                                                                                                         |
| B (flip buys off, profit sells ON)  | EUR -49,947,285                                                              | 14                    | 7.1% (1 of 14)                                                                                            |
| C (flip buys ON, profit sells off)  | EUR +0                                                                       | 0                     | 0.0% (0 of 0) — flip-buy positions open but the profit-sell pass that would realise or record them is off |
| D (flip buys ON, profit sells ON)   | EUR -34,442,588                                                              | 23                    | 21.7% (5 of 23)                                                                                           |

Real 2025/26, for comparison (printed in every arm that shows a Trading
section, and reproduced by the tool's own INCONCLUSIVE message below): **EUR
-55,256,064, 151 round trips, 27.8% win rate**.

## The tool's own verdict

Verbatim from `rehoboam replay-flip-policy`:

```
INCONCLUSIVE on points - every effect is inside the noise floor.
Per the pre-committed rule, the decision falls to the cash evidence:
real flipping lost EUR 55,256,064 at a 27.8% win rate over 151 round trips,
and every round trip pays a measured 11.7% toll. Both switches
default OFF, decided on cash rather than on points.

A labelled control, not the counterfactual season result.
```

## Which evidence decided it

**Points evidence was inconclusive.** All three main effects (buy-side +734,
sell-side +2,057, interaction +1,468) are smaller than the 6,162-point noise
floor REH-68 measured for a single faithfulness decision, on a ~27,000-point
season. None of them can be distinguished from replay-harness noise.

Per the rule fixed in advance of this run (design doc §1), when points are
inconclusive the decision falls to **cash evidence**: real 2025/26 flipping
lost EUR 55,256,064 over 151 round trips at a 27.8% win rate, with every round
trip paying a measured 11.7% toll (mean transaction price 1.117× market value
against an instant sell returning 1.00×).

## Resulting switch values

Both `Settings.enable_flip_buys` and `Settings.enable_profit_sells` remain
**`False`** (unchanged from their Task 11 defaults). The verdict did not
change anything in `rehoboam/config.py`: an INCONCLUSIVE points result means
the pre-committed rule's cash branch applies, and that branch is what the
existing `False` defaults already encode. `tests/test_replay/test_shipped_config.py::test_the_flip_switches_default_off` continues to assert exactly this and was left unchanged, since what it asserts is still what this run supports.
