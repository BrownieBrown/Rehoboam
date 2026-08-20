# REH-78 — The re-run under the re-registered dominance rule: results

Ticket: https://linear.app/jovily/issue/REH-78
Date: 2026-08-20
Design: `2026-08-20-reh-78-dominance-rule-design.md` (binding, pre-registered, **not amended**)
Supersedes: §4 of `2026-08-19-reh-75-flip-diagnosis-results.md`
Status: measurement complete. This is a re-run, not a fix.

______________________________________________________________________

## Summary

The §5 prediction **held**. On byte-identical inputs, nothing the design said must
not move moved: the identity, the horizon sweep, the population total, the
censoring counts and the floor group are all unchanged from REH-75. The things
the design said *may* move did move, and only those.

The re-registered rule returns, at the registered instrument **H = 30 days**:

> **selection + entry premium (co-dominant)**

against REH-75's superseded **exit timing**. The supersession is attributable to
the rule, not to the data — the two rules ran on the same run, over the same
bytes, and both answers are printed by the same report.

______________________________________________________________________

## 1. Input digests

```
76e55eba3c68aa147809c09467336166951935662d800954209a6bc1472f18ce  logs/bid_learning.db
0af472a7ac5a9193348def8bfa8cb53cf83f3650fe2373b1971a4b9314b62999  logs/training_corpus.db
```

**Both match REH-75's pins exactly.** The §5 prediction is therefore a clean
comparison of two runs over the same bytes, and it is a test of REH-77 rather
than of a data change. No "untestable on this run" caveat is needed.

The digests were re-checked after every run in this document — including the
supplementary script — and are unchanged. `run_diagnosis` opens both databases
`mode=ro`, so it cannot alter its own cited evidence.

______________________________________________________________________

## 2. Quality gates

Run before any number below was written down.

| Gate                                                         | Result                                                                                                                                                               |
| ------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `uv run pytest -q`                                           | **786 passed, 1 skipped** (REH-75's document reports 755/1; its own round-2 fix `f97bbe6` took it to 763/1; the 23 since are REH-78's 18, REH-79's 3 and REH-77's 2) |
| `uv run ruff check rehoboam/ tests/`                         | **All checks passed**                                                                                                                                                |
| `uv run mypy rehoboam/diagnostics/ --ignore-missing-imports` | **0 errors in `rehoboam/diagnostics/`**; 47 pre-existing errors in 8 files reached through imports                                                                   |
| `uv run bandit -r rehoboam/diagnostics/ -c pyproject.toml`   | **No issues identified.** 746 lines scanned, 0 high / 0 medium / 0 low                                                                                               |

The two scoped gates are the ones the task brief names. For comparability with
REH-75, which ran both repo-wide, the repo-wide forms were also run and are
unchanged from REH-75:

| Gate (repo-wide)                               | REH-75                | This run                                   |
| ---------------------------------------------- | --------------------- | ------------------------------------------ |
| `uv run mypy rehoboam/`                        | 68 errors in 18 files | **68 errors in 18 files**                  |
| `uv run bandit -r rehoboam/ -c pyproject.toml` | 24 findings, 0 high   | **24 findings (17 low, 7 medium), 0 high** |

Pre-existing errors elsewhere in the repo are recorded, not fixed. The
per-file mypy breakdown reached from `rehoboam/diagnostics/`:
`kickbase_client.py` 26, `bidding_strategy.py` 5, `profit_trader.py` 5,
`formation.py` 4, `bid_learner.py` 2, `config.py` 2,
`services/trend_service.py` 2, `activity_feed_learner.py` 1. **None in
`rehoboam/diagnostics/`.**

______________________________________________________________________

## 3. Determinism gate

```
uv run rehoboam diagnose-flips > run1.txt
uv run rehoboam diagnose-flips > run2.txt
diff run1.txt run2.txt   →   exit 0
```

**DETERMINISTIC.** Both files are 4,968 bytes and share the SHA-256
`723bb546c24c0ebfb7594fa1a06239ea0ba9120b0129bfb63f838b22c1b41f26`. The verbatim
output is in the appendix.

