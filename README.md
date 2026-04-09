# Rehoboam - KICKBASE Trading Bot

An automated trading bot for KICKBASE that maximizes matchday points through smart player acquisition, lineup optimization, and sell/buy swaps.

## Features

- Automated player trading based on expected matchday points (EP scoring pipeline)
- Optimal lineup setting before each matchday
- Sell/buy swap execution when squad is full (15/15)
- Learning system that improves bidding over time
- Budget safety checks to prevent negative budget at kickoff
- Dry-run mode for testing strategies without real trades

## Installation

```bash
python3 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
```

## Configuration

```bash
cp .env.example .env
# Edit .env with your KICKBASE credentials
```

Key settings in `.env`:

- `KICKBASE_EMAIL`, `KICKBASE_PASSWORD` — Credentials
- `DRY_RUN` — Set to `false` to enable live trading (default: `true`)
- `MAX_DEBT_PCT_OF_TEAM_VALUE` — How aggressively to go negative for buys (default: 60%)

## Usage

```bash
rehoboam login              # Test credentials
rehoboam analyze            # EP-first action plan (buy/sell by matchday points)
rehoboam auto               # One-shot automated trading session
rehoboam auto --live        # Live trading (DRY_RUN=false)
```

## Deployment (Azure Functions)

The bot runs automatically on Azure Functions (Consumption plan, free tier). It executes 2x daily at 10:00 and 22:00 Europe/Berlin.

```bash
# Deploy to Azure (requires Azure CLI + Functions Core Tools)
./deploy/deploy_azure.sh

# Go live
az functionapp config appsettings set -n func-rehoboam -g rg-rehoboam --settings DRY_RUN=false
```

See `deploy/` for full deployment configuration.

## Safety

- Always test with `DRY_RUN=true` first
- This bot is unofficial and not affiliated with KICKBASE
- Use at your own risk

## Legal

This project is for educational purposes only. Make sure to comply with KICKBASE's terms of service.
