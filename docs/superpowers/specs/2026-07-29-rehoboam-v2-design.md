# Rehoboam v2 — Season 2026/27 Design

**Date:** 2026-07-29
**Status:** Approved design, pending implementation plan
**Season starts:** ~2026-08-28 (≈4 weeks)
**Goal:** Win the league. Not place, not profit — win.

______________________________________________________________________

## 1. Post-mortem: what the data says about 2025/26

Final position **10th of 14**. 26,170 points against the winner's 37,857 — a gap of
11,687 points, or **+343 points per matchday**.

### 1.1 The squad was destroyed, not built

|                      | Us        | League avg | Winner |
| -------------------- | --------- | ---------- | ------ |
| Final team value     | **75.8M** | 192M       | 327.9M |
| Final squad size     | **11**    | —          | —      |
| Final unspent budget | **93.9M** | —          | —      |

Team value tracked the league (~200M) through matchday 20, then collapsed
192M → 151M → 144M → … → 75M while every rival held steady. We finished last in
team value by 24M, holding 93.9M in cash. **Cash scores zero points.**

At matchday 1 we scored 893 against a league average of 772 and sat **4th**. The
starting squad was good. The bot degraded it over 34 matchdays.

### 1.2 The EP scorer cannot rank players

`scoring/scorer.py:291`:

```python
base_points = min(avg_pts * 2.0, 40.0)  # saturates at avg_points = 20
```

Sampled 1,220 player-seasons from `performance_cache`:

- **93.1% of players average more than 20 points/game** — all receive an identical base of 40
- Median player: 64 pts/game. Elite: 105–170

The one component expressing player quality is a **constant for every player worth
owning**. EP is therefore determined entirely by bonuses (lineup ±20, consistency
±15, fixture +15/−10, minutes +10/−15, form ±14, injury −30), none of which measure
scoring output.

Measured against reality (`matchday_outcomes`, n=27):

| Position   | n   | Predicted | Actual | Bias   | MAE      |
| ---------- | --- | --------- | ------ | ------ | -------- |
| Defender   | 11  | 77.1      | 66.0   | −11.1  | 38.7     |
| Midfielder | 11  | 70.8      | 88.9   | +18.1  | 45.0     |
| Forward    | 3   | 62.3      | 164.0  | +101.7 | 135.7    |
| **All**    | 27  | 73.1      | 89.5   | +16.4  | **51.5** |

Predictions span 25–112; reality spans −4 to 268. The model clamps at 100 and so is
structurally unable to point at a 268-point performance. The units never matched:
a 0–100 index compared against 0–300 real points. `MIN_EP_UPGRADE_THRESHOLD = 5.0`,
marginal-EP ranking, bid tiers and the position calibration multiplier all consume a
number that is not in points — **the calibration loop was correcting a unit mismatch,
not a bias, and could never have converged.**

### 1.3 Trading was a net destroyer of value

151 flips, **−55.3M realised**, 109 losses against 42 wins.

| Hold time         | Flips  | Net      | Avg %      | Win rate |
| ----------------- | ------ | -------- | ---------- | -------- |
| under 1 week      | 81     | **−30M** | −2.5%      | 19%      |
| 1–3 weeks         | 44     | **−39M** | +5.1%      | 36%      |
| 3wk–2mo           | 16     | −14M     | +13.3%     | 31%      |
| **over 2 months** | **10** | **+27M** | **+32.7%** | 60%      |

Average hold: 15.2 days. **Only the >2-month bucket was profitable.** 125 of 151
flips were held under three weeks and lost 69M between them.

**Why short flips cannot work.** Bought at an average **+12.2% overbid**
(`auction_outcomes`, won bids); instant sell returns **95% of MV**
(`decision.py:230`). A round trip requires MV to rise `1.122 / 0.95` = **18.1%** just
to break even. An elite player appreciates ~100% across a *season* — roughly 4% per
fortnight. The bot paid an 18% toll to chase a 4% move, 151 times.

