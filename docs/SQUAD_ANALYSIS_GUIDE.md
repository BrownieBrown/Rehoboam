# Squad Analysis Guide

## Why You Might See "No Sell Recommendations"

There are 4 common reasons:

### 1. **First Run - No Historical Data Yet** 📊

```
Peak detection needs ~7 days of snapshots
Trend analysis needs ~14 days of data

Solution: Run 'rehoboam analyze' daily/weekly
After a few runs, the bot will have enough history
```

### 2. **No Purchase Prices Recorded** 💰

```
If the bot doesn't know what you paid, it can't:
- Calculate profit/loss
- Detect if you should take profits
- Apply profit target (15%) or stop-loss (-15%)

Solution: Record purchase prices manually (see below)
```

### 3. **All Players Protected** 🛡️

```
The bot won't recommend selling:
- Starters (if never_sell_starters=true in config)
- High performers (points >= min_points_to_keep)
- When squad at minimum size

Solution: Check config.toml settings
```

### 4. **All Players Performing Well** ✅

```
If everyone has:
- Good value scores (50+)
- No difficult schedules ahead
- Not peaked and declining
- Stable/rising trends

Then HOLD is the right recommendation!
```

## How to See Your Full Squad

Now when you run `rehoboam analyze`, you'll **ALWAYS** see:

### 1. Market Opportunities (Buy Table)

```
Top 20 Trading Opportunities
───────────────────────────────────────────
Player      Position  Price      Value Score  Recommendation
───────────────────────────────────────────
...
```

### 2. **Your Squad Analysis (NEW!)**

```
📊 Your Squad Analysis
──────────────────────────────────────────────────────────────────────────────
Player      Position  Purchase    Current     Peak        Profit/Loss  Recommendation  Reason
──────────────────────────────────────────────────────────────────────────────
ALL your players shown here!
```

### 3. Recommendation Summary

```
📋 Recommendations: 0 SELL, 25 HOLD

✓ No urgent sell recommendations - all players worth holding

Why players are on HOLD:
  • 15x: Current P/L: ...
  • 8x: Starter - don't sell
  • 2x: High performer - don't sell
```

## Recording Purchase Prices (IMPORTANT!)

For **Danel Sinani** and other players you bought before the tracker:

### Record Purchase Price

```bash
# Format: rehoboam record-purchase "Player Name" <price_in_euros>

# Example: Danel Sinani bought for €6M
rehoboam record-purchase "Danel Sinani" 6000000

# Output:
✓ Recorded purchase for Danel Sinani
  Purchase price: €6,000,000
  Current value: €14,000,000
  Profit/Loss: €8,000,000 (+133.3%)

💰 Excellent profit! Consider selling soon.
```

### Then Analyze Again

```bash
rehoboam analyze

# Now the bot knows:
# - You paid €6M
# - Current value €14M
# - Profit: +133%
# - Peak detection will start tracking
```

### Record All Your Squad

```bash
# List your squad first
rehoboam analyze

# Then record each player's purchase price
rehoboam record-purchase "Player Name" <price>
rehoboam record-purchase "Another Player" <price>
...
```

## What Each Column Means

### Squad Analysis Table

| Column             | Meaning                                      | Example                              |
| ------------------ | -------------------------------------------- | ------------------------------------ |
| **Player**         | Name                                         | Danel Sinani                         |
| **Position**       | FW, MF, DF, GK                               | FW                                   |
| **Purchase**       | What you paid                                | €6,000,000                           |
| **Current**        | Current market value                         | €14,000,000                          |
| **Peak**           | Highest value reached<br>+ decline from peak | €17,000,000<br>-17.6%                |
| **Profit/Loss**    | % gain/loss vs purchase                      | +133.3%                              |
| **Value Score**    | Current performance (0-100)                  | 72.0                                 |
| **Trend**          | 14-day value trend                           | ↘ -12.5%                             |
| **Recommendation** | SELL or HOLD                                 | SELL                                 |
| **Reason**         | Why                                          | Peaked and declining -17.6% over 11d |

