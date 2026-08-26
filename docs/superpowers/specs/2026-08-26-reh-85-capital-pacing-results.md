# Capital pacing (REH-85): measured results

Date: 2026-08-26
Status: measured, recommendation below, not yet merged
Ticket: https://linear.app/jovily/issue/REH-85
Design: `docs/superpowers/specs/2026-08-26-reh-85-capital-pacing-design.md`
Branch: `marcobraun2013/reh-85-capital-pacing`, HEAD `ef05f46`

## Verdict up front

Ship the default unchanged. It is a real, harmless, allocation-quality
improvement (+859 points at the shipped `pacing_window_days=90`) that does not
touch the sell side and regresses nothing. It does **not** meet the design's
own acceptance criterion — buy count must rise — at the shipped default: buy
count is 3 with pacing on, 3 with pacing off, identically. That failure was
predicted in the design's Risks section, and this measurement confirms it,
more starkly than predicted (see below). Do not chase the win at
`pacing_window_days=7`; the mechanism analysis shows it is not a better price
estimate, it is a proxy knob for reserve size sitting on a thin, noisy sample.
Reasoning for all of this follows.

## The sweep

All runs `uv run rehoboam replay-season --with-competition`, same local
`logs/training_corpus.db` / `logs/bid_learning.db`. Baseline for delta is the
genuinely-unpaced run (`--no-pacing`), not the stale 26,391 figure in the
original ticket brief — see Task 8's controller note in
`.superpowers/sdd/2026-08-26-reh-85-capital-pacing/progress.md`: that figure
predates REH-98/99/100 and produced a false alarm when used as the bar.
Actual (real) season total, for reference only: 26,172.

| configuration              | points | delta vs no-pacing | buys | sells |   finish | clears REH-71's 6,162 bar? |
| -------------------------- | -----: | -----------------: | ---: | ----: | -------: | :------------------------- |
| `--no-pacing`              | 24,141 |                  — |    3 |     0 | 11 of 14 | — (baseline)               |
| `--pacing-window-days 7`   | 27,957 |             +3,816 |    8 |     5 |  7 of 14 | no                         |
| `--pacing-window-days 14`  | 27,561 |             +3,420 |    6 |     3 |  7 of 14 | no                         |
| `--pacing-window-days 30`  | 25,000 |               +859 |    3 |     0 | 10 of 14 | no                         |
| `--pacing-window-days 60`  | 25,000 |               +859 |    3 |     0 | 10 of 14 | no                         |
| default (90)               | 25,000 |               +859 |    3 |     0 | 10 of 14 | no                         |
| `--pacing-window-days 180` | 23,676 |               -465 |    3 |     0 | 11 of 14 | no                         |

`--pacing-min-moves` (`pacing_in_season_min_moves`) is not swept above: it
governs `capital_reserve`'s floor once `slots_to_fill <= 0`, i.e. a full
15/15 squad. This replay never reaches 15/15 in any configuration (peak
buys is 8), so the knob is inert in this instrument by construction — sweeping
it would reproduce the same row every time. This was diagnosed during Task 8
(Ruling 9/10 in the ledger) and is why the sweep above varies
`pacing_window_days` instead.

## The mechanism

Diagnosed by querying `manager_transfers` directly at a mid-season instant
inside the corpus, independent of any replay run:

| window | rows |    median | reserve at 14 empty slots |
| -----: | ---: | --------: | ------------------------: |
|      7 |   76 | 4,999,423 |                64,992,499 |
|     14 |  140 | 6,521,550 |                84,780,150 |
|     90 | 1154 | 6,644,985 |                86,384,805 |
|    180 | 1300 | 6,736,287 |                87,571,731 |

The 7-day window is **not** hitting the EUR 3,000,000 floor — its median is
genuinely lower than the 90-day median, not floored. So `pacing_window_days`
behaves as a **proxy knob for reserve size**, not as a better price estimate:
a shorter window happens to produce a smaller number, which happens to make
the reserve satisfiable more often, which happens to unlock more buys (and,
via net-cost pacing on pairs, sells). Nothing about a 7-day window makes it a
*more accurate* estimate of what a move costs — if anything a 76-row sample is
noisier than a 1,154-row one.

