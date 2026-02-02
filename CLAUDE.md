# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Rehoboam is an automated trading bot for KICKBASE (German fantasy football/soccer platform). It analyzes player market values, identifies trading opportunities, and executes bids based on configurable strategies.

## Common Commands

```bash
# Install dependencies (including dev tools)
pip install -e ".[dev]"

# Run the CLI
rehoboam --help
rehoboam login              # Test credentials
rehoboam analyze            # Compact action plan (default)
rehoboam analyze --detailed # Full analysis with predictions
rehoboam trade --max 5      # Dry-run trading
rehoboam trade --live       # Live trading (prompts for confirmation)

# Code quality
black rehoboam/                        # Format code
ruff check rehoboam/ --fix             # Lint and auto-fix
bandit -r rehoboam/ -c pyproject.toml  # Security scan
mypy rehoboam/ --ignore-missing-imports # Type check

# Testing
pytest                                        # Run all tests
pytest tests/test_analyzer.py                 # Single file
pytest tests/test_analyzer.py::test_value_calculation  # Single test
pytest -m "not slow"                          # Skip slow tests
pytest --cov=rehoboam --cov-report=html       # Coverage report

# Pre-commit hooks
pre-commit install
pre-commit run --all-files
```

## Architecture

### Core Components

**API Layer** (`api.py`, `kickbase_client.py`):

- `KickbaseV4Client`: Low-level HTTP client for KICKBASE API v4
- `KickbaseAPI`: Higher-level wrapper used by the rest of the application
- Data classes: `User`, `League`, `Player`, `MarketPlayer`

**Trading System** (`trader.py`, `auto_trader.py`):

- `Trader`: Main orchestrator that combines analysis, bidding, and execution
- Integrates multiple analyzers and learners via dependency injection
- `auto_trade()` method executes the full trading workflow

**Analysis Layer** (`analyzer.py`, `enhanced_analyzer.py`, `value_calculator.py`, `roster_analyzer.py`):

- `MarketAnalyzer`: Core player evaluation with configurable factor weights
- `PlayerAnalysis`: Data class containing recommendation, confidence, value_score, factors
- `RosterAnalyzer`: Roster-aware buy recommendations based on squad composition
- Factor-based scoring system: base value, trends, matchups, discounts, roster impact

**Learning System** (`bid_learner.py`, `factor_weight_learner.py`, `historical_tracker.py`, `activity_feed_learner.py`):

- Adaptive bidding based on auction outcomes
- Factor weight optimization from historical recommendation results
- Competitor analysis from activity feed data

**CLI** (`cli.py`):

- Typer-based CLI with commands: `login`, `analyze`, `trade`, `monitor`, `auto`, `daemon`, `stats`
- Rich console output for formatted tables and status

### Roster-Aware Recommendations

The system uses position **minimums** (not quotas) to provide context-aware buy recommendations:

- GK: 1, DEF: 3, MID: 2, FW: 1 (total: 7 minimum for an 11-player lineup)
- Remaining 4 spots can be filled with any position

**Roster impact logic** (`roster_analyzer.py`, `analyzer.py`):

- Below minimum: Shows "fills gap" with +10 score bonus (high priority)
- At/above minimum: Shows upgrade comparison vs weakest player at position
- Players are **never filtered out** based on position count - roster impact is for display/scoring only

Key data classes:

- `RosterContext`: Position state (`current_count`, `minimum_count`, `is_below_minimum`, `weakest_player`)
- `RosterImpact`: Buy impact (`impact_type`: "fills_gap", "upgrade", "not_upgrade", "additional")

### Key Design Patterns

- **Dependency Injection**: Trader accepts optional learners (`bid_learner`, `activity_feed_learner`, `factor_weight_learner`)
- **Factor-based Scoring**: `ScoringFactor` and `FactorWeights` classes allow transparent and tunable recommendations
- **Configuration via Pydantic**: `Settings` class loads from `.env` with validation

### Data Flow

1. CLI command invokes `Trader` with settings and optional learners
1. `Trader.analyze_market()` fetches market data via `KickbaseAPI`
1. `MarketAnalyzer` evaluates players using factor weights → `PlayerAnalysis`
1. Bidding strategy calculates optimal bid amounts
1. Live mode executes bids via `api.buy_player()`
1. Learning systems record outcomes for future optimization

## Configuration

Settings loaded from `.env` (see `.env.example`):

- `KICKBASE_EMAIL`, `KICKBASE_PASSWORD`: Credentials
- `MIN_SELL_PROFIT_PCT`, `MAX_LOSS_PCT`: Trading thresholds
- `MIN_VALUE_SCORE_TO_BUY`: Minimum score (0-100) for buy recommendations
- `DRY_RUN`: Safety flag (default: true)

Constants in `config.py`:

- `POSITION_MINIMUMS`: Minimum players per position (GK:1, DEF:3, MID:2, FW:1)
- `MIN_UPGRADE_THRESHOLD`: Minimum value score gain to consider a player an upgrade (default: 10.0)

## Testing Notes

- Tests use mock credentials (`KICKBASE_EMAIL=test@example.com`)
- CI runs on Python 3.10, 3.11, 3.12
- Test markers: `slow`, `integration`
