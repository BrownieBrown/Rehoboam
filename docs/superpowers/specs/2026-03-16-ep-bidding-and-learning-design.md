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
    expected_sell_value: int  # Market value (conservative estimate)
    player_ep: float  # EP of player being sold
    is_in_best_11: bool  # Warning flag if selling a starter
```

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

`SmartBidding.calculate_bid()` signature changes:

```python
def calculate_bid(
    self,
    asking_price: int,
    market_value: int,
    expected_points: float,          # NEW: player's EP score
    marginal_ep_gain: float,         # NEW: how much this improves best-11
    confidence: float,
    current_budget: int,             # NEW: available budget
    sell_plan: SellPlan | None,      # NEW: sell plan if going negative
    player_id: str | None = None,
    trend_change_pct: float | None = None,
    # REMOVED: value_score, is_replacement, replacement_sell_value,
    #          predicted_future_value, average_points, is_long_term_hold, roster_impact
) -> BidRecommendation:
```

**Value ceiling replacement:**

Old: `max_profitable_bid = predicted_future_value` (never bid above what the player will be "worth" in market value terms).

New: No fixed market-value ceiling. Instead:

- **Budget ceiling**: `current_budget + sell_plan.total_recovery` (can't spend more than you can recover)
- **EP-proportional ceiling**: Higher EP gain justifies higher spend. A player adding 30 EP to best-11 can command more budget than one adding 5 EP.
- **Diminishing returns**: As bid approaches total available funds, reduce aggressiveness. Don't go all-in if it leaves the squad with no liquidity for emergencies.

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
    matchday_date TEXT NOT NULL,
    predicted_ep REAL NOT NULL,          -- EP at time of purchase/last analysis
    actual_points REAL NOT NULL,         -- What they actually scored
    was_in_best_11 INTEGER DEFAULT 0,    -- Were they in our starting lineup?
    opponent_strength TEXT,              -- Easy/Medium/Hard
    purchase_price INTEGER,             -- What we paid (NULL if not recently bought)
    marginal_ep_gain_at_purchase REAL,  -- What we predicted when buying
    timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(player_id, matchday_date)
);
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
1. Clamp to \[0.5, 1.2\] range
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

#### Enhanced win-rate learning

The existing `BidLearner.get_recommended_overbid()` stays but gets a new dimension:

Current: adjusts overbid % based on win/loss rate.
New: also considers **outcome quality** of won auctions:

```python
# In get_recommended_overbid():
# Factor 1: Win rate (existing)
# Factor 2: Were our wins worth it?
outcome_quality = self._get_won_player_outcome_quality()
# outcome_quality > 1.0 = players we won are outperforming predictions → bid more
# outcome_quality < 1.0 = players we won are underperforming → bid less
```

**`_get_won_player_outcome_quality()`** joins `auction_outcomes` (wins) with `matchday_outcomes` to answer: "For players we won in auctions, how did their actual matchday points compare to predicted EP?"

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

| File                  | Change                                                                                                                                                                                         |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `cli.py`              | Remove `--detailed` mode and all associated flags (lines 55-82 flags, lines 168-513 detailed branch). Move learning update into compact flow.                                                  |
| `bidding_strategy.py` | Rewrite `calculate_bid()` to use `marginal_ep_gain` and `sell_plan`. Replace value_score tiers with EP gain tiers. Remove `predicted_future_value` ceiling, add budget-aware ceiling.          |
| `bid_learner.py`      | Add `matchday_outcomes` table. Add `record_matchday_outcome()`, `get_ep_accuracy_factor()`, `_get_won_player_outcome_quality()`. Update `get_recommended_overbid()` to factor outcome quality. |
| `scoring/decision.py` | Add `calculate_marginal_ep()` method to `DecisionEngine`. Add `build_sell_plan()` method.                                                                                                      |
| `scoring/models.py`   | Add `MarginalEPResult`, `SellPlan`, `SellPlanEntry` dataclasses.                                                                                                                               |
| `compact_display.py`  | Update buy table to show EP Gain, Sell Plan, Net Cost columns. Update sell table sort by EP-based expendability.                                                                               |
| `trader.py`           | Wire EP pipeline into `display_compact_action_plan()`. Pass `marginal_ep_gain` and `sell_plan` to `SmartBidding`.                                                                              |

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
SmartBidding.calculate_bid(    BuyRecommendation
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
SmartBidding.calculate_bid():
  Available budget = €5M + €33M = €38M
  Tier: must_have → aggressive overbid
  EP accuracy factor: 0.9 (slight overpredictor)
  Adjusted EP gain: 25 * 0.9 = 22.5 → still must_have
  |
  v
BidRecommendation:
  bid = €32M, sell_plan attached
  Display: "Buy X €32M | Sell Y (€18M) + Z (€15M) | Net: +€1M, +25 EP"
```

## Testing Strategy

- **Unit: `SmartBidding`** — Test EP-based tier classification, sell plan budget calculations, EP accuracy factor application, no-bid when marginal gain = 0
- **Unit: `calculate_marginal_ep()`** — Test with various squad compositions: player enters best-11, player doesn't crack best-11, position constraint edge cases
- **Unit: `build_sell_plan()`** — Test viable/non-viable plans, protected players (position minimums), ordering by expendability
- **Unit: `BidLearner` outcome tracking** — Record outcomes, verify accuracy factor calculation, test with insufficient data (should return 1.0)
- **Integration: Full analyze flow** — Mock API data through EP pipeline → bidding → display, verify buy recommendations sorted by EP gain
- **Edge cases:** Empty squad, squad at position minimums, budget exactly 0, all bench players protected
