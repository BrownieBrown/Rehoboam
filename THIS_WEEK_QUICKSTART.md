# This Week: Automated Trading Quickstart

## 🚀 Get Trading in 5 Minutes

### Step 1: Test with Dry Run (2 minutes)

```bash
source venv/bin/activate
rehoboam auto --dry-run
```

**What you'll see:**

- Simulated profit opportunities
- Simulated lineup improvements
- What bot WOULD do (no actual trades)

### Step 2: Run One Real Session (2 minutes)

```bash
rehoboam auto
```

**What happens:**

- Finds top 3 profit opportunities
- Executes buy orders
- Finds top lineup improvement
- Executes if beneficial
- Shows summary

### Step 3: Enable Background Trading (1 minute)

```bash
# Start daemon (runs every 2 hours, 8 AM - 10 PM)
rehoboam daemon
```

**Keep terminal open** or set up as service (see AUTO_TRADING_GUIDE.md)

## ⚙️ Recommended Settings for This Week

**Conservative (Recommended for first week):**

```bash
rehoboam daemon \
  --interval 180 \          # Every 3 hours
  --max-trades 2 \          # Max 2 trades per run
  --max-spend 30000000      # Max €30M per day
```

## 📊 What to Expect

**Profit Trading:**

- Bot finds rising players (+15%+ trend)
- Buys 1-2 per session
- Typical hold: 3-7 days
- Target: 10%+ profit per flip

**Lineup Trading:**

- Bot finds N-for-M improvements
- Executes 0-1 per session (very selective)
- Must improve by +2 pts/week minimum
- Uses debt capacity if needed

## 🔍 Monitoring

**View activity:**

```bash
# Check logs
tail -f ~/.rehoboam/logs/auto_trader_$(date +%Y%m%d).log

# Manual check
rehoboam analyze
```

**Daily routine:**

```bash
# Morning: See what bot did overnight
tail -50 ~/.rehoboam/logs/auto_trader_$(date +%Y%m%d).log

# Check current state
rehoboam analyze
```

## ⚠️ Safety Features Active

✅ Max 2-3 trades per session
✅ Max €50M spend per day
✅ Only trades 8 AM - 10 PM
✅ Won't break squad requirements
✅ Budget checks before every trade
✅ Match day awareness
✅ Full logging

## 🎯 Expected Results (This Week)

**Realistic targets:**

- 3-10 profit trades executed
- 0-2 lineup improvements
- €2-5M profit (5-10% of budget)
- 1-3 points/week improvement

**Track in spreadsheet:**

```
Date | Player | Action | Price | Result
-----|--------|--------|-------|-------
11/10| Höler  | BUY    | 8.0M  | Pending
11/12| Höler  | SELL   | 8.8M  | +800K ✓
```

## 🛑 How to Stop

**Temporarily:**

```bash
Ctrl+C  # If running in terminal
```

**Permanently:**

```bash
# Kill daemon, delete service files
```

## 🔧 Troubleshooting

**"No opportunities found"**
→ Normal! Market conditions vary. Bot will find opportunities when they appear.

**"Cannot afford"**
→ Normal! Bot only trades within budget. Wait for budget to free up.

**Bot too aggressive/conservative**
→ Adjust --max-trades and --interval

## 📖 Full Documentation

- `AUTO_TRADING_GUIDE.md` - Complete automated trading guide
- `WEEKLY_BOT_GUIDE.md` - Manual trading guide
- `docs/LEARNING_SYSTEM_PROPOSAL.md` - Future learning system

## Next Week: Add Learning

After this week, we can add a learning system that:

- Tracks trade outcomes
- Calculates success rates
- Adjusts thresholds automatically
- Gets smarter over time

**For now**: Bot uses historical data and smart rules. Works great!

______________________________________________________________________

## Your Action Plan (Right Now!)

```bash
# 1. Test (dry run)
rehoboam auto --dry-run

# 2. If looks good, run once for real
rehoboam auto

# 3. If successful, enable background trading
rehoboam daemon --interval 180 --max-trades 2

# 4. Monitor for rest of week
tail -f ~/.rehoboam/logs/daemon.log
```

**That's it! Bot is now trading for you.** 🤖💰
