# REH-71 — Decide the flip policy: should `auto` trade for profit at all?

Ticket: https://linear.app/jovily/issue/REH-71
Date: 2026-08-05
Status: implemented; **one premise corrected 2026-08-19 — read the correction
below before using anything in this document.**

## Correction (2026-08-19, REH-76)

This design's cash branch rests on "every round trip pays a measured 11.7%
toll" — a mean transaction price of 1.117× market value against an instant
sell returning 1.00×. **The 1.117× is real but was measured on the wrong
channel.** It came from `transfer_type = 2` rows: manager-to-manager
auctions. The live flip path buys only `is_kickbase_seller()` listings
(`trader.py:685`), where price **is** market value by construction and
`ProfitTrader` branches on that equality (`profit_trader.py:121`). A
Kickbase-sourced round trip carries no structural toll, so the argument never
touched the channel it was used to condemn. The verdict it produced was
withdrawn on 2026-08-07 and both switches ship `True`.

What *is* measured, by REH-75 across 136 scored round trips: we paid a **12.2%
premium over market value at entry** (ratio 1.1217), invariant across every
horizon. That is a property of what we bid, not a fee the channel charges —
and note it exceeds the 0–11.7% bracket this document assumed the true
flip-channel premium had to lie within.

The measurement protocol, the 2×2 arm totals and the effects below all stand.
Only this premise, and the conclusion drawn from it, do not. The body is left
as written so the record of what was believed on 2026-08-05 stays legible.

______________________________________________________________________

## Problem

Real profit flipping in 2025/26 destroyed money:

|                       |                               |
| --------------------- | ----------------------------- |
| Completed round trips | 151 (2025-08-10 → 2026-05-15) |
| Net profit            | −€55,256,064                  |
| Win rate              | 27.8% (42 of 151)             |
| Average win / loss    | €1,873,614 / −€1,228,879      |

€55M is comparable to the entire €80M starting budget. If it is recoverable it
converts directly into squad quality, which is the only thing that scores
points. But the inference "turn flipping off" is not yet supported: the two
replay runs that bracket it are not comparable, because `competition, no flips`
(−1,158) predates the fieldability fix and starter protection that
`competition + flips` (+512) has.

## What the ticket assumed, and what is actually true

REH-71 was written expecting "one flag and two runs". Reading the code changed
the scope twice.

**The `--with-flips` flag models only half the behaviour.** `_flip_sells`
(`rehoboam/replay/engine.py:157-212`) is purely sell-side: take profit at
`min_sell_profit_pct`, cut losses at `max_loss_pct`, on squad players who are
not best-eleven starters. The live bot *also buys players purely to flip* —
`auto_trader.py:342-392` pulls `Trader.find_profit_opportunities` candidates and
buys them for expected appreciation rather than expected points. The −€55M came
from both halves. Deciding "should auto trade for profit at all" from a
sell-side-only flag would answer a different question than the one asked.

**Flip income is euros; the attribution table is points.** `attribution_rows`
(`rehoboam/replay/attribution.py:37-58`) decomposes a points delta as
`other = delta - zero_recovered`. Flip P&L cannot be subtracted from it — euros
minus points is a category error. Flips reach the scoreboard only indirectly,
through cash that funds subsequent buys.

## Decisions taken

1. **Model flip buys before running the A/B.** The replay gains a flip-buy pass
   so `--with-flips` covers both halves of the live behaviour.
1. **Call the real shipped heuristics**, not reimplementations — the pattern
   REH-68 established when `make_ep_bid_fn` wired in the actual `SmartBidding`
   rather than approximating it.
1. **Report cash as cash.** A separate Trading block in euros; the points
   contribution is the measured difference between paired runs, not a
   decomposed term.
1. **Two independent Settings switches**, because flip buys and profit sells are
   distinct behaviours that may not warrant the same verdict.

## 1. The experiment: a 2×2 factorial

Separate switches make a single A/B confounded. Four arms, all run with
`--with-competition`, nothing else varying:

| Arm | flip buys | profit sells | what it is                        |
| --- | --------- | ------------ | --------------------------------- |
| A   | off       | off          | pure EP bot — the floor           |
| B   | off       | on           | today's `--with-flips`            |
| C   | on        | off          | buys to appreciate, never banks   |
| D   | on        | on           | closest to live 2025/26 behaviour |

Main effects:

- buy-side effect = mean(C, D) − mean(A, B)
- sell-side effect = mean(B, D) − mean(A, C)
- interaction = (D − C) − (B − A)