The substantive finding: at 14 empty slots (the squad's early-season state)
the reserve is EUR 65-87m against a replay starting budget of roughly EUR 80m.
The constraint is near-unsatisfiable at season start, so the rule degenerates
to "buy nothing beyond what a small handful of early low-slot-count
signings can afford" — which is why buys stay flat at 3 for every window of
30 days or more. **The reserve is calibrated for topping up a squad that
already has most of its 15 slots filled, not for building one from an
under-strength start.**

## Did buy count rise? (the design's own acceptance criterion)

**No, not at the shipped default.** Buy count is 3 with pacing on
(`pacing_window_days=90`) and 3 with pacing off. Identical. The +859-point
gain at the default comes entirely from *which* 3 players get bought and
*how much survives after buying them* — better allocation of the same
number of moves — not from making more moves.

It did rise at the two short windows: 8 buys at 7 days, 6 at 14 days, both
against a baseline of 3. But per the mechanism section above, that rise is a
side effect of the reserve shrinking via a proxy (a lower median from a
thinner sample), not evidence that the design's mechanism — reserving
capital sequentially so more moves remain affordable — is what produced it.
It's closer to "the reserve is smaller, so more things clear it" than "the
bot paced itself into more purchases."

The design's Risks section predicted exactly this shape of failure:

> If the replay shows the reserve merely converting "one huge buy then
> nothing" into "several mid buys then nothing", the sell side is the next
> ticket and this measurement will say so.

**The prediction held, and the shipped default is even flatter than it
anticipated.** The predicted failure mode was "one huge buy converts into
several mid buys" — i.e. some tempo gain, just not champion-level tempo. What
actually happened at the default is that the buy count didn't move at all (3
→ 3); only the allocation of those 3 improved. The reserve isn't merely
insufficient to reach the champions' ~25-buy tempo — at its shipped
configuration it produces no tempo change whatsoever. The sell-side capital
recycling that the design explicitly deferred (Non-goals: "The sell side ...
changing when the bot sells overlaps REH-63, REH-71, REH-43 and REH-57")
remains the real lever for tempo, exactly as the design said it would if this
measurement came back this way.

Where sells *did* appear (5 at 7 days, 3 at 14 days), it wasn't new work —
it's the already-built net-cost-on-pairs mechanism (design §3: "a pair sells
before it bids, so it consumes only `bid - proceeds`") activating because a
shrunk reserve occasionally clears for a pair when it wouldn't for a gross
buy. That's the existing design doing what it says on the tin, not evidence
that a sell-side fix was accidentally delivered.

## REH-71's caution

Per REH-71's recorded faithfulness decision: treat any single replay delta
below 6,162 points with suspicion unless it replicates.

**None of the deltas in this sweep clear that bar.** The largest, +3,816 at
`--pacing-window-days 7`, is a single, non-replicated measurement at 62% of
the threshold. It should be trusted *less* than its size suggests, not more —
the mechanism section shows it rides a 76-row sample, which is exactly the
kind of thin, noise-prone population REH-71's caution exists to catch.

The +859 delta at the shipped default is smaller still and also does not
clear the bar as a single number. It does, however, show something the raw
threshold check misses: three independent configurations —
`pacing_window_days` 30, 60, and 90 — land on the *exact same* result
(25,000 points, 3 buys, 3 sells omitted i.e. 0, finish 10 of 14). That is not
a repeated run of the identical configuration (true replication in REH-71's
sense would want that too), but three different inputs converging on one
output is a meaningful stability signal: the median-move estimate is not
sensitive to window length once the window is wide enough to average out
short-term noise, and the resulting behavior is a stable plateau rather than
a fluke of one particular window setting. It is corroboration of a kind, just
not the kind REH-71 asked for. The 180-day result (-465, i.e. worse than
no-pacing) breaks that plateau, which is itself informative: a window wide
enough to reach back into a different, likely off-season, price regime pulls
the estimate away from the in-season 30-90 day plateau and produces a
slightly worse outcome — another data point that `pacing_window_days` is
functioning as a size knob, not a fidelity knob.

## Recommendation

**Ship the default (`pacing_enabled=true`, `pacing_window_days=90`,
`pacing_in_season_min_moves=2`, `pacing_median_floor_eur=3,000,000`)
unchanged.** Reasoning:

- It is safe. It touches only bid sizing on the buy side; the sell path,
  emergency fill, and compliance re-bid are all explicitly unaffected by
  construction (design §3), and Task 7 closed the one place pacing would
  have silently starved emergency fill.
- It is a real, if modest, improvement: +859 points, and that number is
  stable across three separate window settings rather than being an artifact
  of one lucky configuration.
- It does not require accepting a number that fails REH-71's bar on its own
  merits and is *also* mechanistically suspect. `--pacing-window-days 7`
  scores better (+3,816) but the mechanism analysis shows that gain comes
  from a thin, 76-row sample acting as a proxy for a smaller reserve, not
  from a better estimate of what a move costs. Shipping it would mean tuning
  a knob to sit deliberately close to the cliff edge where the reserve
  starts being satisfiable — exactly the kind of fragile calibration the
  design's own §"price of a move drifts" section warns against ("A
  hardcoded euro figure would be wrong within one transfer window"). A
  window chosen to sit near that edge is nearly as fragile as a hardcoded
  figure; it will drift out of the favorable zone the next time transfer
  volume changes, in either direction, and there is no mechanism telling us
  which way.
- The principled fix for the diagnosed root cause — a reserve of EUR 65-87m
  against an ~EUR 80m starting budget being near-unsatisfiable at season
  start — is not "pick a shorter window," it is to stop sizing the reserve
  purely off `moves_wanted * median_move` and bound it by what is actually
  available: something like
  `reserve = min(moves_wanted * median_move, budget * max_reserve_fraction)`.
  That directly targets the mechanism (the reserve can ask for more than the
  bot owns) instead of indirectly shrinking the reserve by cherry-picking a
  window length. It requires new code and a fresh replay measurement, both
  out of scope for this measure-and-report task. **Recommend filing it as a
  fast-follow ticket** rather than reopening this window-tuning cycle in
  pursuit of a bigger number.

In short: ship what's already on this branch, don't tune the window as a
substitute for fixing the actual constraint, and file the capital-bounded
reserve as the next design task since this measurement shows the buy-side
lockout is fixed only when the squad is nearly full, not while it is being
built — which is exactly the regime the replay exercises and the regime the
live bot is in today (12/15, per the live smoke test below).

## Live smoke test — `uv run rehoboam auto --dry-run`

Ran against the live prod-mirrored local state (`logs/bid_learning.db`,
current league squad). The `pacing session ...` line appears exactly once,
early in the session, as designed:

```
2026-08-26 17:07:22 INFO    rehoboam.trader | pacing session median_move=10800000 slots_to_fill=3 reserve=21600000 open_offers=40717295 n_prices=48
```

- `median_move=10,800,000` — close to, but not identical to, the
  EUR 10,996,594 quoted in this task's brief from an earlier run; the
  90-day rolling window recomputes every session, so this is genuine drift,
  not a discrepancy (`n_prices` moved 47 → 48 too, confirming the window is
  live).
- `slots_to_fill=3` — squad is 12/15 (confirmed in the session-context log
  line: `squad=12/15 budget=62307522 ... pending_bids=1`).
- `reserve=21,600,000` — `capital_reserve` with 2 moves wanted
  (`slots_to_fill - 1 = 2`) at the EUR 10.8m median: `2 x 10,800,000`.
- `open_offers=40,717,295` — the outstanding Raum bid, unchanged since the
  design doc was written.

Every plain-buy candidate this session was capped to 0
(`ep-bid paced player=... bid=... -> 0 (reserve=21600000 open_offers=40717295)`, repeated for all 10 recommended buys, including two
`must_have`-tier candidates). Spendable budget is
`62,307,522 - 40,717,295 = 21,590,227`, which is EUR 9,773 short of the
EUR 21,600,000 reserve — so every plain buy is refused. Trade pairs, which
pace on **net** cost, did clear: two pair candidates capped to nonzero
(1,877,991 and 2,638,508, both reflecting sell-plan proceeds), but the
session's Unified Trade Phase reported "No actionable opportunities" for
other reasons downstream, so nothing executed this run. Session summary:
0 sells, 0 trades, EUR 0 net change — consistent with a dry run where the
single open offer is currently consuming the capacity three empty slots
would otherwise need.

This matches the shape described in this task's brief (an EUR 40.7m
outstanding offer consuming spendable budget), confirming the live wiring
behaves the way the design and the replay both predict, without needing
today's exact reserve number to match a prior run's.

