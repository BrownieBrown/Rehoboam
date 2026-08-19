# REH-75 — Flip loss diagnosis: results

Ticket: https://linear.app/jovily/issue/REH-75
Date: 2026-08-19
Design: `2026-08-19-reh-75-flip-diagnosis-design.md` (binding on method)
Status: measurement complete. This is a diagnosis, not a fix.

______________________________________________________________________

## 0. The population correction — read this before any number

**`flip_outcomes` is a table of completed round trips, not a table of flips.**
This is the most useful thing in this document, and it is a correction to
REH-75's own framing.

`backfill.py`'s `_pair_flips` FIFO-pairs every buy against every later sell for
a `player_id`, and the live writer `LearningTracker.record_flip_outcome` fires
on every instant sell. Neither consults the *motive* for the buy. An EP-driven
squad buy that was later sold is indistinguishable, in this table, from a
`ProfitTrader` flip. So **−€55,256,064 is the P&L of every completed buy→sell
round trip the bot made in 2025/26**, and calling it "what our profit flips
lost" is the same category error that forced REH-71's withdrawal.

Three things make the correction stick:

1. **The field that would settle it is mostly unrecorded.** `transfer_type`
   exists in `manager_transfers` from 2026-01-03 and `league_transfers` from
   2026-04-08. Round trips begin 2025-08-10. Joining a round trip to a recorded
   transfer type on a ±2-day window matches **18 of 151**.

1. **Price is not a usable proxy for provenance.** `ProfitTrader` gates its
   Kickbase branch on `price == market_value`, which looks like it should
   identify Kickbase-sourced buys. During design it did not: all **15** round
   trips satisfying it are buy = sell = exactly €500,000 — the price floor,
   where market value is pinned and the equality holds trivially. Their combined
   P&L is **€0**. The test finds floor-priced filler, not flips. They are
   reported separately throughout and never mixed into a headline total.

1. **The eligible *set* is much smaller than the population — which bounds the
   set, not the loss.** Of the 136 non-floor round trips, **108 were
   flip-eligible at buy time** (the shipped `ProfitTrader` ladder would have
   accepted them) and those 108 net **−€33,929,767**. The other 28 were
   rejections the flip path would never have bought, and they net
   **−€21,326,297**. So: **if every eligible trip were a flip**, the channel
   netted −€33.9M rather than −€55.3M.

   **That is not a ceiling on the flip channel's losses and must not be quoted
   as one.** The flip path bought some unknown *subset* of the 108, and a sum
   over a subset is not bounded by the sum over its superset when the superset
   contains positive terms — and this one does. The `rising` rung alone is
   **+€8,832,920** across 74 trips. Drop only that rung and the remaining
   eligible rungs net **−€42,762,687** (`falling_mean_reversion` −€6,654,959 +
   `recovery` −€10,166,472 + `stable` −€25,941,256), already outside the
   −€33.9M figure; isolating the loss-makers *inside* each rung pushes it
   further still. **108 bounds which round trips the flip path could have
   bought. Nothing in this document bounds what it lost.**

The single largest loss in the table, **Woltemade (−€19,443,381, 35% of the
whole season's round-trip loss)**, is labelled `no_pattern` — a rung the flip
path *rejects*. It was bought for €50,000,000 against a market value of
€31,768,206. Whatever bought that, it was not the profit-flip ladder.

**Everything below is therefore about all round trips.** No sum in this
document is attributed to the flip channel.

______________________________________________________________________

## 1. Evidence handling

Inputs, pinned by digest:

```
76e55eba3c68aa147809c09467336166951935662d800954209a6bc1472f18ce  logs/bid_learning.db
0af472a7ac5a9193348def8bfa8cb53cf83f3650fe2373b1971a4b9314b62999  logs/training_corpus.db
```

**Quality gates**, run before any number was produced:

| Gate                                           | Result                                                                                                                                                                                                                     |
| ---------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `uv run pytest -q`                             | 755 passed, 1 skipped                                                                                                                                                                                                      |
| `uv run ruff check rehoboam/ tests/`           | All checks passed                                                                                                                                                                                                          |
| `uv run mypy rehoboam/`                        | 68 pre-existing errors in 18 files; **0 in `rehoboam/diagnostics/`**                                                                                                                                                       |
| `uv run bandit -r rehoboam/ -c pyproject.toml` | 24 pre-existing findings (0 high) in `trader.py`, `enrichment/corpus.py`, `bid_learner.py`, `bidding_strategy.py`, `services/execution.py`; **0 in `rehoboam/diagnostics/`**, and this branch modifies none of those files |

**Determinism gate**: `rehoboam diagnose-flips` run twice, output diffed
byte-for-byte — `diff` exited 0. **DETERMINISTIC.**

**Identity check against ground truth**:
`select sum(profit) from flip_outcomes` → **−55,256,064**. The floor group's
P&L (**€0**, 15 trips) plus the scored group's total (**−€55,256,064**, 136
trips) equals it exactly, and the `Total` column of the horizon sweep reads
−55,256,064 at every one of H ∈ {14, 21, 30, 45, 60}. The decomposition is an
identity with no residual bucket, and it closes.