The supplementary script (§7 below, and the source of REH-75's §5–§9 figures)
is **not** covered by that gate — the same limitation REH-75's appendix states.
It was nonetheless run twice here and its output diffed byte-for-byte: identical.

______________________________________________________________________

## 4. The prediction

The design registered, before the run:

> - **Must not move:** the identity (§2), the horizon sweep (§3), the
>   population total, the censoring counts, the floor group.
> - **May move:** branch labels (§8), the flip-eligible set and its subtotals
>   (§0), and the mirror-divergence count.

### 4.1 The verdict: HELD

Every one of the five "must not move" items is unmoved.

| Registered as immovable   | REH-75                                 | This run                                     | Moved? |
| ------------------------- | -------------------------------------- | -------------------------------------------- | ------ |
| Horizon sweep, all 5 rows | see below                              | byte-identical                               | **no** |
| Identity, `Σ(b − mv_buy)` | +€116,401,328                          | +€116,401,328                                | **no** |
| Identity, `Σ(s − mv_buy)` | +€61,145,264                           | +€61,145,264 (`Selection + Exit` at every H) | **no** |
| Population total          | −€55,256,064, 136 scored of 151        | −€55,256,064, 136 scored of 151              | **no** |
| Censoring                 | 0 at every H; 0 rows with no MV at buy | 0 at every H; 0 rows with no MV at buy       | **no** |
| Floor group               | 15 trips, €0                           | 15 trips, €0                                 | **no** |

The run's own self-validation line agrees: `Ground truth: sum(realised) over the 136 scored round trips = EUR -55,256,064` and *"Every Total above equals the
realised P&L of the rows behind it — the identity closes with no residual."*

Two figures outside the registered list also did not move, and are recorded
because a reader may check them: the temporal split at 2026-01-03
(+€5,425,455 / 43 trips before, −€60,681,519 / 93 trips on-or-after) and the
hold-instant totals (§6).

### 4.2 The comparison as the task brief specified it — and why it is not the evidence

The brief's step 4 is:

```bash
sed -n '/^Horizon sweep/,/^Ground truth/p' run1.txt > sweep-new.txt
sed -n '/^Horizon sweep/,/^Ground truth/p' \
  docs/superpowers/specs/2026-08-19-reh-75-flip-diagnosis-results.md > sweep-old.txt
diff sweep-old.txt sweep-new.txt && echo "PREDICTION HELD"
```

**As written it exits 1**, and it would be dishonest to print "PREDICTION HELD"
off it. It exits 1 for two mechanical reasons, neither of which is a number:

1. **The sed range does not terminate on the old side.** REH-75's appendix
   contains no line beginning `Ground truth`, so the range ran to end-of-file:
   76 lines extracted from the old document against 9 from the new run. Most of
   the reported diff is the rest of REH-75's appendix and document.

1. **The report's sweep block gained two format elements after REH-75's appendix
   was pasted, in REH-75's own commit.** Commit `f97bbe6`
   ("read-only corpus, a caveat on the bare verdict, and dead-code cleanup",
   item M13) added an `n` column to the sweep and a two-line
   `Ground truth:` / *identity closes* pair beneath it. The results document was
   committed at `1a8383c`, two commits earlier, and its appendix was never
   regenerated. The drift predates REH-78 entirely and is not attributable to it.

The same comparison, terminated on a line both versions share and with the two
REH-75-era additions removed, is the real test:

```bash
sed -n '/^Horizon sweep/,/^Rows with no market value/p' run1.txt \
  | grep -v '^Ground truth:' | grep -v '^  Every Total above equals' \
  | awk '/^(Horizon  |[0-9]+d )/ {print substr($0,1,81) substr($0,89); next} {print}' \
  > sweep-new-normalised.txt
sed -n '/^Horizon sweep/,/^Rows with no market value/p' \
  docs/superpowers/specs/2026-08-19-reh-75-flip-diagnosis-results.md > sweep-old.txt
diff sweep-old.txt sweep-new-normalised.txt
```

**`diff` exits 0.** The nine-line block — header, rule, column header, five data
rows, and the "Rows with no market value at buy: 0" line — is byte-for-byte
identical to REH-75's:

```
Horizon sweep (population totals; the EUR 500,000 floor group is excluded)
------------------------------------------------------------------------
Horizon           Selection              Exit     Entry premium             Total  Censored
14d         EUR -64,936,734  EUR +126,081,998  EUR +116,401,328   EUR -55,256,064         0
21d        EUR -115,271,263  EUR +176,416,527  EUR +116,401,328   EUR -55,256,064         0
30d        EUR -116,527,447  EUR +177,672,711  EUR +116,401,328   EUR -55,256,064         0
45d        EUR -141,559,888  EUR +202,705,152  EUR +116,401,328   EUR -55,256,064         0
60d        EUR -164,802,412  EUR +225,947,676  EUR +116,401,328   EUR -55,256,064         0
Rows with no market value at buy: 0 (fully censored, unlabelled — not the floor group)
```

The `n` column the normalisation strips reads `136` on all five rows, which is
itself a check the old table could not carry: the panel is balanced, so the
curve across H is comparable point to point.

**Why the prediction was right.** REH-77 changed `average_points_at`, and inside
the diagnosis that value reaches only `flip_branches.reconstruct_branch`
(`flip_diagnosis.py:412`). `decompose` (line 63) is computed from market values
and transaction prices alone. The sweep cannot see the branch label, so a
corrected label cannot move it.

______________________________________________________________________

## 5. The registered verdict

The rule, quoted from the design doc that pre-registered it:

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

### 5.1 At the registered instrument, H = 30d

Quoted from the run, not recomputed:

```
Registered verdict at H=30d (REH-78): selection + entry premium (co-dominant)
  Only negative contributions are eligible; a term that reduced the loss cannot be its cause.
```

Beside the superseded REH-75 answer, from the same run:

```
Headline at H=30d: dominant mechanism = exit_timing
  (note: this term's contribution is POSITIVE — the rule ranks by magnitude, so the named term is the largest number, not necessarily the cause of the loss)
  Selection:      EUR -116,527,447
  Exit timing:    EUR +177,672,711
  Entry premium:  EUR +116,401,328  (paid over market value, unnegated)
  Total:          EUR -55,256,064
  (superseded: this rule could not name selection — see the registered verdict below and REH-78)
```

**The verdict delta is attributable to the rule and to nothing else.** Both
rules were evaluated inside a single `format_report` call, on one
`DiagnosisResult`, over the same three totals. `dominant_mechanism` was left
exactly as REH-75 authored it, degeneracy docstring and all, and stays under
test — which is what makes this a controlled comparison rather than an
assertion about deleted code.

### 5.2 At every horizon

Quoted from the run:

```
Dominance by horizon (REH-78 rule)
------------------------------------------------------------------------
14d      entry premium
21d      entry premium + selection (co-dominant)
30d      selection + entry premium (co-dominant)
45d      selection + entry premium (co-dominant)
60d      selection
```

Beside REH-75's superseded rule at the same horizons (from its §4, third table):

| H   | REH-75's rule (superseded)   | REH-78's registered rule      |
| --- | ---------------------------- | ----------------------------- |
| 14d | no single dominant mechanism | **entry premium**             |
| 21d | exit timing                  | **entry premium + selection** |
| 30d | **exit timing** (headline)   | **selection + entry premium** |
| 45d | exit timing                  | **selection + entry premium** |
| 60d | exit timing                  | **selection**                 |

Two things are worth stating plainly.

**The rule reproduced its own pre-written fixture table exactly.** The design
doc, under *"What the rule returns, stated before it is run"*, wrote down all
five verdicts before the implementation existed, derived from REH-75's already
published population totals. All five match, at every horizon, including the
ordering inside the two co-dominant sets (`entry premium + selection` at 21d,
`selection + entry premium` at 30d and 45d — the order is by magnitude
descending, and it flips between those two horizons because selection overtakes
entry premium there). The rule was pinned to numbers that already existed rather
than to whatever it happened to produce.

**Exit timing is eligible at no horizon.** It is positive at all five — from
+€126.1M at 14d to +€225.9M at 60d — so under eligibility-by-sign it can never
be named. That is the defect the ticket exists to correct: REH-75's rule named a
+€177.7M *gain* as the dominant mechanism of a €55.3M *loss*.

**The surviving comparison is not collinear, and the sweep is now a real
robustness check.** `Selection + Exit ≡ Σ(s − mv_buy) = +€61,145,264` is constant
in H, so those two remain mirror images — a property of the decomposition, not
of the rule. But entry premium is invariant in H while selection is not, and the
sweep above shows the consequence: the verdict genuinely migrates from entry
premium at 14d, through a co-dominant band, to selection at 60d, as selection
deepens past the fixed premium. Under REH-75's rule every horizon returned the
same term for the same algebraic reason.

______________________________________________________________________

## 6. The hold-instant view

The design moved this out of `scripts/reh75_supplementary.py` §4 and into
`run_diagnosis`, so it now sits inside the determinism gate. Quoted from the run:

```
Supplementary — the identity at each trip's realised hold (NOT the registered instrument: the sale date is bot-chosen, so selection is conditioned on the outcome)
------------------------------------------------------------------------
  Selection:      EUR +43,371,202
  Exit timing:    EUR +17,774,062
  Entry premium:  EUR +116,401,328  (paid over market value, unnegated)
  Total:          EUR -55,256,064
  Verdict: entry premium
  Agreement with the registered verdict: overlapping
  Censored (no market value at or before the sale): 0
```

All four totals are identical to the values `scripts/reh75_supplementary.py` §4
printed for REH-75, which is the check that the move into `run_diagnosis` was
faithful rather than a re-derivation. `mv_sell` is
`TrainingCorpus.market_value_at` — the **at-or-before** lookup, never
`mv_nearest`, because the sale is a decision instant and a bidirectional lookup
can resolve to a snapshot taken after we sold. **0 trips censored**: every trip
has a snapshot at or before its sale.

The verdict here has exactly one eligible term, as the design predicted:
selection (+€43.4M) and exit (+€17.8M) are both gains at the realised hold, and
only entry premium (−€116.4M) is negative. The agreement label against the H=30d
verdict `{selection, entry premium}` is **overlapping** — also as predicted.

**This view is reported, never binding.** Its selection term is conditioned on
the outcome, because the bot chose the sale date and usually chose a local high.
The fixed horizons are the leak-free instrument and remain the registered one.

______________________________________________________________________

## 7. What moved

Everything in this section is on the design's "may move" list. Every figure is
taken from a run output, and the derivation of each is named.

### 7.1 Mirror divergence: 0 → 0 (unchanged)

```
Mirror divergence: 0 rows (expected — the branch reconstruction agrees with the shipped ProfitTrader ladder on every labelled row).
```

REH-75 reported 0 of 151. This run reports 0 of 151. The reconstruction still
agrees with the shipped `ProfitTrader` ladder on every labelled row — which is
the expected result, since REH-77 changed an input fed to *both* sides of that
reconciliation. As REH-75's §8 already said, the reconciliation "says nothing
about whether those inputs match what the live bot saw"; a corrected input
agrees with itself just as a wrong one did. **A zero here is not evidence that
REH-77 changed nothing.** The branch table below is where the change shows up.

### 7.2 Branch labels (REH-75 §8): 18 of 136 scored trips relabelled

Per-branch decomposition at H=30d, before and after. Both columns are from
`diagnose-flips` output — REH-75's from its appendix, this run's from the
appendix below.

| Branch                   | Trips (REH-75) | Trips (now) |   Total (REH-75) |      Total (now) |
| ------------------------ | -------------: | ----------: | ---------------: | ---------------: |
| `rising`                 |             74 |      **72** |      +€8,832,920 | **−€10,706,950** |
| `low_points`             |              5 |      **11** |      +€4,512,430 |  **−€1,897,937** |
| `recovery`               |             11 |          11 |     −€10,166,472 |     −€10,484,178 |
| `falling_mean_reversion` |             12 |      **10** |      −€6,654,959 |      −€6,033,008 |
| `stable`                 |             11 |       **9** |     −€25,941,256 |     −€21,609,744 |
| `secular_decline`        |             10 |       **9** |      +€1,731,082 |      +€2,453,741 |
| `shallow_dip`            |              3 |       **5** |      −€2,380,807 |      −€2,685,052 |
| `small_sample`           |              5 |       **4** |      −€6,513,904 |      −€5,617,373 |
| `below_min_profit`       |              3 |           3 |      −€6,255,289 |      −€6,255,289 |
| `no_pattern`             |              2 |           2 |     −€12,419,809 |  **+€7,579,726** |
| `dip_in_uptrend`         |              0 |           0 |                — |                — |
| **Total**                |        **136** |     **136** | **−€55,256,064** | **−€55,256,064** |

Both columns sum to −€55,256,064 over 136 trips: relabelling redistributes the
loss between rungs and cannot change it. `no_pattern` keeps a count of 2 while
its total swings by +€20.0M — both of its members changed.

**Exactly 18 of the 136 scored trips changed label** (23 of 151 including the
floor group, whose labels appear in no published table). That count is *not*
derivable from either published run, because neither publishes per-row labels;
it is a supplementary re-derivation, run outside both, and stated as such. Method:
`run_diagnosis` was executed twice in one process, once on HEAD and once with
`rehoboam.replay.flip_buys.average_points_at` monkeypatched back to its
pre-REH-77 body (commit `3e8fbc7`'s parent — the same function with the
`m.get("season") == season` clause removed), and the two label sets compared per
`trip_id`. **Validation:** the pre-fix pass reproduces REH-75's published
per-branch trip counts exactly, all ten rungs. Had it not, this paragraph would
have been dropped rather than published.

The 18 transitions:

| From                     | To             |   n |
| ------------------------ | -------------- | --: |
| `small_sample`           | `rising`       |   4 |
| `rising`                 | `low_points`   |   3 |
| `rising`                 | `small_sample` |   3 |
| `falling_mean_reversion` | `shallow_dip`  |   2 |
| `no_pattern`             | `low_points`   |   1 |
| `stable`                 | `low_points`   |   1 |
| `secular_decline`        | `low_points`   |   1 |
| `recovery`               | `shallow_dip`  |   1 |
| `stable`                 | `no_pattern`   |   1 |
| `shallow_dip`            | `recovery`     |   1 |

**One reading in REH-75 §8 reverses.** It said *"`rising` is 54% of the
population and is net positive (+€8.8M over 74 trips) with the lowest entry
premium of any rung (1.0807)"*. Under the corrected statistic `rising` is 72
trips (53%) netting **−€10,706,950**, at a premium ratio of **1.0753**. It is
still the cheapest-entry rung and still the largest; it is no longer profitable.
REH-75's other §8 reading survives: `stable` is still the worst eligible rung
(−€21.6M over 9 trips, the longest eligible median hold at 12 days).

**Two of REH-75's named labels change, and one narrative claim survives on a
different label.** Woltemade moves `no_pattern` → `low_points` and Stiller moves
`stable` → `low_points`. §0's claim about Woltemade — *"labelled `no_pattern` — a
rung the flip path rejects"* — still holds, because `low_points` is also a
rejecting rung; only the rung's name changes. §7's *"Two of the ten
(`no_pattern`, `small_sample`) are branches the flip path rejects, and they carry
−€24,761,549"* becomes **three of the ten** (Woltemade `low_points`, Stiller
`low_points`, Anselmino `small_sample`), carrying **−€29,649,215** of the
−€60,531,014. The rest of the worst-ten table — every price, date, hold and
realised figure — is unchanged.

