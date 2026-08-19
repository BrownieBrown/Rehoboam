# REH-75 — Flip loss diagnosis: design

Ticket: https://linear.app/jovily/issue/REH-75
Date: 2026-08-19
Status: design approved, not yet implemented

## What this measures, and what it refuses to claim

REH-75 asks why "our profit flips" lost €55,256,064 over 151 round trips at a
27.8% win rate. Before designing a measurement, the population was checked —
the discipline REH-71 lacked, and the reason its conclusion had to be
withdrawn. The population is not what the ticket assumes.

**`flip_outcomes` is a table of round trips, not of flips.** `backfill.py`'s
`_pair_flips` FIFO-pairs every buy against every later sell per `player_id`,
and the live writer, `LearningTracker.record_flip_outcome`, fires on every
instant sell. Neither consults the *motive* for the buy. An EP-driven squad
buy that was later sold is indistinguishable, in this table, from a
`ProfitTrader` flip. The −€55.3M is therefore the P&L of **every completed
round trip the bot made in 2025/26**, and attributing it to the flip channel
is the same category error as REH-71's toll.

**The field that would settle it is mostly unrecorded.** Seller identity and
transfer type exist in `league_transfers` (2026-04-08 → 2026-05-16) and
`manager_transfers` (2026-01-03 → 2026-05-16). Round trips begin 2025-08-10.
Joining flips to a recorded transfer type on a ±2-day window matches 18 of
151\.

**Price is not a usable proxy for provenance.** `ProfitTrader` gates its
Kickbase branch on `player.price == player.market_value`, which suggests
"bought at market value" identifies Kickbase-sourced buys. It does not: all
15 round trips satisfying it are buy = sell = exactly €500,000, the price
floor, where market value is pinned and the equality holds trivially. Their
combined P&L is €0. The test finds floor-priced filler, not flips.

So this design measures **where the money went across all round trips**, an
exactly-decomposable question answerable from data we hold, and treats
channel as a *label with stated uncertainty* rather than a fact. It will not
claim that any sum was caused by the flip path.

## Data inventory

| Source                                      | Coverage                                                                                                                 | Verdict                                                 |
| ------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------- |
| `flip_outcomes`                             | 151 rows, 2025-08-10 → 2026-05-15, −€55,256,064, 42 winners, mean hold 15.2d                                             | population, with the caveat above                       |
| `flip_outcomes.trend_at_buy`                | **0 / 151** populated                                                                                                    | unusable; ticket question 3 cannot be a lookup          |
| `flip_outcomes.average_points`, `.position` | **8 / 151**, all 2026-04-11 → 2026-05-10                                                                                 | unusable as a column; reconstructed instead             |
| `training_corpus.mv_series`                 | 161,492 rows / 531 players, 2025-07-29 → 2026-07-31; **151 / 151** have pre-buy history and data at every horizon to 60d | **source of record** for all market values              |
| `player_mv_history`                         | 39,781 rows / 206 players; **150 / 151** span the full hold window                                                       | cross-check only; disagreements are reportable findings |
| `training_corpus.player_match_history`      | 75,924 rows / 505 players                                                                                                | feeds leak-free average points                          |
| `league_transfers` / `manager_transfers`    | 2026-04-08 and 2026-01-03 onward                                                                                         | provenance labels for a minority only                   |

One temporal fact is recorded here because it shapes what the diagnosis
should look for: round trips bought **before** 2026-01-03 net **+€5,425,455**;
those bought after net **−€60,681,519**. The loss is an event in time, not a
constant defect. A steady-state selection flaw would spread across the
season.

## The decomposition

For round trip *i*: paid `b`, sold for `s`, realised `π = s − b`. Let `mv_buy`
be market value at the buy date and `mv(H)` market value `H` days after the
buy. Then, identically:

```
π  =  [ mv(H) − mv_buy ]   SELECTION      what the market did to this player
   +  [ s − mv(H) ]        EXIT           what our sale got vs. the market at H
   −  [ b − mv_buy ]       ENTRY PREMIUM  what we overpaid to get in
```

`mv_buy`, `mv(H)` and `peak_during_hold` all come from
`training_corpus.mv_series`, taken as the snapshot nearest the target instant.
That series, not `player_mv_history`, is the source of record here: it is
denser (531 players against 206) and runs to 2026-07-31, which is what makes
the 60-day horizon reachable. `player_mv_history` is used only as a
cross-check on the 150 trips it covers, and a disagreement between the two is
a finding to report, not a number to average away. `peak_during_hold` is the
maximum over snapshots in the closed interval `[buy_date, sell_date]`.

**Horizon coverage is complete, so nothing is censored.** The latest buy is
2026-05-10 and the corpus runs to 2026-07-31, so all 151 trips have a
snapshot at every H up to 60 days — within 0.99 days of the target at H=60
(mean 0.46 days), with no trip gapped by more than three days. The sweep is
therefore a balanced panel of n=151 at every horizon, and the curve across H
is comparable point to point. The censoring rule below is kept as a guard for
future reruns, not because it binds on this data.

The three terms cancel to `s − b`, so they sum to exactly −€55,256,064 across
the population. **There is no residual bucket, and none will be added.**
REH-71's attribution table carried an `other = delta − explained` term, which
is where a wrong model hides; an identity cannot have one.

Mapping to the mechanisms REH-75 asks about:

| Mechanism                                     | Term                                                                 |
| --------------------------------------------- | -------------------------------------------------------------------- |
| picked players whose value fell               | Selection                                                            |
| sold winners too early                        | Exit, plus the sub-measure `peak_during_hold − s` (REH-33's angle)   |
| hold windows too short                        | the **shape of Selection as H grows**, H ∈ {14, 21, 30, 45, 60} days |
| overpaid at entry *(not named in the ticket)* | Entry premium                                                        |

The entry-premium term is where REH-71's toll returns in a legitimate form.
REH-71 asserted a structural 11.7% toll on round trips and then withdrew it
because Kickbase-sourced flips pay market value. Both claims are about the
*channel*. This term asks neither — it measures what we actually paid against
market value on the day, and needs no provenance data to be valid. The mean
ratio of buy price to prior-snapshot market value across the 151 is **1.108**.

"Hold too short" is answered as a shape, not a threshold. If Selection is flat
or falling in H, longer holds would not have helped and REH-43's "median hold
≥ 21 days" premise is wrong. If it climbs, REH-43 is right and quantified.
Either way no arbitrary H is privileged.

### Pre-registered dominance rule

Fixed before any number is produced, because "name the dominant mechanism"
is otherwise an invitation to pick the most interesting one after the fact:

> The dominant mechanism is the term with the largest absolute contribution to
> the population total, at **H = 30 days** as the headline, with the full sweep
> reported beside it. Contributions are compared as the magnitude of each
> term's **signed** population sum, entry premium entering as
> `−Σ(b − mv_buy)` exactly as it does in the identity. If the two largest
> land within **20%** of each other, the diagnosis reports *no single dominant
> mechanism* rather than choosing between them.

## Branch labelling

For each buy, at `buy_date`:

1. `history_at(corpus, player_id, buy_date)` — MV history truncated strictly
   before the buy (`replay/flip_buys.py`, leak-tested under REH-71)
1. `TrendService.analyze(history, mv_buy)` — `trend_direction`, `trend_pct`,
   `is_dip_in_uptrend`, `is_secular_decline`, `is_recovery`
1. `average_points_at(corpus, player_id, buy_date)` — leak-free points gate
1. evaluate `ProfitTrader`'s ladder to name the branch: `rising`, `recovery`,
   `dip_in_uptrend`, `stable`, `falling_mean_reversion`, or `not_eligible`

Naming a branch requires re-stating the ladder's conditions, and this repo's
rule is that nothing reimplements a heuristic. The reconstruction is therefore
**validated against ground truth on every row**: the real
`ProfitTrader.find_profit_opportunities`, called through the adapter REH-71
built, produces the authoritative eligible/not-eligible verdict, and the
reconstructed verdict must equal it for all 151 or the test fails. The
reimplementation supplies only the label; the shipped code remains the
authority on the decision.

**Label semantics.** A label means *flip-eligible at buy time* — the flip path
would have accepted this player then. It does **not** mean the flip path
bought them; with provenance unrecorded before 2026-01-03 that claim is not
available. The results document will state this in those words wherever a
per-branch number appears.

## Engineering

- `rehoboam/diagnostics/flip_diagnosis.py` — the decomposition, the horizon
  sweep, the labelling, and the censoring rules. Pure functions over rows;
  no API calls.
- `diagnose-flips` CLI command, mirroring `backtest-baseline` and
  `replay-flip-policy`. Committed and tested rather than a throwaway script:
  REH-72 and REH-75's eventual fix ticket will both re-run this, and a
  one-shot script that produces a headline number is what nobody can re-check
  later.
- Output: per-horizon decomposition totals, per-branch breakdown, the
  pre/post-2026 temporal split, and the floor-price group reported separately.

### Tests, written first

- the identity sums to realised P&L exactly, over synthetic trips
- censoring is explicit when `buy + H` runs past the data range — never a
  silent zero. It does not trigger on this dataset (verified: n=151 at every
  H), so the test drives it with synthetic rows and the run asserts the
  censored count is zero
- truncation is strictly before `buy_date` (no future leak into the trend)
- reconstructed eligibility equals real `ProfitTrader` on all 151 rows
- the €500k floor group is its own case, since it already produced one false
  result during design

### Evidence handling

Carried over from REH-71: SHA-256 of `bid_learning.db` and
`training_corpus.db` recorded in the results document, and a determinism gate
— run twice, diff byte-for-byte — before any number is written down.

## Caveats to state in the results, not paper over

- The counterfactual "we could have held to H" ignores the 15-slot squad cap
  and budget constraints. Selection at large H is an **upper bound** on what
  patience was worth, not an achievable alternative.
- Round trips still open at season end are absent from `flip_outcomes`
  entirely (they are `_pair_flips`'s unpaired buys), so the population is
  conditioned on having sold. Positions held to the end — including any that
  appreciated — cannot appear.
- `mv_buy` comes from a daily snapshot; a buy transacted after that day's MV
  update carries up to one day of staleness in the entry-premium term.

## Out of scope

- Any fix. REH-75's deliverable is a diagnosis; the remedy is a later ticket.
- Ticket question 4, comparing against the winners' picks — it needs REH-72,
  which has not started, and REH-74's full-season backfill.
- Populating `trend_at_buy` going forward. It is a real gap and worth its own
  ticket, but writing it here mixes a code change into a diagnosis.

## Deliverable

`docs/superpowers/specs/2026-08-19-reh-75-flip-diagnosis-results.md` — the
written diagnosis naming the dominant mechanism with its evidence, and the
correction that `flip_outcomes` counts all round trips rather than flips,
which the follow-up tickets need more than they need the headline number.