**Coverage**: 0 censored rows at every horizon, 0 rows with no market value at
the buy instant, 0 rows labelled `no_trend_data`. The sweep is a balanced panel
of n=136 at every H, so the curve across H is comparable point to point. Every
horizon endpoint resolved to a snapshot within **0.50 days** of its target
(mean 0.26), well inside the 3-day guard.

*(The design doc quotes 0.99 days, mean 0.46, for what sounds like the same
quantity. Both are correct and they measure different lookups. 0.99/0.46 is
the age of a **backwards-only** lookup, which over daily snapshots can reach a
full interval. 0.50/0.26 is the gap of the **nearest** lookup `mv_nearest`
actually performs at horizon endpoints, which over daily snapshots can never
exceed half an interval. Neither is a correction of the other.)*

**Cross-check against the second MV source.** The design named
`training_corpus.mv_series` the source of record and `player_mv_history` a
cross-check whose disagreements are reportable. There are none worth reporting:
135 of 136 scored trips have a `player_mv_history` value at or before the buy
date, and against the corpus `mv_buy` the median absolute difference is
**0.00%**, the mean 0.00%, the maximum **0.48%**. The two sources agree.

______________________________________________________________________

## 2. The decomposition

For round trip *i*: paid `b`, sold for `s`, realised `π = s − b`. With `mv_buy`
the market value at the buy date and `mv(H)` the market value `H` days later:

```
π  =  [ mv(H) − mv_buy ]   SELECTION      what the market did to this player
   +  [ s − mv(H) ]        EXIT           what our sale got vs. the market at H
   −  [ b − mv_buy ]       ENTRY PREMIUM  what we overpaid to get in
```

An identity, not a model. There is no `other` bucket and none was added.

Two H-invariant facts fall straight out of it, and they are the spine of this
diagnosis:

- **`Σ(b − mv_buy) = +€116,401,328`** — we paid €116.4M *above* market value to
  open these positions, on €956,090,198 of market value deployed. **+12.2% in
  aggregate.**
- **`Σ(s − mv_buy) = +€61,145,264`** — from buy-day market value to sale, we
  recovered €61.1M.

€61.1M − €116.4M = **−€55,256,064**. The horizon sweep only moves the split
point *inside* the €61.1M; it cannot change either invariant.

______________________________________________________________________

## 3. The horizon sweep

Population totals, floor group excluded, n=136 at every H:

| Horizon |     Selection |          Exit | Entry premium |        Total | Censored |
| ------- | ------------: | ------------: | ------------: | -----------: | -------: |
| 14d     |  −€64,936,734 | +€126,081,998 | +€116,401,328 | −€55,256,064 |        0 |
| 21d     | −€115,271,263 | +€176,416,527 | +€116,401,328 | −€55,256,064 |        0 |
| 30d     | −€116,527,447 | +€177,672,711 | +€116,401,328 | −€55,256,064 |        0 |
| 45d     | −€141,559,888 | +€202,705,152 | +€116,401,328 | −€55,256,064 |        0 |
| 60d     | −€164,802,412 | +€225,947,676 | +€116,401,328 | −€55,256,064 |        0 |

`Entry premium` is shown unnegated — what we paid over market value — and
enters `Total` negated, exactly as in the identity.

Read the table structurally before reading it for a winner. `Selection + Exit`
is `s − mv_buy` and is therefore **constant at +€61,145,264 at every H**;
Selection and Exit are mirror images that trade against each other as H moves,
while Entry premium does not move at all.

______________________________________________________________________

## 4. The pre-registered dominance rule, and what it returned

The rule, fixed in the design before any number existed, quoted in full:

> The dominant mechanism is the term with the largest absolute contribution to
> the population total, at **H = 30 days** as the headline, with the full sweep
> reported beside it. Contributions are compared as the magnitude of each
> term's **signed** population sum, entry premium entering as
> `−Σ(b − mv_buy)` exactly as it does in the identity. If the two largest land
> within **20%** of each other, the diagnosis reports *no single dominant
> mechanism* rather than choosing between them.

Applied mechanically at H=30:

| Term          | Signed contribution | Magnitude |
| ------------- | ------------------: | --------: |
| Exit timing   |       +€177,672,711 |   €177.7M |
| Selection     |       −€116,527,447 |   €116.5M |
| Entry premium |       −€116,401,328 |   €116.4M |

Gap between first and second: **34.4%**, outside the 20% tie band.

> ### The rule names **exit timing** as the dominant mechanism.

That is the answer the rule gives and it is recorded as such. It is not the
answer I expected, and it deserves three honest qualifications rather than a
narrative.

**First: the dominant term is positive.** Exit timing contributed +€177.7M — a
*gain* against the H=30 market, not a loss. The rule as written ranks by
magnitude of signed contribution, so the largest-magnitude term can be the one
that helped. Read literally, the rule's verdict is "the biggest single number in
the decomposition is what our sales got relative to the market thirty days after
we bought". It is not "exit timing lost us the money".

**Second: the rule was structurally incapable of naming Selection.** From §2,
`Selection(H) + Exit(H) = Σ(s − mv_buy) = K = +€61,145,264`, constant in `H`.
So for any horizon where Selection is negative:

```
Selection(H) + Exit(H) ≡ K = +€61,145,264      (constant in H)
⇒  Exit(H) = K − Selection(H) = K + |Selection(H)|
⇒  |Exit(H)| = K + |Selection(H)| > |Selection(H)|,   always
```

Exit beats Selection by **exactly €61,145,264 at every horizon** — which is why
the 30d, 45d and 60d gaps below all rest on one and the same absolute number.
On any dataset where Selection is negative and `Σ(s − mv_buy)` is positive, the
rule *cannot* return `selection`. Its verdict was settled by the algebra of the
decomposition before any data existed. This is a defect in the **rule**, which
the design authored; `dominant_mechanism` transcribes it faithfully. Read
"exit timing won" as *the comparison was degenerate by construction*, not as
*exit timing was a close-run but real winner*. Re-registering a rule over
non-collinear quantities is filed as **REH-78** (§11), and it has to be
re-registered **before** the next re-run, not after seeing its output.

**Third: the verdict is not stable, and the instability is measurable.**

| H   | 1st                       | 2nd                         | Gap   | Verdict                      |
| --- | ------------------------- | --------------------------- | ----- | ---------------------------- |
| 14d | exit timing +€126,081,998 | entry premium −€116,401,328 | 7.7%  | no single dominant mechanism |
| 21d | exit timing +€176,416,527 | entry premium −€116,401,328 | 34.0% | exit timing                  |
| 30d | exit timing +€177,672,711 | selection −€116,527,447     | 34.4% | **exit timing** (headline)   |
| 45d | exit timing +€202,705,152 | selection −€141,559,888     | 30.2% | exit timing                  |
| 60d | exit timing +€225,947,676 | selection −€164,802,412     | 27.1% | exit timing                  |

At H=14 the rule declines to name a mechanism at all. And neither half of the
temporal split (§8) names one at H=30 either. A verdict that survives the
population but not either of its halves, and not the shortest horizon, is a
weak verdict. I am reporting it because it was pre-registered, not because I
think it is load-bearing.

**Why the H=30 anchor behaves this way.** The median hold in this population is
**6 days**, and **87.5% of the 136 scored round trips had already been sold
before day 30**. So
`mv(30)` is, for almost every trip, a market value measured well *after* we were
out. The Exit term at H=30 is not "we sold worse or better than a contemporaneous
market" — it is "we sold before a decline that carried on without us". That
makes Exit's size the mirror of Selection's, and both of them say the same thing.

For completeness, the same identity evaluated at each trip's **actual sale
instant** (H = the realised hold) — a supplementary measurement, **not** the
pre-registered rule, and reported as such:

| Term at H = actual hold |         Value |
| ----------------------- | ------------: |
| Selection               |  +€43,371,202 |
| Exit timing             |  +€17,774,062 |
| Entry premium           | +€116,401,328 |
| Total                   |  −€55,256,064 |

The rule applied there would name **entry premium**. I am not substituting that
for the pre-registered answer, and the H=hold view has a real defect the fixed-H
sweep does not: the sale date is *chosen by the bot*, usually at a local market
high, so its Selection term is conditioned on the outcome. The fixed horizons
are the leak-free instrument, which is exactly why the design chose them. But
the two views agree on the one number neither can move: we paid €116.4M over
market value to get in.

______________________________________________________________________

## 5. REH-75's three questions, answered

### Q1 — What did the market do to what we bought? (Selection)

**In euros:** −€64.9M at 14 days, −€116.5M at 30, −€164.8M at 60.

**Per trip, equal-weighted, as a percentage of buy-day market value:**

| Horizon |   Mean |  Median | Share negative |
| ------- | -----: | ------: | -------------: |
| 14d     | −0.89% | −11.68% |            62% |
| 21d     | −4.73% | −20.79% |            72% |
| 30d     | −0.47% | −15.75% |            66% |
| 45d     | −0.33% | −20.05% |            67% |
| 60d     | +2.22% | −22.30% |            69% |

The mean and the median disagree sharply, and the median is the honest summary
of what a typical buy looked like: **the median player we bought was worth 11.7%
to 22.3% less than his buy-day market value within two to nine weeks**, and two
thirds of buys were underwater on market value at every horizon. A handful of
large winners drag the mean back to roughly flat. Selection is genuinely bad,
and it is bad in the middle of the distribution, not just in the tail.

### Q2 — Did we sell too early? (Exit timing, and the peak sub-measure)

**No, on the evidence available, and this is the clearest negative result here.**

The contemporaneous measure — sale price against market value *on the sell
date* — is **+€17,774,062** across 136 trips, a mean ratio of **1.015** and a
median of **1.020**, with **91 of 136** sold above market.

**The lookup rule, stated because it decides what that number means.**
`mv(sell)` is the most recent snapshot **at or before** `sell_date`
(`TrainingCorpus.market_value_at`) — the same at-or-before rule §6 applies to
`mv_buy`, for the same reason: a sale is a *pricing instant*, so only data at
or before it may enter. `mv_nearest` is deliberately **not** used here; on most
rows it resolves to a post-sale snapshot and would leak price action from after
the exit into the measure. `scripts/reh75_supplementary.py` prints both, so the
difference is checkable: the nearest lookup would read **+€26,191,913**, mean
ratio 1.025.