### Color Coding

**Profit/Loss:**

- 🟢 Green: +20% or more (excellent)
- 🟢 Light green: 0% to +20% (profitable)
- 🟡 Yellow: -10% to 0% (slight loss)
- 🔴 Red: -10% or worse (significant loss)

**Value Score:**

- 🟢 Green: 60+ (good performer)
- 🟡 Yellow: 40-59 (average)
- 🔴 Red: \<40 (underperforming)

**Trend:**

- ↗ Green: Rising (value increasing)
- → Yellow: Stable (no change)
- ↘ Red: Falling (value dropping)

**Recommendation:**

- 🔴 SELL: Strong sell signal
- 🟡 HOLD: Keep the player

## Example Output

After you record purchase prices and run for a week:

```bash
$ rehoboam analyze

📊 Your Squad Analysis
────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
Player          Position  Purchase    Current     Peak         Profit/Loss  Value   Trend      Recommendation  Reason
                                                                            Score
────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
Danel Sinani    FW        €6,000,000  €14,000,000 €17,000,000  +133.3%     72.0    ↘ -12.5%   SELL            Peaked and declining -17.6% over 11d
                                                  -17.6%

Florian Wirtz   MF        €15,000,000 €20,000,000 €20,000,000  +33.3%      85.0    ↗ +15.2%   HOLD            Rising trend (+15.2%) - may rise more
                                                  at peak

Manuel Neuer    GK        €8,000,000  €10,500,000 €11,000,000  +31.3%      68.0    → +2.1%    HOLD            Current P/L: +31.3%
                                                  -4.5%                                                         | 🔥🔥🔥 SOS: Very Difficult next 3 (-10 pts)

... (all 25 players) ...
────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

📋 Recommendations: 1 SELL, 24 HOLD

⚠️  Found 1 player(s) you should consider selling!

Why players are on HOLD:
  • 12x: Current P/L: +X% (value: Y/100)
  • 8x: Starter - don't sell
  • 3x: High performer - don't sell
  • 1x: Rising trend - may rise more
```

## Weekly Routine

For best results:

### Week 1: Setup

```bash
# Record all purchase prices
rehoboam record-purchase "Player 1" <price>
rehoboam record-purchase "Player 2" <price>
...

# Run first analysis
rehoboam analyze
```

### Week 2-3: Build History

```bash
# Run 2-3 times per week
rehoboam analyze

# Bot is learning:
# - Peak values
# - Trends
# - Best selling windows
```

### Week 4+: Automated Sell Signals

```bash
# Run weekly or after matches
rehoboam analyze

# Bot now catches:
# ✅ Peaked players (like Sinani)
# ✅ Difficult schedules ahead
# ✅ Falling trends
# ✅ Stop-losses triggered
```

## Troubleshooting

### "No sell recommendations but I have bad players"

Check if they're protected:

```toml
# In config.toml
[trading]
never_sell_starters = true  # Protecting starters?
min_points_to_keep = 50     # Too low? Bad players still "high performers"?
min_squad_size = 20         # At squad minimum?
```

### "I know Sinani peaked but bot says HOLD"

Likely causes:

1. No purchase price recorded → Can't detect profit to take
1. Insufficient historical data → Can't detect peak yet
1. Player is a starter → Protected from selling

Solution: Record purchase price + wait for history

### "Peak value is wrong"

First run = peak will be current value
After several runs = peak will be accurate historical maximum

### "All trends show 'unknown'"

Need at least 2 snapshots over 14 days
Run `rehoboam analyze` more frequently

## Summary

✅ **Always shows full squad** (like buy table)
✅ **Record purchase prices** for profit tracking
✅ **Run weekly** to build history
✅ **Peak detection** after ~7 days
✅ **Trend analysis** after ~14 days
✅ **Diagnostic output** explains why HOLD

The more you run it, the smarter it gets! 🎯
