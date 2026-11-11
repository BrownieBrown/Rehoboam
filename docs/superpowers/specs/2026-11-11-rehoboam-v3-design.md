# Rehoboam v3 — Design

**Date:** 2026-11-11
**Status:** Accepted. Marco, 2026-11-11: "go with your recommendations if you think they will win".
**Input:** `2026-11-11-rehoboam-v3-evaluation.md` (cited as *Eval §n*)
**Decisions:** `docs/adr/0001` – `0006`
**Supersedes:** the decision layer of `2026-07-29-rehoboam-v2-design.md` (§3, §4.2). Its scorer, data
foundation and calibration gate stay.

______________________________________________________________________

## 0. Summary

The bot becomes one loop that runs every session:

```
observe ──► value ──► plan ──► check ──► act ──► record
   ▲                                               │
   └──────────── next session, from scratch ───────┘
```

- **observe**: the squad, the wallet, open bids, Kickbase listings, the schedule.
- **value**: two numbers per player. These are expected points at the next kickoff, and expected
  market-value change ("drift") over the next 7 days.
- **plan**: a target squad for the next kickoff that maximises points plus the money its drift
  earns, net of premiums. The plan has to field a legal eleven, end the wallet ≥ 0 at kickoff
  *through sales it names*, and respect a premium ceiling and a trade cap.
- **act**: the difference between the target and the squad, as bids and sales.
- **record**: the plan, its inputs, and the actions.

Nothing is remembered between sessions except facts (holdings, cost, open bids). A player is never
a "flip" or a "core" player; every session values him again. The same planner runs behind three
adapters, *backtest*, *paper* and *live*, so the code that is measured is the code that trades.

______________________________________________________________________

## 1. Goal and non-goals

**Goal.** Win matchups. Points come from the eleven, and in this league the eleven is bought with
money (*Eval §7.3*: points ~ team value ρ 0.90, team value ~ transfer P&L ρ 0.80). The bot is
judged on two numbers against the league over the same window:

1. points per matchday, and
1. change in net worth (team value + cash).

**Non-goals.** Manager listings (Kickbase listings only). New outside data sources. Changes to the
scorer's model; the price blend (*Eval §4.1*) is tested as an ablation, not assumed. A dashboard
for v3 beyond the run tables.

______________________________________________________________________

## 2. Strategy — trade for drift with a full squad (ADR-0001)

The leaders' whole profit is value gained while they hold a player (*Eval §7.2*): they overpay by
6–7%, buy after rising nights, sell when the run stops, keep 13–14 players and go −36m to −56m
between rounds. v3 does the same, but by valuation rather than by rule.

**"Hold the level" is the degenerate case, not the alternative.** When no listing's expected
drift clears its premium (a deflating market, the second half of the season, *Eval §7.3* item 2),
the planner's best plan has no trades, and the bot holds. Choosing (a) keeps (b) available
whenever the data says so.

______________________________________________________________________

## 3. The loop is level-triggered (ADR-0003)

v2's stored labels were edge-triggered state. `intent='flip'` was written at the bid and never
re-read against the present, which is how Marino was held through four falling nights
(*Eval §4.4*). v3 recomputes the desired state from the observed state every session, the way a
Kubernetes controller reconciles toward a spec. A missed session or a stale label can't
corrupt it, because there is no label.

Each session plans to the next kickoff and executes only what is due now. The next session
plans again with what actually happened: auctions won or lost, last night's values. This is a
receding horizon (model-predictive control). The plan absorbs uncertainty by being re-made,
not by layered guards.

______________________________________________________________________

## 4. Valuation (ADR-0002)

Every held player and every Kickbase listing gets a `Valuation` each session:

| Field          | Meaning                                                        | Source                                   |
| -------------- | -------------------------------------------------------------- | ---------------------------------------- |
| `ep_next`      | expected points at the next kickoff                            | scorer v2 (`score_player_v2`), unchanged |
| `drift_mean`   | expected market-value change, %, over `DRIFT_HORIZON_DAYS` (7) | drift model, §4.2                        |
| `drift_low`    | its lower quantile (p10), % to the next kickoff                | drift model                              |
| `mv_at_expiry` | the market value when an auction settles                       | `mv_forecast` (existing nightly rule)    |

### 4.1 Points

`ep_next` is the live scorer's output. The blend with market price (*Eval §4.1*:
`actual ≈ −383 + 0.48·EP + 26.3·ln MV`) is a backtest ablation. It is adopted only if it raises
points per matchday in the replay; a regression fit cannot settle it, because the evaluation
showed the owned-player bias survives refitting.

### 4.2 Drift

**Target.** Market value over the next h days, divided by today's, minus 1, for h = 7 and
h = days to kickoff.

**Model.** A conditional-mean lookup table, not a fitted parametric model, with these cells:

- last nightly move (5 buckets);
- three-night move (5 buckets);
- price band (\<5m, 5–15m, 15m+);
- market drift: the universe's median nightly move over the last 3 nights, falling or not.

Thin cells shrink toward their parent cell, `(n·cell + k·parent)/(n + k)` with k = 30. Each cell
stores its mean and its p10. The table is boring on purpose: anyone can read it, and it can be
tested cell by cell.

**Fit and validation.** Fit on every universe player's `mv_series` before a cutoff. Validate out of
sample on 2026/27, with the fit taken from 2025/26 only.

**Hurdle (M1, §10).** Among Kickbase listings in the top decile of predicted `drift_mean`, the
realised 7-day median move must exceed the league's median premium for the band. If it doesn't,
the bot can't execute the strategy on drift, and §10's kill criterion applies.

Candidate features to add only if the backtest asks for them: last match points (after 150+ the
value rose +4.8% in 7 days, *Eval §7.3*), lineup code, status. Each one is added as an ablation
with its own run.

### 4.3 The exchange rate between euros and points

The objective adds points and money, so it needs a rate. **Money is worth the points it buys in
the matchdays that remain before the reset clears everything** (*Eval §7.4*):

```
μ(N) = (N − 1) / P        points per euro
P    = the market's price of one point per match of player quality
       (store/transfer_study.eur_per_point: 255,008 € on 2026-09-23)
N    = matchdays left before the reset (or the season's end), counting the next one
```

