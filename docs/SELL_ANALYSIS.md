# Comprehensive Sell Analysis System

## Overview

The bot now analyzes your squad with the **same criteria** as buying, plus additional sell-specific signals like peak detection and profit tracking. This prevents missing optimal selling windows.

## The Danel Sinani Problem

### What Happened

```
Bought: €6M
Peak: €17M (+183% profit!) 🏆
Now: €14M (-18% from peak) ⚠️

Problem: You missed the selling window!
Lost: €3M by not selling at peak
```

### What the Bot Does

```
Week 1-2: Value rising → HOLD (let it grow)
Week 3: Peaks at €17M → DETECTED ✅
Week 4: Starts declining → SELL NOW! 🔴
Result: Lock in maximum profit automatically
```

## Sell Signals (Comprehensive)

The bot uses **6 sell signals** (same criteria as buying + profit tracking):

### 1. **Peaked and Declining** ⭐ PRIORITY

```python
if player peaked AND declined >5% for >7 days:
    SELL
    # Lock in profits before further decline
```

**Example:**

- Sinani peaked at €17M
- Now €14M (-18% over 11 days)
- **SELL**: Peaked and declining -17.6% over 11d

### 2. **Profit Target Reached**

```python
if profit >= min_sell_profit_pct:  # Default: 15%
    SELL
```

**Example:**

- Bought: €10M
- Now: €12M (+20%)
- **SELL**: Profit target: +20.0% gain

### 3. **Stop-Loss Triggered**

```python
if loss >= max_loss_pct:  # Default: -15%
    SELL
```

**Example:**

- Bought: €10M
- Now: €8.5M (-15%)
- **SELL**: Stop-loss: -15.0% loss

### 4. **Underperforming (Low Value Score)**

```python
if value_score < 30:
    SELL
```

**Example:**

- Value score: 25/100
- Pts/M€: 2.1 (poor)
- **SELL**: Underperforming: 25.0/100

### 5. **Difficult Schedule Ahead** ⚡ SOS!

```python
if SOS = "Very Difficult" AND profit > 5%:
    SELL BEFORE fixtures
    # Value will drop after tough games
```

**Example:**

- Current: €12M (+20% profit)
- Next 3: vs Bayern, Leipzig, Leverkusen
- **SELL**: Sell before difficult fixtures (Very Difficult)

This is **CRITICAL** - don't hold through brutal schedules!

### 6. **Falling Trend + At Profit**

```python
if trend = "falling" AND profit > 5%:
    SELL
    # Lock in profit before it disappears
```

**Example:**

- 14-day trend: -12%
- Current profit: +8%
- **SELL**: Falling trend (-12.0%) - lock in profit

## Visual Sell Table

When you run `rehoboam analyze`, you'll see:

```
🔴 Players You Should Consider Selling

Player          Position  Purchase    Current     Peak         Profit/Loss  Value  Trend        Recommendation  Reason
                                                                            Score
──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
Danel Sinani    FW        €6,000,000  €14,000,000 €17,000,000  +133.3%     72.0   ↘ -12.5%     SELL            Peaked and declining -17.6% over 11d
                                                  -17.6%

Tim Lemperle    MF        €8,500,000  €9,200,000  €9,800,000   +8.2%       55.0   → +2.1%      SELL            Sell before difficult fixtures (Very Difficult)
                                                  -6.1%                                                          | 🔥🔥🔥 SOS: Very Difficult next 3 (-10 pts)

Jonas Hofmann   MF        €12,000,000 €10,500,000 €13,500,000  -12.5%      68.0   ↘ -8.0%      SELL            Stop-loss: -12.5% loss
                                                  -22.2%
```

**Column Guide:**

- **Purchase**: What you paid
- **Current**: Current market value
- **Peak**: Highest value reached (+ decline %)
- **Profit/Loss**: % gain/loss vs purchase
- **Value Score**: Current performance score (0-100)
- **Trend**: 14-day trend (↗ rising, → stable, ↘ falling)
- **Recommendation**: SELL or HOLD
- **Reason**: Why (includes SOS, matchups, peak info)

## How It Works

### 1. Track Squad Values Automatically

Every time you run `rehoboam analyze`, the bot:

```python
# Record current values for all players
value_tracker.record_snapshots_bulk(squad_players)

# Stored in: logs/value_tracking.db
# - Market value
# - Points
# - Average points
# - Timestamp
```

### 2. Detect Peaks

```python
peak_analysis = value_tracker.get_peak_analysis(player_id, current_value)

if peak_analysis.is_declining:
    # Player peaked >7 days ago
    # Value dropped >5% since peak
    # → SELL SIGNAL
```

### 3. Analyze with Full Context

Just like buying, selling uses:

- ✅ Value score (performance metrics)
- ✅ Trend analysis (14-day value change)
- ✅ SOS (next 3 games difficulty)
- ✅ Matchup context (injuries, lineup status)
- ✅ **PLUS:** Peak detection, profit tracking

```python
analysis = analyzer.analyze_owned_player(
    player,
    purchase_price=purchase_price,
    trend_data=trend_data,
    matchup_context=matchup_context,  # Includes SOS!
    peak_analysis=peak_dict,
)
```

### 4. Protect Important Players

Bot won't sell:

- ❌ Starters (if `never_sell_starters=true`)
- ❌ High performers (>= min_points_to_keep)
- ❌ When squad at minimum size

## Recording Purchases

The bot needs to know what you paid to calculate profit/loss.

### Automatic (When Bot Buys)

```python
# When bot executes a buy
bid_monitor.register_bid(player_id=player_id, asking_price=asking_price, ...)

# Automatically records purchase when bid wins
value_tracker.record_purchase(player_id, purchase_price)
```

### Manual (For Existing Squad)

```python
from rehoboam.value_tracker import ValueTracker

tracker = ValueTracker()

# Record what you paid for Danel Sinani
tracker.record_purchase(
    player_id="123",
    player_name="Danel Sinani",
    league_id="your_league_id",
    purchase_price=6_000_000,
    timestamp=datetime.now().timestamp(),
)
```

**If purchase price unknown:**

- Bot uses current market value as baseline
- Profit/loss will be 0% initially
- Peak detection still works!

## SOS Sell Strategy

**CRITICAL:** Use SOS to time sells around difficult fixtures

### Example: Sell Before Cliff

```
Player: Manuel Neuer (GK)
Current value: €15M (+25% profit)
Current form: Excellent (value score 82)

Next 3 games:
  vs Bayern (1st)    🔥
  vs Leipzig (2nd)   🔥
  vs Leverkusen (3rd) 🔥

SOS Rating: 🔥🔥🔥 Very Difficult

Bot says: SELL NOW!

Reasoning:
  • Currently at high value (good form)
  • Brutal schedule ahead (avg rank 2.0)
  • Will likely concede goals → points drop → value crashes
  • Better to sell at €15M now than €12M after tough games

Result: Save €3M by timing the market
```

### Example: Hold Through Easy Run

```
Player: Kevin Trapp (GK)
Current value: €11M (+10% profit)
Current form: Good (value score 68)

Next 3 games:
  vs Bochum (18th)      ⚡
  vs Hoffenheim (14th)  ⚡
  vs Augsburg (16th)    ⚡

SOS Rating: ⚡⚡⚡ Very Easy

Bot says: HOLD

Reasoning:
  • Easy schedule coming up
  • Will likely keep clean sheets → value rises
  • Better to hold and sell at higher price after easy run

Result: Sell at €13M in 3 weeks instead of €11M now
```

## Database Schema

### Value Snapshots

```sql
CREATE TABLE value_snapshots (
    id INTEGER PRIMARY KEY,
    player_id TEXT,
    player_name TEXT,
    league_id TEXT,
    market_value INTEGER,
    points INTEGER,
    average_points REAL,
    timestamp REAL
);

-- Tracks every value check
-- Used to detect peaks and trends
```

### Purchases

```sql
CREATE TABLE purchases (
    player_id TEXT,
    player_name TEXT,
    league_id TEXT,
    purchase_price INTEGER,
    purchase_timestamp REAL,
    UNIQUE(player_id, league_id)
);

-- Tracks what you paid
-- Used to calculate profit/loss
```

**Location:** `logs/value_tracking.db`

## Usage

