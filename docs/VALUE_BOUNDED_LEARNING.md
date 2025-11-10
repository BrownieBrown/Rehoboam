# Value-Bounded Learning System

## Overview

The bot learns from auction outcomes to improve bidding strategy **while NEVER overpaying beyond predicted player value**. This prevents copying irrational human behavior.

## Core Principle

```
NEVER bid above predicted future value, even if competitors do.
Better to lose an auction than overpay for a player.
```

## Why This Matters

### The Yan Diomande Example

**What Happened:**

- Asking Price: €12.4M
- Bot's bid: €14.1M (+13%)
- Winner's bid: €17M (+36%)
- Bot saved: €2.9M by not matching

**Why We Don't Copy the Winner:**

- Winner might be irrational (fan of the player)
- Winner might have different strategy (short-term flip)
- Winner might simply be wrong about player value
- **We only know they won, not if they profited**

## How It Works

### 1. Record All Auction Outcomes

Every bid (win or lose) is recorded in SQLite database:

```python
@dataclass
class AuctionOutcome:
    player_id: str
    player_name: str
    our_bid: int
    asking_price: int
    our_overbid_pct: float
    won: bool
    winning_bid: Optional[int] = None  # If we lost, who won
    winning_overbid_pct: Optional[float] = None
    winner_user_id: Optional[str] = None
    timestamp: float
    player_value_score: Optional[float] = None
    market_value: Optional[int] = None
```

**What We Track:**

- ✅ Our bid amount and overbid %
- ✅ Whether we won or lost
- ✅ Winner's bid amount (if we lost)
- ✅ Winner's user ID
- ✅ Player's value score at time of bid
- ✅ Timestamp for trend analysis

### 2. Calculate Value Ceiling

Before bidding, calculate maximum we're willing to pay:

```python
# Based on value score (0-100)
growth_factor = 1.0 + (value_score / 1000)
predicted_future_value = market_value * growth_factor

# Examples:
# Value score 60 → 6% growth → €10M becomes €10.6M
# Value score 80 → 8% growth → €10M becomes €10.8M
# Value score 100 → 10% growth → €10M becomes €11M
```

**This is our ABSOLUTE MAXIMUM. We never exceed it.**

### 3. Learn Competitive Overbid (Within Ceiling)

Analyze past auctions to find optimal overbid:

```python
def get_recommended_overbid(
    asking_price: int,
    value_score: float,
    market_value: int,
    predicted_future_value: int  # VALUE CEILING
) -> dict:
    # Calculate max overbid allowed
    max_overbid_pct = ((predicted_future_value - asking_price) / asking_price) * 100

    # Learn from past auctions
    if we have auction data:
        if we've been losing:
            recommended = avg_losing_overbid + 5%
        elif we've been winning:
            recommended = avg_winning_overbid
        if competitor data available:
            recommended = avg_competitor_overbid + 2%
    else:
        recommended = 8%  # Conservative default

    # CRITICAL: Apply value ceiling
    recommended = min(recommended, max_overbid_pct)

    return {
        "recommended_overbid_pct": recommended,
        "max_bid": predicted_future_value,
        "value_ceiling_applied": recommended < learned_overbid
    }
```

### 4. Bid with Ceiling Protection

```python
# SmartBidding uses learned overbid BUT respects ceiling
bid = SmartBidding(bid_learner=learner).calculate_bid(
    asking_price=12_000_000,
    market_value=13_000_000,
    value_score=70.0,
    predicted_future_value=14_500_000,  # MAX we'll pay
)

# If learned overbid would exceed ceiling:
# - Bid is capped at predicted_future_value
# - Reasoning shows "⚠️ AT VALUE CEILING"
# - Bot accepts it might lose the auction
```

## Real Examples

### Example 1: High Value Player (Bid Aggressively)

