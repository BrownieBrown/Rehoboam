# Bounding the capital reserve (REH-101): measured results

Date: 2026-08-27
Status: measured, recommendation below, not yet merged
Ticket: https://linear.app/jovily/issue/REH-101
Builds on: REH-85 (`docs/superpowers/specs/2026-08-26-reh-85-capital-pacing-results.md`), PR #85, unmerged
Branch: `marcobraun2013/reh-101-bounded-reserve`

## Verdict up front

Ship at `pacing_max_reserve_fraction=0.5`. The fix is real and provable on the
live position — the plain-buy cap goes from **EUR 50,227 to EUR 10,825,114**,
which is the difference between a bot that cannot buy anything and one that can
afford a median move.

It does **not** meet the acceptance criterion this ticket was filed with ("buy
count must rise above the unpaced baseline of 3"). Neither did REH-85. The
reason turns out to be that **the criterion is untestable in this instrument**,
which is a more useful finding than another failed sweep — see below. The
recommendation therefore rests on the live measurement and on unit-level
proof of the sell/headroom invariant, not on a replay points delta.

## The sweep

All runs `uv run rehoboam replay-season --with-competition`, varying only
`--pacing-max-reserve-fraction`.

|                fraction | points | delta vs no-pacing | buys | sells | final budget |
| ----------------------: | -----: | -----------------: | ---: | ----: | -----------: |
| `--no-pacing` (control) | 24,141 |                  — |    3 |     0 |        EUR 0 |
|                     0.0 | 24,141 |                  0 |    3 |     0 |        EUR 0 |
|                    0.25 | 25,309 |             +1,168 |    3 |     0 |   EUR 62,660 |
|       **0.5 (shipped)** | 25,000 |               +859 |    3 |     0 |        EUR 0 |
|                    0.75 | 25,000 |               +859 |    3 |     0 |        EUR 0 |
|  1.0 (REH-85 behaviour) | 25,000 |               +859 |    3 |     0 |        EUR 0 |

Two sanity checks pass: `0.0` reproduces `--no-pacing` exactly (24,141/3
buys), confirming the fraction genuinely disables the reserve; and `1.0`
reproduces REH-85's +859, confirming the unbounded arithmetic is still
reachable for rollback and A/B.

## Why "buy count must rise" cannot be tested here

**Buy count is 3 in every row — including the rows with no reserve at all.**
`--no-pacing` and `fraction=0.0` both remove the constraint entirely and still
buy exactly 3 players, ending on EUR 0.

That settles something REH-85 left ambiguous. REH-85 read a flat buy count of 3
as evidence that its reserve "converted one lockout into another". It is not:
buy count in this replay is **not reserve-limited at any setting**. It is
limited by how many players clear the EP-gain floor and are visible at all —
the harness's own report says buy availability is `medium`, "only players who
actually traded are visible".

So the acceptance criterion as written cannot discriminate a good reserve from
no reserve, and no amount of tuning this knob will move it. **Any future
reserve work should stop using replay buy count as its bar.** The instrument
that would answer it is a market-availability model, which does not exist.

The points column is not a tiebreaker either. The spread across 0.25-1.0 is
309 points, far below REH-71's 6,162-point noise bar. Reading 0.25 as "best"
because it scored highest would be exactly the window-tuning mistake REH-85
diagnosed and refused.

## The live measurement, which does discriminate

`uv run rehoboam auto --dry-run` against prod state, squad 12/15, budget
EUR 21,650,227, zero open offers:

```
pacing session median_move=11011011 slots_to_fill=3 reserve=10825113 \
  open_offers=0 n_prices=58 budget=21650227 max_fraction=0.50
```

|                       |    reserve |  plain-buy cap | can it afford a median move? |
| --------------------- | ---------: | -------------: | ---------------------------- |
| REH-85, unbounded     | 21,600,000 |     **50,227** | no                           |
| REH-101, fraction 0.5 | 10,825,113 | **10,825,114** | yes (median is 11,011,011)   |

The arithmetic: `min(2 x 11,011,011, 0.5 x 21,650,227) = 10,825,113`, so the
bound binds, and `21,650,227 - 0 - 10,825,113 = 10,825,114`. Candidates with a
sell plan clear more still — 12,983,712 and 15,326,607.

This is the failure the ticket was filed on, fixed: a reserve that demanded
99.8% of the wallet now demands half of it.

## The perverse sale effect, fixed

Because the reserve scales with empty slots, selling a player below the median
move price used to make the next buy *harder* — one more slot costs a full
median of reserve while the sale raises less than that.

Worked from the live position (12/15, EUR 21.65m, median EUR 10.8m), selling
someone for EUR 5m:

|                 | reserve before | headroom before | reserve after | headroom after |
| --------------- | -------------: | --------------: | ------------: | -------------: |
| unbounded (1.0) |     21,600,000 |          50,227 |    26,650,227 |          **0** |
| bounded (0.5)   |     10,825,113 |      10,825,114 |    13,325,113 | **13,325,114** |

Pinned by `TestRaisingCashMustNotMakeBuyingHarder`, which also asserts the
pathology still reproduces at fraction 1.0 — without that the suite could not
tell a real fix from a coincidence.

## Recommended default: 0.5

Chosen on reasoning, not on the 309-point spread:

- It is the point at which the reserve can never take more than half the
  wallet, so **at least half the budget is always spendable** whatever the
  slot count does. That is a property that can be stated and defended, unlike
  a number picked off a noisy sweep.
- At the live budget it clears exactly one median move (10.83m cap against an
  11.01m median), which is the smallest useful amount of freedom.
- 0.25 scored 309 points higher and leaves the bot holding almost nothing back
  (5.4m reserve at the live budget) — the failure mode REH-85 exists to
  prevent. Buying that back for a sub-noise points delta is not a trade worth
  making.
- 0.75 and 1.0 both leave the live position effectively frozen, which is the
  bug.

Re-tunable from `.env` as `PACING_MAX_RESERVE_FRACTION` without a deploy, like
the other three pacing knobs.

## Caveats

- Every REH-85 caveat still applies, in particular that the replay's buy side
  is an upper bound and that its squad builds from near-empty while the live
  bot sits at 12/15.
- The 0.25 row's non-zero final budget (EUR 62,660) is the only run that does
  not end on exactly EUR 0. Not investigated; it is within rounding of zero
  and does not change the recommendation.
- This ticket bounds the reserve. It does not give the bot a way to *raise*
  cash — that is REH-104, and it is still the larger problem. A bot that can
  spend half its wallet is not much better off when the wallet is nearly
  empty.

## Test suite

```
$ uv run pytest -q
1298 passed, 1 skipped in 4.51s
```

14 new tests in `tests/test_pacing_bounded_reserve.py`. Eighteen existing tests
were updated for the signature change — `budget` and `max_reserve_fraction`
are **required** keyword arguments on `capital_reserve`, deliberately: the
whole defect was a reserve that ignored the budget, and a default would let a
new call site reintroduce that by omission. The REH-85 arithmetic tests keep
their exact assertions by passing an ample budget at fraction 1.0, where the
bound does not bind.
