# EP-First Bidding & Outcome-Based Learning

**Date:** 2026-03-16
**Status:** Draft
**Depends on:** [EP-First Scoring Pipeline](2026-03-11-ep-scoring-pipeline-design.md)
**Goal:** Drive bid decisions by expected matchday points (not market value), learn from actual matchday outcomes, and remove the deprecated `--detailed` mode from `analyze`.

## Problem

1. **Bidding optimizes for the wrong thing.** `SmartBidding` uses `value_score` for tier classification, overbid %, and value ceiling. A player who scores 80 pts/game but is "overpriced" by market value gets a conservative bid — the opposite of what wins leagues.

1. **No outcome feedback.** The `BidLearner` tracks auction win/loss rates but never checks whether players we bought actually delivered matchday points. The system can't learn "we overbid for Player X and he sat on the bench."

1. **`analyze --detailed` is dead weight.** It produces 300-500 lines of market-value-focused output (value predictions, flip strategies, portfolio metrics) that duplicates and contradicts the compact mode's EP-first direction. Nobody uses it.

## Scope

### In scope

- Remove `--detailed` mode and associated flags from `analyze`
- Rewrite `SmartBidding` to use marginal EP gain as primary bid driver
- Add budget-aware bidding: allow negative budget if sell plan exists
- Add matchday outcome tracking to `BidLearner`
- Wire EP accuracy feedback into bid aggressiveness

### Out of scope

- The `scoring/` pipeline itself (covered by the EP-first scoring pipeline spec)
- Changes to the `trade` command (still uses `value_score` for profit-trading)
- Double gameweek detection (separate feature, EP pipeline handles the multiplier)

## Implementation Order

This spec **requires** the EP-First Scoring Pipeline (`scoring/` directory) to be implemented first. That pipeline provides `DataCollector`, `Scorer`, `DecisionEngine`, and `PlayerScore` — all of which this spec extends. The `scoring/` directory does not exist yet; it is created by the predecessor spec.

**Phase 1** (prerequisite): Implement EP-First Scoring Pipeline spec → creates `rehoboam/scoring/`
**Phase 2** (this spec): Wire EP into bidding, add outcome learning, remove `--detailed`

## Design

### Part 1: Remove `--detailed` mode

**Changes to `cli.py`:**

Delete from the `analyze` command:

- Flags: `--detailed`/`-d`, `--all`/`-a`, `--simple`/`-s`, `--risk`/`-r`, `--opportunity-cost`/`-oc`, `--portfolio`/`-p`
- Lines 168-513: entire detailed mode branch (market opportunities table, squad analysis, trading strategies, value predictions, learning system updates display)

**Keep:** The learning system update logic (factor weight optimization, lines ~436-506) moves into the compact flow so it still runs silently after every `analyze`.

**Modules no longer imported by `cli.py`:**

- `EnhancedAnalyzer` (position landscape, squad balance, predictions)
- `PortfolioAnalyzer`
- `SquadOptimizer` (display — decision logic moves to `DecisionEngine`)

These modules are not deleted — they may still be used by `trade` or other commands. They just lose their `cli.py` caller.

### Part 2: Marginal EP Gain — The Core Bidding Metric

Every bid is evaluated by: **"How many more matchday points does my best-11 score if I buy this player?"**

#### Calculation

```python
@dataclass
class MarginalEPResult:
    """Result of marginal EP gain calculation for a potential buy."""

    player_id: str
    expected_points: float  # Player's own EP
    current_squad_ep: float  # Current best-11 total EP
    new_squad_ep: float  # Best-11 total EP with this player added
    marginal_ep_gain: float  # new - current (can be 0 if player doesn't crack best 11)
    replaces_player_id: str | None  # Who this player displaces from best-11
    replaces_player_name: str | None
    replaces_player_ep: float  # EP of displaced player (= sell candidate)
```

**How it works:**

