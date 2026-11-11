# Rehoboam v3 — From-scratch evaluation

**Date:** 2026-11-11
**Status:** Evaluation — input to the v3 design spec, not a design
**State:** prod frozen to `TRADING_MODE=lineup_only` on 2026-11-11 ~13:25 UTC

______________________________________________________________________

## 0. Why this document

On 2026-11-11 Marco stopped the bot's trading and ordered a full redesign of its code and
logic, to ship when it is ready. It is the second redesign: the first
(`2026-07-29-rehoboam-v2-design.md`) was done well ahead of the season, and in his words
"it was all for nothing". This document answers three questions before any design is
written:

1. What does the bot actually do, rule by rule? (§5)
1. What did each rule earn or cost us? (§1, §4, §6)
1. What wins in this league? (§7)

Plus a fourth that the first redesign never asked: **why did a good design not survive
contact with the season?** (§2, §8)

______________________________________________________________________

## 1. Results on 2026-11-11 (MD9 played, MD10 on Fri 2026-11-20)

|                        | Us          | League                                 |
| ---------------------- | ----------- | -------------------------------------- |
| Rank by points         | **12 / 14** | leader 10,002 pts; us 5,844            |
| Matchdays above median | **2 of 9**  | MD5 (911 vs 903.5), MD7 (766 vs 723)   |
| Team value             | **120.2m**  | lowest; top three 218.0–251.1m         |
| Transfer P&L, season   | **−66.6m**  | worst; top three +18.5m to +79.8m      |
| Wallet                 | −32.1m      | −25.4m after Marco sold Marino by hand |

No penalties: eleven fielded on every matchday since MD4.

______________________________________________________________________

## 2. Redesign 1: what it decided, what shipped

The July spec diagnosed the loss mechanism of 2025/26 correctly. Its §1.3: "151 flips,
−55.3M realised … bought at an average +12.2% overbid; instant sell returns market value …
short flips cannot work." Its prescription — hold the level, stop trading — was derived from
our own trades only; §7 shows it is not what wins in this league. Its strategy (§3) and
decision layer (§4.2):

| v2 decided (July)                                    | What ran this season                                                        |
| ---------------------------------------------------- | --------------------------------------------------------------------------- |
| Own the level, not the moment; hold                  | Momentum flips, turn exits, rank sells, debt-recovery sales                 |
| Exit on level breaks; never on profit targets        | Profit-target selling kept; turn exit for flips                             |
| Hard overbid cap ~8%                                 | Win-curve bids; core buys at the winners' p75 (+17.5% at 15m+); Petkov +27% |
| ~3 trades per session plus a weekly cap              | Up to 10 per session (15 aggressive)                                        |
| Min hold ~45 days, overridable by decline triggers   | Hold-period guard in hours                                                  |
| Every trade must clear the round-trip toll           | Not enforced as one rule (see §5)                                           |
| Aggressive debt only with a trustworthy timing guard | Debt until gameday; repaid by selling starters (§4.2)                       |

(§5 adds the code evidence row by row.)

The scorer v2 built is the part that works (§3). The decision layer was not built as
designed: the 2026-09-22 go-live shipped flips and "debt until gameday", and 34 PRs merged
between then and 2026-11-11 rebuilt the trading machine v2 had diagnosed — one rule at a
time, each justified by the last incident. Season 2025/26 lost −55.3m trading; 2026/27
has lost −66.6m by the same mechanism: premium in, sale at market value out.

______________________________________________________________________

## 3. What works — keep

- **The scorer ranks the league.** Calibration reports, MD4–MD9 (n ≈ 462–473 a matchday):

  | MD  | MAE  | Spearman | Baseline | Top-11 regret | Baseline regret |
  | --- | ---- | -------- | -------- | ------------- | --------------- |
  | 4   | 36.2 | 0.63     | 0.42     | 1,382         | 1,717           |
  | 5   | 35.1 | 0.64     | 0.34     | 1,633         | 2,026           |
  | 6   | 32.3 | 0.65     | 0.27     | 1,208         | 1,379           |
  | 7   | 33.4 | 0.63     | 0.23     | 1,336         | 2,086           |
  | 8   | 30.5 | 0.66     | 0.29     | 1,537         | 1,988           |
  | 9   | 31.4 | 0.68     | 0.33     | 1,019         | 1,933           |

  The calibration gate's ranking and regret checks have passed six matchdays running; it
  is held only by integrity-clean days (2.88 of 7).

