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

The bot started as a market value trader (buy low, sell high). It now runs a unified EP-first pipeline: scores players by expected matchday points, ranks buys by marginal EP gain (how much they improve the best-11), and uses budget-aware bidding with sell plans.

Current state (post-foundation tier, May 2026):

- `auto` command: Unified aggressive trading — single EP pipeline call, trade pairs compete with plain buys ranked by EP, matchday-aware aggressiveness (aggressive 5+d, moderate 2-4d, locked 0-1d), trend-aware profit selling, up to 10 trades/session (15 aggressive)
- `status` command: Read-only diagnostic — runs the full EP pipeline in dry-run mode so you can preview what `auto` would do
- `SmartBidding.calculate_ep_bid`: the only bidding path. Tier+demand+trend stack with optional learned overbid override from `auction_outcomes` (REH-30 fix).
- `BidLearner`: writes `auction_outcomes`, `flip_outcomes`, `matchday_outcomes` (REH-20), `predicted_eps` (REH-20), `team_value_history` (REH-23), `league_rank_history` (REH-24), `matchday_lineup_results` (REH-25), `player_mv_history` (REH-26). Scorer self-calibration loop fully wired via REH-20.
- Double gameweek awareness: EP calculator supports DGW multiplier (1.8x), DGW detection wired through `MatchupAnalyzer.detect_double_gameweek` + `is_dgw_team`.
- Budget-at-kickoff safety: 24-48h buffer + matchday-locked phase prevents trading 0-1 days before match.
- Structured logging (PR #27): all bidding decisions + override applications written to `logs/rehoboam.log` with rotating file handler.

### Strategic Priorities (in order of impact)

1. **Learning loops** (REH-32 through REH-37) — foundation tables now populated. Loss-cut threshold calibration, sell-timing peak-MV regret, buy-trait correlation, lineup quality scoring, rank-trajectory regression. Wait for ≥5 matchdays of post-deploy data, then ship one loop per PR.
1. **Per-manager transfer P&L (REH-38)** — `/managers/{mid}/dashboard.prft` for competitor flip-revenue intelligence. The one signal `/ranking` doesn't include.
1. **Double gameweek exploitation** — buy 7-10 days before a DGW. DGW detection is wired; a planning recommender that surfaces upcoming DGWs is not.
1. **Budget safety** — Hard block on going negative before kickoff is in place; could tighten the 48h+ early warning.
1. **Squad size enforcement** — At 15/15, the trade table (sell→buy swap pairs) is shown instead of plain buy recommendations.

## Common Commands

```bash
# Install dependencies (including dev tools)
pip install -e ".[dev]"

# Run the CLI (only login, auto, status are exposed)
rehoboam --help
rehoboam login              # Test credentials + list leagues
rehoboam status             # Read-only: show squad + dry-run what auto would do
rehoboam status -v          # Verbose: DEBUG-level decision logs to stderr
rehoboam auto --dry-run     # Simulate one trading session
rehoboam auto               # Live trading session
rehoboam auto --aggressive  # Up to 15 trades, lower EP threshold, +50% spend

# Code quality
black rehoboam/                        # Format code
ruff check rehoboam/ --fix             # Lint and auto-fix
bandit -r rehoboam/ -c pyproject.toml  # Security scan
mypy rehoboam/ --ignore-missing-imports # Type check

# Testing
pytest                                              # Run all tests
pytest tests/test_ep_bidding.py                     # Single file
pytest tests/test_scoring/                          # Whole subpackage
pytest tests/test_ep_bidding.py::TestEPBidTiers     # Single class
pytest -m "not slow"                                # Skip slow tests
pytest --cov=rehoboam --cov-report=html             # Coverage report

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

- `Trader`: Per-call EP pipeline (`get_ep_recommendations`, `get_ep_recommendations_with_trends`, `find_profit_opportunities`) — stateless, takes a league per call.
- `AutoTrader`: Session orchestrator. `run_full_session(league)` is the single entry point used by both the CLI `auto` command and the Azure Function timer trigger.

**EP Scoring Pipeline** (`scoring/models.py`, `scoring/scorer.py`, `scoring/collector.py`, `scoring/decision.py`):

- `score_player(PlayerData) -> PlayerScore`: Pure function computing expected matchday points (0-180 scale)
- `DataCollector`: Assembles player data from pre-fetched API data, flags missing fields
- `DecisionEngine`: Buy/sell/lineup decisions via marginal EP gain calculation and sell plans
- `PlayerScore`: The ONE number driving all decisions — components: base points, consistency, lineup probability, fixture difficulty, form, minutes trend, DGW multiplier
- Data quality grading (A-F) penalizes unreliable predictions
- Position calibration multiplier (REH-20) corrects systematic per-position EP bias from accumulated `matchday_outcomes`

**Legacy roster helpers** (`roster_analyzer.py`, `value_calculator.py`):

- `RosterAnalyzer`: Roster-aware buy context (gap-fill vs upgrade detection) via `value_calculator.PlayerValue`. Currently used by some display paths; not part of the EP decision pipeline.

**Learning System** (`bid_learner.py`, `learning/tracker.py`, `activity_feed_learner.py`):

- `BidLearner`: SQLite writer + reader for all learning tables — auction outcomes, flip outcomes, matchday outcomes (REH-20), predicted EPs (REH-20), team value history (REH-23), league rank history (REH-24), matchday lineup results (REH-25), player MV history (REH-26).
- `LearningTracker`: Lifecycle wrapper around BidLearner — pending bids → resolve_auctions → record_outcome; tracked_purchases → record_flip_outcome.
- `ActivityFeedLearner`: League transfers + market value snapshot events from the activity feed for competitor and demand signals.

**CLI** (`cli.py`):

- Typer-based CLI with three commands: `login`, `auto`, `status`. Global `--verbose`/`-v` flag toggles DEBUG-level console logging (the rotating file handler at `logs/rehoboam.log` is always DEBUG).
- Rich console output for formatted tables and status.

### Roster-Aware Recommendations

The system uses position **minimums** (not quotas) to provide context-aware buy recommendations:

- GK: 1, DEF: 3, MID: 2, FW: 1 (total: 7 minimum for an 11-player lineup)
- Remaining 4 spots can be filled with any position

**Roster impact logic** (`scoring/decision.py`):

- Below minimum: Shows "fills gap" with +10 score bonus (high priority)
- At/above minimum: Shows upgrade comparison vs weakest player at position
- Players are **never filtered out** based on position count - roster impact is for display/scoring only

Key data classes:

- `RosterContext`: Position state (`current_count`, `minimum_count`, `is_below_minimum`, `weakest_player`)
- `RosterImpact`: Buy impact (`impact_type`: "fills_gap", "upgrade", "not_upgrade", "additional")

### Key Design Patterns

- **Dependency Injection**: `Trader` and `AutoTrader` accept optional learners (`bid_learner`, `activity_feed_learner`).
- **Best-effort learning**: Every persistence call is wrapped in `try/except` so a learning-side failure never blocks the EP pipeline. Stack traces flow into `logs/rehoboam.log` via the structured logger.
- **Configuration via Pydantic**: `Settings` class loads from `.env` with validation.
- **Probe-first**: Before relying on a Kickbase endpoint shape, validate against the live API via the read-only scripts in `scripts/probe_*.py`. Field aliases are documented in the user-level memory file `reference_kickbase_field_aliases.md`.

### Data Flow

1. Azure Function timer (or `rehoboam auto` CLI) invokes `AutoTrader.run_full_session(league)`.
1. Step 1 — auction resolution: `LearningTracker.resolve_auctions` reconciles pending bids into won/lost rows in `auction_outcomes`; deferred sell plans execute.
1. Step 2 — session context build: `Trader.get_ep_recommendations_with_trends(league)` fetches squad + market + ranking + per-player performance/MV-history, scores everyone, ranks buys + trade pairs by marginal EP gain.
1. Step 2a — learning snapshots: `LearningTracker.reconcile_finished_matchdays` (REH-20), `snapshot_predictions` (REH-20), `record_team_value_snapshot` (REH-23), `record_player_mv_snapshot` (REH-26). `Trader` itself writes `record_league_rank_snapshot` (REH-24) + `record_matchday_lineup_result` (REH-25) inside its existing /ranking try block.
1. Steps 3-7 — lineup / matchday-locked / sell phase / squad optimization / unified trade phase. Bids placed via `api.buy_player`, sells via `api.sell_player_instant` or `api.sell_player`.
1. Step 8 — `_set_optimal_lineup` finalizes the starting 11.

## Configuration

Settings loaded from `.env` (see `.env.example`) via Pydantic BaseSettings — see `rehoboam/config.py:Settings` for the full field list and defaults. Notable knobs:

- `KICKBASE_EMAIL`, `KICKBASE_PASSWORD`: Credentials
- `MIN_SELL_PROFIT_PCT`, `MAX_LOSS_PCT`: Trade-side thresholds (defaults 15.0 / -15.0)
- `MIN_EP_UPGRADE_THRESHOLD`: Minimum marginal EP gain to recommend a buy (default 5.0)
- `DRY_RUN`: Safety flag (default: true)

A few legacy `Settings` fields (`min_value_score_to_buy`, `min_buy_value_increase_pct`) are defined but no live code reads them after the value-score bidding path was deleted in PR #34 — candidates for a future cleanup PR.

Module-level constants in `config.py`:

- `POSITION_MINIMUMS`: Minimum players per position (GK:1, DEF:3, MID:2, FW:1)
- `MAX_LINEUP_PROB_FOR_BUY`: Skip players with Kickbase lineup probability above this (default 3 — only starter/rotation, never bench-risk)

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