### 1.4 Self-inflicted penalties cost ~1,400 points

Cross-referencing `matchday_lineup_results` against official `league_rank_history`:

- **Matchday 14: 0 official points** despite fielding 11 players worth 1,109 — the
  negative-budget-at-kickoff penalty. **~1,100 points in one day.**
- **Matchdays 6, 17, 21: only 10 players fielded** → −100 each.

**Corrected 2026-07-29, during week-1 implementation.** This section originally
claimed a direct configuration cause: `config.py:90`, `min_squad_size = 10`, on
the reasoning that a 10-player squad cannot fill 11 slots. **That diagnosis was
wrong**, and the correction matters because it changes what the fix has to be.

`min_squad_size` never enforced a sell floor. `SquadOptimizer` assigns it
(`squad_optimizer.py:39`) and **never reads it again** — `optimize_squad` does
not consult it. Its only live consumer was the emergency-mode trigger in
`trader.py`. So the setting could not have caused the three 10-man lineups, and
raising it would not by itself have prevented them.

The real mechanism: nothing stopped the squad reaching an **unfieldable position
shape**. Twelve players with only two defenders cannot form a legal eleven, and
nothing checked fieldability before kickoff. Squad sizes on those matchdays were
11–12 — the bot *had* enough bodies and still fielded ten. These were
**availability and shape failures, not headcount failures**; raw squad size was
only ever a proxy for the question that mattered.

Two consequences, both now implemented:

1. The fix is `formation.can_fill_starting_eleven(available)` driving the
   emergency trigger — asking directly whether a legal eleven can be fielded.
1. Raising `min_squad_size` to 13 **must not** also drive emergency mode.
   Measured against real season data (`team_value_history`, 29 sessions: squad
   size 11 in 41%, 12 in 52%), coupling them would have fired emergency in
   **27 of 29 sessions (93%)**, versus 0 before. Emergency drops the EP quality
   bar from 30 to 10 and skips the marginal-EP-upgrade gate entirely, so the
   guardrail would have put the bot into permanent panic-buy mode — amplifying
   the very churn that cost the season.

`min_squad_size = 13` is retained as a **sell floor** (11 + 2 injury cover, also
leaving 2 slots for open bids under the 15 cap). Note it is still not enforced
anywhere on the sell side; wiring that is week-4 decision-layer work.

Recovering those ~1,400 points moves us from 10th to **8th** (9th was 215 points
ahead). REH-11's budget block landed *after* matchday 14 and has never faced the
failure it was written for — week 1 pinned it with tests and confirmed the
matchday-14 scenario is genuinely blocked today. Two gaps in it remain
documented rather than fixed: it fails open when the match date is unknown, and
it blocks *creating* negative budget within 24h rather than *arriving* at
kickoff negative.

______________________________________________________________________

## 2. The mechanism: how Kickbase market value behaves

From 39,748 `player_mv_history` rows joined to per-match scoring (101 players):

| Points in match | MV at +3d | MV at +7d  | then +14d        | then +21d |
| --------------- | --------- | ---------- | ---------------- | --------- |
| 0–25 (bad)      | −6.6%     | **−10.6%** | −6.3% *more*     | −5.8%     |
| 25–60           | 0.0%      | −1.5%      |                  |           |
| 60–100          | +1.6%     | +2.1%      |                  |           |
| 100–150         | +4.8%     | +9.1%      |                  |           |
| 150+ (huge)     | +7.7%     | **+13.1%** | **+5.3% *more*** | +5.1%     |

**Market value tracks performance level and the moves are permanent in both
directions.** No mean reversion at any horizon. Tested specifically for the case
where reversion should be strongest — a standout game from a mediocre player:

| Standout game (120+ pts)            | n   | MV change day 7 → 21 |
| ----------------------------------- | --- | -------------------- |
| From a high-average player (≥70/gm) | 156 | +3.94%               |
| From a low-average player (\<70/gm) | 139 | +4.20%               |