- **The lineup is set well** and no penalty has been paid this season.

- **The data foundation** (store, ingestion, calibration, league state, dashboard) is what
  made this evaluation possible in one afternoon.

______________________________________________________________________

## 4. What fails — measured 2026-11-11

### 4.1 The scorer is wrong on exactly the players we buy

Among players predicted at 60+ points (MD4–MD9, live predictions):

|                 | n   | Bias (pred − actual) | SD   |
| --------------- | --- | -------------------- | ---- |
| Not owned by us | 876 | +0.8                 | 68.7 |
| Owned by us     | 39  | **+26.7**            | 48.9 |

Split the same population by predicted points per €1m of market value (the night before
kickoff):

| Quartile of EP per €1m      | n   | Predicted | Actual | Bias      |
| --------------------------- | --- | --------- | ------ | --------- |
| Q1 — expensive for their EP | 229 | 98.5      | 116.6  | **−18.1** |
| Q2                          | 229 | 87.4      | 84.3   | +3.1      |
| Q3                          | 229 | 88.9      | 85.3   | +3.6      |
| Q4 — "bargains"             | 228 | 87.3      | 68.4   | **+19.0** |

The market price carries information the scorer lacks, and every rule that rewards EP per
euro selects the scorer's overestimates — the optimizer's curse. A linear blend fitted on
the decision population (EP ≥ 40, n = 1,267: actual = −382.9 + 0.479·EP + 26.3·ln(MV))
improves leave-one-matchday-out ranking among EP ≥ 60 on 5 of 6 matchdays (MD4 0.24→0.36,
MD5 0.32→0.42, MD6 0.14→0.19, MD7 0.13→0.05, MD8 0.24→0.27, MD9 0.38→0.47) but leaves the
owned bias at +14: a regression cannot undo a selection made by the old ranking. That needs
a replay in which the blend chooses.

### 4.2 Debt: allowed by one rule, repaid by another, paid for by the trading book

- **The gate and the planner disagree.** `services/books.debt_cover_reason` allows a debt
  up to the instant-sell value of *all* non-core holdings. `services/debt_recovery ._best_covering_subset` sells at most three players per pass and spares a position at its
  minimum. On 2026-11-11 the best three non-core sales (keeper excluded) raised 28.1m
  against a 32.1m debt, so the locked window had to sell a core player.
- **The planner sells by cost basis.** Every squad player was rank-held, so the hold key
  tied; the tier key (profit before loss) then chose the only two starters in profit —
  Regeer (+24%) and Quansah (+26%), 103 and 102 points a match — and, after Marino's sale
  cut the debt to 25.4m, still chose both: 34.85m raised for 25.4m owed. Selling winners
  and keeping losers is the disposition effect, encoded.
- **Debt starves the profit trades.** `services/flip_sizing.py:35` computes the flip float
  as `max(0, cash) + held flips`. With no flips held, a negative wallet is a zero float and
  every flip is refused. Rohr (18.6m, 2026-11-03) took the wallet from +14.2m to −4.4m the
  day #152 raised the first flip's stake; it has been negative most days since. One flip
  has closed since 2026-10-07 (+0.9m).
- **The cycle repeats.** 10-25 → 10-28: wallet to −29.3m, then team value 154.9m → 114.9m in
  a day (Raum −27%, Kuruçay). 11-03 → 11-11: wallet to −32.1m with every player a starter.

### 4.3 We pay the premium and sell at market value

Auctions since go-live (2026-09-22 → 2026-11-11):

| Band  | Bids | Won | Our premium when won | Winners' premium when we lost |
| ----- | ---- | --- | -------------------- | ----------------------------- |
| \<5m  | 28   | 7   | +9.4%                | +30.0%                        |
| 5–15m | 52   | 22  | +7.3%                | +11.8%                        |
| 15m+  | 15   | 6   | **+16.1%**           | **+8.6%**                     |

