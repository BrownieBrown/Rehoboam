# Pace the season's capital: reserve the ability to keep buying

Date: 2026-08-26
Status: design, approved in conversation, not yet planned
Ticket: https://linear.app/jovily/issue/REH-85
Related: REH-69 (fixed the gradient, not the pacing), REH-72 (the analysis),
REH-97 (the same disease from the ranking side), REH-99 (the bid ceiling this
composes with)

______________________________________________________________________

## Why

`bidding_strategy.py:315` reads `ep_max_bid = int(budget_ceiling * bid_fraction)`,
where `bid_fraction` ramps to 0.8. That asks **"what fraction of my current
budget does this signing justify?"** — a single-decision question with a
defensible answer. Nothing anywhere asks the sequential question: *what does this
signing leave me able to do for the next thirty matchdays?*

REH-72 §6 caught the consequence in a competition-modelled replay, through the
live `SmartBidding.calculate_ep_bid` path:

```
bid=71008000  ceiling=80000000   <- first buy: EUR 71m on ONE player
              ceiling=4687915    <- and thereafter, all season:
bid=0  ep_gain=+85.8  ask=15678910  ceiling=4687915
bid=0  ep_gain=+81.3  ask=18777777  ceiling=4687915
```

Five buys, final budget EUR 500,000. Every declined candidate was rated
`must_have` by the bot's own tiering. It did not misjudge them; it had no money
left.

REH-69 predicted this in its own comment — *"the bot would commit 80% of budget
to the first qualifying candidate and be unable to afford the one that mattered
next week"* — and fixed only the gradient. A +43 and a +195 are now sized
differently, but a large gain still consumes the budget, because the ramp tops
out at a fraction of *whatever is available* rather than at anything sequential.

## What the evidence actually says

The ticket proposes as a first cut "a hard per-transaction ceiling near the
winners' observed mean (~EUR 11m per buy)". **Measured against
`manager_transfers`, that would have banned both champions' biggest signing.**
The mean is an artefact of a heavily skewed distribution:

| manager          | buys |    median |       p75 |           max | >EUR 20m | >EUR 40m |
| ---------------- | ---: | --------: | --------: | ------------: | -------: | -------: |
| 3199978 (winner) |   30 | EUR 10.0m | EUR 12.5m | **EUR 65.0m** |        4 |        1 |
| 1907519 (winner) |   24 |  EUR 6.8m | EUR 14.5m | **EUR 60.0m** |        3 |        1 |
| 3616202 (ours)   |   20 |  EUR 3.2m |  EUR 5.4m |     EUR 38.3m |        1 |        0 |

Each champion made **one enormous buy and roughly 25 more purchases**. We made
one large buy and then stopped. The distinguishing behaviour is not the size of
a single bid; it is retaining the capacity to keep operating afterwards.

Nor is concentration a late-season artefact. The large buys, dated:

| manager | date           | player       |              price |
| ------- | -------------- | ------------ | -----------------: |
| 1907519 | 2026-05-05     | Castello Jr. |     EUR 60,000,000 |
| 3199978 | **2026-08-25** | **Kimmich**  | **EUR 65,000,065** |
| 1907519 | 2026-08-25     | Uzun         |     EUR 32,750,000 |

The champion spent EUR 65m **the day before this design was written**, three days
before MD1, and has made 11 buys this month. We have made 2. Whatever the rule
is, it must not simply forbid a decisive opening signing.

### The price of a "move" drifts, so it must be measured

| population                       | buys |    median |       p75 |
| -------------------------------- | ---: | --------: | --------: |
| all leagues, whole table         |  254 | EUR 6.03m | EUR 11.7m |
| 2026/27 pre-season (since Aug 1) |   45 | EUR 10.8m | EUR 16.8m |

A hardcoded euro figure would be wrong within one transfer window. This is the
same lesson REH-99 learned about the overbid cap: measure the population, and
re-measure it.

## Goals

1. A large signing stays legal. The rule constrains what a buy may *leave
   behind*, never its absolute size.
1. After any discretionary buy, the bot retains enough budget to make the moves
   it still needs.
