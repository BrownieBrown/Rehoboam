# N-for-M Trading Strategy

## Overview

The bot now finds **N-for-M trades** that improve your starting 11 lineup:

- Sell N players from your squad
- Buy M players from the market
- Improve total points and value score

## How It Works

### 1. Select Current Best 11

Bot picks your best starting 11 using:

- **Value Score** (includes points, cost efficiency, SOS, trends)
- **Formation Requirements** (1 GK, 3 DEF, 2 MID, 1 FWD minimum)

### 2. Find Better Lineups

Bot tries different N-for-M combinations:

- **1-for-1**: Replace 1 player with 1 better player
- **1-for-2**: Sell 1 expensive → Buy 2 cheaper (more total points)
- **2-for-2**: Upgrade 2 weak → 2 strong
- **2-for-1**: Consolidate 2 mid-tier → 1 star
- **Any combination** (up to 3-for-3)

### 3. Validate Trades

Each trade must:

- ✅ Improve total points (+2 pts/week minimum)
- ✅ OR improve value score (+10 minimum)
- ✅ Maintain formation requirements (1 GK, 3 DEF, 2 MID, 1 FWD)
- ✅ Stay within squad size limit (max 15 players)
- ✅ Be affordable with current budget

### 4. Budget Constraint

```
Required Budget = Cost of M players to buy
Available Budget = Current budget

IMPORTANT: Must buy ALL M players BEFORE selling N players
```

**Example**:

```
Trade: Sell Player A (€10M) + Player B (€8M)
       Buy Player X (€12M) + Player Y (€9M)

Total Cost: €21M (need this upfront)
Total Proceeds: €18M (get this after selling)
Net Cost: €3M
```

## Squad Requirements

### Formation Minimums

- **1 Goalkeeper** (minimum)
- **3 Defenders** (minimum)
- **2 Midfielders** (minimum)
- **1 Forward** (minimum)

### Squad Limits

- **Max 15 players** total
- **11 players** for starting lineup

## Example Output

```bash
$ rehoboam analyze

💡 Recommended Trades
Found 3 trade(s) that improve your starting 11

Trade #1: 2-FOR-2 (2-for-2)
SELL:
  • Player A (MID) - €10,000,000
  • Player B (FWD) - €8,000,000

BUY:
  • Player X (MID) - €12,000,000
  • Player Y (FWD) - €9,000,000

Financial Summary:
  Total Cost: €21,000,000
  Total Proceeds: €18,000,000
  Net Cost: €3,000,000
  Required Budget: €21,000,000 (buy first!)

Expected Improvement:
  Points/Week: +4.5
  Value Score: +18.2


Trade #2: 1-FOR-2 (1-for-2)
SELL:
  • Expensive Player (DEF) - €20,000,000

BUY:
  • Budget Star 1 (DEF) - €10,000,000
  • Budget Star 2 (MID) - €8,000,000

Financial Summary:
  Total Cost: €18,000,000
  Total Proceeds: €20,000,000
  Net Cost: -€2,000,000 (profit!)
  Required Budget: €18,000,000 (buy first!)

Expected Improvement:
  Points/Week: +3.2
  Value Score: +12.5
```

## Re-evaluation After Auctions

Bot **automatically re-evaluates** after each auction:

### Auction Win

```
Planned Trade: Sell [A, B], Buy [X, Y]
Auction Result: Won Player X ✅

Bot automatically:
1. Removes Player X from market analysis
2. Re-runs trade optimizer
3. Adjusts recommendations (now just need Player Y)
```

### Auction Loss

```
Planned Trade: Sell [A, B], Buy [X, Y]
Auction Result: Lost Player Y auction ❌

Bot automatically:
1. Re-runs analysis without Player Y
2. Finds alternative Player Z
3. Decides: Keep A/B or find different trade
```

### Tracking Auctions

Bot uses `/v4/leagues/{leagueId}/activitiesFeed` to track:

- ✅ Won auctions (players you acquired)
- ❌ Lost auctions (someone else won)
- 💰 Players sold
- 📋 Market listings

## Strategy Comparison

### Old Approach: Individual Sell Signals