**The staleness bracket, which §6 gives the entry premium and this measure
needs too.** The at-or-before snapshot has a median age of **0.52 days** (max
0.91) at the sell instant. Recomputing against the *next* snapshot after the
sale — the opposite-signed extreme, which leaks future information and is
therefore the other end of the bracket rather than an estimate — gives
**+€36,003,050**. The contemporaneous value therefore lies inside
**\[+€17.8M, +€36.0M\]**, a bracket **€18.2M wide — wider than the measure
itself**. That is a far weaker bound than the 6% §6 achieves on the entry
premium, and it is why the claim here is deliberately narrow.

**So: exit execution was at market value, within snapshot resolution.** Both
ends of the bracket are positive, so the *direction* is robust and it is Q2's
answer: **we did not sell below market, and there is no exit-side leak.** What
the data does **not** support is "execution was better than market".
`INSTANT_SELL_PCT = 1.00` (REH-67) means an instant sell transacts *at* market
value by construction, and a +1.5–2.0% edge is the same order as the +0.73%
median day-over-day drift §6 measures — so a small positive is at least as
consistent with snapshot timing as with execution skill. There is no exit-side
leak; there is also no exit-side edge to bank on.

The REH-33 sub-measure, `peak_during_hold − sell_price`, does show money left on
the table: **+€73,912,890** in aggregate, median **+€130,011** per trip,
mean +€621,116, over the 119 scored trips with at least one in-hold snapshot
(the 17 without are all `hold_days == 0`). **25 of 119** sold at or above the
in-hold peak. Split at the turn of the year: +€41.6M before (n=40),
+€32.3M after (n=79).

That number must not be read as a loss. `peak_during_hold` is an *ex-post*
maximum — the best instant in hindsight — so a positive gap is the normal
condition of anyone who is not clairvoyant, and €73.9M is an unattainable
benchmark, not a foregone gain. What it does say is that a peak-aware sell
trigger has something to work with, which is REH-33's premise. It does not say
we sold too early.

### Q3 — Were hold windows too short? (the shape of Selection across H)

**No. Refuted, and the refutation survives every robustness check I ran.**

The design fixed in advance that this is answered by the shape of Selection, not
by a threshold. Selection falls monotonically as H grows: −€64.9M → −€115.3M →
−€116.5M → −€141.6M → −€164.8M. **Every extra week of patience would have made
the population worse, not better.** The equal-weighted median tells the same
story (−11.7% at 14d deepening to −22.3% at 60d).

**REH-43's premise is refuted, twice over.** REH-43 rests on holding longer —
"median hold ≥ 21 days". The current median hold is **6 days** and 83.8% of the
136 scored round trips are under 21 days, so REH-43 would be a real behavioural
change, not
a formalisation of what already happens. And the change would have cost money:
at H=21 the population's Selection is −€115.3M against −€64.9M at H=14. **REH-43
should not be built on this premise.** If there is a case for longer holds, it
has to come from somewhere other than the market-value trajectory of what we
actually bought.

**Robustness check, because this one matters and the design did not anticipate
it.** The corpus runs to 2026-07-31, so for late-season buys `buy_date + H`
lands in the off-season, where Kickbase market values deflate for reasons that
have nothing to do with player selection. At H=60, **53 of 136** trips have a
window past 2026-05-16, contributing −€50.7M of Selection. Restricting to a
balanced panel of the **83** trips whose entire 60-day window stays inside the
season:

| Horizon | Selection (in-season panel, n=83) |
| ------- | --------------------------------: |
| 14d     |                      −€45,255,861 |
| 21d     |                      −€77,729,038 |
| 30d     |                      −€69,358,906 |
| 45d     |                      −€97,729,084 |
| 60d     |                     −€114,136,656 |

Still clearly falling (non-monotone at 30d, but the trend is unambiguous). The
off-season contamination is real and worth knowing about, and it does not change
the answer.

______________________________________________________________________

## 6. The entry premium — the finding the ticket did not ask for

REH-75 named three mechanisms. This is a fourth, it is H-invariant, and it is
the largest thing in the diagnosis that any decision can actually reach.

**We paid €116,401,328 above market value to open 136 round trips — +12.2% on
€956,090,198 of market value deployed.**

| Measure                                   |                                   Value |
| ----------------------------------------- | --------------------------------------: |
| Aggregate `Σb / Σmv_buy`                  |                                  1.1217 |
| Mean per-trip ratio (136 scored)          |                                    1.12 |
| Median per-trip ratio                     |                                  1.0908 |
| Mean per-trip ratio over all 151 rows     |                                  1.1081 |
| Trips paying **above** market value       |                               110 / 136 |
| Trips paying **below** market value       |                                26 / 136 |
| Trips paying **exactly** market value     |                                 0 / 136 |
| Premium percentiles (p10/p25/p50/p75/p90) | −2.3% / +1.1% / +9.1% / +15.3% / +22.3% |

This is REH-71's withdrawn "toll" question asked in a form that needs no
provenance data. **It is not a toll and this document does not call it one.** A
toll is a structural cost of a channel; this is simply *what we paid*, whatever
the channel — a bidding-behaviour measurement, not a market-microstructure one.
REH-71's withdrawal note is right that a Kickbase-sourced listing carries no
structural toll. It is orthogonal to this: whatever the listings were, our bids
landed 12% over market value on average.

