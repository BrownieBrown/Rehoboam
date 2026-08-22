# EP targeting: availability recency, top-flight quality, and an absolute target bar

Date: 2026-08-22
Status: design, approved in conversation, not yet planned
Related: REH-90, REH-52, REH-42, REH-41, REH-71

## Why

On the first matchweek of 2026/27 the bot's buy ranking was close to inverted at
the top. Measured against the live market on 2026-08-22:

| player       | club           | Kickbase avg | bot's EP | bot's verdict                         |
| ------------ | -------------- | -----------: | -------: | ------------------------------------- |
| Ulreich      | Bayern         |          125 |     11.6 | skipped                               |
| **Pavlović** | **Bayern**     |      **119** | **29.1** | **skipped, below the 35.0 buy floor** |
| Baumgartner  | Leipzig        |          117 |     68.5 | 6th                                   |
| Burkardt     | Frankfurt      |           87 |     31.0 | skipped                               |
| Gregoritsch  | Augsburg       |           74 |     69.8 | 3rd                                   |
| El Mala      | Köln           |           71 |     69.5 | 4th                                   |
| **Rohr**     | **Elversberg** |      **0.0** | **89.1** | **1st, bid €12.3M**                   |
| **Gyamerah** | **Elversberg** |      **0.0** | **70.6** | **2nd, bid €8.6M**                    |

Two independent defects produce this. Both were diagnosed against live data
rather than inferred.

### Defect A — availability rests on a single, possibly ancient, observation

`scoring/v2/adapter.last_played_status` returns the status of the most recent
*played* match, with no recency constraint. `compose_ep` then weights every
status branch by `availability.predict(prev_status)`.

Pavlović's most recent played match is **2025/26 matchday 34, played
2026-05-16** — over three months old, the final matchday of last season, where
he was an unused substitute (`st=4`) in a dead rubber. The 2026/27 block holds
34 fixture rows and **zero** played ones.

That single stale observation yields `P(start) = 17%`. His fitted rate is
**131 points if started**, the highest in the market. `0.17 × 131` is what puts
him under the buy floor.

This is the dominant defect. Rohr's `P(start) = 85%` (previous status 5)
against Pavlović's 17% accounts for essentially the entire ranking inversion:
at Rohr's start probability Pavlović scores roughly 111, comfortably first.

### Defect B — second-division form is fitted as if it were Bundesliga form

`rate.quality` is a baked lookup of 389 `player_id → multiplier` values fitted
offline. Seven of the 22 Kickbase-sold listings have **no `ap`/`tp` field at
all** on `get_player_details` — no top-flight history, because they have never
played in the Bundesliga:

| player                 | club       |
| ---------------------- | ---------- |
| Rohr, Gyamerah, Petkov | Elversberg |
| Karaman, Grüger        | Schalke    |
| Gayret                 | Paderborn  |
| Schwolow               | Mainz      |

Six are from newly promoted clubs. `get_player_performance` still returns full
34-match seasons for them — those are **2. Bundesliga** matches — and nothing in
`scoring/v2/` records which competition a match belongs to. Rohr therefore
carries a fitted `quality = 1.124` and a data-quality grade of **A**, higher
confidence than El Mala, who is graded **C**.

Schwolow is the instructive exception: Mainz, top flight, but no `ap` — a
keeper with no Bundesliga appearances. The robust signal is "no top-flight
history", which is broader and more durable than "promoted club" and needs no
annually-maintained list.

This defect is real but **smaller than it first appears**. Rohr's 1.124 is
modest next to Pavlović's 1.432; the scorer does not believe Rohr is the better
player. Correcting it moves Rohr's rate from ~95 to ~80, roughly −16%.

### Missing capability — nothing defines a target worth waiting for

The bot ranks only by marginal EP gain against the current squad, over whatever
is listed today. There is no notion of absolute quality, so in a weak week it
spends slots on the best of a poor market. The buyable pool is genuinely small
— 22 of 51 listings on 2026-08-22 — and elite players rotate through it, so
patience has real value.

## Goals

1. Availability reflects recent evidence, or admits it has none.
1. A player with no top-flight history is not scored as a confident known
   quantity.
1. A player is only worth a squad slot if his **absolute** expected points
   clear a league-elite bar; **marginal** gain then decides price and who he
   displaces.
1. "No target available" is a computed, logged state, not an accident.

## Non-goals

- **Profit trading of any kind.** Considered and dropped. Measured history:
  151 flips, **−€55.3M realised**, driven by a +12.2% average entry premium
  against a ~4%-per-fortnight drift, with only the 10 flips held over two
  months profitable. Buying power is not the constraint: €92,047,144 cash plus
  €88,854,939 of squad market value against a €32.2M target. REH-71 stays open
  and `ENABLE_FLIP_BUYS` stays false.
- **Player-to-player listings.** 27 of 51 listings, including Gnabry (avg 142)
  and Ito (avg 103), are filtered out by `is_kickbase_seller()` at
  `trader.py:225`. Marco confirmed that managers list players for fast sale and
  do not complete trades, so these are not practically buyable. The filter
  stays.
- **A corpus-backed league-wide watchlist.** `training_corpus.db` holds 531
  players and 75,924 match rows, but 2026/27 has **zero played rows**, so it
  would rank on the same 2025/26 form the live pipeline already uses. It is
  15 MB and absent from `azure_blob.DB_FILES`; syncing it is non-trivial given
  that the 17.5 MB `player_history.db` push already times out. Revisit once
  there is real season data to rank on.