The interaction is not decoration. Arm C opens positions it never closes and
arm B closes positions it never opened, so a flip policy that only pays off as a
matched pair would show up here and nowhere else.

### Pre-committed decision rule

REH-68 recorded that a single faithfulness decision moved the season total by
6,162 points. Against a ~27,000-point season that noise floor will plausibly
swallow every flip delta. The rule is therefore fixed **in advance**, so the
result cannot be rationalised after it is seen:

> If every |delta| \< 6,162 points, the points evidence is declared
> **inconclusive** and the decision falls to the cash evidence: real flipping
> lost €55,256,064 at a 27.8% win rate, and every round trip pays a measured
> 11.7% toll (mean transaction price 1.117× market value against an instant sell
> returning 1.00×). Both switches default **off**, and this document records that
> the decision was made on cash, not on points.

If a delta does clear 6,162 points, that arm's verdict is adopted for its
switch, and the other switch follows the same rule independently.

## 2. Flip buys: reuse both shipped heuristics

New module `rehoboam/replay/flip_buys.py`. Two seams make heuristic
reimplementation unnecessary:

- **`TrendService.analyze(history_data, current_market_value)`**
  (`services/trend_service.py:243`) is already a pure `@staticmethod` with no
  I/O. Its input is synthesised from the corpus `mv_series` table, whose
  `snapshot_at` is exactly `dt × 86400` (`enrichment/corpus.py:336-345`), so the
  round trip back to the API's `{"it": [{"dt": …, "mv": …}], "hmv": …, "lmv": …}`
  shape is lossless.
- **`ProfitTrader.find_profit_opportunities(...)`** is called for real, fed a
  `CorpusMarketPlayer` adapter satisfying the attribute surface it reads:
  `.id`, `.price`, `.market_value`, `.average_points`, `.status`, `.position`,
  `.first_name`, `.last_name`.

### Two traps that would each silently zero the pass

**Leak via `peak_value`.** `hmv` must be computed from the window truncated at
`decide_at`, never season-wide. `ProfitTrader`'s mean-reversion branch gates on
`current_vs_peak_pct < -25` (`profit_trader.py:172-175`); a season-wide peak
lets the bot know in August what a player will be worth in March.

**The `is_kickbase` test inverts the model.** `ProfitTrader` branches on
`player.price == player.market_value` (`profit_trader.py:121`). Replay listings
carry real transaction prices, which average 1.117× market value, so equality
never holds. Every candidate would fall to the non-Kickbase branch, where
`value_gap = market_value - price` is negative and the candidate is skipped at
`profit_trader.py:194`. Flip buys would appear modelled and never fire once.

The fix separates decision price from execution price, which is also the
faithful reading: the live bot only flips `is_kickbase_seller()` listings
(`trader.py:685`), where price *is* market value by construction.

- **Decision**: the adapter sets `price = market_value = corpus.market_value_at(pid, decide_at)`.
- **Execution**: the engine pays our winning bid, sized as below.

### Flip bids face competition, at an economically-derived ceiling

Rival managers can target the same flip candidate, so flip buys are subject to
the same competition model as EP buys. But `make_ep_bid_fn` is the wrong bidder
for them: it sizes from `marginal_ep_gain`, which is ~0 for a flip by
construction, so every flip bid would land in the bottom tier, fail the
`our_bid > listing.price` test, and collapse arms C and D into A and B — the
experiment would report "flip buys do nothing" as a modelling artifact.

Flips bid on their own economics instead. Paying `P` is only rational if the
expected exit still clears the margin `ProfitTrader` itself demands. Exit
proceeds are `MV × 1.00` (`INSTANT_SELL_PCT`, measured in REH-67), so:

```
ceiling = MV_now × (1 + expected_appreciation / 100) / (1 + min_profit_pct / 100)
```

`expected_appreciation` comes from the `ProfitOpportunity`; `min_profit_pct` is
8.0, read from `Trader.find_profit_opportunities`'s call site (`trader.py:719`)
rather than `ProfitTrader.__init__`'s unused default. The bid is additionally
capped by what the budget allows, and we win only if it exceeds
`listing.price`.

Losing a contested flip is a real economic outcome — a rival paid more than the
flip could ever return — and must not be papered over by bidding higher.

The alternative considered and rejected: paying `listing.price` uncontested.
That charges a measured 11.7% premium against an 8% expected appreciation, so
nearly every flip loses by construction and the verdict is baked in before the
run.

### Pinned inputs

Documented as bounds, exactly as `make_ep_bid_fn` pins `offer_count=0` and
`trend_change_pct=0.0`:

