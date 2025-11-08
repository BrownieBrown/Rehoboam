# Rehoboam - KICKBASE Trading Bot

An automated trading bot for KICKBASE that helps you buy and sell players based on market value tracking.

## Features

- 🤖 Automated player trading based on market value changes
- 📊 Market analysis and player value tracking
- 💰 Configurable profit thresholds and risk management
- 🔒 Dry-run mode for testing strategies without real trades
- 🎨 Beautiful CLI interface with rich output
- 🛡️ **Smart safeguards to protect your team:**
  - Never sells players in your starting 11
  - Maintains minimum squad size (11 players)
  - Protects high-performing players
  - Only buys from KICKBASE (filters out user listings)
  - Ensures you can always field a complete lineup

## Installation

### Option 1: Docker (Recommended for 24/7 Trading)

```bash
# 1. Set up configuration
cp .env.example .env
nano .env  # Add your KICKBASE credentials

# 2. Start the bot (runs every 4 hours automatically)
docker-compose up -d

# 3. View logs
docker-compose logs -f
```

See [DOCKER.md](DOCKER.md) for full Docker documentation.

### Option 2: Local Python Installation

1. Create a virtual environment:
```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

2. Install the package:
```bash
pip install -e .
```

3. Set up your configuration:
```bash
cp .env.example .env
# Edit .env with your KICKBASE credentials and trading preferences
```

## Usage

```bash
# Show help
rehoboam --help

# Login to KICKBASE
rehoboam login

# Analyze the market
rehoboam analyze

# Start automated trading (dry-run mode)
rehoboam trade

# Start live trading (be careful!)
rehoboam trade --live
```

## Configuration

Edit `.env` to configure:

**KICKBASE Credentials:**
- `KICKBASE_EMAIL` - Your KICKBASE account email
- `KICKBASE_PASSWORD` - Your KICKBASE password

**Trading Strategy:**
- `MIN_SELL_PROFIT_PCT` - Minimum profit % to trigger a sell (default: 5%)
- `MAX_LOSS_PCT` - Maximum loss % before stop-loss (default: -3%)
- `MIN_BUY_VALUE_INCREASE_PCT` - Minimum market value increase to buy (default: 10%)

**Budget Management:**
- `MAX_PLAYER_COST` - Maximum to spend on a single player (default: €5M)
- `RESERVE_BUDGET` - Always keep this much in reserve (default: €1M)

**Squad Safeguards (Protects your team):**
- `MIN_SQUAD_SIZE` - Minimum squad size to maintain (default: 11)
- `NEVER_SELL_STARTERS` - Never sell starting 11 players (default: true)
- `MIN_POINTS_TO_KEEP` - Keep high performers above this threshold (default: 50)

**Safety:**
- `DRY_RUN` - Set to false to enable actual trading (default: true)

## How Trading Works (Bidding System)

**Important to understand:**
- The bot places **offers/bids** on players, not instant purchases
- Other users may also bid on the same player
- Highest bid wins when the auction period ends
- You won't know immediately if you won the bid
- The bot will continue to find opportunities every 4 hours

This is why scheduled runs work well - the bot continuously looks for new opportunities while waiting for bid results.

## Safety

⚠️ **Important**:
- Always test with `DRY_RUN=true` first
- Start with small budgets
- Review the trading strategy before going live
- This bot is unofficial and not affiliated with KICKBASE
- Understand the bidding system - you're making offers, not buying directly

## Future Features

- [ ] Lineup optimization
- [ ] WhatsApp trade notifications
- [ ] Advanced trading strategies
- [ ] Historical performance tracking
- [ ] Multi-league support

## Legal

This project is for educational purposes only. Use at your own risk. Make sure to comply with KICKBASE's terms of service.