The last four points buys, on a flat market (median nightly move +0.05% to +0.31%):
Rohr +17.7% → −11.4%, Petkov +27.0% → −19.9%, Daghim +17.5% → −11.6%, Diks +13.6% → −10.7%.
The loss is the premium. Sales since 2026-10-07: 27, net −34.8m; debt recovery is the
largest bucket.

### 4.4 A player's role is fixed at the bid and never revisited

Marino was bought 2026-10-23 at 5.52m as a `points` buy. He peaked at 8.55m on 11-05 (+55%),
then fell four nights running; Marco sold him by hand on 11-11 near 6.68m. Two rules held
him: the turn exit applies only to `intent='flip'`, and the profit loop skips every
best-eleven player (`auto_trader.py` ~2810) — with eleven players, that is everyone. Marco's
rule since: a falling non-core player is sold at the turn, starter or not; the market
refills the slot.

______________________________________________________________________

## 5. Decision inventory

From a read-only audit of the code on 2026-11-11 (main at `6e5d767`). Paths under
`rehoboam/`; `at` = `auto_trader.py`, `bs` = `bidding_strategy.py`, `dec` = `scoring/decision.py`.

### 5.1 Size

About **60 distinct decision rules** in ≈13,500 lines: `auto_trader` 3,867, `scoring/decision`
1,172, `trader` 1,619, `bidding_strategy` 960, `profit_trader` 525, `league_compliance` 470,
`squad_optimizer` 395, `bid_evaluator` 202, and ≈2,580 in decision-related `services/`.
`Settings` has 120 fields, about 95 of them decision knobs; six are read by no live code
(`max_player_cost`, `reserve_budget`, `min_value_score_to_buy`, `min_buy_value_increase_pct`,
`min_upgrade_value_score_diff`, `watch_player_ids`), and several thresholds that matter are
hard-coded instead (profit targets 5–15% at `at:2807`, stop-loss −5% at `at:2866`, flip
`min_profit_pct=8` / `max_hold_days=7` at `trader.py:1566`).

### 5.2 One session, in order (`at:3038`)

1. Resolve auctions; run deferred sell plans.
1. Build the context once: kickoff, phase, every score, buy recommendations, pairs, bids
   (`at:684` — the only assignment of `ep_result`).
1. Top-5 forced sale; learning writes.
1. Locked window only: debt recovery (`at:3318`).
1. Emergency fill — reads the recommendations built in step 2, before step 4 sold anyone
   (`at:2288`, verified).
1. Exit here in `lineup_only` or the locked phase.
1. Sell phase: flip exits, profit targets, stop-loss, dead weight, rank sells (`at:2614`).
1. Forced bench sale (unknown schedule only); open-bid checks.
1. Trade phase: points buys and pairs by EP gain, *then* flips (`at:1481`).
1. Lineup; session facts and integrity check.

### 5.3 One concept, many copies

| Concept             | Copies                                                                                     | Where they disagree                                                                                      |
| ------------------- | ------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------- |
| Debt allowance      | 6 (`services/budget.py` ×3, `trader.py:1536`, `profit_trader.py:163`, `flip_sizing`)       | The flip search ignores the 33% rule; the gate and the recommender differ when a matchday is in progress |
| Debt cover          | 3 (I3, `books.debt_cover_reason`, `debt_recovery`)                                         | The gate approves debts the recovery cannot repay (§4.2)                                                 |
| Days to kickoff     | 8 thresholds (1, 1, 1, 2, 3, 3, 4, 10)                                                     | `timedelta.days` floors, so "≤ 1" means up to two days                                                   |
| Bid ceiling         | 6 (tier %, price bands, break-even, win-curve quantiles, flip overpay, fallback constants) | The 8% band clips the expiry floor, so a riser above 8% gets a bid Kickbase declines                     |
| Protected from sale | rank hold, `rank_floor_verdict`, core book, recovery `_held`                               | `_held` merges core and rank hold, and rank-held is most of the squad                                    |
| Core set            | 3 (bidder, gate, recovery)                                                                 | Built from different holdings (with or without open bids)                                                |
| Emergency           | 3 (`trader:117`, `at:417`, `urgent`)                                                       | Derived at different points in the session                                                               |

### 5.4 Rules that defeat each other

