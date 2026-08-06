# REH-71 — Flip policy 2×2 results

Ticket: https://linear.app/jovily/issue/REH-71
Date: 2026-08-06
Status: measurement complete, decision recorded (corrected 2026-08-06 — see
"Correction" note below)

This document records the single, once-only run of `rehoboam replay-flip-policy`
against the 2025/26 season replay, per the design in
`2026-08-05-reh-71-flip-policy-design.md`. All four arms run with
`--with-competition`. Nothing else varies between arms.

**Correction (fix round 1):** the first version of this document mischaracterised
arm C as having opened flip-buy positions that a disabled profit-sell pass
then failed to realise. That is wrong — arm C executed **zero** flip buys and
is identical to arm A in every respect, not only in points. See "Arm C: flip
buying was unreachable" below for the corrected account, its arithmetic
consequence for the buy-side main effect, and the mechanism established from
source. The measurement protocol and the verdict are unaffected.

**Every points effect measured positive** — flip buying +734, profit selling
+2,057, interaction +1,468 — even though the decision that follows goes
*against* turning flipping on. That is the pre-committed noise-floor rule
doing its job, not a contradiction to paper over: none of the three effects
clears the bar that would let them override the cash evidence, so the rule
routes to cash regardless of sign. A reader should see this tension plainly
rather than have it smoothed away.

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

**The buy-side effect carries no independent information.** Arm C executed
zero flip buys (see "Arm C: flip buying was unreachable" below) and is
therefore identical to arm A, so `C − A = 0` exactly. Substituting into the
main-effect formula:

```
buy_effect  = mean(C, D) − mean(A, B) = (C + D)/2 − (A + B)/2
            = (A + D)/2 − (A + B)/2        [C = A]
            = (D − B)/2
            = 1,468 / 2 = 734
```

The reported "flip buying" effect of +734 is the interaction, halved, and
nothing else. This run did not measure flip buying as a separable effect —
it measured what happens when flip buying is added on top of profit selling
that is already on (arm B → D), because that is the only pair in which flip
buying ever actually fired.

## Per-arm trading P&L / round trips / win rate

Recovered from `rehoboam replay-season` run once per arm's exact flag
combination (each arm's cash section is not printed by `replay-flip-policy`
itself, only by the underlying per-arm `replay-season` report; arm D's figures
below are the ones already captured by the determinism-gate runs above, so no
arm was replayed more than the gate + one confirmatory pass required).

| Arm                                 | Realised flip P&L                                                            | Round trips completed | Win rate                                                                |
| ----------------------------------- | ---------------------------------------------------------------------------- | --------------------- | ----------------------------------------------------------------------- |
| A (flip buys off, profit sells off) | *(no Trading section — neither flip pass is enabled, so nothing is tracked)* | —                     | —                                                                       |
| B (flip buys off, profit sells ON)  | EUR -49,947,285                                                              | 14                    | 7.1% (1 of 14)                                                          |
| C (flip buys ON, profit sells off)  | EUR +0                                                                       | 0                     | 0.0% (0 of 0) — zero flip buys executed, not zero flips sold; see below |
| D (flip buys ON, profit sells ON)   | EUR -34,442,588                                                              | 23                    | 21.7% (5 of 23)                                                         |

Real 2025/26, for comparison (printed in every arm that shows a Trading
section, and reproduced by the tool's own INCONCLUSIVE message below): **EUR
-55,256,064, 151 round trips, 27.8% win rate**.

## Arm C: flip buying was unreachable

Arm C (flip buys ON, profit sells off) is identical to arm A (flip buys OFF,
profit sells OFF) in every measured respect, not only in points:

| Arm | points | total buys | total sells | final budget |
| --- | ------ | ---------- | ----------- | ------------ |
| A   | 26,391 | 5          | 2           | EUR 500,000  |
| C   | 26,391 | 5          | 2           | EUR 500,000  |

(from `reh71-armA.txt` / `reh71-armC.txt`, the confirmatory `replay-season`
captures behind the per-arm cash table above). Turning flip buying on changed
nothing whatsoever: arm C executed **zero** flip buys across the whole season,
not merely zero *sold* flips.

**Mechanism, established from `rehoboam/replay/engine.py` rather than
speculated:**

- `_flip_buys` (`engine.py:244-324`) runs after the ordinary EP-buy pass, and
  by design **never displaces a squad member** to fund itself — its own
  docstring: "A flip never displaces a squad member, so this stops at
  `MAX_SQUAD_SIZE` rather than calling `_fieldable_sale_victim`." There is no
  sale attached to a flip buy that could raise cash for it.
- Every flip candidate must clear `_solvent_after(state, cost)`
  (`engine.py:315-316`), called with `proceeds=0`. `_solvent_after`
  (`engine.py:89-109`) requires `state.budget - cost >= 0` — the *literal*
  cash balance must cover the purchase, with **no debt allowance**. This is
  deliberately stricter than `can_buy`'s own credit-line check
  (`rules.py:29-31`), which permits going negative up to a team-value-based
  floor; the docstring on `_solvent_after` explains why: a negative balance at
  kickoff zeroes the whole matchday, so this is an additional decision-time
  gate on top of the standing legality rule.
- Both arms A and C end the season on **EUR 500,000** — a balance far below
  the real transaction prices observed throughout the replay (asks in the
  captured logs range from ~EUR 4,000,000 to ~EUR 71,000,000). Because the EP
  pass runs first and spends the budget down, and because profit selling is
  off in both A and C so nothing replenishes it, the cash balance available to
  `_flip_buys` for the rest of the season sits far under what any candidate
  costs.
- This is consistent with what the arm C output actually shows: candidates
  *were* being generated and evaluated — `ProfitTrader.find_profit_opportunities`
  printed 49 "Filtered ... from profit trades: Likely small sample size"
  warnings during the arm C run, meaning that many candidates cleared its own
  affordability check (`profit_trader.py:88-102`) far enough to reach the
  small-sample filter. That affordability check is *more lenient* than
  `_solvent_after`: it uses `current_budget + max_debt`, a debt-capacity figure
  derived from team value, not the literal cash balance. So the opportunity
  finder kept finding and screening candidates throughout the season, while
  the engine's actual purchase gate — which allows no debt at all for a flip —
  rejected every single one.

**What the captured output cannot settle:** there is no per-candidate log
inside `_flip_buys` itself, so this run cannot distinguish "every candidate
was rejected specifically by `_solvent_after`" from "some were instead
rejected earlier by the wash-trade guard, the team-limit check, or the
`with_competition` ask-vs-ceiling comparison at `engine.py:303-305`." The
identical A/C trade counts prove the *outcome* (zero flip buys) beyond doubt;
the architecture (flips fund nothing, and the solvency gate is the strictest
budget check in the path, with budget already exhausted by the EP pass) makes
insufficient real cash the best-supported explanation for *why*, but it is an
inference from reading the code together with the aggregate result, not a
traced observation of each rejected candidate. No new replay was run to
settle this further, per instruction.

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
season. None of them can be distinguished from replay-harness noise — and, per
the correction above, the buy-side figure is not even an independent
measurement to begin with (it is the interaction halved, because arm C never
executed a flip buy). Every effect that was measured came out positive, which
makes the routing to cash evidence a deliberate application of the
pre-committed rule, not a coincidence that happened to favour it.

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