- `status = 0` (available). The corpus's per-match status is participation, not
  injury or market availability. Pinning it means nothing is skipped as injured,
  so the replay buys **more** flips than live — an upper bound on flip activity,
  and therefore on flip harm.

### Engine integration

A new `flip_buy_fn` parameter on `run_season`, running **after** the EP buy loop
— mirroring `auto_trader.py:533`, "Execute profit flips with remaining slots" —
and only while `squad_size < MAX_SQUAD_SIZE`. A flip never displaces a squad
member, because the live bot does not sell to make room for one. The same
guards apply as for EP buys: `can_buy`, `_solvent_after`, fieldability, and the
dead-weight check.

Flip-bought players are not best-eleven starters (they are chosen for
appreciation, not points), so `_flip_sells`'s starter protection does not block
them and the round trip closes naturally.

## 3. Reporting: cash stays cash

`attribution_rows` stays in points and is not modified.

`SeasonResult` gains a flip ledger: one `FlipRecord` per closed round trip
(`player_id`, `buy_price`, `proceeds`, `bought_at`, `sold_at`). The report gains
a clearly separated block — the figures below are **illustrative layout only**,
not results:

```
Trading (cash — does not enter the points attribution above)
--------------------------------------------------------------------
  Realised flip P&L                        EUR -3,204,118
  Round trips completed                               18
  Win rate                                         33.3%   (6 of 18)
  Real 2025/26, for comparison             EUR -55,256,064
                                                     151 trips, 27.8%
```

Round trips count only players the replay **bought**. The opening squad carries
a cost basis — market value at assignment (`state.py:96-100`) — so without an
explicit `acquired: "assigned" | "bought"` field on `ReplayPlayer`, assigned
disposals would inflate the count and make it incomparable to the real 151.

`FIDELITY_NOTES` is updated: "Sell decisions … profit flips NOT modelled" is now
conditional on the arm.

### CLI

- `rehoboam replay-flip-policy` — runs all four arms, prints the factorial
  table, and applies the pre-committed decision rule to its own output. Sits
  apart from `replay-season` the way `replay-buy-control` does, because it is a
  labelled control, not a counterfactual result.
- `--with-flip-buys` added to `replay-season`, so each arm is individually
  reproducible.

The Trading block appears in both.

## 4. Settings

```python
# Shown with the inconclusive-case values. Each default is set by §1's
# pre-committed rule once the four arms have run — fixing a default before the
# measurement is exactly what that rule exists to prevent.
enable_flip_buys: bool = Field(
    default=False,
    description="Buy players for expected appreciation rather than expected points (REH-71)",
)
enable_profit_sells: bool = Field(
    default=False,
    description="Take profit / cut losses on squad players against their cost basis (REH-71)",
)
```

Gating:

- `enable_flip_buys` → the flip-candidate block at `auto_trader.py:344`
- `enable_profit_sells` → `run_profit_sell_phase` at `auto_trader.py:751`

Defaults are whatever §1's decision rule yields once the runs are in. The replay
reads both through the existing `_shipped_default` helper (`driver.py:159-167`),
so a production re-tune cannot leave the harness describing a bot nobody
deployed.

Out of scope: `min_value_score_to_buy` and `min_buy_value_increase_pct` are dead
fields belonging to REH-73.

## 5. Testing

- **Leak**: a market value recorded after `decide_at` must not reach `hmv`.
- **Contract**: `CorpusMarketPlayer` satisfies every attribute `ProfitTrader`
  reads, failing loudly if `ProfitTrader` grows a new one.
- **Fires at all**: at least one flip buy occurs in a season containing obvious
  momentum candidates. The `is_kickbase` trap produces a silent zero, and only a
  positive assertion catches it.
- **Guards**: never exceeds `MAX_SQUAD_SIZE`, never displaces a squad member,
  respects `_solvent_after`.
- **Round-trip closure**: a flip buy that later crosses the profit target is
  sold by `_flip_sells` and lands in the ledger as exactly one round trip.
- **Ledger scope**: assigned opening-squad disposals are excluded from round
  trips.
- **Determinism**: two consecutive runs are byte-identical.
- **Settings gating**: each phase is skipped when its switch is off.

## Carried constraints

- Read-only: both DB SHA-256s unchanged, `coefficients.json` byte-identical.
- Run once per arm. Credibility decays with every tuning iteration.
- All four arms are labelled control runs, never the headline counterfactual.
- No threshold may be adopted from these runs; that is a separate decision with
  its own evidence.
- A favourable result does not vindicate live behaviour. The replay's flip model
  protects best-eleven starters and pins injury status to available, so it is
  both more disciplined and more active than whatever produced the −€55M.