1. **Points buys starve flips.** Points buys run first and take the wallet negative; flip
   sizing then sees a zero float (`flip_sizing.py:35`). They also share the trade cap and the
   free slots.
1. **The I3 refusal skips the main buy path.** `_execute_buy` builds its gate without
   `session_refusal` (`at:1277`, verified); pairs and flips pass it (`at:1835, 1947, 1987`).
   The path that ran the debt up is the one path the "deficit not covered" invariant does not
   guard.
1. **The 48-hour hold guard runs before the turn exit** (`at:2733`): a flip that turns on its
   first night cannot be sold on it, and the stop-loss is skipped with it.
1. **Recovery then fill.** The recovery may sell below a position minimum "because the fill
   buys the slot back"; the fill reads candidates built before the recovery with ordinary
   filters, and can find none.
1. **Buy side vs sell side.** Pairs do not read the rank hold, so a pair can sell a player
   that sell plans protect; the profit, dead-weight and stop-loss sells ignore it too.
1. **A core buy is bid to win, but the EP filters drop it first** (`dec:444, 571`).
1. **The daily spend cap resets with every session** (`at:524`), so it is a per-session cap.

### 5.5 Against the July design

| v2 §3 / §4.2                         | Status   | Evidence                                                                                                          |
| ------------------------------------ | -------- | ----------------------------------------------------------------------------------------------------------------- |
| Min hold ~45 days                    | Reversed | `min_hold_hours_before_sell` 48 (`config:842`)                                                                    |
| Profit-target selling deleted        | Reversed | `enable_profit_sells` True (`config:132`); a whole flip book added (`profit_trader.py`)                           |
| Decline-detector sells               | Partly   | status 1 and rank only; lineup code ≥ 4, minutes collapse and role loss are read on the buy side only (`dec:468`) |
| ~8% overbid cap                      | Partly   | marginal 8%, solid 15%, strong 25%, must-have 35%; core buys to the winners' p90                                  |
| ~3 trades per session, weekly cap    | Reversed | 10 (15 aggressive); no weekly cap                                                                                 |
| Buy only if the gain clears the toll | Partly   | a fixed 25-point gate; the premium is priced, never weighed against holding                                       |
| Deletions (~4,000 lines)             | Not done | `bid_evaluator`, `league_compliance`, `profit_trader` all live                                                    |

## 6. Our record, buy by buy

All 60 of our buys this season (`manager_transfers`, from 2026-08-21), each matched to its
next sale, the auction row and the proposal that names its tier. Market value at the buy is
the `mv_series` value in force (Berlin cutoff 22:30); every one of the 49 instant sales
landed exactly on market value, so each buy's P&L is its premium plus the value change while
held — nothing else. *Bot-auto* = bid by the bot from 2026-09-22 20:00 on (n = 30);
*hand* = Marco's (n = 26, inferred from manual-offer and bid evidence); *early bot* = n = 4.

### 6.1 The loss is the premium

| Set           | P&L (€m)   | Premium paid | Value change while held |
| ------------- | ---------- | ------------ | ----------------------- |
| Bot-auto (30) | −23.07     | −32.80       | +9.73                   |
| Early bot (4) | −8.84      | −7.94        | −0.90                   |
| Hand (26)     | −34.54     | −31.38       | −1.32                   |
| **All (60)**  | **−66.45** | **−72.12**   | **+7.51**               |

The players we bought *gained* value while we held them. We lost €66m by paying €72m over
market value to get them. The −66.45m matches Kickbase's own −66.57m transfer P&L closely.

| Premium paid (all 60 buys) | n   | P&L (€m)   |
| -------------------------- | --- | ---------- |
| 0–5%                       | 11  | **+7.37**  |
| 10–20%                     | 16  | −29.07     |
| 20% and more               | 15  | **−33.65** |

### 6.2 The bot's own buys (n = 30, realised + unrealised, €m)

| Group                  | n   | P&L    | Sold at a gain |
| ---------------------- | --- | ------ | -------------- |
| Must-have              | 7   | −14.76 | 1 of 3         |
| Strong upgrade         | 7   | −1.65  | 2 of 5         |
| Solid upgrade          | 6   | −1.58  | 0 of 2         |
| Flip                   | 6   | −3.83  | 1 of 6         |
| Emergency fill         | 3   | −0.73  | 0 of 3         |
| 15m+                   | 5   | −14.49 | 0 of 1         |
| Premium above 10%      | 13  | −20.09 |                |
| Premium at or below 5% | 10  | −0.69  |                |
| Sold within 0–3 days   | 11  | −7.16  | 1 of 11        |
| Exit by the rank rule  | 5   | −5.59  | 0 of 5         |

