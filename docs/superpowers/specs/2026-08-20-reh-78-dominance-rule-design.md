# REH-78 — A non-degenerate dominance rule: design

Ticket: https://linear.app/jovily/issue/REH-78
Date: 2026-08-20
Status: design approved, not yet implemented

Supersedes the "Pre-registered dominance rule" section of
`2026-08-19-reh-75-flip-diagnosis-design.md` for every run made after this
commit. REH-75's results document keeps its verdict: it records what that
rule returned, which is a fact about the rule and stays true.

## Why the old rule is replaced rather than repaired

REH-75 pre-registered a rule that could not return one of the three answers
it ranked. From the decomposition `π = Selection + Exit − Entry premium`,
with `Selection(H) = mv(H) − mv_buy` and `Exit(H) = s − mv(H)`, the `mv(H)`
cancels:

```
Selection(H) + Exit(H) ≡ Σ(s − mv_buy) = K = +€61,145,264   (constant in H)
⇒ whenever Selection < 0:  |Exit| = K + |Selection| > |Selection|,  always
```

So on any population where Selection is negative and `K` is positive, Exit
wins by exactly `K` at every horizon. REH-75's measured gaps at H=30, 45 and
60 all rest on that one number. The verdict was settled by the algebra before
any data existed.

The rule had a second defect the first one hides: it ranked by magnitude
without regard to sign, and so named a **+€177.7M gain** as the dominant
mechanism of a **loss**.

Repairing it in place, after seeing what it returned, is the post-hoc tuning
that pre-registration exists to prevent. Hence a new rule, written down and
committed before the measurement that uses it, and the old one left standing.

## The re-registered rule

> **Dominant loss mechanism.** Only terms whose **signed** population
> contribution is **negative** are eligible: a term that reduced the loss
> cannot be its cause. Entry premium enters as `−Σ(b − mv_buy)`, exactly as
> it enters the identity; a contribution of exactly zero is not negative and
> is not eligible. Among eligible terms, the one of **largest magnitude** is
> the dominant loss mechanism, and any other eligible term whose magnitude
> falls within **20%** of it — `|winner| − |other| ≤ 0.20 · |winner|` — is
> reported alongside it as **co-dominant**. If no term is eligible, the rule
> returns *no loss to explain*. The registered instrument is the fixed
> horizon **H = 30 days**; the rule is applied at every horizon in the sweep
> and all verdicts are reported, so its stability across H is visible rather
> than asserted.

Three deliberate differences from the rule it replaces:

- **Eligibility by sign.** This is what removes the degeneracy. Exit and
  Selection remain collinear — that is a property of the decomposition, not
  of the rule — but they can no longer be ranked against each other while
  one of them is a gain. The surviving comparison, Selection against Entry
  premium, is not collinear: entry premium is invariant in `H` and selection
  is not, so the sweep across `H` is a real robustness check for the first
  time.
- **A set, not a single winner.** REH-75's rule collapsed a near-tie to *no
  single dominant mechanism*, which discards the finding that two mechanisms
  are of comparable size. Reporting `{selection, entry premium}` says
  strictly more than reporting nothing, and it is the honest reading when the
  two are 0.1% apart.
- **Only *no loss to explain* is silence.** The rule now declines to answer
  in exactly one circumstance — when nothing lost money — rather than
  whenever its top two are close.

### What the rule returns, stated before it is run

Applied to the population totals **already published** in REH-75's results
document (§3, pre-REH-77), the rule returns:

| H   | Eligible (negative) terms                          | Verdict                               |
| --- | -------------------------------------------------- | ------------------------------------- |
| 14d | entry premium −116,401,328; selection −64,936,734  | **entry premium** (gap 44.2%)         |
| 21d | entry premium −116,401,328; selection −115,271,263 | **entry premium + selection** (0.97%) |
| 30d | selection −116,527,447; entry premium −116,401,328 | **selection + entry premium** (0.11%) |
| 45d | selection −141,559,888; entry premium −116,401,328 | **selection + entry premium** (17.8%) |
| 60d | selection −164,802,412; entry premium −116,401,328 | **selection** (gap 29.4%)             |

Exit timing is positive at every horizon and is eligible at none.

These are written down here for two reasons. They become the test fixtures,
so the implementation is pinned to numbers that already exist rather than to
whatever it happens to produce. And they are a prediction: given §5 below,
the re-run must reproduce this table exactly. If it does not, either the
prediction is wrong or the implementation is.

## The supplementary hold-instant view

The rule promises the realised-hold ranking beside the verdict. That figure
currently exists only in `scripts/reh75_supplementary.py` §4, outside the
determinism gate — the same "one-shot script that produces a headline number"
the REH-75 design argued against. It moves into `run_diagnosis`.

For each trip, the identity is evaluated a second time at the **sale
instant**: `Selection = mv_sell − mv_buy`, `Exit = s − mv_sell`, entry
premium unchanged. `mv_sell` is `TrainingCorpus.market_value_at(player_id, sell_date)` — the **at-or-before** lookup, never `mv_nearest`. The sale date
is a decision instant, so it follows `mv_buy`'s rule (`flip_diagnosis.py`
lines 314-321): `mv_nearest` is bidirectional and can resolve to a snapshot taken
after we sold, leaking post-sale price action into the term that measures our
exit. Trips with no snapshot at or before the sale date are censored from
this view and counted, never silently zeroed.