### 7.3 The flip-eligible set (REH-75 §0 and §8): 108 → 102

`ELIGIBLE_BRANCHES` is unchanged: `{rising, recovery, dip_in_uptrend, stable, falling_mean_reversion}`.

| Cohort at H=30d       | REH-75 trips | REH-75 total | REH-75 `Σb/Σmv` | Now trips |        Now total | Now `Σb/Σmv` |
| --------------------- | -----------: | -----------: | --------------: | --------: | ---------------: | -----------: |
| **flip-eligible**     |      **108** | −€33,929,767 |          1.0908 |   **102** | **−€48,833,880** |   **1.0880** |
| **not flip-eligible** |       **28** | −€21,326,297 |          1.2541 |    **34** |  **−€6,422,184** |   **1.2225** |
| Population            |          136 | −€55,256,064 |          1.1217 |       136 |     −€55,256,064 |       1.1217 |

Entry premium carried by each cohort: eligible **+€70,387,110 → +€62,999,753**,
not-eligible **+€46,014,218 → +€53,401,575** — €7,387,357 moved across the
boundary, and the population premium is unchanged at +€116,401,328.

Sixteen trips crossed the eligibility boundary: **11 eligible → not eligible**,
**5 not eligible → eligible**, net −6. (Crossing counts come from the same
validated re-derivation as §7.2; the cohort totals and ratios come from
`scripts/reh75_supplementary.py` §8, the committed instrument REH-75 used for
them.)