```
Asking Price: €10M
Market Value: €11M
Value Score: 85/100
Predicted Value: €13M (we think he'll grow)

Max overbid allowed: (€13M - €10M) / €10M = 30%
Learned overbid: 8% (conservative, insufficient data)
Final bid: €10.8M

✅ RESULT: Bid €10.8M aggressively
   Still €2.2M below our value ceiling
   Room to compete if needed
```

### Example 2: Player at Value Ceiling (Skip)

```
Asking Price: €15M
Market Value: €14.5M
Value Score: 55/100
Predicted Value: €15.5M (not much upside)

Max overbid allowed: (€15.5M - €15M) / €15M = 3.3%
Learned overbid: 8%
Final bid: €15.5M (capped)

⚠️ RESULT: Only 3.3% overbid possible
   Competitors will likely bid more
   RECOMMENDATION: SKIP
```

### Example 3: Yan-like Scenario (Accept Loss)

```
Asking Price: €12M
Market Value: €13M
Value Score: 70/100
Predicted Value: €14.5M

Our max bid: €14.5M (20.8% overbid)
Competitor bids: €16.3M (36% overbid)

⚠️ RESULT: We lose by €1.8M
   But we saved €1.8M by not matching
   Winner overpaid beyond rational value
```

## What We Learn From

### From Our Wins (Efficiency)

```python
# When we win, check if we overbid more than needed
if won and our_overbid > 10%:
    # Could we have won with 8%?
    # Adjust down for next similar auction
```

**Goal:** Win auctions at minimum necessary overbid

### From Our Losses (Validation)

```python
# When we lose, track outcome weeks later
validation = track_outcome_validation(player_id, current_market_value)

if current_market_value > winning_bid:
    # Winner got a good deal
    # Our prediction was too conservative
    # Adjust value model upward
elif current_market_value < our_bid:
    # Winner overpaid
    # We were right to skip
    # Keep conservative approach
```

**Goal:** Validate if our value predictions are accurate

### From Competitor Patterns

```python
competitor = analyze_competitor(user_id="1821396")
# {
#   "times_beaten_us": 1,
#   "avg_overbid": 36.2%,
#   "message": "This user typically overbids 36.2%"
# }
```

**Goal:** Identify irrational bidders to avoid competing with them

## Integration Points

### 1. BidMonitor (Automatic Recording)

```python
# When bid wins
bid_status.status = "won"
self._record_auction_outcome(league, player_id, won=True)

# When bid loses
bid_status.status = "lost"
self._record_auction_outcome(league, player_id, won=False)
# Fetches winner's bid from API automatically
```

### 2. SmartBidding (Uses Learning)

```python
# Initialize with learner
learner = BidLearner()
bidding = SmartBidding(bid_learner=learner)

# Automatically uses learned patterns with value ceiling
bid = bidding.calculate_bid(
    asking_price=asking_price,
    market_value=market_value,
    value_score=value_score,
    confidence=confidence,
    predicted_future_value=predicted_future_value,  # CEILING
)
```

### 3. Trader (Calculates Ceiling)

```python
# Trader calculates predicted value from analysis
analysis = analyzer.analyze_market_player(player, ...)
predicted_value = analysis.market_value * (1 + analysis.value_score / 1000)

# Passes to bidding strategy
bid = bidding.calculate_bid(..., predicted_future_value=predicted_value)
```

## Database Schema

```sql
CREATE TABLE auction_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id TEXT NOT NULL,
    player_name TEXT NOT NULL,
    our_bid INTEGER NOT NULL,
    asking_price INTEGER NOT NULL,
    our_overbid_pct REAL NOT NULL,
    won INTEGER NOT NULL,
    winning_bid INTEGER,
    winning_overbid_pct REAL,
    winner_user_id TEXT,
    timestamp REAL NOT NULL,
    player_value_score REAL,
    market_value INTEGER
);

CREATE INDEX idx_player_id ON auction_outcomes(player_id);
CREATE INDEX idx_timestamp ON auction_outcomes(timestamp);
```

**Location:** `logs/bid_learning.db`

## Learning Statistics

