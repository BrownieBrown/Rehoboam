# Bid Management Features

## Feature 1: Active Bid Re-evaluation & Cancellation

### Problem

The bot wasn't checking existing bids to see if they're still good value. Players can:

- Get injured after you bid
- Start falling in value
- No longer meet profit criteria

### Solution

New `BidEvaluator` module that:

1. Fetches all your active bids
1. Re-analyzes each bid using current data
1. Recommends KEEP or CANCEL
1. Automatically cancels bad bids

### When Bids Are Canceled

**For Profit Flips:**

- ❌ Player is injured (status != 0)
- ❌ Player is in falling trend
- ❌ Bid >15% over market value
- ❌ Profit potential \<10%

**For Lineup Improvements:**

- ❌ Player is injured
- ❌ Bid >20% over market value (unless elite)
- ✅ Keep if high performer (>50 pts/game)

### API Endpoint

**Cancel Bid:**

```
DELETE /v4/leagues/{leagueId}/market/{playerId}
```

Implemented in:

- `kickbase_client.py`: `cancel_offer()`
- `api.py`: `cancel_bid()`
- `bid_evaluator.py`: Full evaluation logic

### Usage

**Automatic (during auto trading):**

```bash
rehoboam auto --max-trades 3
```

The bot will:

1. Check all active bids
1. Display evaluation
1. Cancel bad bids automatically
1. Then proceed with new opportunities

**Manual (analyze command):**

```bash
rehoboam analyze
```

Shows bid evaluation but doesn't cancel (view-only)

### Example Output

```
📊 Evaluating 3 active bids...

Bid Evaluation Summary:
  Keep: 1
  Cancel: 2

⚠️  Recommend canceling 2 bid(s):

  ❌ Paul Nebel
     Your bid: €14,037,128
     Market value: €14,000,000
     Reason: Falling trend (-8.2%) - not good for flips

  ❌ Max Rosenfelder
     Your bid: €3,500,000
     Market value: €3,200,000
     Reason: Player is injured (status: 4)

✓ Keep these bids:

  ✓ Lennard Maloney
     Your bid: €2,325,608
     Market value: €2,114,189
     Good flip potential: 20.0% expected appreciation

Canceling bid on Paul Nebel...
✓ Bid canceled on Paul Nebel

Canceling bid on Max Rosenfelder...
✓ Bid canceled on Max Rosenfelder

Canceled 2 bid(s) that no longer make sense
```

______________________________________________________________________

## Feature 2: Aggressive Bidding for Elite Players

### Problem

For exceptional players you want to keep all season, the bot was too conservative:

- Max 15% overbid on asking price
- Wouldn't bid near market value
- Treated all players the same

But for elite long-term holds, you should be willing to pay MORE to secure them.

### Solution

Enhanced `SmartBidding` with elite player detection:

- **Elite threshold**: Players with ≥70 average points/game
- **Elite max overbid**: Up to 30% over asking price (vs 15% normal)
- **Only for lineup improvements** (not profit flips)

### How It Works

**Normal Player (50 pts/game):**

```
Asking price: €10M
Smart bid: €10.5M (+5%)
Max bid: €11.5M (+15%)
```

**Elite Player (75 pts/game, long-term hold):**

```
Asking price: €10M
Smart bid: €12M (+20%)   ← Much more aggressive!
Max bid: €13M (+30%)     ← Higher ceiling
Reason: 🌟 ELITE PLAYER - Long-term hold
```

### When Elite Bidding Triggers

Conditions (ALL must be true):

1. ✅ `is_long_term_hold = True` (lineup improvement, not flip)
1. ✅ `average_points >= 70` (elite performance)
1. ✅ High value score (>70) or starter quality

**Elite players bid at +20-30% over asking:**

- Starters with 70+ pts/game
- High confidence plays
- Players you'll keep all season

**Normal players bid at +5-15% over asking:**

- Everyone else
- Profit flips (always conservative)

### Parameters

**In `bidding_strategy.py`:**

```python
SmartBidding(
    default_overbid_pct=5.0,  # Normal: +5%
    max_overbid_pct=15.0,  # Normal max: +15%
    high_value_threshold=70.0,  # Value score for +5% more
    elite_player_threshold=70.0,  # Avg pts for elite status
    elite_max_overbid_pct=30.0,  # Elite max: +30%  ← NEW!
)
```