**REH-75 §0's reframing changes size but not shape.** Its sentence *"if every
eligible trip were a flip, the channel netted −€33.9M rather than −€55.3M"*
becomes **−€48.8M rather than −€55.3M**. The gap between the two closes
substantially: the eligible set is now smaller *and* carries a much larger share
of the loss.

**§0's "this is not a ceiling" argument is unchanged and, if anything,
stronger.** Its worked demonstration used `rising`'s positive total; under the
corrected labels `rising` is negative, so that particular arithmetic no longer
reads the same. It is replaced by the same instrument's other two figures, both
from `scripts/reh75_supplementary.py`: eligible-minus-`rising` (n=30) nets
**−€38,126,930**, and the loss-makers *within* the eligible set (n=69) net
**−€86,090,901** — far outside the −€48.8M cohort figure. A subset sum is still
not bounded by its superset. **102 bounds which round trips the flip path could
have bought. Nothing in this document bounds what it lost.**

### 7.4 What did not move that a reader might expect to

Every figure REH-75 produced from `scripts/reh75_supplementary.py` that does
**not** read a branch label was re-derived here and is unchanged to the euro:
the horizon-resolution gaps (max 0.50 d, mean 0.26 d over n=680), the
`player_mv_history` cross-check (135/136, median 0.00%, max 0.48%), the §4
hold-instant totals, all of §5 Q1/Q2/Q3, all of §6 (premium +€116,401,328, ratio
1.1217, staleness floor €109,285,611, same-day cohort, fat tail), and §7's
loss-concentration cohorts. Only the `Branch` column of §7's worst-ten table and
the whole of §8 moved.