Both keep rising. Kickbase's MV algorithm behaves like a smoothed function of recent
performance — momentum, not a market with bubbles to sell into.

**Consequences:**

1. **"Sell after the spike" loses on both axes** — it pays 5% to exit a still-appreciating
   asset and forfeits that player's future points. Buying back afterwards is worse:
   sold at 107.4, repurchased at 118.8 × 1.05 = 124.7 (index MV = 100).
1. **The money is on the downside.** A decliner bleeds ~17% over three weeks,
   compounding, without rebound, while scoring 0–25 points a week. Exiting fast is
   worth ~14% of the player's value versus holding three weeks — more than any flip.
1. **Points and money are the same asset.** Players averaging \<40 pts/game appreciated
   **0.0%**; elite scorers appreciated **+101%**. Correlation between scoring and
   appreciation: +0.30. Owning good players *is* the income strategy; there is no
   separate income engine to build.

### 2.1 Availability dominates everything

| Minutes played   | n      | Mean points |
| ---------------- | ------ | ----------- |
| 0 — did not play | 2,067  | **−0.1**    |
| 1–30             | 5,214  | 18.2        |
| 31–60            | 2,956  | 33.8        |
| 61–89            | 5,884  | 75.1        |
| 90+              | 13,464 | **94.6**    |

A ~95-point swing driven purely by whether the player is on the pitch. The current
scorer models this as a ±20 bonus on a 0–100 index. **This is the largest effect in
the game and it is modelled as a minor adjustment.** Form, consistency and fixture
are rounding errors beside it.

### 2.2 Overbidding bought nothing

| Our overbid | n   | Win rate |
| ----------- | --- | -------- |
| \<3%        | 1   | 0%       |
| 3–8%        | 6   | 50%      |
| 8–15%       | 12  | 50%      |
| >15%        | 7   | 57%      |

Weak evidence (26 auctions), but the direction is clear: we paid 7+ points of margin
for ~7 points of win rate. Related defect — `winning_overbid_pct` is **never captured
on lost auctions**, so the learner could never discover this.

______________________________________________________________________

## 3. Strategy

1. **Selection before acquisition.** Extracting points from the squad you already own
   is free; buying is taxed 8–18%. This makes the scorer the highest-ROI work.
1. **Own the level, not the moment.** Buy for scoring rate, minutes security and
   fixtures. Hold — appreciation compounds and does not revert.
1. **Exit on level breaks, immediately. Never on profit targets.** Injury, benching,
   minutes collapse, role loss.
1. **Every trade must clear the toll** (~8–13% at a disciplined overbid).
1. **Buy early and heavily** — compounding rewards time held.
1. **Never pay a penalty.**

**Tension between (5) and cold start, resolved.** "Buy early" and "don't trade blind
in matchdays 1–3" pull against each other. The resolution is that they apply to
different players, and the shrinkage model enforces it automatically: established
players carry last season's record, so their estimates are confident and they can be
bought immediately — that is where "early" pays. New signings and promoted-team
players have little history, so shrinkage pulls them toward the position prior and
they will not rank highly enough to buy until real data arrives. No separate rule is
needed for the distinction; the matchday 1–3 trade cap in §4.3 exists only to bound
*total* exposure while the rebuilt model is unproven live.

Note on the standing preference for aggressive negative-budget buying: sound
*between* matchdays, but budget must be ≥0 at kickoff. That is precisely what cost
matchday 14. The preference stands; the timing guard must be trustworthy.

______________________________________________________________________

## 4. Architecture

### 4.1 The scorer — Availability × Rate

```
EP = Σ_bucket  P(bucket) × rate(player, bucket) × context
```

Output in **real Kickbase points**, making marginal EP gain directly comparable
against the transaction toll for the first time.

**Availability — `P(minutes bucket)`** over {DNP, 1–30, 31–60, 61–89, 90+},
conditioned on lineup probability (`prob`), rolling recent minutes, and injury status
(`st`). Fitted as a shrunk empirical lookup table — no ML runtime in the Function.
Validated independently: does a `prob=1` player actually start 90?