### Example Scenarios

**Scenario 1: Elite Striker (Long-term)**

```
Player: Robert Lewandowski
Position: Forward
Average points: 85 pts/game
Market value: €25M
Asking price: €24M

Normal bid: €25.2M (+5%)
Elite bid: €28.8M (+20%) 🌟
Reason: ELITE PLAYER - Long-term hold | Exceptional value | aggressive +20% overbid
```

**Scenario 2: Same Player (Profit Flip)**

```
Same player, but for_profit=True:

Bid: €25.2M (+5%)
Reason: Exceptional value | competitive +5% overbid

Note: Elite bidding DISABLED for flips - always conservative!
```

**Scenario 3: Good But Not Elite**

```
Player: Average Midfielder
Average points: 55 pts/game
Market value: €8M
Asking price: €8M

Normal bid: €8.4M (+5%)
Elite bid: Not triggered (< 70 pts threshold)
```

### Configuration in Trade Optimizer

The trade optimizer automatically marks trades as `is_long_term_hold=True` for lineup improvements, so elite bidding will trigger for high-performers.

### Safety Limits

Even with elite bidding:

- ✅ Never exceeds predicted future value (value ceiling)
- ✅ Max 30% overbid (vs 15% normal)
- ✅ Only for healthy players (injured = canceled)
- ✅ Only for rising/stable trends (falling = canceled)

### Benefits

**Before:**

```
Elite player asking €20M, market value €22M
Bot bids: €21M (+5%)
Result: Outbid by others → lose elite player
```

**After:**

```
Elite player asking €20M, market value €22M
Bot bids: €24M (+20%)
Result: Win elite player → improve lineup significantly
Over 1 season: Player gains 300+ points → worth it!
```

______________________________________________________________________

## Integration

Both features work together:

1. **Start of session**: Re-evaluate active bids, cancel bad ones
1. **Find opportunities**: Look for profit flips + lineup upgrades
1. **Calculate bids**: Use elite bidding for exceptional players
1. **Execute trades**: Place bids with appropriate aggressiveness
1. **Next session**: Re-evaluate again, cancel if situation changed

## Files Modified

1. **`rehoboam/kickbase_client.py`**

   - Added `cancel_offer()` method (DELETE endpoint)

1. **`rehoboam/api.py`**

   - Added `cancel_bid()` wrapper

1. **`rehoboam/bid_evaluator.py`** (NEW)

   - `evaluate_active_bids()` - Re-analyze all bids
   - `display_bid_evaluations()` - Show results
   - `cancel_bad_bids()` - Execute cancellations

1. **`rehoboam/auto_trader.py`**

   - Integrated bid evaluation at start of profit session
   - Cancels bad bids before finding new opportunities

1. **`rehoboam/bidding_strategy.py`**

   - Added `elite_player_threshold` parameter
   - Added `elite_max_overbid_pct` parameter
   - Added `is_elite` detection in `calculate_bid()`
   - Updated `_calculate_overbid_percentage()` for elite players
   - Updated reasoning to show elite status

## Testing

**Test bid re-evaluation:**

```bash
rehoboam auto --dry-run
# Watch for bid evaluation section
```

**Test elite bidding:**

```bash
rehoboam analyze
# Look for "🌟 ELITE PLAYER" in lineup trade recommendations
```

## Configuration

**Adjust elite threshold:**

```python
# In trade_optimizer.py or auto_trader.py
bidder = SmartBidding(
    elite_player_threshold=75.0,  # Stricter: only 75+ pts players
    elite_max_overbid_pct=25.0,  # More conservative: max +25%
)
```

**Disable elite bidding:**

```python
bidder = SmartBidding(
    elite_max_overbid_pct=15.0,  # Same as normal max
)
```

______________________________________________________________________

**Summary:**

- ✅ Bot now manages bids actively (cancels bad ones)
- ✅ Bot bids aggressively for elite long-term holds
- ✅ Both features protect your budget and maximize value
- ✅ Fully automated when using `rehoboam auto`