______________________________________________________________________

## 8. A deviation from the design doc, recorded rather than amended

The design contradicts itself about where the per-horizon verdicts should be
rendered.

- Its **"Engineering"** section says `flip_report.py` gains *"a verdict column in
  the horizon sweep, the hold-instant block with its agreement label, and the
  superseded line"* — i.e. the sweep table itself grows a sixth column.
- Its **§5 prediction** requires the horizon sweep to be unmoved so it can be
  diffed against REH-75's appendix. A new column in that table would change
  every one of its rows and make the prediction untestable by construction.

**The implementation followed §5.** The per-horizon verdicts are rendered as a
separate `Dominance by horizon (REH-78 rule)` block appended after the sweep,
and the sweep table is byte-identical to REH-75's (§4.1). §5 wins because it is
the section the whole ticket turns on: a pre-registered prediction that its own
engineering note would have destroyed is not a prediction.

**The design is deliberately not being amended.** Quietly editing a
pre-registered document once its consequences are visible is the exact failure
REH-78 exists to correct — it is the same move as repairing REH-75's rule in
place after seeing what it returned. The contradiction stands in the design, and
this paragraph is the record of which reading was followed and why.

______________________________________________________________________

## 9. Caveats

Carried verbatim from the design doc's *"Caveats to state in the results, not
paper over"*:

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