μ falls as the reset nears and is zero for the last matchday before it, when money is worthless
and only that eleven counts. The planner then spends for points, still subject to wallet ≥ 0 at
kickoff. The drift horizon is capped at the reset. μ is the one dial that sets the bot's
character, so the backtest sweeps it (×0.5, ×1, ×2) and the bar (§8.4) must hold across the
sweep.

______________________________________________________________________

## 5. The planner

### 5.1 Decision

The planner chooses the **kickoff squad** `T ⊆ held ∪ listings`. From it:

- **buys** `B = T ∩ listings`, each with a premium (§5.5);
- **exits** `X = held \ T`, each with a timing: *sell now*, or *sell later*, which is a named
  sale before kickoff, held meanwhile for its drift (§5.6);
- **kept** `K = held ∩ T`.

### 5.2 Objective (in points)

```
U(T) = EP of the best legal eleven in T at the next kickoff
     + μ(N) · [ Σ_{p ∈ K ∪ B} MV_p · drift_mean_p        drift while held
              + Σ_{x ∈ X_later} MV_x · drift_to_sale_x     drift of a named sale until sold
              − Σ_{b ∈ B} premium_eur_b ]                  the premium is a cost
```

Selling pays exactly market value (*Eval §6*), so a sale has no toll. Every euro of churn is the
premium on the buy, and it sits in the objective. A swap happens only when the drift and points it
adds exceed the premium it costs, which is the "trade must clear the toll" rule that v2 wrote in
prose and never enforced as one rule (*Eval §5.5*).

### 5.3 Constraints (hard)

| #   | Constraint                                                                                                                                             | One predicate                                      |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------- |
| C1  | T fields a legal eleven at kickoff, counting open bids as **lost**                                                                                     | `desk/eleven.py` over `formation.LEGAL_FORMATIONS` |
| C2  | \|T\| ≤ 15; the target size is 13–14 when the cash allows, never a floor                                                                               | `planner`                                          |
| C3  | **Solvency**: `wallet − Σ open bids (as won) − Σ new bids + Σ sell-now · MV + Σ sell-later · MV · (1 + drift_low) ≥ 0`, with `X_later ∩ eleven(T) = ∅` | `desk/solvency.py`, the only debt rule             |
| C4  | Kickbase's own debt cap (33% of worth), today                                                                                                          | `desk/solvency.py`                                 |
| C5  | Every bid sits between `mv_at_expiry` (Kickbase declines anything below it, *break review*) and the band ceiling (§5.5)                                | `desk/bids.py`                                     |
| C6  | At most `MAX_BUYS_PER_SESSION` (3) bids and `MAX_SALES_PER_SESSION` (3) sales                                                                          | `planner`                                          |

Open bids count as **won** for solvency (C3) and as **lost** for fielding (C1): each check
assumes the outcome that hurts it. This is designing for the failure, not the forecast.

### 5.4 Search

The search is a greedy local search from `T = held`, with *add a listing*, *drop a holding* and
*swap one for one* moves, taking the best feasible improvement until none is left or the caps
bind. It is pure Python and adds no dependency to the Function app. The backtester also runs an
exhaustive search over the same moves on a sample of sessions and reports the optimality gap.
If the gap exceeds 2% of U, the search gets two-for-one moves; a MILP solver comes only after
that.

### 5.5 Bids — the premium is a cost with a ceiling (ADR-0004)

The **ceiling** is the league's median winning premium for the band (`services/win_curve`,
ours excluded, last 120 days); it was +8% at 15m+ on 2026-10-25. The **floor** is `mv_at_expiry`,
the value the settled auction is judged against. If the floor exceeds the ceiling, there is no
bid. Between the two, the bid premium p maximises `F(p) · (V − p · MV)`, where F is the band's
win curve and V is the player's marginal value to the plan, `U(T) − U(T \ {player})`, in euros.
This is the `value_bid.optimal_premium` idea with a hard top.

No "bid to win" exists for any class of player: no must-have exemption, no core floor, no
urgency bump.

### 5.6 Timing of exits and the locked window

- An exit is sold **now** when its `drift_to_next_session ≤ 0`, or when C3 or C4 need the cash
  today. Otherwise it is a named sale, sold later.
- The **last session before kickoff** sells every named sale still held. If values fell and the
  sales no longer cover the debt, the planner re-plans with C3 at the current values and
  `drift_low = 0`, choosing among *all* holdings by the same objective. There is no second
  sacrifice order. The safety gate stays the last check.
- The **last-day fill** stays: if C1 cannot be met from the plan, the planner runs once more with
  the floors relaxed, restricted to the positions the formation needs, as today
  (`services/emergency_window`).

### 5.7 What the planner deletes

Each row is one function in `desk/`, replacing every copy in *Eval §5.3*:

| Concept                  | v3 home                                               | Replaces                                                                                                                             |
| ------------------------ | ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| Debt allowance and cover | `solvency.check`                                      | `services/budget` ×3, `books.debt_cover_reason`, `debt_recovery`, I3's copy, `trader.py:1536`, `profit_trader.py:163`, `flip_sizing` |
| Days to kickoff          | `kickoff.next_round` (exists), consumed as a datetime | 8 day-count thresholds                                                                                                               |
| Bid ceiling              | `bids.ceiling`                                        | tier %, price bands, break-even, quantiles, flip overpay                                                                             |
| Who may be sold          | the objective                                         | rank hold, core book, `_held`, profit-before-loss tiers                                                                              |
| Exit rules               | the objective plus drift                              | turn exit, stop-loss, profit targets, rank sell, dead weight                                                                         |

______________________________________________________________________

## 6. Invariants, as tests

Every invariant is a test that runs on every plan the backtester produces (thousands of real
states), not only on hand-built fixtures:

- **I-S1** Every plan satisfies C3 and C4.
- **I-S2** No named sale is in the planned kickoff eleven.
- **I-B1** No bid above the band ceiling or below `mv_at_expiry`.
- **I-E1** The kickoff squad fields a legal eleven, or the plan says `fill_needed`.
- **I-T1** No more trades in a session than the caps allow.
- **I-K1** In replay, no kickoff with a negative wallet and no short eleven.
- **I-A1** Architecture: no module under `desk/` or `sim/` imports `bidding_strategy`,
  `profit_trader`, `bid_evaluator`, `squad_optimizer`, `trader` or `auto_trader`. One predicate per
  concept is enforced by the import graph.

______________________________________________________________________

## 7. Architecture

