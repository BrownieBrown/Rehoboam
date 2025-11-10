# Learning System Proposal

## Current State: Rule-Based (No Learning)

The bot uses fixed rules and thresholds:

- Min profit: 10%
- Max risk: 50
- Buy threshold: 40/100 value score
- Hold time: 3-7 days

These never change based on results.

## Proposed: Adaptive Learning System

### Phase 1: Trade History Tracking

**Database Schema:**

```python
Trade:
  - id
  - player_id
  - player_name
  - action (BUY/SELL)
  - price
  - reason (why bot recommended)
  - strategy (profit/lineup)
  - timestamp
  - league_id

TradeOutcome:
  - trade_id (links to Trade)
  - profit_loss (actual result)
  - profit_pct
  - hold_days
  - success (boolean)
  - notes
```

**What we track:**

- Every recommendation the bot makes
- Which you execute
- The outcome (profit/loss)
- Time held
- Market conditions at time

### Phase 2: Strategy Performance Analysis

**Analyze which strategies work:**

```python
# Rising trend strategy
rising_trend_trades = filter(trades, reason="Rising trend")
success_rate = sum(success) / len(rising_trend_trades)
avg_profit = mean(profit_pct)

# Below peak strategy
below_peak_trades = filter(trades, reason="Below peak")
success_rate = sum(success) / len(below_peak_trades)
avg_profit = mean(profit_pct)
```

**Output:**

```
Strategy Performance (Last 30 days):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Rising Trend (+15%)
  Trades: 12
  Success Rate: 83%
  Avg Profit: +14.2%
  ✅ Keep using

Below Peak (>30%)
  Trades: 8
  Success Rate: 62%
  Avg Profit: +8.1%
  ⚠️  Lower threshold to 25%

Falling + Profit
  Trades: 5
  Success Rate: 40%
  Avg Profit: +2.1%
  ❌ Stop using
```

### Phase 3: Adaptive Thresholds

**Auto-adjust based on success:**

```python
# If rising trend strategy has 80%+ success rate
if rising_trend_success > 0.8:
    # Lower threshold to catch more opportunities
    min_trend_pct = 10  # From 15

# If below peak strategy only 50% success
if below_peak_success < 0.6:
    # Raise threshold to be more selective
    min_below_peak_pct = 35  # From 25

# If overall profit trades succeeding
if overall_profit_success > 0.75:
    # Be more aggressive
    min_profit_pct = 8  # From 10
```

### Phase 4: Player-Specific Learning

**Track individual player behavior:**

```python
PlayerHistory:
  - player_id
  - trades_count
  - success_rate
  - avg_profit
  - typical_hold_time
  - best_entry_point (% below peak)
  - best_exit_point (% profit)
```

**Use it:**

```python
# Robin Hack historically:
# - 5 trades, 80% success
# - Avg profit: +18%
# - Best entry: 50-60% below peak
# - Best exit: +15% profit

# New opportunity: Robin Hack
# Current: 55% below peak, rising +20%
# Historical data: Perfect entry point!
# Confidence: 95% (vs normal 70%)
```

### Phase 5: Market Condition Learning

**Identify market patterns:**

```python
MarketCondition:
  - date
  - overall_trend (bull/bear/neutral)
  - volatility (high/low)
  - trade_success_rate
  - optimal_strategy
```

**Adapt to market:**

```python
if market_condition == "high_volatility":
    # Be more conservative
    max_risk_score = 30  # From 50
    hold_time = 2  # From 3-7

elif market_condition == "bull_market":
    # Be more aggressive
    min_profit_pct = 7  # From 10
    max_debt_usage = 80%  # From 60%
```

## Implementation Plan

### Week 1: Trade Tracking

- [ ] Create SQLite database
- [ ] Add `TradeTracker` class
- [ ] Store every recommendation
- [ ] Track outcomes manually (user inputs)

### Week 2: Performance Analysis

- [ ] Calculate strategy success rates
- [ ] Show performance report
- [ ] Identify best/worst strategies

### Week 3: Simple Adaptation

- [ ] Adjust thresholds based on success
- [ ] Confidence scoring (high/low based on history)
- [ ] Warning for low-success strategies

### Week 4: Advanced Learning

- [ ] Player-specific learning
- [ ] Market condition detection
- [ ] Automatic strategy selection

## Example Usage (After Learning Added)

```bash
# Regular analysis (now shows confidence based on history)
rehoboam analyze

📊 Learning Stats:
  Total Trades: 47
  Success Rate: 78%
  Avg Profit: +12.3%
  Best Strategy: Rising Trend (85% success)

💡 Profit Opportunities:
  1. Robin Hack - €6.7M
     Expected: +20% (€1.3M)
     Strategy: Rising trend + Below peak
     Historical: 4 trades, 100% success ✅
     Confidence: 95% 🌟🌟🌟

  2. Lucas Höler - €8.0M
     Expected: +20% (€1.6M)
     Strategy: Rising trend
     Historical: 2 trades, 50% success ⚠️
     Confidence: 65% 🌟🌟
```

```bash
# New command: See learning insights
rehoboam learn

📈 Strategy Performance (Last 30 days):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ Rising Trend: 85% success, +14% avg profit
✅ Below Peak (30%+): 75% success, +11% avg profit
⚠️  Below Peak (20-30%): 60% success, +7% avg profit
❌ Falling + Profit: 40% success, +3% avg profit

🎯 Recommended Adjustments:
  - Lower rising trend threshold: 15% → 10%
  - Raise below peak threshold: 25% → 30%
  - Stop using "Falling + Profit" strategy

💎 Best Players (Historical):
  1. Robin Hack: 5/5 trades, +18% avg
  2. Dominik Kohr: 3/4 trades, +15% avg
  3. Lucas Höler: 2/4 trades, +12% avg
```

## Benefits

**With Learning:**

- ✅ Better recommendations over time
- ✅ Confidence scoring (know which trades to trust)
- ✅ Automatic threshold tuning
- ✅ Avoid strategies that don't work
- ✅ Learn individual player patterns
- ✅ Adapt to market conditions

**Without Learning (Current):**

- ⚠️ Same recommendations regardless of past results
- ⚠️ Fixed thresholds might be suboptimal
- ⚠️ Can't identify which strategies work
- ⚠️ No player-specific insights
- ⚠️ Doesn't adapt to your league

## Should We Add It?

**Pros:**

- Much smarter bot
- Improves with every trade
- Personalized to your league
- Higher success rate over time

**Cons:**

- Need data (20+ trades minimum)
- More complex codebase
- Requires manual outcome entry initially

**Timeline:**

- Basic tracking: 2-3 hours
- Analysis + reporting: 3-4 hours
- Simple adaptation: 2-3 hours
- **Total: ~8-10 hours of work**

## Recommendation

**Start without learning this week:**

1. Use bot as-is (works great!)
1. Track trades manually in spreadsheet
1. See what works in your league
1. After 20-30 trades, we add learning

**Or add learning now:**

- Start collecting data immediately
- Learning kicks in after ~20 trades
- Bot improves automatically

Your choice! The bot will work great either way.