Three pieces of corroboration:

**The staleness bound.** `mv_buy` is the most recent daily snapshot at or before
`buy_date` (deliberately: a decision instant must not see post-buy prices). Its
median age is **0.56 days** (max 0.99), and market values drift a median
**+0.73%** day-over-day, so the term is biased upward. Recomputing the whole
premium against the *next* snapshot after the buy — the opposite-signed extreme,
which leaks future information and is therefore a floor, not an estimate — gives
**€109,285,611**. Staleness can account for at most **€7.1M of €116.4M (6%)**.
The premium is not a snapshot artifact.

**The same-day trips.** **21** scored round trips were bought and sold on the
same day. Their combined realised P&L is **−€7,608,221** against a combined
entry premium of **+€7,486,663**. With no time for the market to move, the loss
is the premium, to within 1.6%. This is the premium being destroyed in a
controlled setting.

**"Zero trips paid exactly market value" is weak evidence, and I am flagging it
rather than using it.** Because `mv_buy` is an up-to-one-day-stale daily
snapshot and market values move most days, an exact match would be near
impossible even for a buy transacted at that instant's market value. Do not read
that row as evidence about the channel.

**The premium has a fat tail.** The top 10 trips by premium carry
**€52,364,840 — 45% of the total**. Woltemade alone is €18,231,794 (57.4% over
market value). But the median trip still paid **+9.1%**, so this is a chronic
condition with an outlier tail, not an outlier story alone.

______________________________________________________________________

## 7. Loss concentration — ten trades

This was not a designed measurement and it is the finding I would act on first.

| Cohort                    |    Realised P&L | Share of the −€55.3M |
| ------------------------- | --------------: | -------------------: |
| Worst 1 (Woltemade)       |    −€19,443,381 |                  35% |
| Worst 3                   |    −€32,282,100 |                  58% |
| Worst 5                   |    −€42,487,934 |                  77% |
| Worst 10                  |    −€60,531,014 |                 110% |
| The other 126 round trips | **+€5,274,950** |                    — |