The report prints the registered verdict, the hold-instant verdict, and one
of three agreement labels — **identical**, **overlapping**, **disjoint** —
computed on the two verdict sets. Two empty sets are identical; one empty
set against one non-empty set is disjoint. On the published numbers the hold-instant
view has exactly one eligible term (selection +43,371,202 and exit
+17,774,062 are both gains there; entry premium −116,401,328 is not), so it
returns **entry premium**, and against the H=30 verdict the label is
**overlapping**.

This view is reported, never binding. Its Selection term is conditioned on
the outcome, because the bot chose the sale date and usually chose a local
high. The fixed horizons are the leak-free instrument and remain the
registered one.

## The prediction this re-run tests

REH-77 changed `average_points_at` to a season statistic. Inside the
diagnosis that value reaches only `flip_branches.reconstruct_branch`
(`flip_diagnosis.py` line 354). The decomposition is computed from market
values and transaction prices alone (`decompose`, line 63). Therefore, on
inputs identical to REH-75's:

- **Must not move:** the identity (§2), the horizon sweep (§3), the
  population total, the censoring counts, the floor group.
- **May move:** branch labels (§8), the flip-eligible set and its subtotals
  (§0), and the mirror-divergence count.

Registered before the run. A moved sweep is not a REH-77 effect and must be
treated as an unexplained second change, not written up as a new headline.

## Engineering

- `rehoboam/diagnostics/flip_diagnosis.py` — `dominant_loss_mechanisms(totals, *, tie_band=0.20) -> tuple[str, ...]`, returning eligible terms ordered by
  magnitude descending, empty for *no loss to explain*. It reuses
  `signed_contributions`, which already owns the one definition of each
  term's sign.
- `dominant_mechanism` is **left exactly as it is**, degeneracy docstring and
  all, and stays under test. The re-run prints its answer on a line marked
  superseded. Keeping both callable is what makes the re-run a controlled
  comparison: same data, two rules, and a difference attributable to the rule
  rather than to the fix.
- `TripRow` gains `at_hold: Decomposition | None`; `DiagnosisResult` gains the
  hold-view censored count.
- `rehoboam/diagnostics/flip_report.py` — a verdict column in the horizon
  sweep, the hold-instant block with its agreement label, and the superseded
  line.

No CLI surface changes: `diagnose-flips` takes the same arguments.

### Tests, written first

- a positive term never wins, however large — the direct regression on the
  defect that named a €177.7M gain as the cause of a loss
- a single winner when the gap exceeds the band; a co-dominant set when it
  does not; the boundary case at exactly 20%, which is co-dominant under
  `≤`, matching the old rule's arithmetic
- all-positive totals return empty, and the report renders that as *no loss
  to explain* rather than as an empty list
- the five fixtures above, from REH-75's published totals, return the five
  verdicts above
- `dominant_mechanism` still returns `exit_timing` on the H=30 fixture, so
  the degeneracy stays demonstrable rather than becoming a claim about
  deleted code
- the hold view uses the at-or-before lookup: a snapshot dated after
  `sell_date` must not be selected — mirroring the existing `mv_buy` leak
  test
- the hold view censors explicitly when no snapshot exists at or before the
  sale, and the hold-instant identity sums to realised P&L exactly

### Evidence handling

As REH-75: SHA-256 of `bid_learning.db` and `training_corpus.db` recorded in
the results document, and a determinism gate — run twice, diff byte-for-byte
— before any number is written down.

One gate REH-75 did not need: both digests must **equal** the ones REH-75
pinned. The §5 prediction compares two runs, and it is only a test of REH-77
if the inputs are the same bytes. If a digest differs, the prediction is
untestable on this run and the results document says so instead of quietly
comparing across different data.

## Caveats to state in the results, not paper over

- The rule's verdict is a statement about **all completed round trips**, not
  about the flip channel. REH-75 §0's population correction is unchanged by
  anything here.
- Eligibility by sign is itself a modelling choice. A term that is positive
  in aggregate can contain large negative contributions from individual
  trips; the rule ranks population sums and says nothing about that
  dispersion. Per-trip ranking is a different instrument, considered and
  deliberately not built here.
- The 20% band is carried over from REH-75 unchanged. It was not re-derived,
  and there is no evidence behind that particular width.

## Out of scope

- REH-71's replay re-run and the flip-policy decision. It reads the same
  corrected statistic and also wants re-running, but it has its own design
  doc and a once-only-run convention, so it is a deliberate separate act.
- Any fix to flip behaviour: the entry-premium and per-trade size caps
  (REH-75 §11), and REH-64's re-scope after REH-76 disproved its toll
  premise.
- Populating `trend_at_buy` and the buy's motive going forward (REH-75 §11
  item 1), which would retire the population correction but is a change to
  live writers, not to a diagnosis.

## Deliverable

`docs/superpowers/specs/2026-08-20-reh-78-rerun-results.md` — the re-run
under the re-registered rule, reporting the verdict, whether the §5
prediction held, and what changed in the branch labels and the eligible set.
REH-75's results document gains a header naming the sections this one
supersedes and stays otherwise untouched.
