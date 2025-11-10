# Dual Trading Strategy

## Overview

The bot now uses **two separate trading strategies** that work together:

1. **Profit Trading (Market Making)** - Build budget
1. **Lineup Improvement Trading** - Win more points

## Strategy 1: Profit Trading 💰

### Goal

Buy undervalued players and flip them for profit to accumulate budget.

### How It Works

#### 1. Find Undervalued Players

```
Player Market Value: €10,000,000
KICKBASE Asking Price: €8,500,000
Profit Potential: €1,500,000 (17.6%)
```

#### 2. Buy and Hold

- Buy from KICKBASE at asking price
- Hold for 3-7 days
- Don't need to put in lineup
- Wait for value to increase

#### 3. Sell for Profit

- Sell back to KICKBASE at market value
- Or wait for value increase from performance
- Take profit when target reached

### Criteria

```python
min_profit_pct = 10.0  # Need at least 10% profit potential
max_hold_days = 7  # Hold max 7 days
max_risk_score = 50.0  # Moderate risk tolerance
```

### Risk Assessment

Bot calculates risk 0-100:

- **Low risk (\<30)**: Rising trend, high points, moderate gap
- **Med risk (30-60)**: Unknown trend, average points
- **High risk (>60)**: Falling trend, low points, suspicious gap

### Budget Allocation

```
Total Budget: €10,000,000
├─ 70% Reserved for Lineup: €7,000,000
└─ 30% Available for Flips: €3,000,000
```

## Strategy 2: Lineup Improvement Trading 💡

### Goal

Strengthen your starting 11 through N-for-M player trades.

### How It Works

- Analyze current best 11
- Find N-for-M trades that improve total points/value
- Only recommend if lineup gets better

### Budget Allocation

Uses the 70% reserved budget for important acquisitions.

## Combined Workflow

### Daily Cycle

```bash
$ rehoboam analyze
```

Bot shows:

#### 1. Market Analysis

```
Top 20 Trading Opportunities
(Best market players to buy for lineup)
```

#### 2. Squad Analysis

```
📊 Your Squad Analysis
(All your players with sell signals)
```

#### 3. Profit Trading Opportunities 💰

```
Player              Buy Price    Market Value  Profit Pot.  Hold  Risk
──────────────────────────────────────────────────────────────────────
Budget Striker      €8,500,000   €10,000,000   €1,500,000   3d    Low
                                                (17.6%)
Rising Midfielder   €12,000,000  €14,500,000   €2,500,000   5d    Low
                                                (20.8%)
```

#### 4. Lineup Improvement Trades 💡

```
Trade #1: 2-FOR-2
SELL:
  • Weak Player A (MID) - €5,000,000
  • Weak Player B (DEF) - €4,000,000

BUY:
  • Strong Player X (MID) - €7,000,000
  • Strong Player Y (DEF) - €6,000,000

Improvement: +4.5 pts/week, +18.2 value score
```

### Execution Priority

#### Phase 1: Profit Trading (First 30 minutes)

1. Buy undervalued players with 30% budget
1. Accumulate 2-3 flip trades
1. Hold and wait for value increase

#### Phase 2: Monitor Flips (Throughout day)

```bash
$ rehoboam check-flips  # (Future command)
```

- Check held players
- Sell if profit target reached
- Sell if max hold period reached
- Recycle profit into new flips

#### Phase 3: Lineup Improvements (When needed)

1. Use 70% budget + flip profits
1. Execute N-for-M trades
1. Strengthen starting 11

## Example Scenarios

### Scenario 1: Accumulate Budget

**Morning**:

```
Budget: €10,000,000
Flip Budget: €3,000,000 (30%)

Buy Flip #1: €2,500,000 (undervalued 15%)
Buy Flip #2: €500,000 (undervalued 12%)

Remaining: €7,000,000 (reserved for lineup)
```

**3 Days Later**:

```
Flip #1 value increased: €2,500,000 → €2,875,000
Sell for profit: €375,000 (+15%)

Flip #2 value increased: €500,000 → €560,000
Sell for profit: €60,000 (+12%)

New Budget: €10,435,000 ✅
```

**Result**: Made €435,000 profit without touching lineup

### Scenario 2: Lineup Improvement

**After accumulating profit**:

```
Budget: €10,435,000
Reserved for Lineup: €7,304,500 (70%)
Flip Budget: €3,130,500 (30%)

Execute 2-for-2 Trade:
  SELL: 2 weak players (€9,000,000)
  BUY: 2 strong players (€13,000,000)
  Net Cost: €4,000,000

After Trade Budget: €6,435,000
```