Three further limits specific to this run:

1. **The mirror reconciliation cannot validate REH-77's fix.** It feeds the
   corrected statistic to both sides, so it reports 0 divergence whether the
   statistic is right or wrong (§7.1). The evidence that the season figure is
   the right one is REH-77's own eight-row check against recorded `ap`, not
   anything in this document.

1. **The relabel count and the eligibility-crossing counts (§7.2, §7.3) are a
   re-derivation, not a published run.** They come from a scratch script that
   monkeypatches the pre-REH-77 function body back in. It is validated against
   REH-75's published per-branch counts, which it reproduces exactly, and it is
   not under the determinism gate. The cohort *totals* are not from that script
   — they are from the committed `scripts/reh75_supplementary.py`.

1. **REH-75's caveats §10.1–§10.6 still apply unchanged**, in particular that
   the population is conditioned on having sold and that branch labels are
   eligibility, not provenance. Its §10.7 — the career-vs-season
   `average_points` defect — is what this re-run discharges; §8's table and the
   eligible split are no longer provisional.

______________________________________________________________________

## 10. What this changes downstream

- **REH-75 §4's verdict is superseded**, and its results document now carries a
  header saying so. The rest of that document stands; §4 remains a true record
  of what that rule returned.
- **REH-75 §8 and §0's 108/28 split are no longer provisional.** They are
  replaced by the 102/34 split above.