### Analyze Squad

```bash
# Analyze squad (recommended weekly)
rehoboam analyze

# Shows:
# 1. Market opportunities (buy recommendations)
# 2. Squad sell recommendations (peak detection, SOS, etc.)

# Full squad analysis (all players, not just sells)
rehoboam analyze --all
```

### Manual Testing

```bash
# Test with Danel Sinani scenario
python test_sinani_sell.py

# Shows:
# - Peak detection
# - Profit calculation
# - Sell recommendation
# - What you missed by not selling at peak
```

## Configuration

In `config.toml`:

```toml
[trading]
min_sell_profit_pct = 15.0  # Sell if profit >= 15%
max_loss_pct = -15.0        # Sell if loss >= -15%
min_squad_size = 20         # Don't sell below this
never_sell_starters = true  # Protect starting XI

[analysis]
min_value_score_to_buy = 40.0  # Buy threshold
# Same criteria used for sells!
```

## Real-World Examples

### Example 1: Caught the Peak

```
Player: Julian Brandt
Week 1: Bought €8M
Week 2: Rose to €10M → Bot: HOLD (rising trend)
Week 3: Peaked €13M → Bot: DETECTED PEAK ✅
Week 4: Dropped to €12.5M → Bot: SELL NOW! 🔴

You sell at: €12.5M
Profit: €4.5M (56%)

Without bot:
Week 5: €11M (-15% from peak)
Week 6: €9.5M (-27% from peak)
Lost profit: €3M
```

### Example 2: SOS Cliff Prevention

```
Player: Florian Wirtz
Current: €20M (bought €15M, +33% profit)
Form: Excellent (85 value score)

Next 3: Bayern, Leipzig, Dortmund (🔥🔥🔥)

Bot: SELL before fixtures

Result:
Sold at: €20M
After tough games: €17M
Saved: €3M by timing around SOS
```

### Example 3: Stop-Loss Protection

```
Player: Marius Wolf
Bought: €7M
Week 1-3: Injured, no games
Current: €6M (-14%)

Bot: HOLD (not at -15% stop-loss yet)

Week 4: Dropped to €5.9M (-16%)
Bot: SELL (stop-loss triggered)

Sold at: €5.9M
Loss: €1.1M

Better than:
Week 5: €5M (-29%)
Week 6: €4.5M (-36%)
Saved: €1.4M in prevented losses
```

## Comparison: Before vs After

### Before (Manual Selling)

```
❌ Sold at wrong time (panic or greed)
❌ Missed peak values
❌ Held through difficult fixtures
❌ No profit targets or stop-losses
❌ Emotional decisions

Result: Suboptimal profits
```

### After (Bot Selling)

```
✅ Detects peaks automatically
✅ Uses SOS to time around fixtures
✅ Strict profit targets & stop-losses
✅ Same analytical criteria as buying
✅ Data-driven decisions

Result: Maximize profits, minimize losses
```

## Future Enhancements

### 1. Auto-Sell Command

```bash
# Execute recommended sells automatically
rehoboam sell --auto --max 3

# Lists players and confirms before selling
```

### 2. Price Alerts

```
If player value > €15M:
    Notify: "Sinani hit €15M target!"

If player declined 10% from peak:
    Alert: "Sinani falling fast - sell now?"
```

### 3. Profit Optimization

```python
# Analyze: "What if I sold at peak?"
show_missed_opportunities()

# Shows how much profit you lost
# Helps calibrate sell timing
```

### 4. Replacement Selling

```python
# When buying a player:
if squad_full:
    find_worst_player_to_sell()
    execute_replacement_trade()
```

## Summary

The comprehensive sell analysis system:

- ✅ **Tracks squad values** automatically
- ✅ **Detects peaks** (missed €3M on Sinani!)
- ✅ **Uses SOS** to time sells around fixtures
- ✅ **Applies same criteria** as buying (value score, trends, matchups)
- ✅ **Protects important players** (starters, high performers)
- ✅ **Shows visual table** with profit/peak/trend info
- ✅ **Automates profit-taking** and stop-losses

**Bottom line:** Never miss a selling window again! The bot catches peaks, times fixtures, and locks in maximum profits automatically.
