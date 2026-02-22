# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Rehoboam is a bot for KICKBASE (fantasy football/soccer platform). The goal is to **win the league** — which means maximizing matchday points, not market value profits.

## How Kickbase Works (What Actually Wins)

**Game mode: Seasonal, Total Points.** The league champion is whoever accumulates the most total points across the entire season. There is no head-to-head, no captain mechanic, no vice-captain. Every matchday point counts equally toward the final ranking.

Kickbase tracks 100+ in-game actions per match and converts them to fantasy points in real-time. **You win by scoring more total matchday points than your opponents over the full season.**

### Scoring Rules That Matter

- **Total points across the season** determine the winner. Consistency every single matchday is what wins.
- **Empty lineup position = -100 points penalty**. Always fill every slot.
- **Negative budget at kickoff = ZERO points for the entire matchday**. Budget must be >= 0 when games start.
- **No captain mechanic** in Seasonal/Total Points mode. All 11 starters score equally.
- **Double gameweeks**: Players from teams with 2 matches accumulate points from both games.
- **10 formations available**: Tactical flexibility to maximize points from your best performers.

### What This Means for the Bot

Since every matchday point accumulates toward the season total, the winning strategy is:

1. **Maximize expected points every single matchday** — pick the best starting 11 for THIS week's fixtures
1. **Acquire high-scoring players** — buy players who score the most matchday points, even if "overpriced" by market value
1. **Never lose points to penalties** — no empty slots (-100), no negative budget (0 pts for entire matchday)
1. **Exploit double gameweeks** — DGW players play twice, effectively doubling their point contribution

The bot started as a market value trader (buy low, sell high). Recent work has shifted toward matchday points optimization, but the core trade workflow is still driven by market value trends and `value_score`. **The strategic priority is to repoint decisions around expected matchday points, not market appreciation.**

Current state:

- `value_score` (0-100): Still weighted toward market value factors (trend, discount, momentum) with average_points as a quality gate (max 40 pts)
- `expected_points`: Calculator exists and powers the `lineup` command, but is NOT used in buy/sell decisions
- Double gameweek awareness: Not implemented
- Budget-at-kickoff safety: Basic 24-48h buffer exists

### Strategic Priorities (in order of impact)

1. **Expected points in the main `analyze` flow** — The `lineup` command already computes EP per player, but `analyze` (the main command) still uses value_score for best-11 selection. Wire EP into analyze so the user sees the right data.
1. **Buy/sell for matchday points** — Reweight trade decisions around expected_points, not market value appreciation. A player who scores 80 pts/game but is "overpriced" by market value is still a great buy.
1. **Double gameweek exploitation** — Detect DGW schedules, prioritize those players. Buy 7-10 days before DGW.
1. **Budget safety** — Hard block on going negative before kickoff. Warn early (48h+), auto-suggest sells to stay solvent.
1. **Squad size enforcement** — At 15/15, trade table (sell→buy swaps) is now shown instead of plain buy recommendations. Never suggest adding a 16th player without a paired sell.

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

### Display Layer (`compact_display.py`)

- `CompactDisplay`: Main display for the `analyze` command
- **Trade mode**: When squad is full (15/15), buy recommendations are shown as sell→buy swap pairs with net cost instead of plain buys. Uses `_build_trade_pairs()` to match each buy with a sell target (natural upgrade swap first, then most expendable).
- `display_lineup()`: Shows optimal starting 11 + bench based on expected points

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

## Lessons Learned & Development History

### The core mistake: optimizing for the wrong metric

The bot was built as a **market value trader** — buy undervalued players, ride appreciation, sell at profit. This is useful for building budget, but **Kickbase is won by matchday points, not team market value**. A league winner with a €80M squad that scores 200 pts/week beats a €120M squad scoring 150 pts/week.

### What we built that matters

- **Expected points calculator** — Estimates matchday output from form, consistency, fixture difficulty, lineup probability. This is the right metric to optimize for and should drive all decisions.
- **Average points as primary quality gate** — Players must average ≥20 pts/game to be recommended (≥10 for emergency fills). This filters out "cheap but useless" players.
- **Consistency + starter bonuses** — Reliable performers who actually play are rewarded in scoring.
- **Lineup command** — Shows optimal 11 based on expected points, not market value.
- **Trade pairs at full squad** — When at 15/15, shows actionable sell→buy swaps with net cost instead of impossible plain buys.

### What we built that turned out to be less important

- **Market value trend analysis** (14d/30d/90d trends) — Useful for trading profit but doesn't directly help win matchdays.
- **Flip opportunities** — Quick-profit trades that churn the squad without improving matchday output.
- **Demand score / market momentum** — Predicts price movement, not on-pitch performance.
- **Overbid percentage optimization** — Helps win auctions but doesn't help pick the right players to bid on.

### Data available but underused

| Data                 | Where it exists    | How it's used               | How it should be used                                       |
| -------------------- | ------------------ | --------------------------- | ----------------------------------------------------------- |
| `average_points`     | Player object      | Quality gate (≥20 to buy)   | Primary driver of all buy/sell decisions                    |
| `expected_points`    | ExpectedPointsCalc | `lineup` command only       | Should drive buy/sell/hold decisions too                    |
| `consistency_score`  | Performance data   | Small penalty/bonus         | Major factor — consistent 60pts > volatile 40-100pts        |
| `lineup_probability` | Player status      | Expected points calc        | Should penalize buy recommendations for bench-warmers       |
| `minutes_trend`      | Performance data   | Starter bonus if increasing | Should hard-block declining-minutes players                 |
| Fixture difficulty   | SOS rating         | ±10 value score             | Should weight ±20-30 for next 3 fixtures, drive sell timing |
| Double gameweeks     | Not implemented    | N/A                         | DGW players play twice, ~1.8x expected points               |

### Guiding principles going forward

1. **Every feature should answer: "does this help score more matchday points?"** If not, it's low priority.
1. **No captain in our mode (Seasonal/Total Points).** All 11 starters score equally. Focus on having the best possible starting 11 every week.
1. **Don't over-engineer market analysis.** Simple "buy good scorers, sell bad scorers" beats sophisticated market-value prediction for winning leagues.
1. **Budget is a constraint, not an objective.** Profit from trades is only useful if it lets you buy better point-scorers. Don't hold a declining player "because they might recover value" if they're dragging matchday scores down.
1. **The bot should tell the user what to do THIS WEEK.** Captain pick, lineup, and one or two trades max. Not a wall of 15 analyses.