**Result**: Stronger lineup + still have budget for flips

### Scenario 3: Continuous Cycle

**Week 1**:

- Profit flips: +€400,000
- Budget: €10,400,000

**Week 2**:

- More flips: +€350,000
- Budget: €10,750,000

**Week 3**:

- Big lineup upgrade: -€5,000,000
- Better team: +6 pts/week
- Budget: €5,750,000

**Week 4**:

- Rebuild flip budget with profits
- Continue cycling

## Benefits

### ✅ Continuous Budget Growth

- Always making small profits
- Compounds over time
- More budget for big acquisitions

### ✅ Separate Concerns

- Profit trading: Low risk, quick wins
- Lineup trading: Strategic, long-term

### ✅ Risk Management

- Only 30% budget in flips
- 70% reserved for important moves
- Stop-loss protection

### ✅ Market Efficiency

- Exploit KICKBASE pricing gaps
- Buy low, sell high
- Profit from market inefficiency

## Configuration

### Profit Trading Settings

```python
# In profit_trader.py
min_profit_pct = 10.0  # Minimum 10% profit to flip
max_hold_days = 7  # Hold max 7 days
max_risk_score = 50.0  # Skip high-risk players
```

### Budget Allocation

```python
# In trader.py find_profit_opportunities()
reserve_for_lineup = 70%   # Reserve for lineup trades
flip_budget = 30%          # Use for profit flips
```

### Lineup Trading Settings

```python
# In trade_optimizer.py
max_players_out = 3  # Max sell in one trade
max_players_in = 3  # Max buy in one trade
min_improvement_points = 2.0
min_improvement_value = 10.0
```

## Future Enhancements

### Flip Tracking

```bash
$ rehoboam check-flips
```

Shows active flips:

```
Active Profit Trades
──────────────────────────────────────────────────
Player           Bought      Current     Profit   Days   Action
──────────────────────────────────────────────────
Budget Striker   €8,500,000  €9,200,000  +8.2%    2d     Hold
Rising Mid       €12,000,000 €13,500,000 +12.5%   4d     SELL ✅
```

### Automatic Selling

Bot automatically:

1. Checks flip values daily
1. Sells when profit target reached
1. Sells at max hold period
1. Triggers stop-loss if needed

### Performance Metrics

```
Flip Trading Stats (Last 30 Days)
──────────────────────────────────
Trades Executed: 12
Win Rate: 83% (10 wins, 2 losses)
Total Profit: €1,250,000
Average Hold: 4.2 days
ROI: 14.3%
```

## Example Output

```bash
$ rehoboam analyze

💰 Profit Trading Opportunities
Buy undervalued players and flip for profit

Player              Position  Buy Price    Market Value  Profit Pot.  Hold  Risk   Reason
────────────────────────────────────────────────────────────────────────────────────────
Budget Forward      FWD       €6,500,000   €7,800,000    €1,300,000   3d    Low    20.0% undervalued | Rising trend (+8.2%)
                                                          (20.0%)

Undervalued DEF     DEF       €8,000,000   €9,200,000    €1,200,000   5d    Med    15.0% undervalued | High performer
                                                          (15.0%)

Quick Flip MID      MID       €4,000,000   €4,500,000    €500,000     3d    Low    12.5% undervalued | Mean reversion
                                                          (12.5%)

Strategy: Buy these players, hold for a few days, sell when value increases


💡 Recommended Trades
Found 2 trade(s) that improve your starting 11

Trade #1: 2-FOR-2
SELL:
  • Weak MID (MID) - €5,000,000
  • Weak DEF (DEF) - €4,000,000

BUY:
  • Strong MID (MID) - €7,000,000
  • Strong DEF (DEF) - €6,000,000

Financial Summary:
  Total Cost: €13,000,000
  Total Proceeds: €9,000,000
  Net Cost: €4,000,000
  Required Budget: €13,000,000 (buy first!)

Expected Improvement:
  Points/Week: +5.2
  Value Score: +22.4
```

## Summary

🎯 **Dual Strategy Goals**:

1. **Build Budget**: Profit flips with 30% of budget
1. **Win Games**: Lineup improvements with 70% budget

**Workflow**:

1. Daily profit flips → Accumulate budget
1. Weekly lineup upgrades → Improve team
1. Continuous cycle → Compound growth

**Result**:

- Growing budget from flips
- Stronger lineup from trades
- More points each week
- Dominate your league! 🏆

**Just run**:

```bash
rehoboam analyze
```

And execute both strategies! 🚀