**Ten round trips out of 136 account for more than the entire season's loss.
The remaining 126 were collectively profitable** — but only just, and the
margin should not be read as health: **+€5,274,950 on €780,867,595 of market
value deployed is +0.68% across a whole season**, earned *after* paying the
same chronic entry premium §6 documents (this cohort's own ratio is 1.0992).
This row does not say "everything except ten trades was fine"; it says the
other 126 roughly broke even while ten destroyed the season. Those ten carry
€38,920,173 of entry premium between them — a third of the whole premium term.

The worst ten, with their reconstructed branch labels:

| Player      | Buy date   |        Paid |        Sold |     Realised | Entry premium | Hold | Branch         |
| ----------- | ---------- | ----------: | ----------: | -----------: | ------------: | ---: | -------------- |
| Woltemade   | 2025-08-13 | €50,000,000 | €30,556,619 | −€19,443,381 |  +€18,231,794 |   16 | `no_pattern`   |
| Martel      | 2025-12-28 | €22,484,086 | €15,532,827 |  −€6,951,259 |   +€1,659,148 |   35 | `stable`       |
| Sugawara    | 2025-12-28 | €18,344,585 | €12,457,125 |  −€5,887,460 |   +€1,288,046 |   35 | `stable`       |
| Anselmino   | 2025-09-01 | €12,086,344 |  €6,768,176 |  −€5,318,168 |   +€4,975,356 |   31 | `small_sample` |
| Stiller     | 2025-08-17 | €40,000,000 | €35,112,334 |  −€4,887,666 |   +€2,840,526 |  126 | `stable`       |
| Muheim      | 2026-03-17 | €11,507,102 |  €7,276,066 |  −€4,231,036 |   +€1,008,529 |   19 | `rising`       |
| Svensson    | 2026-04-13 | €25,066,414 | €21,334,764 |  −€3,731,650 |   +€4,566,761 |   14 | `recovery`     |
| Wahl        | 2026-01-27 | €11,544,633 |  €7,813,598 |  −€3,731,035 |   +€1,366,585 |   11 | `rising`       |
| Torunarigha | 2026-03-06 | €11,612,943 |  €8,166,387 |  −€3,446,556 |   +€1,703,156 |    9 | `rising`       |
| Martel      | 2026-03-06 | €11,496,669 |  €8,593,866 |  −€2,902,803 |   +€1,280,272 |   21 | `recovery`     |

Two of the ten (`no_pattern`, `small_sample`) are branches the flip path
**rejects**, and they carry −€24,761,549 of the −€60,531,014 between them. A
per-trade size cap, or a premium cap, reaches this population in a way that a
policy switch on the flip channel does not.

______________________________________________________________________

## 8. Per-branch decomposition

> **Branch labels mean flip-eligible at buy time. They do not mean the flip
> path bought the player — provenance is unrecorded before 2026-01-03.**

That sentence governs every number in this section. The reconstruction is a
*mirror* of `ProfitTrader`'s ladder that names the rung; the shipped code
remains the authority on the eligible/not-eligible verdict, and the two are
reconciled on every row.

**What that reconciliation does and does not prove.** It proves the mirror and
the shipped ladder return the same verdict *given identical inputs*. It says
nothing about whether those inputs match what the live bot saw — a wrong
statistic fed to both sides would still reconcile at zero divergence. One such
mismatch is known and is stated in §10 (caveat 7): the replayed
`average_points` is a career mean per appearance, while the shipped ladder
reads a season figure.

**The eligible count of 108 is an upper bound on the eligible set, and a loose
one.** `reconstruct_branch` models the ladder, not the whole live buy path.
Beyond `status != 0` and affordability, the live path only ever considers
listings passing `is_kickbase_seller()` (`trader.py:685`) — the hardest
provenance gate of all, and unmodelled here; `max_opportunities` caps bids at
5–10 per session (`trader.py:719`); and `BidEvaluator` cancels a flip bid more
than 25% over market value (`bid_evaluator.py:116-119`), against a p90 entry
premium of +22.3% (§6). Every one of those shrinks the true eligible set below
108, all in the same direction. This matters beyond this section: 108 is
load-bearing for §0's reframing.

**Mirror divergence: 0 rows of 151.** The reconstruction agrees with the shipped
`ProfitTrader` ladder on every labelled row. This is the expected result at the
live `FLIP_MAX_RISK_SCORE = 60.0` threshold, and it is reported explicitly
because a non-zero count would be a *defect in `flip_branches.py`*, not a market
outcome, and would make the whole table below untrustworthy. It is zero, so the
table stands.

Totals at H=30, floor group excluded:

| Branch                    |    Selection |         Exit | Entry premium |            Total |   Trips | Σb/Σmv | Median hold |
| ------------------------- | -----------: | -----------: | ------------: | ---------------: | ------: | -----: | ----------: |
| `rising`                  | −€53,189,234 | +€98,709,493 |  +€36,687,339 |      +€8,832,920 |      74 | 1.0807 |         6.5 |
| `falling_mean_reversion`  |  −€4,844,987 |  +€7,189,803 |   +€8,999,775 |      −€6,654,959 |      12 | 1.1510 |         3.0 |
| `recovery`                | −€12,184,852 | +€11,335,959 |   +€9,317,579 |     −€10,166,472 |      11 | 1.0985 |         4.0 |
| `stable`                  | −€21,956,243 | +€11,397,404 |  +€15,382,417 |     −€25,941,256 |      11 | 1.0925 |        12.0 |
| `secular_decline`         | −€12,118,575 | +€16,898,810 |   +€3,049,153 |      +€1,731,082 |      10 | 1.0879 |         2.5 |
| `low_points`              |  −€5,665,345 | +€18,752,264 |   +€8,574,489 |      +€4,512,430 |       5 | 1.3150 |         2.0 |
| `small_sample`            |  −€3,516,847 |  +€3,466,036 |   +€6,463,093 |      −€6,513,904 |       5 | 1.3442 |         1.0 |
| `below_min_profit`        |  −€5,539,087 |  +€5,333,224 |   +€6,049,426 |      −€6,255,289 |       3 | 1.1402 |        14.0 |
| `shallow_dip`             |  −€2,699,823 |  +€3,999,451 |   +€3,680,435 |      −€2,380,807 |       3 | 1.1629 |        36.0 |
| `no_pattern`              |  +€5,187,546 |    +€590,267 |  +€18,197,622 |     −€12,419,809 |       2 | 1.5246 |        20.0 |
| **flip-eligible (5)**     |              |              |  +€70,387,110 | **−€33,929,767** | **108** | 1.0908 |           — |
| **not flip-eligible (5)** |              |              |  +€46,014,218 | **−€21,326,297** |  **28** | 1.2541 |           — |

`dip_in_uptrend` labelled zero trips. No round trip fell to `too_risky` — at the
live threshold that rung is unreachable, as `label_for`'s docstring documents.

Two things stand out. **`stable` is the worst eligible rung** — 11 trips,
−€25.9M, the longest median hold among the eligible rungs at 12 days, and three
of the worst ten. **`rising` is 54% of the population and is net positive**
(+€8.8M over 74 trips) with the lowest entry premium of any rung (1.0807). And
the buys the flip path would have *rejected* overpaid far harder — a 1.2541
premium ratio against 1.0908 for the eligible ones, which is what you would
expect if those are the aggressive EP-driven squad buys the population
correction in §0 is about.

______________________________________________________________________

## 9. The temporal split

Boundary on `buy_date` at 2026-01-03 (the first date `manager_transfers`
covers), H=30, floor group excluded. Both totals reproduce the design-time
figures exactly:

| Cohort            | Trips |     Selection |          Exit | Entry premium |            Total | Σb/Σmv |
| ----------------- | ----: | ------------: | ------------: | ------------: | ---------------: | -----: |
| Before 2026-01-03 |    43 |   −€3,923,203 |  +€57,118,130 |  +€47,769,472 |  **+€5,425,455** | 1.1228 |
| On/after          |    93 | −€112,604,244 | +€120,554,581 |  +€68,631,856 | **−€60,681,519** | 1.1211 |

**Does the same mechanism dominate on both sides? No — and neither side names a
mechanism at all.** The pre-registered rule applied to each half at H=30 returns
*no single dominant mechanism* on both: before, exit €57.1M vs entry premium
€47.8M is a 16.4% gap; after, exit €120.6M vs selection €112.6M is a 6.6% gap.
Both inside the 20% tie band. The headline verdict of §4 does not survive the
split.

**What the split does say clearly is which invariant moved.** The entry-premium
ratio is essentially identical on both sides — **1.1228 before, 1.1211 after**.
The premium is chronic and did not change at the turn of the year. What changed
is the other invariant:

| Cohort            | `Σ(s − mv_buy)` | as % of `Σ mv_buy` |
| ----------------- | --------------: | -----------------: |
| Before 2026-01-03 |    +€53,194,927 |             +13.7% |
| On/after          |     +€7,950,337 |              +1.4% |

**Before the boundary the players we bought appreciated enough between buy and
sale to more than cover a 12% entry premium. After it they appreciated 1.4% and
the same premium ate everything.** The loss is a *selection* event dated to
roughly the winter break, sitting on top of a chronic premium that was always
there. The premium is what makes the second half fatal; the selection collapse
is what changed.

I cannot say from this data *why* the second half is worse. The candidates —
the winter break, a different market regime, the bot's own behaviour changing —
are not separated by anything measured here.

______________________________________________________________________

## 10. Caveats

Stated because they bound what the numbers above may be used for.

1. **The horizon counterfactual is not an achievable alternative.** "We could
   have held to H" ignores the 15-slot squad cap and the budget constraint, and
   ignores that a held position also blocks a slot and freezes cash. Selection
   at large H is an **upper bound** on what patience was worth, not a strategy.
   Here that cuts in the safe direction: Selection is negative and deepening, so
   the refutation of REH-43 in §5 is if anything conservative.

1. **The population is conditioned on having sold.** Round trips still open at
   season end never enter `flip_outcomes` at all — they are `_pair_flips`'s
   unpaired buys. Positions held to the end, *including any that appreciated*,
   cannot appear. This is a genuine survivorship hole and it is not quantified
   here.

1. **`mv_buy` carries up to one day of snapshot staleness.** It is deliberately
   an at-or-before lookup (`TrainingCorpus.market_value_at`), so that no post-buy
   price action can leak into the branch label or the entry-premium baseline.
   Median age 0.56 days, max 0.99. §6 bounds the resulting bias on the entry
   premium at €7.1M of €116.4M.

1. **Late-season horizons run into the off-season.** Not anticipated by the
   design. At H=60, 53 of 136 windows end after 2026-05-16, where market values
   deflate for reasons unrelated to selection. §5 shows the in-season balanced
   panel and the conclusion holds, but any *future* rerun of this instrument
   should carry the in-season panel alongside the full one.

1. **The H = actual-hold decomposition in §4 is endogenous.** Sale timing is
   chosen by the bot, typically at a local market high, so that view's Selection
   term is conditioned on the outcome. It is reported as corroboration of the
   H-invariant premium, not as a horizon result.

1. **Branch labels are eligibility, not provenance.** Repeated here because it
   is the claim most likely to be quoted out of §8.

1. **The branch labels evaluate the ladder's points gates against a *career*
   average, not the season figure the live path reads.**
   `average_points_at` (`replay/flip_buys.py`) truncates through
   `backtest.snapshot.matches_before`, which by design **includes every match
   from earlier seasons in full** — and the corpus holds seasons back to
   2013/2014. It therefore returns a career mean points-per-appearance,
   whereas the shipped ladder reads `player.average_points` ← Kickbase `ap`
   (`kickbase_client.py:88`), a season statistic. **94 of the 115 distinct
   flipped players carry pre-2025/26 history**, and for **74 of the 136 scored
   round trips the two averages differ by more than 10 points** — a full step
   across the ladder's 20/30/40 gates. Early-season buys are the worst
   affected, because almost all of their prior appearances are from earlier
   seasons: Woltemade, Stiller and Anselmino — three of the worst ten (§7) —
   are labelled essentially from previous-season form. The §8 reconciliation
   cannot detect this (see §8), because it compares the mirror against the
   shipped ladder on the *same* inputs. Filed as **REH-77**; the labels in §8
   are the only figures affected, and §2-§7 and §9 do not use them.

______________________________________________________________________

## 11. What I would file next

1. **Populate `trend_at_buy` (and `average_points` / `position`) on every flip
   write, going forward.** All three are 0/151 or 8/151 populated today, which
   is why §8's labels had to be *reconstructed* from an MV-history replay and
   reconciled against the shipped ladder — several hundred lines of machinery
   for what should be a column read. Next season's version of this diagnosis
   should be a lookup. Recording the buy's *motive* (EP pipeline vs. profit
   flip) at the same moment would retire the §0 population correction entirely.

