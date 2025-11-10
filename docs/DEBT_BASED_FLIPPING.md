# Debt-Based Flipping Strategy

## Overview

The bot now uses **aggressive debt-based flipping** to maximize profit opportunities:

- ✅ Can go into **negative budget (debt)** when buying flips
- ✅ Use **full debt capacity** (60% of team value)
- ✅ Must be **positive by gameday** (sell flips before match)
- ✅ Find **10+ opportunities** instead of 5

## How It Works

### Budget Calculation

```python
Team Value: €50,000,000
Max Debt: €30,000,000 (60% of team value)
Current Budget: €2,000,000

Available for Flips:
- If match >5 days away: €32,000,000 (budget + full debt)
- If match 3-4 days away: €17,000,000 (budget + 50% debt)
- If match ≤2 days away: €2,000,000 (positive budget only)
```

### Gameday Awareness

The bot adjusts aggressiveness based on days until next match:

#### **>5 Days Until Match** 🟢

```
Use FULL debt capacity
Can buy €32M of flips with €2M budget
Hold 5-7 days
Plenty of time to sell before match
```

#### **3-4 Days Until Match** 🟡

```
Use 50% debt capacity
Can buy €17M of flips
Hold 3-4 days
Conservative - must sell soon
```

#### **≤2 Days Until Match** 🔴

```
Use ONLY positive budget
Can buy €2M of flips
Hold 1-2 days
Very conservative - match approaching
```

## Example Scenarios

### Scenario 1: Aggressive Flipping (5+ days until match)

**Starting Position**:

```
Budget: €2,000,000
Team Value: €50,000,000
Max Debt: €30,000,000
Days Until Match: 6 days
```

**Bot Finds 10 Opportunities**:

```
💰 Profit Trading Opportunities

Player A     €8,000,000   €9,500,000   €1,500,000 (18.8%)   5d   Low
Player B     €6,500,000   €7,800,000   €1,300,000 (20.0%)   4d   Low
Player C     €5,000,000   €5,800,000   €800,000   (16.0%)   3d   Low
Player D     €4,000,000   €4,600,000   €600,000   (15.0%)   3d   Med
Player E     €3,500,000   €4,000,000   €500,000   (14.3%)   4d   Low
... (10 total)

Total Investment: €32,000,000
Total Profit Potential: €5,200,000 (avg 16.2%)

Debt Needed: €30,000,000
→ After buying: Budget = -€30,000,000
→ After selling: Budget = €7,200,000 profit
```

**Timeline**:

```
Day 1: Buy all 10 players → Budget: -€30,000,000 (in debt)
Day 2-5: Hold players, values increase
Day 5: Start selling → Budget: -€10,000,000 (selling)
Day 6: Match day → Budget: €7,200,000 ✅ POSITIVE!
```

### Scenario 2: Conservative Approach (2 days until match)

**Starting Position**:

```
Budget: €2,000,000
Team Value: €50,000,000
Max Debt: €30,000,000
Days Until Match: 2 days ⚠️
```

**Bot Limits Flips**:

```
⚠️ Match in 2 days - using only positive budget for flips

Available for Flips: €2,000,000 (no debt allowed)

💰 Profit Trading Opportunities

Player A     €1,800,000   €2,100,000   €300,000 (16.7%)   1d   Low
Player B     €200,000     €250,000     €50,000  (25.0%)   1d   Low

Total Investment: €2,000,000
Total Profit Potential: €350,000 (avg 17.5%)

Strategy: Quick flips only - must sell before match
```

**Timeline**:

```
Day 1: Buy 2 players → Budget: €0
Day 2: Sell both → Budget: €2,350,000 ✅ POSITIVE before match!
```

### Scenario 3: Already in Debt

**Starting Position**:

```
Budget: -€5,000,000 (already in debt from previous flips)
Team Value: €50,000,000
Max Debt: €30,000,000
Debt Used: €5,000,000
Debt Remaining: €25,000,000
Days Until Match: 4 days
```

**Bot Adjusts**:

```
Match in 4 days - conservative flip budget

Current Debt: €5,000,000 (€25,000,000 remaining capacity)
Available for Flips: €7,500,000 (50% of remaining debt)

💰 Profit Trading Opportunities
(Shows 5 opportunities totaling €7,500,000)

Strategy: Moderate flips, sell existing + new before match
```

## Benefits

### ✅ **10x More Opportunities**

```
Before: Limited to €3M with 30% allocation
After: Can use €32M with full debt capacity
Result: 10x more flips = 10x more profit potential
```

### ✅ **Compound Profits Faster**

```
Week 1: €32M flips → €5M profit → Budget: €7M
Week 2: €37M flips → €6M profit → Budget: €13M
Week 3: €43M flips → €7M profit → Budget: €20M
Month 1: €20M budget from €2M starting!
```

### ✅ **Gameday Safety**

