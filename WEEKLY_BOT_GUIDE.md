# Weekly Bot Usage Guide

## Daily Workflow

### Every Morning (Before Market Activity)

```bash
# Activate environment
source venv/bin/activate

# Run full analysis
rehoboam analyze

# Review recommendations and decide which to execute
```

### What You'll See

**1. Profit Trading Opportunities**

- Players to buy and flip for quick profit
- Expected profit percentage
- Hold time recommendation
- Risk score

**2. Lineup Improvement Trades**

- N-for-M player swaps
- Expected points improvement
- Value score improvement
- Budget requirements

**3. Squad Analysis**

- Current players' value scores
- Sell recommendations with reasons
- Trend analysis (rising/falling)
- Peak analysis (declining players)

## Decision Making

### Profit Trading

**Look for:**

- ✅ Rising trends (+15% or more)
- ✅ Players 20-40% below peak
- ✅ Low risk scores (\<30)
- ✅ 10%+ profit potential

**Execute if:**

- You have budget/debt capacity
- More than 3 days until next match
- Risk score acceptable

### Lineup Trading

**Look for:**

- ✅ +2 or more points/week improvement
- ✅ Rising trend players IN, falling trend players OUT
- ✅ Affordable within budget
- ✅ Formation requirements met

**Execute if:**

- Clear improvement shown
- Budget allows (can go into debt if >3 days to match)
- All target players available

### Sell Decisions

**Sell immediately if:**

- ✅ Player is "declining" (below peak + falling trend)
- ✅ Profit target reached (>10% gain)
- ✅ Stop loss triggered (\<-5% loss)
- ✅ Very difficult schedule ahead + profitable

**Hold if:**

- Player rising and below peak (recovery potential)
- Match day approaching and squad at minimum
- Small loss but trend improving

## Manual Tracking (Until Learning is Added)

### Track Your Trades in a Spreadsheet

**For Each Trade:**

```
Date | Player | Action | Price | Reason | Outcome
-----|--------|--------|-------|--------|--------
11/10| Höler  | BUY    | 8.0M  | Rising +28%, 31% below peak | Pending
11/12| Höler  | SELL   | 8.8M  | Hit 10% profit | +800K (+10%)
```

**Track Success Rates:**

- Profit trades: X wins / Y total = Z% success
- Lineup trades: Points gained per week
- Sell signals: How often correct

### Weekly Review

**Every Sunday:**

1. Calculate total profit/loss
1. Review which signals worked
1. Note any patterns
1. Adjust strategy if needed

## Example Week

**Monday:**

```bash
rehoboam analyze
```

Finds:

- Robin Hack: Rising +22%, 64% below peak → BUY for flip
- Lucas Höler: Rising +28%, 31% below peak → BUY for flip
- Your player "Nicolas": At profit, but rising +12% → HOLD

Decision: Buy Höler (lower risk, closer to peak)

**Tuesday-Thursday:**
Monitor Höler's value. Check daily:

```bash
rehoboam analyze
```

**Friday (Match in 2 days):**

```bash
rehoboam analyze
```

Höler now +10% profit → Bot recommends SELL (hit target)
Decision: SELL before match day

**Result:** +800K profit in 4 days!

## Tips for Success

### Budget Management

**Always leave buffer:**

- Don't use 100% of debt capacity
- Reserve 20% for emergencies
- Must be positive by match day

**Calculate available:**

```
Current budget: €3.5M
Team value: €170M
Max debt (60%): €102M
Available for flips: €105.5M
```

### Timing

**Best times to trade:**

- Early in week (5+ days to match)
- After good player performance (value rising)
- When bot shows multiple rising opportunities

**Avoid:**

- 1-2 days before match (must be positive)
- During matches (prices volatile)
- When squad at minimum size

### Risk Management

**For profit trading:**

- Start with 1-2 players
- Use low-risk opportunities first (\<30 risk score)
- Don't invest >50% of budget in one player

**For lineup trading:**

- Prefer 1-for-1 or 2-for-2 (simpler)
- Must improve by at least 2 points/week
- Check all players available before selling

## Troubleshooting

**"No profit opportunities found"**

- Check your budget/debt capacity
- Try lowering min_profit_pct in config (from 10% to 8%)
- Wait for market conditions to improve

**"Bot recommends selling all my players"**

- This is normal if you have falling/peaked players
- Doesn't mean sell all at once
- Look for lineup improvement trades instead

**"I can't afford the lineup trades"**

- You have debt capacity - can go negative if >3 days to match
- Sell declining players first to get budget
- Start with cheaper 1-for-1 trades

## Configuration Tuning

Edit `.env` to adjust bot behavior:

```bash
# More aggressive profit trading
MIN_SELL_PROFIT_PCT=8.0  # From 10.0

# More conservative (less risky)
MAX_DEBT_PCT_OF_TEAM_VALUE=40.0  # From 60.0

# Require better trades
MIN_VALUE_SCORE_TO_BUY=50.0  # From 40.0
```

## Success Metrics

**Track these weekly:**

- Total profit made
- Squad value increase
- Points per week improvement
- Trade success rate

**Good targets:**

- 5-10% budget increase per week (profit trading)
- +2-5 points per week (lineup improvement)
- 70%+ trade success rate