1. The constraint self-calibrates as league prices drift.
1. Every parameter is a `Settings` field, re-tunable from `.env` without a
   deploy.

## Non-goals

- **The sell side.** The champions sustain their tempo by recycling — buying
  EUR 130–227m and selling EUR 123–203m per season while staying near
  cash-neutral. Changing when the bot sells overlaps REH-63, REH-71, REH-43 and
  REH-57, each needing its own replay measurement. Deferred deliberately, and
  the honest consequence is recorded under Risks.
- **Tuning `min_ep_gain`.** Swept in REH-72 §6 and monotonically worse (26,960
  at the shipped 40.0; 25,646 at 20; 23,628 at 5). It is not the lever.
- **Ranking by EP-per-euro.** That is REH-97, the same disease approached from
  the ranking side rather than the sizing side. Kept separate so each is
  measurable alone.

## Design

### 1. The rule

One additional cap inside `SmartBidding.calculate_ep_bid`, composed with the
caps already there:

```
median_move  = median(league buy prices over a trailing window)
moves_wanted = max(squad_slots_to_fill, in_season_min_moves)
reserve      = moves_wanted * median_move
pace_max_bid = (budget - open_offers) - reserve

bid = min(existing ep_max_bid, REH-99 ceiling, pace_max_bid)
```

`moves_wanted` is **the moves still wanted, not a constant**. A constant is the
obvious formulation and it is wrong: with a fixed 3 moves at EUR 10.8m the
reserve stays EUR 32.4m while the budget falls, so the second buy is capped near
EUR 4.8m and the bot freezes one purchase later than before. Deriving it from
unfilled slots makes the reserve unwind as the squad completes.

`squad_slots_to_fill` is measured **to the 15-player cap, counting open offers as
filled** — the same quantity `_available_squad_slots` already computes, and for
the same reason: Kickbase counts a pending offer toward the cap. Measuring to
eleven instead would reserve nothing for a squad that can field a legal eleven
but has no cover, which is the position we are in today.

### 2. How it unwinds

Live state on 2026-08-26 — budget EUR 62,307,522, squad 11 players + 1 open
offer, `median_move` EUR 10.8m:

| step                | squad | budget    | reserve              | max spend |
| ------------------- | ----- | --------- | -------------------- | --------: |
| now                 | 12/15 | EUR 62.3m | 3 x 10.8 = EUR 32.4m | EUR 29.9m |
| after a EUR 25m buy | 13/15 | EUR 37.2m | EUR 21.6m            | EUR 15.6m |
| after a EUR 15m buy | 14/15 | EUR 21.6m | EUR 10.8m            | EUR 10.8m |
| after a EUR 10m buy | 15/15 | EUR 10.8m | in-season floor      |         — |

Several mid-sized buys instead of one that ends the season — the champions'
observed shape.

### 3. Where it does and does not apply

| path                     | pacing applies  | why                                                               |
| ------------------------ | --------------- | ----------------------------------------------------------------- |
| plain squad-improvement  | yes             | the discretionary buy this is about                               |
| trade pairs              | yes, on **net** | a pair sells before it bids, so it consumes only `bid - proceeds` |
| profit flips             | yes             | discretionary, and a flip is capital parked rather than deployed  |
| **emergency squad fill** | **no**          | an empty lineup slot is **-100**, which outranks pacing           |
| compliance re-bid        | no              | mandatory; its only legal alternatives are raise or cancel        |

The emergency-fill exemption mirrors the REH-100 refusal policy: that path fails
toward *fielding someone*.

**Pairs are paced on net cost, and this is load-bearing.** A pair is forced to
sell before it bids, because at 15/15 the sell is what frees the slot. Applying
the reserve to the gross bid would therefore freeze pair trading exactly when it
is the only move available: at 15/15 `moves_wanted` falls to
`in_season_min_moves`, the budget is by then small, and no gross bid could clear
the reserve. Pacing the net cost states the truth — a pair recycles capital
rather than consuming it — and keeps the one mechanism that lets a full squad
improve. It is also the same net figure `_run_trade_phase` already tests against
`flip_budget`.

### 4. Open offers count as spent