**Rate — expected points for this player in a given bucket.** Decomposed as a
league-wide bucket base rate (from §2.1: DNP ≈ 0, 1–30 ≈ 18, 31–60 ≈ 34, 61–89 ≈ 75,
90+ ≈ 95) multiplied by a per-player quality factor, where that factor is shrunk
toward a position prior:

```
rate(player, bucket) = base_rate(bucket) × quality(player)
quality(player)      = (n × observed + k × prior) / (n + k)
```

`k` fitted from data. This is how cold start is handled honestly — a player with 3
matches is pulled hard toward the prior; one with 30 stands on his record. No
special-casing for newcomers or promoted teams.

**Context** — fixture difficulty, home/away (derivable from `t1`/`t2`), DGW. All
fitted multipliers, **including DGW**: the current ×1.8 is an unverified assumption.

### 4.2 Decision layer

| Change                       | From                               | To                                                                                                    |
| ---------------------------- | ---------------------------------- | ----------------------------------------------------------------------------------------------------- |
| `min_hold_hours_before_sell` | 48 hours                           | ~45 days, overridable only by decline triggers                                                        |
| Profit-target selling        | `MIN_SELL_PROFIT_PCT` drives sells | **Deleted**                                                                                           |
| Sell trigger                 | profit/loss thresholds             | **Decline detector**: injury status change, lineup prob → 4+, rolling 3-game minutes \< 45, role loss |
| Max overbid                  | 12.2% actual                       | Hard cap ~8%                                                                                          |
| Trades per session           | 10 (15 aggressive)                 | ~3, plus a weekly cap                                                                                 |
| Buy gate                     | EP index delta > 5.0               | Real-points EP gain clearing the round-trip toll                                                      |

### 4.3 Guardrails

**Shipped in week 1** (see §1.4 for the corrected diagnosis that reshaped these):

- **Position-aware fieldability check** — `formation.can_fill_starting_eleven`,
  the actual fix for the 10-man lineups. It asks whether a legal eleven can be
  formed from available players, rather than inferring it from headcount.
  *Known limitation:* squad `Player` objects carry no injury/status field, so
  every squad member counts as available. This catches headcount and
  position-shape emergencies — the pattern behind the historical bug — but not
  "twelve well-shaped players, one injured." Real availability arrives with the
  week-2 scorer; the caveat is documented in the code, not just here.
- **Emergency trigger decoupled from `min_squad_size`** and driven by that
  fieldability check. Coupling them would have fired emergency in 93% of
  sessions (§1.4).
- `min_squad_size` **10 → 13**, re-scoped as a **sell floor** only. Still not
  enforced anywhere on the sell side — `SquadOptimizer` ignores it. Wiring a
  real floor is week-4 work.
- **15-player cap now counts open bids.** Kickbase caps the squad at 15
  *including open trades*; the code previously counted only `len(squad)`, so 13
  players plus 3 open bids could commit to 16. Bidding is now gated on
  `len(squad) + len(open_bids) < 15`.
- **Budget-at-kickoff guard pinned** with tests against the matchday-14
  scenario. Confirmed genuinely blocked today in both live and dry-run paths.
  Two gaps documented as characterisation tests rather than fixed: it fails
  open when `days_until_match` is unknown, and it blocks *creating* negative
  budget within 24h rather than *arriving* at kickoff negative — recovery
  depends on `optimize_squad_for_gameday`, which is unverified.

**Still to do:**

- Reduced trade budget for matchdays 1–3, when cold-start uncertainty is
  highest (week 4, with the decision layer).

______________________________________________________________________

## 5. Data enrichment

The model is only as good as its inputs, and the current inputs are both narrow
(353 players cached, 206 with MV history) and biased toward players the *old* bot
happened to touch. Enrichment is the **long pole** — API-bound and slow — so it
starts first.

**Governing principle: only enrich with data that will be available at prediction
time during the season.** A feature we can train on but cannot serve on a Friday
morning is worthless. This specifically rules out API-Football, whose free tier
excludes the current season: usable for training, unusable live, therefore not
usable at all.