```
rehoboam/desk/   pure decision layer: frozen dataclasses, no I/O
  model.py        Holding, Listing, Valuation, Book, Plan, Bid, Sale
  drift.py        drift table: fit(series) → DriftTable; predict(features)
  exchange.py     μ(N)
  eleven.py       best legal eleven's EP (wraps formation.select_best_eleven)
  solvency.py     C3 + C4: the only debt rule
  bids.py         ceiling, floor, premium choice
  planner.py      search → Plan
rehoboam/sim/    the verdict: same planner, historical or paper adapter
  history.py      loads listings, mv paths, transfers, predictions, points from the store
  auction.py      counterfactual auction resolution (§8.2)
  engine.py       session-by-session replay; persistent state for paper trading
  validate.py     reproduces every manager's real P&L from their real trades (§8.3)
  report.py       the verdict against the bar (§8.4)
live adapter     a new TRADING_MODE=v3 session in place of auto_trader's sell and trade phases
```

**Kept:** the store, ingestion, scorer v2, `kickoff.py`, `formation.py`, the lineup setter,
`services/safety_gate`, `services/execution`, `services/win_curve`, `services/mv_forecast`,
the Top-5 forced sale, the learning tracker's auction resolution, integrity, notify.

**Deleted once v3 runs live** (M4): `bidding_strategy`, `profit_trader`, `bid_evaluator`,
`squad_optimizer`, `league_compliance`, `trader`'s recommendation path, `auto_trader`'s sell and
trade phases and debt recovery, and `services/` `books`, `budget`, `debt_recovery`, `flip_*`,
`rank_sell`, `entry_direction`, `value_bid`, `pacing`, `bid_ceiling`, `profit_curve`. Also the
`Settings` fields only they read.

**Config.** Six knobs, each a `Settings` field: `DRIFT_HORIZON_DAYS`, `MAX_BUYS_PER_SESSION`,
`MAX_SALES_PER_SESSION`, `PREMIUM_CEILING_QUANTILE` (0.5), `MU_SCALE` (1.0) and
`TARGET_SQUAD_SIZE` (14). Anything else that looks like a knob is a fitted value with a stored
fit, or a constant with a cited measurement.

______________________________________________________________________

## 8. The verdict before going live (ADR-0005)

### 8.1 Why a backtest is honest here

Market value is set across all Kickbase leagues (*Eval §7.4*), so our trades don't move the
price paths we replay against. Sales at market value are exact. The only modelled step is
whether a bid would have won (§8.2).

### 8.2 Auction model

For each Kickbase listing in `market_listings` (since 2026-09-16; 928 settled by 2026-11-10):

- **a league manager bought it** (341, 37%): our bid wins only if it is strictly above that
  manager's price; equal bids go to the earliest bidder, and we assume we were not.
- **nobody in the league bought it** (587): our bid wins if it is at or above market value at
  expiry.

The approximation: a win we would have taken changes nothing else the replay reads.

### 8.3 The simulator must reproduce history first

Before any counterfactual, `sim/validate.py` (`rehoboam desk-validate-sim`) checks the
simulator's accounting against everything the store can observe:

1. **Squads.** Replaying each manager's transfers from his first stored squad must reproduce
   ≥ 95% of his later squad snapshots exactly.
1. **Values.** The value paths under the 22:00 Berlin rule must price ≥ 95% of the snapshots
   within 5% or €2m of the values Kickbase reported.
1. **Our wallet.** Our transfers must explain every budget change to within €1m, net of
   same-second races.

*Amended 2026-11-11, before the first run.* The first draft asked for each manager's "net worth
at each kickoff". Rivals' cash isn't visible on Kickbase, so their net worth is reconstructed,
never observed. These three checks are what can be observed.

The value paths are `mv_series` with gaps filled from `player_status_daily`, keyed by the
value day of its fetch time. `mv_series` is refreshed weekly, so it held only 1–9 players a
day after 11-04. The two sources agreed on all 24,230 overlapping readings. A simulator that
can't reproduce what happened can't be trusted on what didn't.

### 8.4 The bar, written before the first run

> **Amended 2026-11-11 by ADR-0007, after M2-5.**
>
> - **Before the reset:** v3 must beat our own real record from the same start, on money and
>   points, as an ensemble mean.
> - **From the reset:** the rivals' median, as below.
> - **Freezing:** configurations are frozen as git tags (`v3-frozen-N`) and judged only on
>   rounds after their freeze (`rehoboam desk-holdout`).
>
> The original text follows. W1 scores MD5–MD9: MD4 kicked off on 09-18, before go-live.

**Window W1.** 2026-09-22 20:00 UTC (go-live) → 2026-11-11, starting from our real squad and
wallet at go-live, scoring MD4–MD9 on real points.

| #   | Go to paper trading only if                                                                             | Against                    |
| --- | ------------------------------------------------------------------------------------------------------- | -------------------------- |
| B1  | net-worth change over W1 ≥ the league median manager's                                                  | same window, real managers |
| B2  | points per matchday (real points of the planned eleven) ≥ the league median's                           | MD4–MD9                    |
| B3  | zero I-S1 … I-K1 violations                                                                             | every plan                 |
| B4  | B1 and B2 still hold at μ × 0.5 and × 2, and with the drift table fitted on data before 2026-07-01 only | robustness                 |
| B5  | §8.3 passes                                                                                             | the simulator              |

**Live at the reset only if paper trading** (MD10 to the last matchday before the reset, at
least three matchdays) also has net worth ≥ the league median, points per matchday ≥ the league
median, and zero invariant violations. The top three are reported beside it as the aspiration;
they are not the bar. A bot at the median on both would be the best this bot has ever done,
against 12th of 14 today.

Also reported, never gating: our real record over W1, a do-nothing policy (hold the go-live squad),
the optimality gap (§5.4), and the ablations (price blend, each extra drift feature, the premium
ceiling at p60/p75).

### 8.5 Paper trading

`sim/engine.py` with a persistent state is paper trading: after each ingestion run it advances
the paper book by the sessions since the last run, on the newest listings, values and
predictions, and resolves the paper auctions the next day against the league's real transfers.
Paper state lives in the store (`paper_*` tables), next to the real squad on the dashboard.

______________________________________________________________________

## 9. Change control

- A rule or constant changes by amending this spec or an ADR, with a backtest run attached:
  run id, the bar's numbers before and after.