1. **Fix the replayed `average_points` statistic (REH-77).** Sitting beside the
   item above, and more urgent than it: `average_points_at` returns a career
   mean per appearance where the live ladder reads a season figure (§10, caveat
   7). This is **not** local to REH-75 — `replay/flip_buys.py` is REH-71's
   module and its replay reads the same statistic, so the fix belongs to shared
   replay foundations rather than to either diagnosis. Whatever replaces it
   needs a test that fails on a player with prior-season history, which the
   mirror reconciliation structurally cannot provide.

1. **Re-register a non-degenerate dominance rule before the next re-run
   (REH-78).** §4 shows the pre-registered rule could never have named
   Selection: `Selection + Exit` is the H-invariant constant `Σ(s − mv_buy)`, so
   the two terms it ranks are collinear by construction. A replacement must
   compare quantities that are not — e.g. Selection, Entry premium, and Exit
   evaluated at each trip's **realised hold**, the only non-degenerate exit
   measure available. Pre-registration is the whole value of the rule, so this
   must be fixed **before** REH-72 or the fix ticket re-runs `diagnose-flips`,
   never afterwards.

1. **A per-trade entry-premium cap, and a per-trade size cap.** §6 and §7
   together are the actionable pair: a chronic +12% premium with a tail where
   ten trades exceed the whole season's loss. A cap on `buy_price / market_value`
   is a one-line policy with a directly measured effect, and it reaches the
   Woltemade-shaped losses that a flip-channel switch does not.