- **`rising` is not a profitable rung.** REH-75's one clearly positive eligible
  rung was an artifact of the career-mean statistic. Any ticket resting on
  "the `rising` rung pays for itself" needs re-reading.
- **The entry premium is now named by the rule at every horizon** — alone at
  14d, co-dominant at 21d/30d/45d, and it is the single eligible term in the
  hold-instant view. REH-75 §11's proposed per-trade entry-premium cap is the
  action this verdict points at, and it is unaffected by anything REH-77 changed:
  +€116,401,328 is H-invariant, label-invariant and unmoved across both runs.
- **REH-71's replay re-run is still outstanding**, deliberately out of scope
  here. It reads the same corrected statistic and has its own design doc and a
  once-only-run convention.

______________________________________________________________________

## Appendix — verbatim run output

`uv run rehoboam diagnose-flips`, the first of the two determinism-gate runs
(SHA-256 `723bb546c24c0ebfb7594fa1a06239ea0ba9120b0129bfb63f838b22c1b41f26`):

```
========================================================================
FLIP LOSS DIAGNOSIS — REH-75
========================================================================

151 completed ROUND TRIPS (not flips — see REH-75 design §1)
136 scored below; the remaining 15 are the EUR 500,000 floor group, reported separately at the end.
Divide by the scored count, never by the population count.

Mirror divergence: 0 rows (expected — the branch reconstruction agrees with the shipped ProfitTrader ladder on every labelled row).

Horizon sweep (population totals; the EUR 500,000 floor group is excluded)
------------------------------------------------------------------------
Horizon           Selection              Exit     Entry premium             Total      n  Censored
14d         EUR -64,936,734  EUR +126,081,998  EUR +116,401,328   EUR -55,256,064    136         0
21d        EUR -115,271,263  EUR +176,416,527  EUR +116,401,328   EUR -55,256,064    136         0
30d        EUR -116,527,447  EUR +177,672,711  EUR +116,401,328   EUR -55,256,064    136         0
45d        EUR -141,559,888  EUR +202,705,152  EUR +116,401,328   EUR -55,256,064    136         0
60d        EUR -164,802,412  EUR +225,947,676  EUR +116,401,328   EUR -55,256,064    136         0
Ground truth: sum(realised) over the 136 scored round trips = EUR -55,256,064
  Every Total above equals the realised P&L of the rows behind it — the identity closes with no residual.
Rows with no market value at buy: 0 (fully censored, unlabelled — not the floor group)

Headline at H=30d: dominant mechanism = exit_timing
  (note: this term's contribution is POSITIVE — the rule ranks by magnitude, so the named term is the largest number, not necessarily the cause of the loss)
  Selection:      EUR -116,527,447
  Exit timing:    EUR +177,672,711
  Entry premium:  EUR +116,401,328  (paid over market value, unnegated)
  Total:          EUR -55,256,064
  (superseded: this rule could not name selection — see the registered verdict below and REH-78)

Registered verdict at H=30d (REH-78): selection + entry premium (co-dominant)
  Only negative contributions are eligible; a term that reduced the loss cannot be its cause.

Dominance by horizon (REH-78 rule)
------------------------------------------------------------------------
14d      entry premium
21d      entry premium + selection (co-dominant)
30d      selection + entry premium (co-dominant)
45d      selection + entry premium (co-dominant)
60d      selection

Supplementary — the identity at each trip's realised hold (NOT the registered instrument: the sale date is bot-chosen, so selection is conditioned on the outcome)
------------------------------------------------------------------------
  Selection:      EUR +43,371,202
  Exit timing:    EUR +17,774,062
  Entry premium:  EUR +116,401,328  (paid over market value, unnegated)
  Total:          EUR -55,256,064
  Verdict: entry premium
  Agreement with the registered verdict: overlapping
  Censored (no market value at or before the sale): 0

Branch labels mean flip-eligible at buy time. They do not mean the flip path bought the player — provenance is unrecorded before 2026-01-03.

Per-branch decomposition at H=30d
------------------------------------------------------------------------
Branch                           Selection              Exit     Entry premium             Total   Trips
below_min_profit            EUR -5,539,087    EUR +5,333,224    EUR +6,049,426    EUR -6,255,289       3
falling_mean_reversion      EUR -4,154,934    EUR +6,598,619    EUR +8,476,693    EUR -6,033,008      10
low_points                  EUR +1,897,087   EUR +29,197,126   EUR +32,992,150    EUR -1,897,937      11
no_pattern                  EUR +7,425,636      EUR +194,918       EUR +40,828    EUR +7,579,726       2
recovery                   EUR -18,230,677   EUR +17,097,324    EUR +9,350,825   EUR -10,484,178      11
rising                     EUR -70,927,452   EUR +92,925,846   EUR +32,705,344   EUR -10,706,950      72
secular_decline            EUR -13,217,514   EUR +18,623,675    EUR +2,952,420    EUR +2,453,741       9
shallow_dip                 EUR +2,655,949    EUR -1,170,730    EUR +4,170,271    EUR -2,685,052       5
small_sample                EUR +2,954,827    EUR -1,375,720    EUR +7,196,480    EUR -5,617,373       4
stable                     EUR -19,391,282   EUR +10,248,429   EUR +12,466,891   EUR -21,609,744       9

Temporal split at 2026-01-03 (boundary on buy_date, H=30d)
------------------------------------------------------------------------
  Before 2026-01-03:    EUR +5,425,455  (43 trips)
  On/after 2026-01-03: EUR -60,681,519  (93 trips)

EUR 500,000 floor group (reported separately — never mixed into
the headline totals above)
------------------------------------------------------------------------
  Trips: 15
  P&L:   EUR +0

========================================================================
```