- **Must-haves are the worst tier**: −14.76m on 7 buys, 43.5k € lost per lineup point,
  against −1.58m on 6 solid upgrades that scored 1,150 points.
- **Churn**: 15 of the bot's 20 sales came within 7 days of the buy. 11 buys (−8.72m) were
  sold before any round's first kickoff, so they never had a chance to score. 14 of the 27
  bot buys made before MD9 never scored a point in our lineup.
- **Round trips**: Sander (sold 8.26m, re-bought 9.18m) and Diks (sold 8.89m, re-bought
  8.70m at +13.6%) were bought, sold and bought again; seven more were sold and later bid on
  again (Potulski: sold 7.15m, re-bid 11.66m).
- **Direction is confounded with the premium.** The 18 buys that rose the night before
  gained a median +3.5% while held (non-risers −2.1%, n = 12) but carried a median 11.5%
  premium, so they lost −19.34m overall.

### 6.3 The bot against Marco, during autonomy (2026-09-22 → 2026-11-11)

|                | Buys | Spent  | P&L       | Lineup points (MD4–MD9) |
| -------------- | ---- | ------ | --------- | ----------------------- |
| Bot            | 30   | 299.2m | −23.1m    | 1,832                   |
| Marco, by hand | 14   | 77.7m  | **+4.2m** | 1,230                   |

On a quarter of the spend, Marco's buys made money and scored two thirds of the bot's
points. His three biggest losses of the season — Castello Jr. (−15.73m, a bot proposal he
raised to +40%), Raum (−11.10m) and Demirović (−8.29m) — were pre-autonomy buys, and the
latter two were cleared by the bot's debt recovery.

### 6.4 Debt

The wallet was negative on 26 of the 51 days of autonomy and **never at a round's first
kickoff** (thinnest margins: +0.56m before MD3, +1.35m before MD9) — the kickoff rule
works. Seven debt episodes; the 11 sales that repaid them realised −26.8m.

| Episode (Berlin) | Low     | Run up by               | Repaid by (P&L)                    |
| ---------------- | ------- | ----------------------- | ---------------------------------- |
| 09-27 → 10-07    | −29.17m | Conté and 7 more buys   | Poreba +0.14, Pavlović −3.24       |
| 10-12 → 10-15    | −21.70m | Diks, Kuruçay           | Demirović −8.29, Burger −4.80      |
| 10-17 → 10-18    | −9.49m  | Seiwald, Hendriks, Hack | Hack −0.99, Tanaka +1.51 (by hand) |
| 10-19            | −0.21m  | Regeer                  | Beste −0.63, Seiwald −0.81         |
| 10-25 → 10-29    | −29.32m | Tietz, Coufal           | Kuruçay −0.09, Raum −11.10         |
| 11-04 → 11-05    | −4.30m  | Rohr                    | Rômulo +1.48                       |
| 11-07 → open     | −32.06m | Petkov, Daghim, Diks    | —                                  |

Gaps: lineups for MD1–MD3 are missing; exit rules are untagged before 2026-10-07; points
are gross, not marginal; tiers for flips and fills are inferred from the session phase.

## 7. What wins in this league

From every manager's transfers (`manager_transfers`, `transfer_premiums`, `transfer_outcomes`),
squad snapshots and matchday points; 2026/27 to 2026-11-11 and 2025/26 in full. Correlation,
not cause; n = 13 managers.

### 7.1 Side by side, 2026/27

