# League Compliance System

## Overview

Your unofficial league has a rule: **No bidding below market value**. Since market values update daily after 10:00, bids that were legal yesterday might become illegal today.

The bot automatically detects and fixes these violations.

## The Problem

```
Your bid: €10,000,000
Market value (yesterday): €9,500,000 ✓ Legal
Market value (today): €11,000,000 ❌ ILLEGAL!
```

Without automation, you'd need to manually check all bids 3 times daily to avoid violations.

## The Solution

### 1. Automatic Detection

The bot checks all active bids every session:

- Compares your bid vs current market value
- Flags any bids below market value
- Calculates required new bid (market value + 2% buffer)

### 2. Profitability Check

For each flagged bid, the bot evaluates:

```python
New bid = Market value × 1.02  # +2% buffer
Predicted profit = Predicted future value - New bid

If profit > 10%:
    → Adjust bid
Else:
    → Cancel bid (no longer profitable)
```

### 3. Automatic Resolution

**Adjust Example:**

```
Player: Max Mustermann
Current bid: €10M
Market value: €11M (ILLEGAL)
New required bid: €11.22M (+2% buffer)
Predicted value: €13M
Action: Cancel €10M → Place €11.22M
Reason: Still profitable (€1.78M margin)
```

**Cancel Example:**

```
Player: Hans Schmidt
Current bid: €10M
Market value: €12M (ILLEGAL)
New required bid: €12.24M (+2% buffer)
Predicted value: €12.5M
Action: Cancel €10M → Don't re-bid
Reason: Not profitable (only €260k margin)
```

## When It Runs

### Automatic (Daemon Mode)

```bash
rehoboam daemon --interval 120  # Every 2 hours
```

The bot runs compliance checks:

- After 10:00 (when market values update)
- At 14:00
- At 18:00

### Manual

```bash
rehoboam auto  # Part of auto-trading session
```

Compliance check runs at session start before finding new opportunities.

## Output Example

```
📊 Checking 3 active bid(s) for compliance...

⚠️  Found 2 bid(s) below market value:
  To adjust: 1
  To cancel: 1

  ⚠️  Max Mustermann
     Current bid: €10,000,000
     Market value: €11,000,000
     Violation: €1,000,000 (9.1% below)
     Action: Adjust bid to €11,220,000
     Estimated profit: €1,780,000 (predicted value: €13,000,000)

  ❌ Hans Schmidt
     Current bid: €10,000,000
     Market value: €12,000,000
     Would need to bid: €12,240,000 (not profitable)
     Action: Cancel bid

Resolving 2 bid compliance violation(s)...

Adjusting bid on Max Mustermann...
Old bid: €10,000,000 → New bid: €11,220,000
✓ Canceled old bid
✓ Adjusted bid to €11,220,000

Canceling bid on Hans Schmidt...
Reason: Would need €12,240,000 but not profitable
✓ Canceled bid on Hans Schmidt

Bid compliance: 1 adjusted, 1 canceled
```

## Prevention on New Bids

The bot also prevents placing illegal bids in the first place:

```python
# In bidding_strategy.py
if recommended_bid < market_value:
    recommended_bid = market_value  # Enforce minimum
```

This ensures all new bids comply with the market value rule.

## Configuration

### Buffer Percentage

Default is 2% buffer above market value. To adjust:

```python
# In league_compliance.py line 252
buffer_pct = 2.0  # Change to 3.0 for +3% buffer
```

### Profit Margin Threshold

Default requires 10% profit margin. To adjust:

```python
# In league_compliance.py line 272
min_profit_margin = new_required_bid * 0.10  # Change to 0.15 for 15%
```

## Benefits

✅ **Never violate league rules** - Automatic compliance
✅ **No manual checking** - Bot handles it 3x daily
✅ **Smart decisions** - Only adjusts if still profitable
✅ **Transparent** - Clear logging of all actions
✅ **Safe** - Respects profit margins and value ceilings

## Files

- `rehoboam/league_compliance.py` - Core compliance logic
- `rehoboam/auto_trader.py` - Integration (line 98-116)
- `rehoboam/bidding_strategy.py` - Prevention (line 119-123)
