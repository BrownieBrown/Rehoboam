# Running Rehoboam with Docker

Docker makes it easy to run the trading bot in the background without worrying about virtual environments or keeping terminals open.

## Quick Start

### 1. Build the Docker Image

```bash
docker-compose build
```

### 2. Test First (Dry Run)

Before running live trades, test with dry-run mode:

```bash
# Edit docker-compose.yml and add to the command section:
#   --dry-run

docker-compose up
```

Press `Ctrl+C` to stop and check the logs to see what it would do.

### 3. Run in Background (Live Trading)

Once you're happy with dry run:

```bash
# Start the bot in background
docker-compose up -d

# View logs in real-time
docker-compose logs -f

# Stop the bot
docker-compose down
```

## Configuration

Edit `docker-compose.yml` to adjust settings:

```yaml
command: >
  daemon
  --interval 180          # Run every 3 hours (180 minutes)
  --max-trades 3          # Max 3 trades per session
  --max-spend 30000000    # Max €30M per day
  --start-hour 8          # Start trading at 8 AM
  --end-hour 22           # Stop trading at 10 PM
  --dry-run               # Add this line to test without real trades
```

### Current Settings (Conservative)

**Interval:** Every 3 hours (good while you have pending bids)
**Max Trades:** 3 per session
**Max Spend:** €30M per day
**Trading Hours:** 8 AM - 10 PM

### Aggressive Settings (After Bids Resolve)

```yaml
command: >
  daemon
  --interval 120          # Every 2 hours
  --max-trades 5          # Up to 5 trades per session
  --max-spend 100000000   # €100M per day (uses debt capacity!)
  --start-hour 8
  --end-hour 23           # Trade until 11 PM
```

## Useful Commands

```bash
# Start bot
docker-compose up -d

# View logs
docker-compose logs -f

# View just last 50 lines
docker-compose logs --tail=50

# Stop bot
docker-compose down

# Restart bot (after config changes)
docker-compose restart

# Rebuild (after code changes)
docker-compose build
docker-compose up -d

# Run manual analysis
docker-compose run --rm manual analyze

# Run one-off trade session
docker-compose run --rm manual auto --dry-run
```

## Viewing Logs

**Real-time logs:**

```bash
docker-compose logs -f
```

**Logs are also saved to your local machine:**

```bash
# View saved logs
cat ~/.rehoboam/logs/auto_trader_$(date +%Y%m%d).log

# Tail saved logs
tail -f ~/.rehoboam/logs/auto_trader_$(date +%Y%m%d).log
```

## Checking Status

```bash
# Is the bot running?
docker-compose ps

# What's the bot doing right now?
docker-compose logs --tail=20
```

## Environment Variables

The bot reads credentials from `.env` file:

```bash
KICKBASE_EMAIL=your-email@example.com
KICKBASE_PASSWORD=your-password
```

## Timezone

The default timezone is `Europe/Berlin`. Change it in `docker-compose.yml`:

```yaml
environment:
  - TZ=Europe/London  # Or your timezone
```

## Troubleshooting

**Bot not starting?**

```bash
# Check logs
docker-compose logs

# Test credentials
docker-compose run --rm manual login
```

**Want to change trading schedule?**

```bash
# 1. Edit docker-compose.yml
# 2. Restart
docker-compose restart
```

**Want to stop trading immediately?**

```bash
docker-compose down
```

## What the Bot Does Each Session

1. ✅ Login to KICKBASE
1. ✅ Check your active bids (Kohr €9.2M, Hack €7.2M)
1. ✅ Find profit opportunities (10+ found)
1. ✅ Execute up to 3 trades (using €95.4M debt capacity)
1. ✅ Find lineup improvements (16,756 trades analyzed)
1. ✅ Log all activity
1. 💤 Sleep until next interval (3 hours)

## Example Output

```
2025-11-10 19:39:49 - Starting trading session
2025-11-10 19:39:50 - Active bids: 2
  - Dominik Kohr: €9,281,213
  - Robin Hack: €7,226,114
2025-11-10 19:39:51 - Found 10 profit opportunities
2025-11-10 19:39:52 - Buying Lucas Höler for €7,972,792
2025-11-10 19:39:53 - ✓ Trade executed
2025-11-10 19:39:54 - Session complete: 3 trades, €24M spent
2025-11-10 19:39:55 - Next run: 2025-11-10 22:39:55
```

## Safety Features

- ✅ **Max trades limit:** Won't execute more than X trades per session
- ✅ **Max daily spend:** Won't exceed daily budget limit
- ✅ **Trading hours:** Only trades during configured hours
- ✅ **Dry run mode:** Test without executing real trades
- ✅ **Bid awareness:** Won't double-bid unless 5%+ higher value
- ✅ **Debt capacity management:** Uses €93.7M for flips, tracks exposure

______________________________________________________________________

**Pro Tip:** Start with dry-run mode and watch for a few cycles before going live!
