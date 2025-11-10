# Automated Trading Guide

## Overview

Rehoboam can trade automatically for you in the background, executing both **profit trades** (buy low, sell high) and **lineup improvements** (N-for-M player swaps).

## ⚠️ Important Safety Features

The bot includes multiple safety mechanisms:

✅ **Daily Spend Limit** - Max €50M per day by default
✅ **Max Trades Per Session** - Max 3 trades per run
✅ **Trading Hours** - Only trades during specified hours (8 AM - 10 PM default)
✅ **Budget Checks** - Won't trade if can't afford
✅ **Formation Validation** - Won't break squad requirements
✅ **Match Day Awareness** - Adjusts strategy near game day
✅ **Comprehensive Logging** - Everything logged to `~/.rehoboam/logs/`
✅ **Dry Run Mode** - Test without executing

## Quick Start

### Option 1: Single Session (Manual Trigger)

Run one trading session now:

```bash
# Test with dry run first
rehoboam auto --dry-run

# Execute for real
rehoboam auto

# Custom settings
rehoboam auto --max-trades 5 --max-spend 100000000
```

### Option 2: Automated Daemon (Background)

Run continuously in background:

```bash
# Test with dry run first
rehoboam daemon --dry-run

# Run for real
rehoboam daemon

# Custom schedule (every hour, 6 AM - 11 PM)
rehoboam daemon --interval 60 --start-hour 6 --end-hour 23
```

### Option 3: System Service (Always Running)

Set up as a system service that starts automatically.

## Configuration

### Command Line Options

**`rehoboam auto`** - Single session

```bash
--dry-run              # Simulate without executing
--max-trades 3         # Max trades per session
--max-spend 50000000   # Max daily spend (€50M)
--league-index 0       # Which league to trade in
```

**`rehoboam daemon`** - Continuous trading

```bash
--dry-run              # Simulate without executing
--interval 120         # Minutes between sessions
--start-hour 8         # Trading start hour (0-23)
--end-hour 22          # Trading end hour (0-23)
--max-trades 3         # Max trades per session
--max-spend 50000000   # Max daily spend (€50M)
```

### Environment Variables

Edit `.env` to tune trading behavior:

```bash
# Profit trading
MIN_SELL_PROFIT_PCT=10.0        # Min profit % to buy
MAX_DEBT_PCT_OF_TEAM_VALUE=60.0 # Max debt for flips

# Lineup trading
MIN_VALUE_SCORE_TO_BUY=40.0     # Min value score to buy
MIN_BUY_VALUE_INCREASE_PCT=5.0  # Min improvement needed

# Safety
MIN_SQUAD_SIZE=11               # Min squad size to maintain
```

## How It Works

### Every Trading Session:

1. **Login** to KICKBASE
1. **Analyze Market** for profit opportunities
1. **Execute Profit Trades** (buy undervalued players)
1. **Analyze Lineup** for improvement opportunities
1. **Execute Lineup Trades** (N-for-M swaps)
1. **Log Results** and wait for next session

### Profit Trading Logic:

- Finds players with rising trends (+15%+)
- Identifies players 20-40% below peak
- Filters by risk score (\<50)
- Executes top 3 opportunities
- Uses debt capacity if >3 days to match

### Lineup Trading Logic:

- Analyzes current best 11
- Finds N-for-M trades that improve lineup
- Must improve by +2 pts/week minimum
- Only executes 1 lineup trade per session (safety)
- Buys all players first, then sells

## Running as a Service

### macOS (launchd)

```bash
# 1. Edit the plist file with your paths
nano setup/com.rehoboam.trading.plist

# 2. Copy to LaunchAgents
cp setup/com.rehoboam.trading.plist ~/Library/LaunchAgents/

# 3. Load and start
launchctl load ~/Library/LaunchAgents/com.rehoboam.trading.plist
launchctl start com.rehoboam.trading

# Check status
launchctl list | grep rehoboam

# View logs
tail -f ~/.rehoboam/logs/daemon.log

# Stop
launchctl stop com.rehoboam.trading
launchctl unload ~/Library/LaunchAgents/com.rehoboam.trading.plist
```

### Linux (systemd)

```bash
# 1. Edit the service file with your paths
sudo nano setup/rehoboam.service

# 2. Copy to systemd
sudo cp setup/rehoboam.service /etc/systemd/system/

# 3. Enable and start
sudo systemctl daemon-reload
sudo systemctl enable rehoboam
sudo systemctl start rehoboam

# Check status
sudo systemctl status rehoboam

# View logs
sudo journalctl -u rehoboam -f

# Stop
sudo systemctl stop rehoboam
sudo systemctl disable rehoboam
```

### Windows (Task Scheduler)

```powershell
# 1. Open Task Scheduler
# 2. Create Basic Task
# 3. Trigger: At startup
# 4. Action: Start a program
#    Program: C:\path\to\venv\Scripts\python.exe
#    Arguments: -m rehoboam.scheduler
#    Start in: C:\path\to\rehoboam
# 5. Enable "Run whether user is logged on or not"
```

## Monitoring

### View Logs