- **Refitting the rate model.** Serving-time exclusion achieves the same result
  for the affected players; a refit is a separate, larger piece of work.

## Design

### 1. Recency-gated availability

`last_played_status` gains an age bound. When the most recent played match is
older than the bound, return `None` rather than a stale status.

`None` is already a first-class input: the function's own docstring records
that the availability model "handles \[it\] by falling back to its marginal
prior". No new path is introduced and no renormalisation is required — the
`rate.py` caveat about a ~24% starter bias applies only to overriding
`P(status)` directly at serving time, which this does not do.

The bound is a `Settings` field so it is tunable from `.env` without a deploy.
It must exceed a normal in-season gap (including the winter break) and fall
below the off-season gap.

Expected effect: Pavlović stops being scored off a 2026-05-16 bench appearance
and is weighted by the league-wide prior until 2026/27 produces evidence.

### 2. Top-flight quality gate

`rate.predict` already falls back to `position_prior` when a `player_id` is
absent from `quality`:

```python
multiplier = self.quality.get(player_id)
if multiplier is None:
    multiplier = self.position_prior.get(position or "", 1.0)
```

So the fix is a serving-time exclusion, not a refit: when a player has no
top-flight history, do not consult his fitted quality. Detection is the absence
of `ap`/`tp` on `player_details`, which `PlayerData.player_details` already
carries into the scorer.

This is **not** a discount multiplier. REH-80's blanket cold-start discount was
reverted for costing 782 points and a league place; this instead declines to
apply a coefficient fitted on inapplicable data, and lets the existing
shrinkage-to-prior mechanism do exactly what it was built for.

Data quality must stop reporting grade **A** for a player with zero top-flight
appearances.

Expected effect: Rohr's multiplier moves from 1.124 to the 0.941 defender
prior, rate ~95 → ~80.

### 3. Absolute target bar

A new gate, applied alongside the existing marginal-gain threshold: a market
player is a **target** only if his absolute expected points clear an elite bar.

Two-stage, as agreed:

- **absolute EP** answers *is this player worth a squad slot at all* — stable
  week to week, independent of my squad, and the thing the bot waits for;
- **marginal EP gain** answers *is he worth today's price, and who does he
  displace* — unchanged from today.

The bar is a `Settings` field **derived from the measured distribution**, not
chosen. `rehoboam derive-thresholds` already reports the percentiles. It must
not be set from a pre-season measurement: the 2026-07-31 derivation ran at
n=0 and n is only 9 as of 2026-08-22.

### 4. Availability state and holding

`competitor_player_ids` (`trader.py:280`) already rebuilds the set of every
player in every opponent's squad each session, and is currently used for
nothing but an `uncontested` metadata flag.

Wire it into a logged state each run: how many targets exist, how many are held
by opponents, how many are listed now. When none are listed, **hold** — set the
lineup, buy nothing, and log the reason.

Holding a slot is not the same as keeping dead weight. The sell path continues
to run, so the bot still clears players like Klaas (avg 0.0) and Maksimovic
(avg 18) while waiting.

Two exceptions, stated so the implementation cannot read "buy nothing" too
literally:

- **The emergency fieldability fill still fires.** `_emergency_slots_short`
  (REH-82) exists to prevent the −100 empty-slot penalty and must override the
  hold, exactly as it already overrides the matchday-locked no-trade rule. An
  unfieldable squad is never an acceptable state to hold in.
- **Position minimums still bind.** If holding would leave the squad unable to
  field a legal formation at kickoff, the hold yields.

## Verification

**This is the first change in this sequence the replay can actually measure.**
Per REH-84 the replay calls the real scorer, so `rehoboam replay-season` is a
genuine gate here against the **26,960 / 26,172 / +788 / 9th of 14** baseline.
Three PRs merged on 2026-08-21/22 (#66, #67, #68) each left that number
untouched because none of their code paths are exercised by the replay — see
REH-88. That excuse does not apply to this work.

Tests must pin the live cases that motivated the change:

- Pavlović's availability must not rest on a match played 2026-05-16.
- Rohr must not outrank Pavlović.
- A player absent from `quality` must use the position prior (guards the
  existing fallback against regression).
- The hold state must produce no buys when no target is listed, and must not
  suppress sells.

Live `--dry-run` against production before merge, per the standing rule for
changes that reinterpret API fields.

## Risks

- **The bar is set too high and the bot never buys.** Mitigated by deriving it
  from the measured distribution, making it `.env`-tunable without a deploy,
  and logging the target-count state every run.
- **The recency bound is set too short**, discarding usable in-season evidence
  after a winter break or an injury layoff. Mitigated by choosing it against
  observed gap lengths rather than intuition.
- **Correcting promoted-club quality tanks a player who is genuinely good.**
  This is what the replay is for; the ~16% correction is modest by design.
- **Fixing availability lifts players who genuinely will not start.** The
  marginal prior is a weaker claim than a confident 85%, not a stronger one, so
  the failure mode is under- rather than over-confidence.

## Open questions

1. The exact recency bound, and the exact absolute bar. Both to be derived from
   data during implementation, not chosen here.
1. Whether the data-quality grade should be capped for no-top-flight players or
   given a distinct grade of its own.
1. Whether the hold state should still permit an exceptional non-target buy —
   deferred; the simplest version holds unconditionally and can be relaxed once
   the state is observable in the logs.