## Addendum: the final whole-branch review (2026-08-26, after this document was first written)

A whole-branch review found **three Important defects, every one of them invisible
to the measurements above**. All three were of the same kind — the bot stops
trading when it should not — and none could cause overspending. Fixed in
`ebfe5bb`; the sweep numbers were re-measured afterwards and are **unchanged**,
which is itself the point: the replay could not see any of them.

1. **At 15/15 the reserve froze every trade pair, including budget-positive
   ones.** `max_bid` demanded "budget after this trade >= reserve"; when the
   budget was already below the reserve, nothing satisfied it — so a trade
   selling a EUR 12m player to buy an EUR 8.86m one, netting **+EUR 3.14m into
   the budget**, was refused. Design section 3 explicitly claimed net-cost pacing
   prevented this. It did not: at 15/15 the budget is small *by construction*
   (section 2's own unwind table ends at "15/15, budget EUR 0"), and at 15/15
   plain buys and flips are already blocked by `available_slots <= 0`, so pairs
   are the only improvement mechanism left.
   The replay never reached 15/15 (peak 8 buys) and the live smoke ran at 12/15.
   **The feature spends a season driving the bot toward the one state nothing
   measured.**
   Fixed by clamping the effective reserve at the pre-trade spendable budget:
   `min(reserve, max(0, current_budget - open_offers))`. The rule changes from
   "refuse unless you can hold the full reserve" to "refuse only if this leaves
   things worse than now". Verified: the 15/15 pair now yields a cap of
   EUR 12,000,000, while a healthy EUR 62,307,522 budget still yields
   EUR 40,707,522 — the clamp does not loosen a reserve that already binds.

1. **Profit flips were never paced**, despite section 3's table saying they were.
   Flips are sized by `ProfitTrader` and never reach `calculate_ep_bid`. Since
   `enable_flip_buys` defaults to True, this ran in production: a flip could
   consume the exact capital the reserve was protecting, in the same session
   where pacing refused a squad-improvement buy for lack of it. The sweep could
   not see it either — it ran without `--with-flip-buys`.

1. **An under-strength squad froze buying** exactly when empty slots cost -100
   points, because the reserve scales with empty slots (8/15 reserves EUR 75.6m).
   The emergency-fill exemption did not cover it: that path runs only in the
   locked phase, so two to five days before kickoff such a squad got no
   autonomous buy *and no proposal*. `is_emergency` was already computed two
   lines above where the pacing context was built, and was never consulted.

**What this says about the measurement in this document.** The sweep is sound for
what it covers, and it covers a squad-building regime only. Three separate
"the bot goes quiet" defects lived entirely outside it. Read the +859 as an
allocation result from one regime, not as evidence the feature is safe across
the season — the fixes above, not the sweep, are what make it so.

## Caveats

- **The replay's buy side is an upper bound**, per the design's own Risks
  section (citing REH-72 §8). The pacing finding here does not rest on the
  points delta alone but on a logged reserve collapse and a buy count, both
  read from the live bidding path (`SmartBidding.calculate_ep_bid`), the
  same path the live smoke test exercised.
- **The replayed squad builds from near-empty** (starting at 0/15, reaching
  12-15/15 only well into the season in the configurations that buy more).
  That is precisely the regime in which the mechanism section shows the
  reserve misbehaves most (14 empty slots against an ~EUR 80m budget). A
  mid-season bot that is already at or near 15/15 — arguably closer to the
  live bot's actual state today, 12/15 — sits in a very different part of
  the reserve's behavior, where `pacing_in_season_min_moves` (untested by
  this sweep, see above) becomes the operative floor instead of
  `slots_to_fill`. The replay measures the failure mode that matters at
  squad-building time; it does not measure, and should not be read as
  measuring, steady-state mid-season pacing.
- The two windows that did show a buy-count rise (7, 14 days) are the ones
  the mechanism analysis flags as least trustworthy, not most — a smaller
  number of days is not a better estimate, only a smaller and noisier one
  that happens to produce a smaller reserve.

## Test suite

```
$ uv run pytest -q
1272 passed, 1 skipped in 16.68s
```

No production code or tuning defaults were changed by this task. HEAD is
still `ef05f46` (Task 8's final commit); this document is the only change.