**Discipline: probe-first.** Per the existing project pattern, every source gets a
read-only `scripts/probe_*.py` validating response shape, coverage and freshness
before any code depends on it.

### 5.1 Tier 1 — Kickbase API (no new dependencies, highest confidence)

| Endpoint                                                | Unlocks                                                                                                                                |
| ------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `/v4/competitions/{cid}/players`                        | **The full player universe.** Eliminates survivorship bias at its root                                                                 |
| `/v4/competitions/{cid}/players/{pid}/performance`      | Per-match history for every player, not just ones we owned                                                                             |
| `/v4/competitions/{cid}/players/{pid}/marketValue/{tf}` | MV history league-wide (widen `mv_backfill.py` beyond `_distinct_flip_player_ids`)                                                     |
| `/v4/competitions/{cid}/matchdays`                      | **Fixture schedule + kickoff times** — feeds DGW detection and the budget-at-kickoff guard                                             |
| `/v4/competitions/{cid}/table`                          | Real Bundesliga standings — replaces the homegrown SOS rating                                                                          |
| `/v4/leagues/{lid}/players/{pid}/transferHistory`       | **Real transaction prices across the season** — the best available repair for the empty `market_prices.db` and the buy-side simulation |
| `/v4/leagues/{lid}/managers/{mid}/squad`                | Competitor squads — market scarcity and rival strength                                                                                 |
| `/v4/competitions/{cid}/playercenter/{pid}?dayNumber`   | Richer per-matchday player context                                                                                                     |

Requires throttling and caching; a full-league sweep is thousands of requests.

### 5.2 Tier 2 — external free REST APIs

- **ClubElo** — team strength ratings, free REST API. Replaces homegrown SOS with a
  maintained, calibrated rating.
- **OpenLigaDB** — free Bundesliga fixtures, results and lineups. Cross-check for
  Kickbase fixture data and a possible independent lineup signal.

### 5.3 Tier 3 — probe before committing

- **Understat xG/xA** — genuinely predictive beyond raw points, but a scrape rather
  than an API, therefore fragile. Contrary to the standing preference for APIs over
  scrapers; only pursue if Tiers 1–2 leave the availability model wanting.

### 5.4 Explicitly excluded

- **API-Football** — free tier excludes the current season. Trainable, not servable.

______________________________________________________________________

## 6. Validation

Two instruments, deliberately kept separate.

### 6.1 Scorer harness — a *tuning* instrument

Replays matchday by matchday, seeing only data from before each matchday. Runs
hundreds of times.

Reports:

- **Lineup regret (primary)** — points of the chosen 11 vs the hindsight-best 11 from
  the same squad, in league-table units
- **Rank correlation** — Spearman, per matchday
- **Buy quality** — did intended buys outscore intended sells

**Baseline to beat: rank by season average points.** A one-line model. If the new
scorer cannot beat it on replay, we ship the baseline.

**Not MAE.** Median per-player game-to-game standard deviation is 54.8 points, so a
*perfect* model of a player's true level still scores MAE ≈ 44. Targeting MAE \< 30
would drive us to overfit noise. The bot does not need to know Musiala scores 143 on
Saturday; it needs to rank him correctly.

**Week 1 measured the baseline** (`rehoboam backtest-baseline`, season-average model,
22 usable matchdays of 2025/26). Regret is sensitive to the assumed squad size, and
the sensitivity runs one way — more assumed bench depth can only inflate the
hindsight-optimal eleven, never what the ranker actually picks:

| assumed squad cap          | 12    | 13    | 14    | 15 (CLI default) | 16    | uncapped |
| -------------------------- | ----- | ----- | ----- | ---------------- | ----- | -------- |
| mean regret (pts/matchday) | 43.5  | 61.7  | 78.0  | 87.0             | 91.7  | 97.1     |
| points captured            | 95.0% | 93.0% | 91.3% | 90.4%            | 89.9% | 89.4%    |