`budget - open_offers`, not `budget`. Kickbase's reported budget does not deduct
pending offers, so two bids sized against the same nominal budget can both land.
`notify/approval.py` already makes exactly this subtraction, and
`_compute_flip_budget` subtracts `pending_bid_total` for the same reason.

### 5. It caps, it does not refuse

`pace_max_bid` enters the existing `min(...)`. Where the cap falls below the
asking price the buy is skipped as a natural consequence, but pacing never
introduces a second, competing refusal path alongside REH-99's ceiling and
REH-100's gate. One number, computed once, composed with the others.

### 6. A proposal that pacing would refuse is not proposed

Plain squad-improvement buys became proposals on 2026-08-24. REH-99's lesson was
that offering a buy which the gate then refuses makes the Approve button
unusable. Pacing therefore applies at sizing time, before `_propose_buy`
renders, so no proposal reaches Telegram that pacing would block.

### 7. Configuration

| field                        | first value | rationale                                   |
| ---------------------------- | ----------- | ------------------------------------------- |
| `pacing_enabled`             | true        | one switch to revert without a deploy       |
| `pacing_in_season_min_moves` | 2           | the reserve once the squad is at 15/15      |
| `pacing_window_days`         | 90          | trailing window for `median_move`           |
| `pacing_median_floor_eur`    | 3,000,000   | a thin window must not collapse the reserve |

`median_move` is read from `manager_transfers` (`transfer_type = 1`), the same
table this design's evidence comes from. It currently holds 565 rows spanning
2026-01-03 to 2026-08-26, so the trailing window is populated today — but the
table's freshness is REH-74's concern, and a stale table degrades this feature
silently. That is what `pacing_median_floor_eur` is defending against. When the window holds too few rows,
`pacing_median_floor_eur` applies — the failure mode of a near-empty window is a
reserve of nearly zero, which silently disables the feature.

## Verification

1. **Unit tests** for the reserve arithmetic: the unwind sequence in §2, the
   constant-N failure it replaces, open-offer subtraction, the emergency-fill
   exemption, and the thin-window floor.
1. **`replay-season --with-competition`**, the configuration where the failure is
   visible. Baseline to beat: **26,391**. The plain-configuration baseline is
   26,960.
1. **Buy count and terminal budget** reported alongside points. The failure being
   fixed is "5 buys, EUR 500,000 left"; a variant that scores similarly while
   still ending on 5 buys has not fixed it, and points alone would hide that.
1. **A sweep** of `pacing_in_season_min_moves` and `pacing_window_days`,
   published as a table rather than a single number.
1. **Live `auto --dry-run`** before merge, per the standing rule for changes that
   touch the bidding path.

Per REH-71's recorded faithfulness decision, treat any single replay delta below
6,162 points with suspicion unless it replicates.

## Risks

- **Buy-side-only pacing cannot reproduce the champions' tempo.** They sustain
  25+ purchases a season by recycling capital, which is out of scope here. This
  design prevents the lockout; it does not by itself produce their volume. If the
  replay shows the reserve merely converting "one huge buy then nothing" into
  "several mid buys then nothing", the sell side is the next ticket and this
  measurement will say so.
- **It restricts exactly the signing the champion just made.** On today's
  numbers the live EUR 40,717,295 bid on Raum would be refused (it leaves
  EUR 21.6m against a EUR 32.4m reserve), as would Tah (EUR 44.1m) and Undav
  (EUR 54.4m); Asllani (EUR 25.1m) passes. This is the rule working as intended,
  and it is also the strongest argument for measuring
  `pacing_in_season_min_moves` rather than assuming it.
- **`manager_transfers` mixes manual and automated trades.** REH-72 §0a retracted
  every attribution of behaviour to the bot for this reason. It is used here only
  as a *price* population — what a move costs in this league — which does not
  depend on who initiated it.
- **The replay's buy side is an upper bound.** REH-72 §8. The pacing finding does
  not rest on a points delta but on a logged ceiling collapse and a buy count,
  both from the live bidding path.

## Out of scope, filed separately if confirmed

- Sell-side recycling to sustain tempo (see Non-goals).
- Recording provenance on transfers, which would retire the manual/automated
  ambiguity that forced REH-72 §0a.