1. **Re-examine the `stable` rung** (§8): −€25.9M over 11 trips, the longest
   eligible hold, three of the worst ten. It is the weakest eligible rung by a
   wide margin.

1. **Do not build REH-43 on "median hold ≥ 21 days".** §5 refutes the premise.
   Re-scope or close it.

1. **REH-33 (peak-MV sell timing) has a real signal but a smaller one than it
   looks.** €73.9M against an ex-post oracle peak, median €130k per trip, and
   exit execution is already at contemporaneous market value within snapshot
   resolution (§5 Q2) — so there is no below-market execution gap for it to
   recover. Size the ticket against the median, not the aggregate.

1. **Investigate the winter-break selection break** (§9), ideally against
   REH-72/REH-74's competitor data. Something changed between the two halves of
   the season and this instrument can date it but not explain it.

______________________________________________________________________

## Appendix — verbatim run output

`uv run rehoboam diagnose-flips`, the first of the two determinism-gate runs:

```
========================================================================
FLIP LOSS DIAGNOSIS — REH-75
========================================================================

151 completed ROUND TRIPS (not flips — see REH-75 design §1)

Mirror divergence: 0 rows (expected — the branch reconstruction agrees with the shipped ProfitTrader ladder on every labelled row).

Horizon sweep (population totals; the EUR 500,000 floor group is excluded)
------------------------------------------------------------------------
Horizon           Selection              Exit     Entry premium             Total  Censored
14d         EUR -64,936,734  EUR +126,081,998  EUR +116,401,328   EUR -55,256,064         0
21d        EUR -115,271,263  EUR +176,416,527  EUR +116,401,328   EUR -55,256,064         0
30d        EUR -116,527,447  EUR +177,672,711  EUR +116,401,328   EUR -55,256,064         0
45d        EUR -141,559,888  EUR +202,705,152  EUR +116,401,328   EUR -55,256,064         0
60d        EUR -164,802,412  EUR +225,947,676  EUR +116,401,328   EUR -55,256,064         0
Rows with no market value at buy: 0 (fully censored, unlabelled — not the floor group)

Headline at H=30d: dominant mechanism = exit_timing
  Selection:      EUR -116,527,447
  Exit timing:    EUR +177,672,711
  Entry premium:  EUR +116,401,328  (paid over market value, unnegated)
  Total:          EUR -55,256,064

Branch labels mean flip-eligible at buy time. They do not mean the flip path bought the player — provenance is unrecorded before 2026-01-03.

Per-branch decomposition at H=30d
------------------------------------------------------------------------
Branch                           Selection              Exit     Entry premium             Total   Trips
below_min_profit            EUR -5,539,087    EUR +5,333,224    EUR +6,049,426    EUR -6,255,289       3
falling_mean_reversion      EUR -4,844,987    EUR +7,189,803    EUR +8,999,775    EUR -6,654,959      12
low_points                  EUR -5,665,345   EUR +18,752,264    EUR +8,574,489    EUR +4,512,430       5
no_pattern                  EUR +5,187,546      EUR +590,267   EUR +18,197,622   EUR -12,419,809       2
recovery                   EUR -12,184,852   EUR +11,335,959    EUR +9,317,579   EUR -10,166,472      11
rising                     EUR -53,189,234   EUR +98,709,493   EUR +36,687,339    EUR +8,832,920      74
secular_decline            EUR -12,118,575   EUR +16,898,810    EUR +3,049,153    EUR +1,731,082      10
shallow_dip                 EUR -2,699,823    EUR +3,999,451    EUR +3,680,435    EUR -2,380,807       3
small_sample                EUR -3,516,847    EUR +3,466,036    EUR +6,463,093    EUR -6,513,904       5
stable                     EUR -21,956,243   EUR +11,397,404   EUR +15,382,417   EUR -25,941,256      11

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

Figures in this document that are **not** in that output — the per-trip
percentage distributions, the contemporaneous exit measure, the staleness
bound, the in-season balanced panel, the loss-concentration table, the
per-branch premium ratios and median holds, and the `player_mv_history`
cross-check — were computed read-only over the same two databases using
`rehoboam.diagnostics.flip_diagnosis`'s public functions, with no code changes
and no writes.