```bash
# Today's log
tail -f ~/.rehoboam/logs/auto_trader_$(date +%Y%m%d).log

# Real-time daemon output
tail -f ~/.rehoboam/logs/daemon.log

# All logs
ls -lh ~/.rehoboam/logs/
```

### Log Format

```
2025-11-10 10:30:15 - INFO - Starting trading session
2025-11-10 10:30:20 - INFO - Found 5 profit opportunities
2025-11-10 10:30:25 - INFO - Buying Robin Hack for €6,721,967
2025-11-10 10:30:28 - INFO - ✓ Buy order placed for Robin Hack
2025-11-10 10:30:30 - INFO - Session complete: 1/1 profit trades, €6.7M spent
```

### Check Trade Activity

```bash
# Manual check
rehoboam analyze

# See current squad
# See profit opportunities
# See lineup recommendations
```

## Examples

### Conservative (Safe Settings)

```bash
rehoboam daemon \
  --interval 180 \          # Every 3 hours
  --start-hour 9 \          # 9 AM
  --end-hour 20 \           # 8 PM
  --max-trades 2 \          # Max 2 trades
  --max-spend 30000000      # €30M max
```

### Aggressive (More Trading)

```bash
rehoboam daemon \
  --interval 60 \           # Every hour
  --start-hour 6 \          # 6 AM
  --end-hour 23 \           # 11 PM
  --max-trades 5 \          # Max 5 trades
  --max-spend 100000000     # €100M max
```

### Test Mode (No Execution)

```bash
# Test for a day to see what it would do
rehoboam daemon --dry-run --interval 60

# Check logs to see opportunities
tail -f ~/.rehoboam/logs/daemon.log
```

## Safety Recommendations

### Week 1 (This Week): Start Conservative

```bash
# Run manually with dry-run
rehoboam auto --dry-run

# Check results
# If looks good, run for real once
rehoboam auto

# Monitor the trade
# If successful, enable daemon with safe settings
rehoboam daemon --interval 180 --max-trades 2
```

### Week 2+: Increase Automation

After successful trades:

- Lower interval to 120 minutes
- Increase max trades to 3-5
- Extend trading hours if desired
- Set up as system service

## Troubleshooting

### "No opportunities found"

- Check budget/debt capacity
- Try during different market hours
- Lower min_profit_pct in .env
- Wait for better market conditions

### "Daily spend limit reached"

- Bot hit €50M limit (safety feature)
- Wait until midnight (auto-resets)
- Or increase --max-spend

### "Cannot afford player"

- Normal - bot only trades within budget
- Will try next session when budget available
- Check if debt capacity being used

### Daemon not starting

```bash
# Check logs
cat ~/.rehoboam/logs/daemon-error.log

# Test manually first
rehoboam daemon --dry-run

# Check credentials in .env
cat .env | grep KICKBASE
```

### Trades not executing

1. Check dry-run is OFF
1. Verify credentials in .env
1. Check logs for errors
1. Ensure within trading hours
1. Check daily limits not exceeded

## Monitoring Best Practices

### Daily Checks (First Week)

```bash
# Morning: Check overnight activity
tail -50 ~/.rehoboam/logs/auto_trader_$(date +%Y%m%d).log

# Review what bot did
rehoboam analyze

# Check budget/team value
```

### Weekly Review

- Total profit made
- Number of trades executed
- Success rate (from logs)
- Adjust settings if needed

## Stopping Automated Trading

### Temporary (Keep Config)

```bash
# If running in terminal
Ctrl+C

# If running as service (macOS)
launchctl stop com.rehoboam.trading

# If running as service (Linux)
sudo systemctl stop rehoboam
```

### Permanent

```bash
# macOS
launchctl unload ~/Library/LaunchAgents/com.rehoboam.trading.plist
rm ~/Library/LaunchAgents/com.rehoboam.trading.plist

# Linux
sudo systemctl stop rehoboam
sudo systemctl disable rehoboam
sudo rm /etc/systemd/system/rehoboam.service
sudo systemctl daemon-reload
```

## FAQ

**Q: Will it trade while I'm sleeping?**
A: Only within trading hours (8 AM - 10 PM default). Configure with --start-hour and --end-hour.

**Q: What if I want to trade manually?**
A: You can do both! Bot won't interfere. Just avoid double-buying same players.

**Q: How much will it spend?**
A: Max €50M per day by default. Configure with --max-spend.

**Q: Will it sell my best players?**
A: No - it only executes trades that improve your lineup. Won't sell unless replacing with better.

**Q: What if it makes a bad trade?**
A: Safety limits prevent disasters. Max 3 trades per session, daily spending cap. You can always override manually.

**Q: Can I pause it?**
A: Yes - Ctrl+C if running in terminal, or `launchctl stop` / `systemctl stop` if service.

**Q: Should I use dry-run first?**
A: YES! Always test with --dry-run for a day before enabling real trades.

## Next Steps

1. **Test with dry-run**: `rehoboam auto --dry-run`
1. **Review results**: Check logs
1. **Single real session**: `rehoboam auto`
1. **Enable daemon**: `rehoboam daemon --interval 180`
1. **Monitor for a week**: Check logs daily
1. **Set up as service**: Follow platform instructions above
1. **Adjust settings**: Based on results

**Remember**: Start conservative, monitor closely, adjust as you gain confidence!