- CLAUDE.md points to this spec and the ADRs. It is not a changelog of exceptions
  (*Eval §8*).
- Every backtest run is stored (`sim_runs`: code commit, parameters, data cutoff, B1–B5),
  so a claim in a PR can be checked.

______________________________________________________________________

## 10. Rollout

| Milestone | Delivers                                                        | Gate                                                                                                                                                    |
| --------- | --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **M0**    | this spec + ADRs; `sim/history`, `sim/validate`                 | B5: the simulator reproduces every manager's P&L                                                                                                        |
| **M1**    | `desk/drift`, out-of-sample report                              | **Kill criterion**: the drift hurdle (§4.2) fails → stop; drift becomes a tie-breaker in a points-first planner, and Marco decides before more is built |
| **M2**    | `desk/` planner, bids, solvency, invariant tests; backtest runs | B1–B4 on W1                                                                                                                                             |
| **M3**    | paper trading in the ingestion app                              | runs from MD10 or MD11                                                                                                                                  |
| **M4**    | reset mode, the live adapter (`TRADING_MODE=v3`), deletions     | paper bar (§8.4); Marco flips the mode                                                                                                                  |

**Reset mode (M4).** Squads, budgets and the table clear at the winter break; in 2025/26 that was
2025-12-20/21 (*Eval §7.4*). The planner needs no special case for an empty squad or plenty of
cash. Reset mode adds two things:

1. **Supply pressure.** Listings arrive 5–10 a day, so C1 is planned across the days to the
   first kickoff rather than for today's market alone.
1. **The reset's own drift.** In 2025/26 the median market value rose +17.8% from 12-15 to 01-18
   and fell 22% by March. That is a drift table fitted on the reset window, validated on the
   2025/26 reset before it is used.

Until M4 the bot stays `lineup_only`. Nothing in M0–M3 touches prod's trading.

______________________________________________________________________

## 11. Risks and open questions

- **Short windows.** W1 has six matchdays, and paper adds three or four. The bar compares against
  the league over the *same* window, which removes the market's common move but not luck. B4
  is there so a single lucky setting can't pass.
- **The drift table may not survive the reset.** The market's regime changes at the reset;
  reset mode fits its own table, and the paper verdict covers the first half only.
- **Optimizer's curse, again.** The planner selects on noisy estimates. Three things limit the
  damage: the drift table is validated on what it selects (the top decile, not the average), the
  premium ceiling caps the cost of a wrong pick, and the trade cap caps how many wrong picks one
  session can make.
- **The league's reset settings** (random squad plus cash, or cash only) are set per league;
  confirm in the app before M4.
- **Session timing.** Sessions run at 08:00 and 20:00 UTC; values move at ~22:00 Berlin. The
  backtest measures whether a session after the nightly update (the 21:45 UTC pass) buys better.
  It moves only if the measurement says so.

______________________________________________________________________

## 12. Run log

Every run that a decision rests on (§9). Command: `rehoboam desk-drift-report`.

| Run      | Date       | Code                              | Parameters                                                                                                                  | Result                                                                                                                                                                                                               |
| -------- | ---------- | --------------------------------- | --------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| M1-1     | 2026-11-11 | branch `v3-redesign`, uncommitted | fit before 2026-07-01 (150,712 samples); lag 1, horizon 7; edges as §4.2                                                    | 731 Kickbase listings (09-16 → 11-02). Spearman +0.594. Top decile (74): median realised +69.9%, median excess over the band premium **+55.2% → PASSED**                                                             |
| M1-2     | 2026-11-11 | same                              | fit before 2026-03-01 (B4 robustness)                                                                                       | Spearman +0.588; excess +52.4% → PASSED                                                                                                                                                                              |
| M0-1     | 2026-11-11 | same                              | `desk-validate-sim`, snapshots 09-16 → 11-11                                                                                | 13 managers × 263 snapshots: squads 100% exact, values 100% (median error 0.00%), our wallet 144 of 144 intervals explained → **B5 PASSED**                                                                          |
| M1-3     | 2026-11-11 | same                              | as M1-1 with the merged value paths                                                                                         | 834 listings, excess +57.2% → PASSED                                                                                                                                                                                 |
| M0-2     | 2026-11-11 | same                              | points: our real squad at each kickoff, best eleven by EP, real points                                                      | MD5–MD9: 911/549/766/604/768, identical to Kickbase's official matchday points                                                                                                                                       |
| **M2-1** | 2026-11-11 | same                              | `desk-backtest`, W1 = MD5–MD9 (MD4 kicked off 09-18, before go-live); μ×1 as registered                                     | +€54.1m (rivals' median +€29.3m, us real −€26.2m); **657.2 pts/MD** (rivals' median 864.3, us real 719.6). **B1 PASS, B2 FAIL, B3 PASS**. Sweep: ×2 +€74.1m/494, ×0.5 +€46.8m/718, ×0.25 +€16.0m/603, ×0 −€19.6m/644 |
| M2-2     | 2026-11-11 | same + C1 fix                     | the planner had counted new bids as won when fielding, against spec C1; sold a starter, lost the auction, fielded 10 at MD5 | ×1 +€44.1m/577, ×0.5 +€31.7m/757, **×0.25 +€36.6m/795**, ×0 −€10.3m/681, ×2 +€57.7m/558. Best: B1 PASS, B2 FAIL (−69 pts/MD)                                                                                         |

| M2-3 | 2026-11-11 | + μ by derivation | μ = (N−1)/(N·P): points and money over the same matchdays (`desk/exchange.py`) | ×1 +€13.1m/793 (B1 fail, B2 fail), ×0.5 −€8.5m/733, ×2 +€19.9m/810. The derivation undervalues money: drift profits compound into capital that buys better players later |
| M0-3 | 2026-11-11 | same | the eleven's EP at kickoff against its real points | our real squads +46 a matchday over; the planner's +144 (derived μ×2) and +289 (μ×0). The optimizer's curse (§4.1), confirmed |
| M2-4 | 2026-11-11 | + walk-forward price blend (`desk/points.py`) | refit after each finished round on all earlier rounds (EP ≥ 10); usable from kickoff + 4 days; μ×2 registered, ×1 and ×4 for B4 | ×2 +€13.4m/709 (both fail). **×1 +€9.9m/962.8 (B1 fail, B2 PASS; predicted 964, scored 963: the curse gone)**. ×4 +€45.5m/690 (B1 pass, B2 fail) |

