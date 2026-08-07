# REH-71 — Flip policy 2×2 results

Ticket: https://linear.app/jovily/issue/REH-71
Date: 2026-08-06
Status: measurement complete, decision recorded (corrected 2026-08-06 — see
"Correction" note below; further corrections from the whole-branch review
appended 2026-08-07, marked "fix round 2")

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

**Fixed in fix round 2 (I1):** `replay-flip-policy` now prints a per-arm cash
block of its own, so the four confirmatory runs described above are no longer
needed to read these figures. The numbers in the table below are the ones
measured at the time and are unchanged — the tool was taught to print what it
already held, not re-run.

| Arm                                 | Realised flip P&L                                                            | Round trips completed | Win rate                                                                |
| ----------------------------------- | ---------------------------------------------------------------------------- | --------------------- | ----------------------------------------------------------------------- |
| A (flip buys off, profit sells off) | *(no Trading section — neither flip pass is enabled, so nothing is tracked)* | —                     | —                                                                       |
| B (flip buys off, profit sells ON)  | EUR -49,947,285                                                              | 14                    | 7.1% (1 of 14)                                                          |
| C (flip buys ON, profit sells off)  | EUR +0                                                                       | 0                     | 0.0% (0 of 0) — zero flip buys executed, not zero flips sold; see below |
| D (flip buys ON, profit sells ON)   | EUR -34,442,588                                                              | 23                    | 21.7% (5 of 23)                                                         |