`total_chosen_points` is identical (17,939) at caps 15, 16 and uncapped, while
`total_best_points` keeps climbing — confirming the inflation is one-sided. A second,
compounding selection effect sharpens this further: squad reconstruction unions two
membership sources (flip hold-windows and the fielded eleven), so a player held but
never fielded nor flipped stays invisible to both — the 12 of 34 matchdays excluded
from the 22 "usable" ones for having fewer than 12 reconstructed players are precisely
this *under*-counted case, so the surviving 22 are systematically selected for the
*over*-counted side instead.

**The uncapped headline figure (97.1 pts/matchday) is therefore an upper bound, not a
point estimate.** The defensible restatement is **~60–85 pts/matchday, ~2,000–2,900
points/season, 17–25% of the 11,687-point gap** to last season's winner — not a single
~97 → ~3,300 → 28% number. It remains sound as a **relative** bar, though: weeks 2-3
score their scorer on this exact same fixture set — identical squads, identical days,
identical actuals — so the pool bias is common-mode and cancels in a paired
comparison against this baseline. That is exactly what the §8 safety valve needs: not
an absolute verdict on how many points selection is worth, but a same-conditions bar
the week-2/3 scorer either clears or doesn't. Selection quality remains the
highest-ROI work available, surviving intact at 17-25% of the gap either way.

### 6.2 Full-bot season replay — a *verdict* instrument

Replays the entire agent — buys, sells, budget, squad evolution, lineup — across
2025/26. Run a handful of times at most.

**Credibility decays with every tuning iteration against it.** That is why it is
separate from the harness.

**Fidelity is not uniform and the output must say so:**

| Component                      | Fidelity         | Basis                                                                                                                                                         |
| ------------------------------ | ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Lineup selection, 34 matchdays | **High**         | Exact record of who we fielded; 81% have per-match scoring (→ ~100% after enrichment)                                                                         |
| Sell decisions                 | **High**         | Instant sell = 95% of MV; no market inventory needed. 97% of fielded players have MV history                                                                  |
| Penalty avoidance              | **Exact**        | Deterministic                                                                                                                                                 |
| Buy decisions                  | **Low → medium** | `market_prices.db` is **empty (0 rows)**; `league_transfers` covers only 2026-04-08 → 05-16. Tier-1 `transferHistory` enrichment should widen this materially |

Where market data is missing, the sim assumes any player with MV history was
purchasable at `MV × (1 + overbid)`, ignoring competition — a market that always has
what v2 wants, at a fair price, with no rival bidders. **Buy-side results are an upper
bound and are labelled as such.** If v2 only wins the simulated league through
optimistic buys, that is not evidence.

**Output is an attribution table, not a verdict:**

| Source of gain                      | Points      | Fidelity        |
| ----------------------------------- | ----------- | --------------- |
| Penalties avoided                   | ~1,400 est. | exact           |
| Better lineup from the *same* squad | ?           | high            |
| Better sell timing                  | ?           | medium          |
| Better buys                         | ?           | **upper bound** |
| Simulated total vs actual 26,170    |             | winner: 37,857  |

The first two lines are the trustworthy ones, and they need no market data at all.

### 6.3 Three ways the simulation could lie

1. **Leakage.** Performance JSON contains the whole season. Every scoring call is
   truncated to pre-matchday data, enforced by a test that deliberately tries to cheat
   and must fail.
1. **Survivorship.** Data covers players v1 touched. A player v2 would want but v1
   never saw does not exist in the sim. Mitigated by §5.1 full-league enrichment.
1. **Overfitting to one season.** **Train on seasons ≤ 2024/25, validate on 2025/26**
   (median 3 seasons of history per player, back to 2013/14). Never tune on the test
   season.

______________________________________________________________________

## 7. Deletions (~4,000 lines)

