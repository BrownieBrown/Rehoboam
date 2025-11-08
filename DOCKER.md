# Docker Deployment Guide

Run Rehoboam as a scheduled Docker container that automatically trades every 4 hours.

## Quick Start

### 1. Build and Start

```bash
# Build the Docker image
docker-compose build

# Start the bot (runs in background)
docker-compose up -d

# View logs
docker-compose logs -f
```

### 2. View Trading Logs

```bash
# Live logs
tail -f logs/trade.log

# Or via Docker
docker-compose logs -f rehoboam
```

### 3. Stop the Bot

```bash
docker-compose down
```

## Configuration

The bot uses your `.env` file mounted into the container. Edit `.env` to change settings:

```bash
nano .env
# Change settings
docker-compose restart  # Apply changes
```

## Trading Schedule

**Automatic trades every 4 hours:**
- 00:00 (midnight)
- 04:00 (4 AM)
- 08:00 (8 AM)
- 12:00 (noon)
- 16:00 (4 PM)
- 20:00 (8 PM)

Plus one trade immediately on startup.

### Change Schedule

Edit `docker/crontab`:
```bash
# Every 2 hours instead of 4
0 */2 * * * cd /app && /usr/local/bin/rehoboam trade --max 5 >> /var/log/rehoboam/trade.log 2>&1

# Every 6 hours
0 */6 * * * cd /app && /usr/local/bin/rehoboam trade --max 5 >> /var/log/rehoboam/trade.log 2>&1

# Specific times (e.g., 9 AM and 9 PM)
0 9,21 * * * cd /app && /usr/local/bin/rehoboam trade --max 5 >> /var/log/rehoboam/trade.log 2>&1
```

Then rebuild:
```bash
docker-compose build
docker-compose up -d
```

## Manual Commands

Run one-off commands without affecting the scheduled bot:

```bash
# Analyze market
docker-compose run --rm manual analyze

# Manual trade (dry-run)
docker-compose run --rm manual trade --max 3

# Check login
docker-compose run --rm manual login

# View config
docker-compose run --rm manual config
```

## Deployment Options

### Local Server

```bash
# Keep running 24/7
docker-compose up -d
```

### Cloud (AWS, DigitalOcean, etc.)

1. Copy project to server
2. Set up `.env` with credentials
3. Run `docker-compose up -d`
4. Monitor with `docker-compose logs -f`

### Raspberry Pi

Works perfectly on Raspberry Pi for 24/7 trading:

```bash
# Build for ARM architecture
docker-compose build
docker-compose up -d
```

## Monitoring

### Check if Bot is Running

```bash
docker-compose ps
```

### View Recent Trades

```bash
tail -50 logs/trade.log
```

### Check Next Scheduled Run

```bash
docker-compose exec rehoboam crontab -l
```

## Troubleshooting

### Bot Not Trading

```bash
# Check logs
docker-compose logs rehoboam

# Check cron is running
docker-compose exec rehoboam ps aux | grep cron

# Manually trigger a trade to test
docker-compose run --rm manual trade --max 1
```

### Login Issues

```bash
# Test credentials
docker-compose run --rm manual login

# If fails, check .env file
cat .env | grep KICKBASE
```

### Restart Bot

```bash
docker-compose restart
```

## Important Notes

### Bidding System

- The bot places **offers** on players, not instant purchases
- Other users may bid higher on the same player
- You won't know if you won until the auction ends
- The bot will try again on the next run (4 hours later)

### Dry Run Mode

- Default is `DRY_RUN=true` (safe mode)
- Change to `DRY_RUN=false` in `.env` for live trading
- Always test with dry-run first!

### Safeguards

The bot will NEVER:
- Sell players in your starting 11
- Reduce squad below 11 players
- Sell high-performing players (50+ points)
- Buy from user listings (only KICKBASE sellers)

## Logs Location

- **Container logs**: `docker-compose logs`
- **Trade logs**: `./logs/trade.log` (on host machine)
- **Inside container**: `/var/log/rehoboam/trade.log`

## Updating the Bot

```bash
# Pull latest changes (if using git)
git pull

# Rebuild container
docker-compose build

# Restart
docker-compose down
docker-compose up -d
```