Real 2025/26, for comparison (printed in every arm that shows a Trading
section, and reproduced by the tool's own INCONCLUSIVE message below): **EUR
-55,256,064, 151 round trips, 27.8% win rate**.

### The replay's round trips are NOT the same thing as the real 151

The two round-trip counts sit next to each other in the table above and invite
a like-for-like reading. They are not like-for-like, and the difference runs in
the direction that flatters the replay.

`FlipRecord` is appended in exactly one place, `engine._flip_sells` — so the
replay counts a round trip only when the **profit-taking pass** is what closed
the position. The live counterpart, `LearningTracker.record_flip_outcome`,
fires on *every* instant sell of a tracked purchase, including forced ones: a
player liquidated to restore solvency, or sold to make room for a better
signing, is a completed round trip in the real ledger and invisible in the
replay's.

The arithmetic shows the gap plainly. Arm D:

| Arm D                | count |
| -------------------- | ----- |
| Total buys           | 35    |
| Total sells          | 32    |
| Round trips recorded | 23    |

Nine sells closed positions the ledger never saw. Those are exactly the forced
exits — sales made under budget pressure or to fund an upgrade — which skew
loss-making, because a position sold because you *had* to sell it is not a
position sold at a moment of your choosing. Excluding them makes the replay's
realised P&L **optimistic**.

So: EUR -34,442,588 over 23 replay round trips is a **narrower, optimistic**
definition than EUR -55,256,064 over the real 151. It is a lower bound on flip
harm, not a measurement of it, and the two figures must not be differenced,
ratioed, or otherwise treated as the same quantity at different scales.

This gap is documented, not closed. Fixing the definition would change the
figures printed above, and REH-71's protocol fixed those numbers to a single
run by design. The comment on `FlipRecord` (`rehoboam/replay/engine.py`) states
the same caveat at the point where a future reader would extend the ledger.

Note this cuts the same way as the verdict rather than against it: the cash
evidence that decided the ticket is the *real* -EUR 55,256,064, and the replay
figure understating flip harm cannot have made flipping look worse than it is.

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

**Scope of `enable_profit_sells` (corrected in fix round 2, I2).** The switch
originally early-returned from `AutoTrader.run_profit_sell_phase`, which also
contains the **dead-weight sell** branch — releasing a position-saturated bench
player (a 5th goalkeeper, a 6th defender) so the squad slot is free for a
points upgrade. That branch serves points, not profit; the replay never
modelled it and this factorial measured nothing about it, so switching it off
with the flip verdict would have been an unmeasured live regression. The gate
now covers profit-taking and loss-cutting only, and the dead-weight release
runs regardless of the switch. No measured number in this document is affected
— the change is to live `auto` behaviour, not to the harness.

## Static checks

`uv run mypy rehoboam/ --ignore-missing-imports`:

| commit                                       | errors |
| -------------------------------------------- | ------ |
| `5592a3d` (main, the branch point)           | 68     |
| `feat/reh-71-flip-policy` before fix round 2 | 71     |

An earlier task report recorded all 71 as pre-existing. That was wrong: the
true baseline is **68 pre-existing + 3 introduced by this branch**.

The three:

- `rehoboam/replay/engine.py` — two `arg-type` errors on the
  `_would_create_dead_weight(player, state.players)` call. This is the
  deliberate duck typing the plan sanctions: the shipped guard is annotated
  against `MarketPlayer` but reads only `.position`, which `ReplayPlayer` has.
  Calling the real rule beats reimplementing it, and widening the shipped
  signature to a `Protocol` would be a refactor of live scoring code that a
  replay-only change has no business making.
- `rehoboam/replay/flip_buys.py` — one `attr-defined` error on `o.player.id`.
  `ProfitOpportunity.player` is annotated `any` (the builtin function, not
  `typing.Any`) in shipped code this module does not own.

All three now carry narrowly-scoped `# type: ignore[...]` comments explaining
why, which returns the branch to the 68-error baseline. No unrelated typing
debt was touched.

## Appendix: the arm A / arm C captures

The correction above ("Arm C: flip buying was unreachable") is load-bearing —
it is what establishes that the +734 buy-side effect is the interaction halved
rather than an independent measurement. Its evidence was a pair of
confirmatory `replay-season` captures held in a session scratchpad. Reproduced
here verbatim so the claim stays verifiable after that scratchpad ages out.

**Arm A** — `replay-season --with-competition` (flip buys off, profit sells
off). No Trading section is printed, because neither flip pass is enabled:

```
Simulated total:    26,391 points
Actual total:       26,172 points
Difference:           +219 points

FINISHING POSITION: 9 of 14

Matchdays zeroed by negative budget: none
Total buys: 5   Total sells: 2
Final budget: EUR 500,000

Bid competition IS modelled: a listing is won only by bidding above
what the real buyer paid, and the winning bid is what we pay.
Profit flipping is NOT modelled: the bot sells only to make room or
to restore solvency, while the live bot also trades for gain.
Flip BUYING is NOT modelled: every buy here is justified by expected
points, while the live bot also buys purely for appreciation.
```

**Arm C** — `replay-season --with-competition --with-flip-buys` (flip buys ON,
profit sells off). Identical points, identical trade counts, identical final
budget; the Trading section appears but is empty:

```
Simulated total:    26,391 points
Actual total:       26,172 points
Difference:           +219 points

FINISHING POSITION: 9 of 14

Trading (cash - does not enter the points attribution above)
--------------------------------------------------------------------
  Realised flip P&L                                EUR +0
  Round trips completed                                 0
  Win rate                                           0.0%   (0 of 0)
  Real 2025/26, for comparison            EUR -55,256,064
                                                      151 trips, 27.8%

Matchdays zeroed by negative budget: none
Total buys: 5   Total sells: 2
Final budget: EUR 500,000

Bid competition IS modelled: a listing is won only by bidding above
what the real buyer paid, and the winning bid is what we pay.
Profit flipping is NOT modelled: the bot sells only to make room or
to restore solvency, while the live bot also trades for gain.
Flip BUYING is modelled: candidates come from the real ProfitTrader,
bid at an economic ceiling rather than by marginal EP gain.
```

The arm C capture also carried 49 `⚠️ Filtered ... from profit trades: Likely small sample size` warnings ahead of the report, from
`ProfitTrader.find_profit_opportunities` — the evidence cited above that
candidates were being generated and screened throughout the season while the
engine's purchase gate rejected every one. Two of them, verbatim:

```
⚠️  Filtered   from profit trades: Likely small sample size (101.5 pts/game,
+232.4% trend)
⚠️  Filtered   from profit trades: Likely small sample size (80.2 pts/game,
+226.4% trend)
```

Fix round 2 note: arm C's `EUR +0 / 0 trips / 0.0%` above is a **structural
zero** — with profit selling off, `_flip_sells` returns before the ledger is
ever written, so no round trip could close there regardless of how much the
arm bought. The report now says so on its face (M1); the capture above predates
that annotation.