```
Never get stuck in debt on match day
Automatic sell urgency increases near match
Always positive when lineup matters
```

### ✅ **Risk Managed**

```
Far from match: Aggressive (full debt)
Near match: Conservative (no debt)
Dynamic adjustment based on gameday
```

## Console Output

### Far from Match (Aggressive)

```bash
$ rehoboam analyze

Analyzing profit trading opportunities...
Market: 150 total, 95 KICKBASE-owned, 55 human listings (filtered out)
Match in 6 days - full flip budget available ✅
Current Budget: €2,000,000
Max Debt Allowed: €30,000,000 (60% of team value)
Available for Flips: €32,000,000
No debt currently (€30,000,000 available if needed) ✅

💰 Profit Trading Opportunities
Buy undervalued players and flip for profit (can go into debt, sell before gameday)

[10 opportunities shown]

Total Investment: €32,000,000
Total Profit Potential: €5,200,000 (avg 16.2%)
Debt Needed: €30,000,000
→ After buying: Budget = -€30,000,000
→ After selling: Budget = €7,200,000 profit

Strategy: Buy these players, hold 3-7 days, sell when value increases, be positive by gameday
```

### Near Match (Conservative)

```bash
$ rehoboam analyze

Analyzing profit trading opportunities...
⚠️ Match in 2 days - using only positive budget for flips
Current Budget: €2,000,000
Max Debt Allowed: €30,000,000 (60% of team value)
Available for Flips: €2,000,000

💰 Profit Trading Opportunities
[2-3 quick flip opportunities shown]

Total Investment: €2,000,000
Total Profit Potential: €350,000

Strategy: Quick flips only - sell before match tomorrow
```

### Already in Debt

```bash
$ rehoboam analyze

Analyzing profit trading opportunities...
Match in 4 days - conservative flip budget
Current Budget: -€5,000,000
Max Debt Allowed: €30,000,000 (60% of team value)
Available for Flips: €7,500,000
Current Debt: €5,000,000 (€25,000,000 remaining capacity) ⚠️

💰 Profit Trading Opportunities
[5-7 moderate opportunities shown]
```

## Configuration

### Debt Limits

```python
# In config.py
max_debt_pct_of_team_value = 60.0  # Can go into debt up to 60% of team value
```

### Gameday Thresholds

```python
# In trader.py find_profit_opportunities()
if days_until_match <= 2:
    flip_budget = max(0, current_budget)  # Only positive budget
elif days_until_match <= 4:
    flip_budget = current_budget + (max_debt * 0.5)  # 50% debt
else:
    flip_budget = current_budget + max_debt  # Full debt
```

### Opportunity Limits

```python
# In profit_trader.py
max_opportunities = 10  # Show up to 10 flip opportunities
```

## Safety Mechanisms

### 1. **Gameday Check**

- Always checks days until next match
- Reduces aggressiveness as match approaches
- Forces positive budget by gameday

### 2. **Debt Tracking**

- Shows current debt usage
- Shows remaining debt capacity
- Warns if debt limit approaching

### 3. **Risk Scoring**

- Still applies risk assessment (0-100)
- Only shows low-medium risk flips
- Skips high-risk opportunities

### 4. **Hold Period Limits**

- Max 7 days hold period
- Shorter near gameday
- Automatic sell triggers

## Workflow

### Monday-Thursday (Far from Match)

```bash
1. Run: rehoboam analyze
2. See: 10 aggressive flip opportunities
3. Execute: Buy all 10 (go into debt)
4. Hold: 3-7 days
```

### Friday-Saturday (Near Match)

```bash
1. Run: rehoboam analyze
2. See: "Match in 2 days" warning
3. Action: Sell existing flips
4. Execute: Only quick 1-day flips
```

### Sunday (Match Day)

```bash
1. Check: Budget must be positive ✅
2. Action: All flips sold
3. Result: Profit banked
4. Monday: Start new flip cycle
```

## Comparison

### Old Strategy (30% Budget Only)

```
Budget: €10M
Flip Budget: €3M (30%)
Opportunities: 5
Max Profit: €500K/week
```

### New Strategy (Full Debt Capacity)

```
Budget: €10M
Team Value: €50M
Max Debt: €30M
Flip Budget: €40M (budget + debt)
Opportunities: 10
Max Profit: €6M/week
```

**Result**: 12x more profit potential! 🚀

## Summary

🎯 **Goal**: Maximize flip profits using debt capacity

**Strategy**:

1. Use full debt capacity when match is far
1. Reduce debt usage as match approaches
1. Always be positive by gameday
1. Find 10 opportunities instead of 5
1. Compound profits exponentially

**Safety**:

- Gameday awareness
- Debt tracking
- Risk assessment
- Hold period limits

**Just run**:

```bash
rehoboam analyze
```

And execute aggressive flips! 💰🚀