```
❌ Sinani peaked → SELL
❌ But no better replacement available
❌ Weakens lineup
```

### New Approach: Holistic Lineup Improvement

```
✅ Analyze entire starting 11
✅ Find multi-player upgrades
✅ Only recommend if lineup improves
✅ Smart budget management
```

## Configuration

### Trade Limits

```python
# In trade_optimizer.py
max_players_out = 3  # Max players to sell in one trade
max_players_in = 3  # Max players to buy in one trade
```

### Improvement Thresholds

```python
min_improvement_points = 2.0  # Need +2 pts/week to recommend
min_improvement_value = 10.0  # OR +10 value score
```

## Benefits

### ✅ Smarter Selling

- Don't sell good players without replacements
- Find multi-player upgrades
- Maximize lineup strength

### ✅ Budget Efficiency

- Optimize spending across multiple players
- Find "sell 1 expensive, buy 2 cheap" opportunities
- Account for upfront budget needs

### ✅ Formation Safety

- Never break formation requirements
- Stay within squad size limits
- Validate before recommending

### ✅ Automatic Re-evaluation

- Tracks auction results
- Adjusts plan after wins/losses
- Finds alternatives automatically

## Workflow

### 1. Daily Analysis

```bash
rehoboam analyze
```

Bot shows:

1. Market opportunities (top 20 players)
1. Your squad analysis (all players)
1. **Trade recommendations** (N-for-M upgrades)

### 2. Execute Trades

Follow recommended trades:

1. **Buy first**: Place bids for all M players
1. **Sell after**: List N players once you've won auctions
1. **Re-evaluate**: Run `analyze` again after auctions complete

### 3. Post-Auction

Bot automatically:

- Detects auction results
- Re-runs analysis
- Shows updated recommendations

## Example Scenarios

### Scenario 1: Upgrade 2 Weak Players

```
Current Lineup:
- Weak MID (30 value score) - €5M
- Weak FWD (35 value score) - €6M
- Total: 65 value score, €11M

Market:
- Strong MID (55 value score) - €8M
- Strong FWD (60 value score) - €9M
- Total: 115 value score, €17M

Trade Recommendation:
SELL: Weak MID + Weak FWD (€11M proceeds)
BUY: Strong MID + Strong FWD (€17M cost)
Net Cost: €6M
Improvement: +50 value score, +5 pts/week ✅
```

### Scenario 2: Consolidate to Star

```
Current Lineup:
- Mid-tier DEF (45 value score) - €10M
- Mid-tier DEF (40 value score) - €8M
- Total: 85 value score, €18M

Market:
- Star DEF (80 value score) - €20M

Trade Recommendation:
SELL: 2x Mid-tier DEF (€18M proceeds)
BUY: Star DEF (€20M cost)
Net Cost: €2M
Improvement: -5 value score ❌ (rejected, not better)
```

### Scenario 3: Split Expensive Player

```
Current Lineup:
- Expensive MID (60 value score) - €25M
- Team needs depth

Market:
- Budget MID (50 value score) - €12M
- Budget FWD (48 value score) - €10M
- Total: 98 value score, €22M

Trade Recommendation:
SELL: Expensive MID (€25M proceeds)
BUY: Budget MID + Budget FWD (€22M cost)
Net Cost: -€3M (profit!)
Improvement: +38 value score, +3 pts/week ✅
```

## Limitations

### Computation

- Tries all combinations: **O(n³ × m³)**
- Limited to 3-for-3 max to keep fast
- Analyzes only affordable market players

### Assumptions

- Future performance = average points
- Value score = quality
- Market availability constant

### Not Considered (Yet)

- Opponent bidding behavior
- Player form trends
- Injury risks
- Schedule timing

## Summary

🎯 **Goal**: Improve starting 11 through smart N-for-M trades

**Strategy**:

1. Analyze all possible player combinations
1. Find trades that improve lineup
1. Validate formation + budget
1. Show top 5 recommendations
1. Re-evaluate after each auction

**Result**:

- Stronger starting 11
- Better use of budget
- Safer recommendations (always have replacements)
- Automatic adjustment to auction outcomes

**Just run**:

```bash
rehoboam analyze
```

And follow the trade recommendations! 🚀