**The instrument is the next problem.** No setting passes B1 and B2 together. Neighbouring
settings swing ±150–250 points a matchday; MD7 scores 831, 586 or 443 depending only on μ.
With five matchdays, one replayed path is one draw. The noise between neighbouring settings is
larger than the gaps the bar measures, and the MD10–MD14 holdout has the same problem. Before a
configuration is frozen, the backtest has to judge the *expected* result of a configuration: an
ensemble of paths started on different days and sessions, reported as a mean with its spread.
The bar then applies to the mean. That is the next change. Named-sale debt waits until a
configuration is chosen on the ensemble.

**The ensemble, and the rule that picks the frozen configuration.** These were written before the
first ensemble run.

- **Paths.** Eight per configuration, each starting from our real squad and wallet at 20:00 UTC
  on 09-22, 09-24, … 10-06. All end 11-11 and all play MD5–MD9.
- **Scoring.** Each path is measured against the rivals' median over its own window.
- **The bar.** B1 and B2 apply to the mean excess.
- **The candidates.** The price blend with derived μ at ×0.5, ×1, ×2 and ×4 (`backtest.choose`).
- **The rule.** Among clean candidates (no violations) whose mean points excess is ≥ 0, freeze
  the one with the highest mean money excess. If none reaches the points bar, freeze the one with
  the highest mean points excess. Points come first because they win matchups; money buys them.
- **What the frozen configuration faces.** It goes to the MD10–MD14 holdout whatever its W1
  result. The holdout costs nothing and decides live.