|                                            | KickAss           | LOREY            | Bebè              | **Us**                | Median |
| ------------------------------------------ | ----------------- | ---------------- | ----------------- | --------------------- | ------ |
| Rank / points per matchday                 | 1 / 1,111         | 2 / 1,061        | 3 / 988           | **12 / 649**          | 852    |
| Team value at kickoff (avg)                | 213m              | 186m             | 201m              | **130m**              | 161m   |
| Squad size now                             | 14                | 13               | 14                | **10**                |        |
| Trades per week                            | 12.4              | 8.3              | 16.0              | 10.6                  | 7.3    |
| Median premium paid                        | 6.6%              | 6.1%             | 6.8%              | **10.2%**             |        |
| Buys after a rising night (base 46–52%)    | 71%               | 80%              | 79%               | **59%**               |        |
| Resold within 14 days: n / won / P&L       | 40 / 62% / +17.5m | 27 / 56% / +4.2m | 63 / 71% / +39.7m | **38 / 24% / −10.7m** |        |
| Held over 14 days: n / won / P&L           | 18 / 78% / +40.1m | 8 / 62% / +2.6m  | 16 / 69% / +27.9m | **11 / 9% / −50.8m**  |        |
| 7-day move before a sale                   | +8.4%             | +1.1%            | +17.9%            | **+0.5%**             |        |
| Sales after a falling week                 | 22%               | 43%              | 23%               | **45%**               |        |
| Lowest cash between rounds (reconstructed) | −55.7m            | −40.0m           | −36.1m            | −38.2m                |        |

2025/26 says the same: the winner (Eduard) made 230 buys, held a median 6.3 days, paid a
median 6.1%, bought 81% after a rising night, and made +116.2m; we made −45.4m.

### 7.2 Where their profit comes from

| Resales         | n   | Net        | Premium | Drift while held | Exit  |
| --------------- | --- | ---------- | ------- | ---------------- | ----- |
| Bebè 2026/27    | 75  | +64.9m     | −34.3m  | +96.7m           | +2.4m |
| KickAss 2026/27 | 55  | +50.5m     | −35.9m  | +85.4m           | +0.9m |
| LOREY 2026/27   | 33  | +0.7m      | −21.6m  | +19.3m           | +3.1m |
| Eduard 2025/26  | 179 | +104.1m    | −149.9m | +248.3m          | +5.7m |
| **Us 2026/27**  | 45  | **−59.0m** | −53.9m  | **−5.1m**        | 0.0   |

**Nobody makes money on the price.** Everyone overpays; the leaders' whole profit is the
value their players gain while held. We pay more premium per resale (€1.20m against their
€0.46–0.65m) *and* our players fall while we hold them.

### 7.3 What wins

1. **Points follow team value, and team value follows trading profit.** Points correlate with
   team value at ρ = 0.90 and with transfer P&L at ρ = 0.80 (0.77 in 2025/26). In this league,
   money is how points are bought; v2's "own the level, hold" (§2) does not describe any
   winner here.
1. **Profit is drift.** Buy players whose value is about to rise, sell when it stops. The
   market value is momentum: after a 150+ point match it rose a median +4.8% in 7 days and
   +7.8% in 14 (n = 258–312; 68% still rising at day 14); after a 0–25 point match it fell
   −12.9% and −21.3%.
1. **High volume, short holds, high hit rate.** Median holds of 6–8 days; 62–71% of quick
   resales won (ours 24%). Volume correlates with points (ρ = 0.59).
1. **They sell after a run, when it stops** — not straight after a big match (3–8% of their
   sales), and rarely after a falling week (22–23%, ours 45%).
1. **Full squads.** 13–14 players (ρ = 0.67 with points). The bench is both lineup cover and
   inventory.
1. **Debt is normal.** Everyone goes −36m to −56m between rounds and lands at +0.1m to +1.2m
   at kickoff. The one negative kickoff in the league (Emilian, MD4, −7.7m) scored 0 points.
   What separates us is not the depth of the debt but what repays it: they sell risers, we
   sell what is left.
1. **The premium is a cost to minimise, not a lever.** The leaders pay a median 6–7%; our
   buys above 10% lost −62.7m across the season (§6.1).

### 7.4 Kickbase facts from the web (official help pages unless noted)

- Market value is set by supply and demand **across all leagues** plus recent form (the MLS
  help page names the last three matchdays), updated once a day around 22:00. One league's
  trades barely move it: our trading decisions can be backtested against the real value
  paths.
- An instant sale pays exactly market value; equal bids go to the earliest; bids are blind.
- Head-to-head: win 3, draw 1; ties broken on total points; the schedule is fixed and visible
  from the season start.