| Item                                                                 | Lines       | Evidence                                                                               |
| -------------------------------------------------------------------- | ----------- | -------------------------------------------------------------------------------------- |
| `api/` FastAPI app                                                   | 1,977       | Last touched 2026-02-22; nothing imports it                                            |
| `railway.toml`, `deploy_lambda.sh`, `deploy/requirements-lambda.txt` | —           | Dead Railway + AWS targets; we deploy to Azure                                         |
| `value_calculator.py`, `roster_analyzer.py`                          | 847         | Only imported by `api/` and legacy `expected_points.py`                                |
| `expected_points.py`                                                 | 162         | Superseded by `scoring/`; one caller (`auto_trader.py:1320`)                           |
| `bid_evaluator.py`, `league_compliance.py`                           | 691         | Single lazy import each (`auto_trader.py:1148-1149`)                                   |
| `profit_trader.py`                                                   | 372         | One lazy import (`trader.py:661`); part of the value-destroying path                   |
| Root markdown sprawl                                                 | 28 of 32    | Mostly Nov 2025, documenting strategies that no longer exist                           |
| `.worktrees/ep-scoring/`                                             | 17+ scripts | Scratch files on a stale branch                                                        |
| ~40 merged remote branches                                           | —           |                                                                                        |
| Dead config fields                                                   | —           | `min_value_score_to_buy`, `min_buy_value_increase_pct`, `min_upgrade_value_score_diff` |
| `fastapi`, `uvicorn` deps                                            | —           | Only used by `api/`                                                                    |

**Data defects to repair while in here:**

- `transfer_pnl` records **0 for all 14 managers** (REH-38 reads the wrong field or an
  empty one)
- `winning_overbid_pct` is **never captured** on lost auctions

______________________________________________________________________

## 8. Schedule

| Week | Ships                                                                                      |
| ---- | ------------------------------------------------------------------------------------------ |
| 1    | **League-wide enrichment kicks off (long pole)** · scorer harness · guardrails · deletions |
| 2    | Availability model + rate model, validated on held-out seasons                             |
| 3    | Context, integration, harness vs baseline · **rough scorer-only replay**                   |
| 4    | Decision layer · **full-bot replay + attribution** · live dry-run vs prod · enable         |

**Safety valve:** if the scorer is not beating "rank by season average" by end of
week 3, ship the **baseline model plus the new guardrails**. A dumb ranker with
correct hold discipline and no penalties comfortably beats last season. We do not
ship an unproven model merely because we built it.

**Autonomy:** full auto on the existing 2×/day Azure schedule, relying on the
rebuilt scorer, hold discipline and hard squad/budget floors.

**Decomposition.** This spec covers five workstreams and is too large for a single
implementation plan. Each week's block becomes its own plan, written and executed in
sequence — week 1 (enrichment + harness + guardrails + deletions) is written first.
Later plans are written only once the preceding week's results are in, since the
harness output legitimately changes what is worth building next.

______________________________________________________________________

## 9. Risks

| Risk                                                       | Mitigation                                                                                |
| ---------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| Full-bot replay lands week 4, leaving little reaction time | Rough scorer-only replay in week 3                                                        |
| Enrichment sweep is slow / rate-limited                    | Starts week 1, runs in background, cached                                                 |
| Buy-side sim fidelity stays low                            | Labelled as upper bound; conclusions rest on lineup + sell lines                          |
| 45-day hold too blunt for genuine upgrades                 | Decline triggers override; tune to 30 if replay shows cost                                |
| 8% overbid cap costs contested stars                       | Drawn from only 26 auctions — revisit once the new learner captures `winning_overbid_pct` |
| Cold start: model unproven live in matchdays 1–3           | Reduced trade budget; shrinkage toward priors                                             |
| New scorer fails to beat baseline                          | Safety valve (§8)                                                                         |

______________________________________________________________________

## 10. Judgment calls open to revision

1. **45-day minimum hold** — deliberately aggressive to force the behaviour change.
   Could be 30.
1. **8% overbid cap** — weak evidence base (26 auctions).
1. **Profit-target selling deleted entirely** rather than retained as a rare override.