| M2-5 | 2026-11-11 | + ensemble (`backtest.ensemble`) | 8 paths × blend μ ×0.5/1/2/4; vs the rivals' median over each path's window | mean money −€18.3m/−€14.4m/−€8.4m/+€1.8m; **mean points −194/−172/−182/−208 per MD**; 0 violations. The rule picks **blend μ×1**. M2-4's 962.8 was its luckiest path (+98; the others −98 to −268) |
| M2-6 | 2026-11-11 | same | the same 8 paths against **our real record from each start** | blend μ×1: **money +€22.3m** (every path, +14.6 to +36.1m), **points −27 per MD** (−124 to +243), ahead on both in 2 of 8 paths. ×4: +€38.5m, −64 |
| M2-7 | 2026-11-11 | + kickoff timing | the eleven's points diagnosed: v3 held 11.1 players and €15.3m of cash at kickoffs, €10.6m of it tied in bids that settle after the lock, whose points the planner had credited. Fix: a bid counts for the coming eleven only if it settles before the kickoff (`Offer.arrives_before_kickoff`) | blend μ×1, 8 paths vs our real record: **money +€26.9m** (+16.6 to +37.8m), **points +23.8 per MD** (−111 to +106), **ADR-0007 bar PASS**. Vs the rivals' median: −€9.7m, −120.9. Cash tied in open bids at kickoff 10.6m → 3.3m; free cash at kickoff 9.6m |
| M2-8 | 2026-11-11 | + planned debt (`Policy.planned_debt`, `solvency.bench_cover`) | a debt up to the bench's p10 value and a third of the squad value, repaid by the last session before kickoff | 8 paths vs our real record: money +€25.3m, **points −5.6 per MD → FAIL**. Squad 11.9, cash at kickoff €11.1m, 0 kickoff violations. **Not adopted**: the switch stays off. Debt is borrowed against a bench v3 doesn't hold, and it already leaves €11m idle, so capital is not the bottleneck |
| M2-9 | 2026-11-11 | frozen-2, profit split | W1 from 09-22, profit = drift − premium, against the leaders | v3 **+€11.5m** (drift +€35.8m, premium −€24.3m, 68 buys); KickAss +€58.4m, Bebè +€64.0m, LOREY +€37.8m; us −€26.2m. Per position: v3 held a median **3.2 days** for a median **+3.0%**, €0.54m each; Bebè, at the same €4.7m median buy, held 5.2 days for +18.5%, €1.41m each; KickAss and LOREY held 10.9 and 6.8 days for €2.01m and €1.66m. The gap is churn and pick quality, not capital |
| M2-10 | 2026-11-11 | frozen-2, its sales | what each sold player did in the 7 days after the sale (8 paths, 281 sales, 275 of them swaps) | hold model predicted a mean **+3.3%**; realised median **−5.0%** (the typical sale is right) but mean **+8.0%**, and 33% kept rising. The model misses the right tail of runs that continue. Next: a longer-momentum feature in the drift table (§4.2 candidate), validated out of sample on held players first, then one ensemble run |
| M2-11 | 2026-11-11 | `desk/drift` refinements (`move_14n`, `last_points`), off by default | hold table refined above the full cell, fit before 2026-07-01, tested out of sample on W1; gate written before the run: Spearman must improve on the universe (16,049) **and** on frozen-2's sale points (202). Then one ensemble with the same freeze rule as M2-14 | gate passed: hold ρ +0.508 → **+0.604** on the sale points (+0.651 → +0.693 universe); listings +0.591 → +0.632, but the 14-night move lowers the listing top decile's mean (+104% → +88%). The miss was **rebounds**, not runs: sold after a 14-night fall >18%, a mean +42.7% in the next week; no match in 10 days, +29.4%. Ensemble: (a) both tables +€26.3m / −22.5 pts, (b) hold both + buy points +€26.4m / −12.0 → **FAIL, not adopted**: a better forecast bought no money. Correction: M2-10 called momentum a §4.2 candidate; §4.2 lists match points, lineup code and status (the last two have no 2025/26 data) |
| M2-12 | 2026-11-11 | frozen-2, diagnostic | the top decile of W1 Kickbase listings by predicted drift (70; realised median +48% in 7 days) followed through v3's funnel, 8 paths; why each was not bid, read off the plan at every open session | **no bid 71%**, won 12%, lost 15%, declined 2%. Not bid because of **cash 73%**, a full squad 18%, the buy cap 8%; the objective wanted them (median +6.8 pts of U). Lost at our +8.9% to rivals paying +15.6%. Won positions would have made a mean **+35.8%** held 7 days; v3 held them ~2 |
| M2-13 | 2026-11-11 | `Policy.planned_debt` with the trading book as cover (`solvency.bid_cover`, `Book.open_bid_cover`; a bid counts at its p10 if it settles a session before kickoff) | ensemble, frozen-2 freeze rule | W1 profit **−€6.0m** (frozen-2 +€14.3m): drift +€31.9m but premium **€37.9m** (€15.0m), hold 1 day, 71 buys. 0/8 paired wins → **not adopted**. More cash bought churn, not drift |
| M2-14 | 2026-11-11 | `Policy.commit_hold`: a player we won within 7 days, hold drift ≥ 0, is not sold to fund a swap (the repair still may) | ensemble; freeze rule written before the run: pass ADR-0007, beat frozen-2's money vs real on the mean and on ≥ 6 of 8 paired paths, points vs real no more than 10 pts/MD below | money vs real **+€31.5m** (frozen-2 +€26.9m), W1 profit +€18.8m, hold **7.0d**, premium unchanged; but points vs real **−18.4** (frozen-2 +23.8), 4/8 paired wins → **FAIL, not frozen**. With trading-book debt: −€1.8m, hold 3.3d. Diagnosis: the plan values 7 days of drift and re-plans every 12h, so it pays a premium for a horizon it never collects; commitment fixes the money and costs points (≈1.2 SE: per-path points swing ±100). Next: commit against drift-only swaps but let a sale that raises the kickoff eleven through |
| M2-15 | 2026-11-11 | `bids.value_premium` (argmax F(p)·(drift − p), F = the band's past rival premiums, capped at p90) and `Policy.value_bid`; `committed` now yields to a move that raises the kickoff eleven | target written before the run ("beat the leaders": mean W1 profit above each of Bebè, KickAss, LOREY over the same windows, 8 paths, and ADR-0007); freeze rule as M2-14. 5 configs | value +€13.0m (premium €15.0m → **€5.3m**, wins less), commit-with-EP-exception +€10.0m (the exception reopens the churn: hold 3.2d), value+commit +€11.3m, value+commit+debt +€12.7m, value+debt +€13.6m. Leaders over the same windows: Bebè +€56.7m, KickAss +€49.3m, LOREY +€39.7m → **none close, none frozen** |
| M2-16 | 2026-11-11 | diagnostics + an offline gate | (a) each manager's W1 change split into the starting squad's drift and trading profit; (b) the leaders' 109 Kickbase-listing buys scored by v3's forecast; (c) a value-gap feature (season points × the universe's €/point ÷ value), gate as M2-11 | (a) Bebè +€21.8m squad / **+€42.2m trades**, KickAss +€18.0m / +€40.4m, LOREY +€10.1m / +€27.7m, v3 −€0.8m / +€12.3m; 109 of the leaders' 113 buys were Kickbase listings: the supply the simulator offers. (b) at their last sighting the leaders' picks sit at v3's 76th percentile: the forecast sees them. (c) value gap: universe ρ +0.648 → +0.652, listings +0.588 → **+0.574 → FAIL**, not adopted (overpriced listings −8%, underpriced +8%, but momentum already carries it) |
| M2-17 | 2026-11-11 | `desk/flipbook.py` + `Policy.flipbook`: keep the best eleven of what we did not buy, sell the rest; buy the listings with the highest drift that clears its premium into free slots, on debt up to 33% of squad value (a bid that cannot be sold before kickoff needs cash); sell a flip at the first session that sees a night without a rise; the last session before a kickoff repays (flips first, then least EP per euro) | first as a scratch benchmark ("can a leader-style flip book make leader money in this simulator at all?"), then the repo code through `backtest.ensemble`; freeze rule and target as M2-15. Rules from earlier evidence (turn exit 2026-10-07, which overlaps W1's first two weeks; entry by the 2025/26 table; Kickbase's 33%), none tuned on W1 | do nothing +€10.8m (frozen-2 +€14.3m: the planner adds €3.5m to doing nothing). Flip book: **+€47.4m** (paths +32 .. +65), premium €9.6m, hold **6.1d**, money vs real **+€60.0m**, points vs real **+20.2** → ADR-0007 **PASS**; paired money wins over frozen-2 **8/8 → FREEZE as `v3-frozen-3`**. Target: beats LOREY, short of KickAss (−€1.9m) and Bebè (−€9.3m) → not met. Cash only: +€29–31m, points −92 to −100 vs real. Configs tried on W1 so far: 12 (+ 4 scratch benchmark variants) |
| M2-18 | 2026-11-11 | flip book diagnostics; `flipbook.entries(by_euros=…)`, `Policy.flip_by_euros` (off); `best_eleven` breaks EP ties by player id | (a) what binds the flip book's buying; (b) rank by euros per slot, freeze rule vs frozen-3; (c) each manager's change split into trading profit and starting-squad drift over all 8 windows (a split chosen after the target was missed) | (a) **squad slots bind 99%**, debt uses 22% of its allowance. (b) by euros +€45.1m, 2/8 paired wins → **not adopted** (bigger players drift less). (c) trading profit, 8-path means: **flip book +€38.6m**, Bebè +€37.9m, KickAss +€26.4m, LOREY +€16.4m, frozen-2 +€10.1m; starting squads €133m (ours, +€8.7m drift) against €193–216m (+€18.7–23.3m). The pre-registered target (total profit) is **not met**; the trading split says the gap is the starting book, which the reset sets to zero for everyone. Also found: the kickoff iterated a `frozenset`, so EP ties fielded a hash-order-dependent eleven (frozen-3 path 1 moved 8 pts/MD between runs); fixed in `best_eleven`. Score `v3-frozen-1..3` with `PYTHONHASHSEED=0` for repeatable points. Configs tried on W1: 14 |
| M2-19 | 2026-11-11 | ADR-0008/0009 (committed before this run): `desk/core.py`, `Policy.core_upgrades`, bids and wins tagged by book; 15 daily starts | frozen-3 as the baseline on 15 starts; two books with the core's first claim on wallet and allowance; ADR-0009's freeze rule and leader-level bar | frozen-3 on 15 starts: total **+€49.3m**, trading **+€39.8m**, +€61.5m / +26.5 pts vs real → **same level** as the leaders (third total +€39.7m; median trading +€26.4m; Bebè +€56.7m / +€37.8m). Two books: **+81.1 pts/MD** over frozen-3 (12/15 paths) but **−€26.2m** (0/15) → not frozen |
| M2-20 | 2026-11-11 | `core.upgrade(holds_value=…)`, `Policy.core_holds_value` (off); per-book P&L | the core only buys upgrades whose drift covers the premium; why the two books lose money | holds value: −€29.9m, −7.9 pts → not frozen. Per book (15-path means): the core's own buys break even (+€1.7m on 14.7 buys); the **flip book is crowded out** (39.5 → 22.7 flips, +€39.8m → +€17.0m): each upgrade buys dearer than it displaces, and the repayment sells flips to pay for it. μ prices cash as static wealth; here capital has a yield |
| M2-21 | 2026-11-11 | ADR-0010: `core.upgrade(capital_neutral=…)`, `Policy.core_capital_neutral` | an upgrade bids at most what the starters it displaces sell for; ADR-0009's freeze rule vs frozen-3 | total **+€50.4m**, trading **+€39.0m**, +€62.6m / **+71.8 pts** vs real; vs frozen-3 **+45.2 pts/MD on 15/15 paths, +€1.1m on 13/15 → FREEZE as `v3-frozen-4`**. Leader level: **same level** (above KickAss's total, above Bebè's trading), does not beat Bebè's total. Points over the same windows: Bebè 1028, KickAss 1145, LOREY 1205 pts/MD against our real 720 and frozen-4's ~792: their squads are €60–80m bigger, and a capital-neutral core never converts profit into squad value. Configs tried on W1: 17 |
| M2-22 | 2026-11-11 | `core.upgrade(surplus=…)`, `Policy.core_spends_surplus` (off) | Marco: "go ahead". The flip book is slot-bound (M2-18) and the repayment returns the week's profit as positive cash, so the core may add that surplus to what it displaces, never debt; freeze rule vs frozen-4 | **+15.4 pts/MD but −€9.2m**, points higher on only 8/15 paths → **not frozen**. Across M2-19/21/22 W1 prices points at roughly €0.3–0.6m per point a matchday; how much profit to convert depends on what money is worth after the reset (it is wiped), which W1 cannot say. Configs tried on W1: 18 |
| M2-23 | 2026-11-11 | `flipbook.repay` searches sets of up to 3 sales for the fewest points lost (the smallest sufficient sacrifice; now always on); `Policy.flip_debt_covered` and `Policy.core_fills_holes` (off) | found while preparing the live adapter: **frozen-4 fields fewer than eleven at 24 of 75 kickoffs** (−100 a slot; its repayment sold a third defender before a spare midfielder, and the capital-neutral core can never fill a hole). Baseline = frozen-4's own `repay` from git; ADR-0009's freeze rule | **A** set repay: holes 24 → 12 (8 of them on paths that start in our real debt), −€0.6m (6/15), +15.8 pts (8/15) → not frozen. **B** + flip debt ≤ the flips' p10 cover: −€8.4m, −77 pts. **C** + the core fills holes in its own eleven: **0 holes, +172.3 pts/MD on 14/15 paths (≈964 pts/MD; Bebè 1028), −€14.3m** → fails the €3m tolerance; mostly it turns flip slots into core starters, about €0.08m a point a matchday, 4–7× cheaper than M2-19/M2-22. **C′** only holes the whole squad has: 2 holes, +70.7 pts (11/15), −€12.9m. **B+C′**: 53 holes, −€31.6m. Configs tried on W1: 23 |
| M2-24 | 2026-11-11 | ADR-0011 (Marco: one set of rules before and after the reset; an empty slot is always filled) | no new run: M2-23's C′ (set repay + the core fills holes the whole squad has) becomes the configuration by decision, outside ADR-0009's freeze rule | **`v3-frozen-5`**: 2/75 short kickoffs, +70.7 pts/MD over frozen-4 (11/15), −€12.9m (1/15); money vs real +€49.8m, points vs real **+142.4** → ADR-0007 PASS. Total profit +€37.5m (third leader +€39.7m): below ADR-0009's same-level money bar; points ≈862/MD against the leaders' 1028–1205 |
| M2-25 | 2026-11-11 | ADR-0012: `backtest.scorecard`, `rehoboam desk-scorecard`, `backtest.NAMED_POLICIES`; `core.upgrade(cheap_fill=…)`, `Policy.cheap_fill` (off) | Marco: every change against the leaders on money, missing players and points. All named configurations on the current code (set repay on), 15 windows; then a fill by the most points per euro | planner +€13.3m / 30 empty / 703 pts; flip book +€49.8m / 13 / 776; **two books +€49.8m (trading +€40.5m) / 12 / 807 (money: same level)**; no empty slot +€37.5m / 2 / 862 (below); core fills its eleven +€36.0m / 0 / 964 (below); **cheap fill, squad +€44.5m (trading +€34.9m) / 2 / 813: money same level and no avoidable empty slot, but −€5.3m against two books → REJECT under the keep rule**; cheap fill, core +€35.6m / 0 / 795. Leaders: Bebè +€56.7m / 1028, KickAss +€49.6m / 1145, LOREY +€39.7m / 1205. No configuration is at their level on all three; points cost money at a steady rate. Configs tried on W1: 25 |
| M2-26 | 2026-11-11 | day-0 mode: `backtest.day0_state`, `scorecard(day0_budget=…)`, `desk-scorecard --day0` (no squad, cash, horizon = season end); `core.bids` fills every open slot in one session (open core bids count as filled); named configurations with `core_spends_surplus` | Marco: "pretend its day 0 … the bot should behave like its day 0 so we can win after the reset". Every named configuration from zero with €200m (cash-only reset, Eval §7.4), 15 starts; W1 market as the supply (leftovers: a pessimistic floor for a reset market) | Day 0: flip book / two books +€68.0m but **387 pts, 43/75 empty** (the eleven is never built); fill with stars +€19.2m / 632 / 0; core fills its eleven +€19.5m / 653 / 0; cheap fills +€27–30m / 360–374 (benches); **eleven + surplus +€4.5m / 908 / 0**; every slot + surplus +€5.8m / 919 / 0. On W1 from our squad: **eleven + surplus +€30.0m / 928 / 0**; every slot + surplus +€28.3m / 889 / 0; two books +€49.8m / 807 / 12. Capital neutrality hoards cash once the eleven stands; spending the surplus turns it into points while net worth still grows. Configs tried: 35 |
| M2-27 | 2026-11-11 | ADR-0013 (Marco: "go with your recommendations"): "eleven + surplus", money valued to the season's end | W1 scorecard with the season horizon (`desk-scorecard --season-horizon`) | **`v3-frozen-6`**: W1 from our squad **+€31.3m (trading +€25.5m), 941 pts/MD, 0/75 empty**; day 0 (€200m) +€4.5m, 908 pts/MD, 0/75 empty. Leaders: Bebè +€56.7m / 1028, KickAss +€49.6m / 1145, LOREY +€39.7m / 1205. Money below the leaders' level by choice (points over profit) |

**A flaw in the bar (B1/B2), found by M2-5 and pending Marco's decision.** B2 compares a level
reached from unequal starts. We start W1 with the league's lowest team value (~130m against
~200m for the rivals), and points follow team value (ρ 0.90, *Eval §7.3*). No policy closes a
70m gap in five matchdays: every path started later, from a weaker real squad, scores lower
across all settings. That was knowable from the evaluation before the bar was written. The
proposed amendment:

- **Windows before the reset (unequal starts):** v3 must beat **our real record from the same
  start** on both money and points, as an ensemble mean.
- **From the reset (everyone starts from zero):** the level bar against the rivals' median
  applies as written.

Under the amended bar the frozen candidate still **fails**: +€22.3m money, −27 points a
matchday. So the amendment does not move the goalposts to a pass. The points side is the open
problem.

**Frozen configurations (ADR-0007).** Each one is scored only on rounds after its freeze, from
a worktree of its tag.

| Tag           | Frozen at                 | `desk-holdout` flags                                                                                                                                        | Chosen by                                                                                  | Its own W1 ensemble (development, vs our real record)                                                                      |
| ------------- | ------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------- |
| `v3-frozen-1` | 2026-11-11T16:19:29+00:00 | `--frozen-at 2026-11-11T16:19:29+00:00 --mu-scale 1 --blend`                                                                                                | the pre-registered rule (M2-5)                                                             | money +€22.3m, points −27.1 per MD: fails the ADR-0007 bar on points                                                       |
| `v3-frozen-2` | 2026-11-11T16:28:59+00:00 | `--frozen-at 2026-11-11T16:28:59+00:00 --mu-scale 1 --blend`                                                                                                | frozen-1 plus the kickoff-timing correctness fix (M2-7)                                    | money +€26.9m, points +23.8 per MD: passes the ADR-0007 bar                                                                |
| `v3-frozen-3` | 2026-11-11T18:42:55+00:00 | `--frozen-at 2026-11-11T18:42:55+00:00 --blend --flipbook --debt`                                                                                           | the freeze rule (M2-17): 8/8 paired money wins over frozen-2                               | money +€60.0m, points +20.2 per MD: passes the ADR-0007 bar; W1 profit +€47.4m                                             |
| `v3-frozen-4` | 2026-11-11T19:15:30+00:00 | `--frozen-at 2026-11-11T19:15:30+00:00 --blend --flipbook --debt --core --capital-neutral` (daily paths)                                                    | ADR-0009's freeze rule (M2-21): +45.2 pts/MD on 15/15 paths, +€1.1m on 13/15 over frozen-3 | money +€62.6m, points +71.8 per MD: passes the ADR-0007 bar; same level as the leaders on money (ADR-0009)                 |
| `v3-frozen-5` | 2026-11-11T19:45:07+00:00 | `--frozen-at 2026-11-11T19:45:07+00:00 --blend --flipbook --debt --core --capital-neutral --fill-holes squad` (daily paths)                                 | Marco's decision, ADR-0011 (M2-24): the eleven is never short                              | money +€49.8m, points +142.4 per MD: passes the ADR-0007 bar; total profit +€37.5m (below ADR-0009's same-level money bar) |
| `v3-frozen-6` | 2026-11-11T20:16:05+00:00 | `--frozen-at 2026-11-11T20:16:05+00:00 --blend --flipbook --debt --core --capital-neutral --fill-holes core --spend-surplus --season-horizon` (daily paths) | Marco's decision, ADR-0013 (M2-27): behave like day 0                                      | W1 +€31.3m, 941 pts/MD, 0/75 empty; day 0 (€200m) +€4.5m, 908 pts/MD, 0/75 empty                                           |