- The lineup locks at the round's first kickoff; no substitutions inside a matchday.
- The reset after the winter break clears squads, budgets, points and the table; managers
  start either with a random ~100m squad plus 30/50/80m or with no squad and 150/200/250m,
  and players come back to the market at random, 5–10 a day. In 2025/26 the league's squads
  were liquidated on 2025-12-20/21.
- The daily bonus is already collected: the wallet reconstruction measures +100k a day on
  ours, plus €1,000 per matchday point.

Nothing reliable was found online on how much to bid, weekday effects or break effects: the
league's own transfers (above) are better evidence than anything published.

______________________________________________________________________

## 8. Why designs erode here

- **No verdict instrument for trading.** The v2 season replay judges the scorer. For the
  trading rules added since go-live, CLAUDE.md records the replay as "inert by
  construction" again and again: each rule was tested alone (unit tests, one dry run on the
  day's squad) and none against money and points as a system.
- **Rules were added, never removed.** Each incident produced a guard; guards were layered
  on guards (debt allowance → core book → recovery ordering → rank hold); copies of one
  concept drifted apart (§4.2).
- **The design lived in a document; the code lived in CLAUDE.md.** The project's real
  specification became a dated changelog of exceptions.
- **The design contradicted the goal.** v2 said "do not trade"; the owner wants a bot that
  trades for players and profit, and the league's winners do exactly that (§7). A design
  that fights its owner's goal is eroded by every well-meant fix. v2 was benchmarked against
  our own history, never against the managers who win.

What v3 needs that v2 lacked:

1. **Invariants as tests**, not prose — e.g. "the squad held at kickoff costs no more than
   net worth", "no premium above the cap", "one predicate per concept".
1. **A money-and-points verdict before going live**: paper trading on live data, scored
   against the league, with an explicit bar — the trading equivalent of the calibration
   gate.
1. **Change control**: a rule changes by amending the spec, with the evidence, not by
   appending a bullet.

______________________________________________________________________

## 9. Decisions the v3 spec needs from Marco

The evidence points one way; these are the choices that shape the architecture, with a
recommendation each.

1. **Strategy.** (a) The leaders' game — trade for drift every week with a full squad,
   field the best eleven of what you hold — or (b) v2's hold-the-level. *Recommend (a)*:
   §7.1–7.3; it is also Marco's Marino rule (§4.4) and his hand trading during autonomy (§6.3).
1. **No labels.** Every held and listed player is valued every session on two numbers: expected
   points over the next one or two matchdays (the scorer, with market price folded in, §4.1)
   and expected value change over the next 7–14 days (a momentum model on `mv_series`, built
   on the nightly forecast that already calls the direction 95% of the time and that no trading
   decision reads today). "Flip", "points buy" and "core" stop being stored states.
1. **One planner instead of ~60 rules.** Each session computes a target squad of 13–14 that
   maximises drift plus the value of its best eleven's points, subject to: legal eleven,
   wallet ≥ 0 at the next kickoff *through named sales*, premium as a cost, a per-session trade
   cap. The trades are the difference between the target and the squad — a reconcile loop. One
   predicate per concept; the safety gate stays as the last check.
1. **Premium policy.** A hard ceiling at the league's median premium for the band unless the
   valuation shows the expected drift and points cover it — never a "bid to win" floor.
1. **What survives.** Keep the store, ingestion, scorer, lineup, kickoff rule and safety gate.
   Rebuild the decision layer (`auto_trader` trade and sell phases, `trader` recommendations,
   `bidding_strategy`, `profit_trader`, `bid_evaluator`, `squad_optimizer`, the flip and debt
   services) and delete what it replaces.
1. **The verdict before going live.** (i) A trading backtester on the real value paths
   (`mv_series`, `market_listings` since 2026-09-16, the league's win curve for whether a bid
   would have won) — possible because one league's trades do not move the market; (ii) paper
   trading on live data, scored against the league's median and top three on money and
   points; (iii) an explicit bar to go live, written down before the first run.
1. **When.** Live at the winter reset, with a reset mode (start from cash, a thin random
   market, no empty slot at the first kickoff). Until then the bot stays `lineup_only` and the
   new planner paper-trades.