```python
stats = learner.get_statistics()
# {
#   "total_auctions": 10,
#   "wins": 6,
#   "losses": 4,
#   "win_rate": 60.0,
#   "avg_winning_overbid": 8.5,
#   "avg_losing_overbid": 12.3,
#   "avg_value_score_wins": 75.2,
#   "avg_value_score_losses": 68.1
# }
```

**Insights:**

- Win rate shows auction competitiveness
- Avg winning overbid shows our efficiency
- Avg losing overbid shows where we're not competitive enough
- Value scores show if we're picking the right players

## Advantages Over Blind Learning

### ❌ Blind Learning (BAD)

```python
# Just copy what winners did
if someone_won_with_36_percent:
    next_bid = asking_price * 1.36  # DANGEROUS!
```

**Problems:**

- Copies irrational behavior
- No profit validation
- Overpays systematically
- Loses money long-term

### ✅ Value-Bounded Learning (GOOD)

```python
# Learn competitiveness, respect value
if someone_won_with_36_percent:
    if predicted_value allows 36%:
        bid = asking_price * 1.36  # OK, worth it
    else:
        accept_loss()  # Skip, they overpaid
```

**Benefits:**

- Only bids when profitable
- Validates winner's decisions
- Protects against irrationality
- Profitable long-term

## Testing

Run `python test_learning.py` to see examples:

```bash
$ python test_learning.py

TEST CASE 1: High Value Player
  Recommended bid: €10,800,000 (8.0%)
  ✅ Well below value ceiling

TEST CASE 2: Player Above Ceiling
  Recommended bid: €15,500,000 (3.3%)
  ⚠️ AT VALUE CEILING - likely to lose

TEST CASE 3: Yan-like Scenario
  Our max bid: €12,960,000 (8%)
  Competitor: €16,320,000 (36%)
  ⚠️ We lose but saved €3.4M
```

## Manual Recording

To manually record auction outcomes:

```python
from rehoboam.bid_learner import BidLearner, AuctionOutcome

learner = BidLearner()

outcome = AuctionOutcome(
    player_id="10771",
    player_name="Yan Diomande",
    our_bid=14097338,
    asking_price=12477338,
    our_overbid_pct=13.0,
    won=False,
    winning_bid=17000000,
    winning_overbid_pct=36.2,
    winner_user_id="1821396",
    timestamp=datetime.now().timestamp(),
)

learner.record_outcome(outcome)
```

Or run: `python record_yan_loss.py`

## Future Enhancements

### 1. Outcome Validation (Weeks Later)

```python
# 2-4 weeks after losing Yan
validation = learner.track_outcome_validation("10771", current_market_value)

if validation["winner_profit_pct"] < 0:
    # Winner lost money - we were right to skip
    # Keep conservative value predictions
elif validation["winner_profit_pct"] > 20:
    # Winner profited significantly
    # Our value model was too conservative
    # Adjust growth_factor upward for similar players
```

### 2. Player Similarity Clustering

```python
# Group players by position, team, points, value
# Learn different overbid patterns for each cluster

if player is "defensive midfielder on top team":
    use defensive_mid_patterns
elif player is "striker on relegation team":
    use relegation_striker_patterns
```

### 3. Time-Based Learning

```python
# Auction competitiveness varies by day/time
# Weekend auctions might be more competitive

if is_weekend:
    overbid *= 1.2  # 20% more competitive
```

### 4. Budget-Aware Bidding

```python
# When low on budget, be more selective
# Increase value ceiling threshold

if budget < 5_000_000:
    only_bid_if value_score > 80
```

## Summary

The value-bounded learning system gives you:

- ✅ **Learning** from auction outcomes (competitiveness)
- ✅ **Protection** from irrational overbidding (value ceiling)
- ✅ **Validation** of predictions (outcome tracking)
- ✅ **Profitability** by accepting some losses
- ✅ **Long-term success** over short-term wins

**Bottom line:** The bot gets smarter without getting reckless.