**What M2 found.** At μ×1 the planner is a money machine. It went into kickoffs with €47–91m of
idle cash, and cash scores nothing. The cause is a derivation error in §4.3: μ(N) = (N−1)/P values
a euro as if it will buy quality for every remaining matchday, while each session credits a
player's points for one matchday against a one-time premium. So converting cash into points is
never worth it on paper. Even μ×0, which counts points alone, scores only 681. The rest of the gap
is on the points side, and the prime suspect is §4.1: the scorer overrates exactly the cheap
high-EP players the planner buys.

**From here, W1 is the development set.** Three changes are queued, one run each, all logged
here:

1. μ fixed in its derivation, not by its scale;
1. the price blend refitted walk-forward, on matchdays before each kickoff only;
1. planned debt through named sales.

The decisive verdict moves to data no choice has seen: MD10 to MD14, replayed with a
configuration frozen (committed) before MD10 kicks off on 2026-11-20. The store records
listings, predictions, values and transfers in `lineup_only` too. So paper trading needs no live
job: it is this backtest, run on a window that didn't exist when the configuration was frozen.

Per band, M1-1, top decile of each band (informational):

| Band  | Listings | Top | Median realised | Band premium | Excess | Median gain |
| ----- | -------- | --- | --------------- | ------------ | ------ | ----------- |
| \<5m  | 462      | 47  | +71.8%          | +9.4%        | +57.0% | €2.19m      |
| 5–15m | 219      | 22  | +15.8%          | +5.7%        | +9.6%  | €1.46m      |
| 15m+  | 50       | 5   | +4.6%           | +7.1%        | −2.3%  | €0.85m      |

**What it means for the design.** The drift edge is under 15m. At 15m+ the predicted drift
doesn't cover the premium ceiling, so the objective (§5.2) buys there only for points, and that
needs no rule. On the top decile, bidding at the band median wins 45 of 74 (2 unknown) with a
median return of +38.0% on the wins. Bidding +5% wins 34, for +55.6%. Deciles 7–10 lost 17–26%
in a week, which is what the held-player table (lag 0) is for: the turn exit becomes a valuation.

**Caveats.** The test window is one first half (an inflating market, October's break included),
and decile 1 is ~10 listings a week. The verdict that matters is still B1–B4 (money and points
with real auctions) and paper trading.