As in REH-75, the figures in §7.3 and §7.4 above that are **not** in that output
come from `scripts/reh75_supplementary.py`, which reads the same two pinned
databases strictly read-only and is not covered by the determinism gate:

```
uv run python scripts/reh75_supplementary.py
```

Its §8 block, quoted, is the source of the eligible/not-eligible cohort figures:

```
  Branch                    Trips   sum(b)/sum(mv)  Median hold             Total
  rising                       72           1.0753          6.0   EUR -10,706,950
  low_points                   11           1.2825           10    EUR -1,897,937
  recovery                     11           1.0977            4   EUR -10,484,178
  falling_mean_reversion       10           1.1473          3.0    EUR -6,033,008
  secular_decline               9           1.0909            2    EUR +2,453,741
  stable                        9           1.0970           12   EUR -21,609,744
  shallow_dip                   5           1.1772           10    EUR -2,685,052
  small_sample                  4           1.3481         16.0    EUR -5,617,373
  below_min_profit              3           1.1402           14    EUR -6,255,289
  no_pattern                    2           1.0119         14.5    EUR +7,579,726
  flip-eligible               102           1.0880           --   EUR -48,833,880   premium EUR +62,999,753
  not flip-eligible            34           1.2225           --    EUR -6,422,184   premium EUR +53,401,575
```