1. Score all current squad players via the EP pipeline → get current best-11 and total EP
1. For each market player: temporarily add to squad, re-run `select_best_eleven()` with EP scores
1. `marginal_ep_gain = new_total_ep - current_total_ep`
1. If the player doesn't enter the best 11, `marginal_ep_gain = 0`
1. The displaced player becomes the natural sell candidate

**Where this lives:** New method `calculate_marginal_ep()` in `scoring/decision.py` (`DecisionEngine`), since it already has `recommend_buys()` and roster context.

**Performance:** For 200 market players, this means 200 calls to `select_best_eleven()`, each with ~16 players. This is fast (sub-millisecond per call) and needs no optimization.

**Known limitation:** `select_best_eleven()` uses a greedy two-pass algorithm that is not globally optimal. For marginal EP calculations this means small inaccuracies (1-2 EP) are possible when a candidate creates complex position-swap chains. This is acceptable — the greedy approach is fast, deterministic, and correct for the vast majority of cases. If this becomes a problem, the algorithm can be improved independently without changing this spec's interfaces.

#### Bid Aggressiveness Tiers (EP-based)

| Marginal EP Gain | Tier           | Behavior                                             |
| ---------------- | -------------- | ---------------------------------------------------- |
| >= 20            | must_have      | Very aggressive bid. Can go negative with sell plan. |
| 10 - 19.9        | strong_upgrade | Aggressive bid within available budget + sell plan.  |
| 5 - 9.9          | solid_upgrade  | Moderate bid, within budget preferred.               |
| 1 - 4.9          | marginal       | Conservative bid, only if price is reasonable.       |
| 0                | no_improvement | Don't bid — player doesn't improve the starting 11.  |

These replace the current `value_score`-based tiers (anchor/strong/tactical/opportunistic).

### Part 3: Budget-Aware Bidding with Sell Plans

Current system hard-blocks bids exceeding budget. New system allows going negative if there's a viable sell plan.

#### SellPlan

```python
@dataclass
class SellPlan:
    """Plan to recover budget after an expensive purchase."""

    players_to_sell: list[SellPlanEntry]  # Ordered by priority
    total_recovery: int  # Sum of expected sell values
    net_budget_after: int  # budget - bid_amount + total_recovery
    is_viable: bool  # net_budget_after >= 0
    ep_impact: float  # Net EP change after selling these players
    reasoning: str


@dataclass
class SellPlanEntry:
    player_id: str
    player_name: str
    expected_sell_value: (
        int  # Market value * 0.95 (conservative — Kickbase sells at slight discount)
    )
    player_ep: float  # EP of player being sold
    is_in_best_11: bool  # Warning flag if selling a starter
```

**Sell value estimation:** In Kickbase, selling a player lists them on the market. The system typically buys them at or near market value, but not instantly. `expected_sell_value` uses `market_value * 0.95` as a conservative estimate. The sell plan's `is_viable` check uses this discounted value. If a sell plan's viability depends on getting full market value (margin \< 5%), it's flagged as risky in the reasoning.

#### Budget flow

1. **Available budget** = current_budget + sell_plan_recovery
1. If `bid_amount <= current_budget` → bid normally, no sell plan needed
1. If `bid_amount > current_budget`:
   - Build sell plan from displaced player + bench players sorted by expendability
   - Expendability = low EP + not in best-11 + not at position minimum
   - If `sell_plan.is_viable` (net_budget >= 0) → allow bid, attach sell plan
   - If not viable → don't bid
1. **Never sell best-11 starters to fund a buy** unless the buy replaces them (the displaced player)

#### Integration with SmartBidding

**New method `calculate_ep_bid()`** — the old `calculate_bid()` is kept as-is for the `trade` command and other legacy callers. The new EP-based method lives alongside it:

```python
def calculate_ep_bid(
    self,
    asking_price: int,
    market_value: int,
    expected_points: float,          # Player's EP score
    marginal_ep_gain: float,         # How much this improves best-11
    confidence: float,
    current_budget: int,             # Available budget
    sell_plan: SellPlan | None,      # Sell plan if going negative
    player_id: str | None = None,
    trend_change_pct: float | None = None,
) -> BidRecommendation:
```

The old `calculate_bid()` (value_score-based) remains unchanged. Callers in `auto_trader.py`, `trader.py` (trade command path), `compact_display.py`, and `calculate_batch_bids()` continue using it. Only the `analyze` command path calls `calculate_ep_bid()`.

**`calculate_batch_bids()` unchanged** — it uses the legacy `calculate_bid()` and is only called by the `trade` flow.

**`BidRecommendation` dataclass update:**

The `max_profitable_bid` field is kept as a property alias for backward compatibility, while the canonical field becomes `budget_ceiling`:

```python
@dataclass
class BidRecommendation:
    base_price: int
    recommended_bid: int
    overbid_amount: int
    overbid_pct: float
    reasoning: str
    budget_ceiling: int  # Was max_profitable_bid — now means max available funds
    sell_plan: (
        SellPlan | None
    )  # NEW: attached sell plan (if bid exceeds current budget)
    marginal_ep_gain: float  # NEW: EP improvement to best-11

    @property
    def max_profitable_bid(self) -> int:
        """Backward compat alias for api/routes/trading.py and other legacy callers."""
        return self.budget_ceiling
```

The legacy `calculate_bid()` still populates `budget_ceiling` with `predicted_future_value`. The `@property` alias ensures `api/routes/trading.py` (line 253: `recommendation.max_profitable_bid`) continues working without changes.

**Value ceiling replacement:**

Old: `max_profitable_bid = predicted_future_value` (never bid above what the player will be "worth" in market value terms).

New: No fixed market-value ceiling. Instead:

- **Budget ceiling**: `current_budget + sell_plan.total_recovery` (can't spend more than you can recover)
- **EP-proportional ceiling**: Higher EP gain justifies higher spend, using a concrete formula:
  `max_bid_fraction = min(0.8, 0.2 + (marginal_ep_gain / 50))` — this determines what fraction of the budget ceiling the bot is willing to spend. At EP gain = 30, that's `0.2 + 0.6 = 0.8` (80% of available budget). At EP gain = 5, that's `0.2 + 0.1 = 0.3` (30%). The floor of 0.2 ensures even marginal players can get a bid if cheap enough.
- **Diminishing returns**: The formula naturally caps at 80% — always reserves 20% of available budget as liquidity buffer.

The market value floor (Kickbase rule: can't bid below market value) remains unchanged.

#### Overbid calculation changes

Base overbid % is still learned from `BidLearner` (win-rate adaptive). But the **tier bonuses** change:

```python
tier_bonuses = {
    "must_have": 10.0,  # Was "anchor": 8.0
    "strong_upgrade": 6.0,  # Was "strong": 5.0
    "solid_upgrade": 3.0,  # Was "tactical": 2.0
    "marginal": 0.0,  # Was "opportunistic": 0.0
}
```

League competitive intelligence (from `ActivityFeedLearner`) and trend adjustments remain — they're orthogonal to EP vs value_score.

### Part 4: Outcome-Based Learning

#### New: Matchday Outcome Tracking

After each matchday, record what players actually scored vs what was predicted.

**New database table** (in `bid_learner.py`'s SQLite DB):

```sql
CREATE TABLE IF NOT EXISTS matchday_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id TEXT NOT NULL,
    player_position TEXT NOT NULL,       -- GK/DEF/MID/FWD (for position-level accuracy fallback)
    matchday_date TEXT NOT NULL,
    predicted_ep REAL NOT NULL,          -- EP from most recent analyze before this matchday
    actual_points REAL NOT NULL,         -- What they actually scored
    was_in_best_11 INTEGER DEFAULT 0,    -- Were they in our starting lineup?
    opponent_strength TEXT,              -- Easy/Medium/Hard
    purchase_price INTEGER,             -- What we paid (NULL if not recently bought)
    marginal_ep_gain_at_purchase REAL,  -- What we predicted when buying
    timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(player_id, matchday_date)
);

CREATE INDEX IF NOT EXISTS idx_matchday_player ON matchday_outcomes(player_id);
CREATE INDEX IF NOT EXISTS idx_matchday_position ON matchday_outcomes(player_position);
```

**New method: `BidLearner.record_matchday_outcome()`**

Called after each matchday with actual results for all squad players.

**New method: `BidLearner.get_ep_accuracy_factor()`**

Analyzes historical prediction accuracy and returns a multiplier (0.5 - 1.2):

```python
def get_ep_accuracy_factor(
    self,
    player_id: str | None = None,
    position: str | None = None,
    min_matchdays: int = 3,
) -> float:
    """
    Returns multiplier based on how accurate EP predictions have been.

    - 1.0 = predictions match reality
    - < 1.0 = we tend to overpredict (bid more conservatively)
    - > 1.0 = we tend to underpredict (bid more aggressively)

    Can be player-specific or position-level fallback.
    """
```

Logic:

1. Query `matchday_outcomes` for player (or position if player has \< `min_matchdays`)
1. Calculate `accuracy = avg(actual_points) / avg(predicted_ep)`
1. Clamp to \[0.5, 1.0\] range — **capped at 1.0** to prevent tier inflation (an accuracy factor > 1.0 could artificially promote a `strong_upgrade` to `must_have`, causing overbidding). If we underpredict, the raw EP scores should be improved in the scorer, not compensated in bidding.
1. Default to 1.0 if insufficient data

#### Integration: Accuracy factor in bidding

In `SmartBidding.calculate_bid()`, the EP accuracy factor modifies bid aggressiveness:

```python
# Get EP accuracy factor from learner
ep_accuracy = 1.0
if self.bid_learner:
    ep_accuracy = self.bid_learner.get_ep_accuracy_factor(
        player_id=player_id,
        position=position,
    )

# Adjust marginal EP gain by accuracy factor
adjusted_ep_gain = marginal_ep_gain * ep_accuracy
# Use adjusted_ep_gain for tier classification
```

If we historically overpredict EP for midfielders (accuracy = 0.7), we'll bid less aggressively for midfielders until predictions improve.

#### New method: `BidLearner.get_ep_recommended_overbid()`

The existing `get_recommended_overbid()` stays unchanged for legacy callers (it takes `value_score` and `predicted_future_value`). A new parallel method is added:

```python
def get_ep_recommended_overbid(
    self,
    asking_price: int,
    marginal_ep_gain: float,
    market_value: int,
    budget_ceiling: int,
) -> dict:
    """
    EP-aware overbid recommendation.
    Returns: {"recommended_overbid_pct": float, "reason": str}
    """
```

This method:

1. Queries historical auction win/loss data (same as existing)
1. Uses `marginal_ep_gain` instead of `value_score` for minimum overbid floors
1. Does NOT apply a predicted_future_value ceiling (replaced by budget ceiling)
1. Adds an **outcome quality** factor from won auctions

#### Outcome quality in overbid learning

```python
# In get_ep_recommended_overbid():
# Factor 1: Win rate (existing logic)
# Factor 2: Were our wins worth it?
outcome_quality = self._get_won_player_outcome_quality()
# outcome_quality > 1.0 = players we won are outperforming predictions → bid more
# outcome_quality < 1.0 = players we won are underperforming → bid less
```

**`_get_won_player_outcome_quality()`** joins `auction_outcomes` (wins) with `matchday_outcomes` to answer: "For players we won in auctions, how did their actual matchday points compare to predicted EP?" Returns a value clamped to \[0.5, 1.2\]. Unlike the EP accuracy factor (capped at 1.0 to prevent tier inflation), outcome quality is allowed to exceed 1.0 because it only affects overbid %, not tier classification — a modest +20% to overbid percentage for proven-good picks is intentional and bounded.

#### Trigger: When matchday outcomes are recorded

Outcome recording happens **automatically during `analyze`**. The `analyze` command already fetches squad data and performance. The new flow adds:

```python
# In cli.py analyze command, after EP scoring:
# 1. Check if any matchdays completed since last recording
# 2. For each squad player, compare last predicted EP vs actual matchday points
# 3. Call bid_learner.record_matchday_outcome() for each
```

This is lightweight — it only records outcomes for players currently in the squad, using performance data already fetched by the EP pipeline. No separate command needed. The `daemon` command also triggers this as part of its trading sessions.

**Snapshot timing for `predicted_ep`:** The predicted EP stored in `matchday_outcomes` is the EP score computed during the `analyze` run **immediately preceding the matchday** (the most recent prediction before the game). This is stored in a transient `last_predicted_ep` cache (dict in BidLearner, keyed by player_id, overwritten each analyze run). When matchday results come in, we use this cached value. If no recent prediction exists (e.g., player was bought without running analyze), we use the EP at time of purchase from the scoring pipeline.

### Part 5: Changes to `analyze` Display

The compact display buy table gains new columns and loses old ones:

**Buy table columns (new):**

| Column    | Description                               |
| --------- | ----------------------------------------- |
| Player    | Name + position                           |
| EP        | Expected points score                     |
| EP Gain   | Marginal gain to best-11 (the key number) |
| Price     | Asking price                              |
| Bid       | Smart bid recommendation                  |
| Sell Plan | Who to sell to fund (if needed)           |
| Net Cost  | Price - sell recovery                     |

**Sell table changes:**

- Sorted by expendability (low EP + not in best-11)
- Shows: who on the market could replace them, EP impact of selling

**Value score** remains visible as a secondary column for users who want to see market-value signals, but is no longer the sort key or primary recommendation driver.

## File Changes

### Changed files

| File                  | Change                                                                                                                                                                                                                                                   |
| --------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `cli.py`              | Remove `--detailed` mode and all associated flags (lines 55-82 flags, lines 168-513 detailed branch). Move learning update into compact flow. Add matchday outcome recording to analyze flow.                                                            |
| `bidding_strategy.py` | Add new `calculate_ep_bid()` method (EP-based). Update `BidRecommendation` dataclass (rename `max_profitable_bid` → `budget_ceiling`, add `sell_plan` and `marginal_ep_gain` fields). Keep `calculate_bid()` and `calculate_batch_bids()` unchanged.     |
| `bid_learner.py`      | Add `matchday_outcomes` table with position column and indexes. Add `record_matchday_outcome()`, `get_ep_accuracy_factor()`, `get_ep_recommended_overbid()`, `_get_won_player_outcome_quality()`. Keep `get_recommended_overbid()` unchanged for legacy. |
| `scoring/decision.py` | Add `calculate_marginal_ep()` method to `DecisionEngine`. Add `build_sell_plan()` method. (Requires scoring pipeline to be implemented first.)                                                                                                           |
| `scoring/models.py`   | Add `MarginalEPResult`, `SellPlan`, `SellPlanEntry` dataclasses. (Requires scoring pipeline to be implemented first.)                                                                                                                                    |
| `compact_display.py`  | Update buy table to show EP Gain, Sell Plan, Net Cost columns. Update sell table sort by EP-based expendability.                                                                                                                                         |
| `trader.py`           | Wire EP pipeline into `display_compact_action_plan()`. Pass `marginal_ep_gain` and `sell_plan` to `SmartBidding.calculate_ep_bid()`.                                                                                                                     |

### Unchanged files

- `api.py`, `kickbase_client.py` — API layer
- `activity_feed_learner.py` — League intelligence (demand score, competitor stats) still used as-is
- `bid_monitor.py` — Monitors pending bids, feeds outcomes to learner (unchanged interface)
- `formation.py` — `select_best_eleven()` takes `{id: ep}` dict, already works
- `expected_points.py` — Deprecated by scoring pipeline, but not changed here
- `matchup_analyzer.py` — Used by scorer, no changes

### Modules losing their `cli.py` caller (not deleted)

- `enhanced_analyzer.py` — Position landscape, squad balance, predictions (value-focused)
- `portfolio_analyzer.py` — Portfolio diversification metrics
- `squad_optimizer.py` — Budget optimization (absorbed into DecisionEngine)

## Data Flow

### Analyze command (new flow)

```
analyze command
  |
  v
DataCollector -> Scorer -> DecisionEngine
  |                            |
  |                     calculate_marginal_ep() for each market player
  |                     build_sell_plan() where needed
  |                            |
  v                            v
SmartBidding.calculate_ep_bid(  BuyRecommendation
  marginal_ep_gain,              (with sell_plan attached)
  sell_plan,                      |
  ep_accuracy_factor)             v
  |                         CompactDisplay.display_action_plan()
  v
BidRecommendation
```

### Outcome learning flow (after each matchday)

```
Matchday completes
  |
  v
For each squad player:
  actual_points = api.get_player_performance()  # real matchday points
  predicted_ep = last analysis EP
  |
  v
BidLearner.record_matchday_outcome(
    player_id, matchday_date,
    predicted_ep, actual_points,
    was_in_best_11, opponent_strength,
    purchase_price, marginal_ep_gain_at_purchase
)
  |
  v
Next analyze run:
  ep_accuracy_factor = BidLearner.get_ep_accuracy_factor()
  # Feeds into SmartBidding to calibrate aggressiveness
```

### Bidding flow with sell plan

```
Market player has EP gain = 25 (must_have tier)
  Price = €30M, Budget = €5M
  |
  v
DecisionEngine.build_sell_plan():
  Displaced player: €18M sell value
  Bench expendable: €15M sell value
  Total recovery: €33M
  Net budget after buy + sells: €8M (viable!)
  |
  v
SmartBidding.calculate_ep_bid():
  Budget ceiling = €5M + €33M = €38M
  Tier: must_have → aggressive overbid
  EP accuracy factor: 0.9 (slight overpredictor)
  Adjusted EP gain: 25 * 0.9 = 22.5 → still must_have
  EP-proportional max: 0.2 + (22.5/50) = 0.65 → 65% of €38M = €24.7M
  Asking price = €30M > €24.7M → bid capped at asking + modest overbid
  |
  v
BidRecommendation:
  bid = €31.5M, sell_plan attached
  Display: "Buy X €31.5M | Sell Y (€18M) + Z (€15M) | Net: -€1.5M → +€1.5M after sells, +25 EP"
```

## Testing Strategy

- **Unit: `SmartBidding`** — Test EP-based tier classification, sell plan budget calculations, EP accuracy factor application, no-bid when marginal gain = 0
- **Unit: `calculate_marginal_ep()`** — Test with various squad compositions: player enters best-11, player doesn't crack best-11, position constraint edge cases
- **Unit: `build_sell_plan()`** — Test viable/non-viable plans, protected players (position minimums), ordering by expendability
- **Unit: `BidLearner` outcome tracking** — Record outcomes, verify accuracy factor calculation, test with insufficient data (should return 1.0)
- **Integration: Full analyze flow** — Mock API data through EP pipeline → bidding → display, verify buy recommendations sorted by EP gain
- **Edge cases:** Empty squad, squad at position minimums, budget exactly 0, all bench players protected
- **Regression:** Compare EP-based bids vs value-score-based bids for same player set — verify they differ meaningfully and EP-based bids favor high-scoring players over high-market-value players
