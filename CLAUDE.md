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

- **The store is live (2026-09-13)**: every learner writes to Supabase Postgres; `DATABASE_URL` is required at startup.
- **Lineups only (2026-09-11)**: prod is switched to `TRADING_MODE=lineup_only` with this PR's deploy (app setting first, then code) and stays there until the data foundation's calibration gate passes (spec §4). The mode skips every sell and buy phase *except the emergency fill and the league's Top-5 forced sale* — the latter is a league rule, not a trade. Fieldability knows Kickbase's legal formations (`formation.LEGAL_FORMATIONS`, the ten from `/v4/config` `cps[].lts`, re-verified by `scripts/probe_formations.py`); `select_best_eleven` returns the best legal eleven; the emergency fill buys the position a formation needs; `_set_optimal_lineup` never submits an illegal eleven.
- **League-wide calibration (2026-09-15, PR E)**: every session writes `rehoboam.predictions` for every live player; the ingestion app reports each finished matchday (`calibration_reports`: Spearman, played-only Spearman, top-eleven regret, our squad's regret, all against a baseline scored on the same rows) and the gate verdict spec §4 defines. A matchday that finished before predictions existed is settled once with an empty, unsent report. The gate is a verdict; trading resumes by changing `TRADING_MODE`.
- **League state (G1, 2026-09-16)**: six tables (`market_listings`, `manager_squads`, `managers`, `fixtures`, `league_table`, `teams`) and the `rehoboam.player_table` view give the bot the market, ownership, fixtures and the table alongside our own predictions. The trading session writes the market, the managers and every squad from what it already fetched — no new API call; the ingestion app refreshes all six (~35 requests) once per run. `rehoboam players` and `rehoboam market` read the view and the newest market snapshot; `rehoboam backfill-league` backfills transfer history.
- **The dashboard (2026-09-16)**: `web/` is a Next.js app on Vercel — a private, read-only view of the store (Players, Squad & lineup, Market, League, Calibration & health) behind Supabase Auth (email and password, with an emailed link as the fallback), restricted in code to the owner's email via server-only `ALLOWED_EMAILS` (`web/src/lib/allowed-emails.ts`, `web/src/lib/gate.ts`) — fails closed on a missing/empty list, exact match only; a signed-in but not-allowed account is signed out and redirected, never handed data. Every page is a server component reading the `rehoboam.web_*` views (migrations 007 and 008) over the pooler as `rehoboam_bot`; the site never names a table and never writes. Telegram keeps approvals and alerts.
- **Market-value forecast (2026-09-17)**: the ingestion stores Kickbase's last-update change (`tfhmvt` → `player_status_daily.mv_change`) and, after every run, `enrichment/mv_forecast.run_mv_forecast` scores yesterday's forecasts against the morning's readings and writes today's into `rehoboam.mv_forecasts` (rule in `services/mv_forecast.py`: `MV_FORECAST_MOMENTUM` (0.95) × the last move, capped at ± `MV_FORECAST_CAP` (0.30)). `reading_window(fetched_at)` names which update a reading sits against — `(day, PRE)` before that Berlin day's ~22:00 update, `(day, POST)` from 22:30 on, `None` in between (used for neither). A score counts only when the reading's value minus its change equals the forecast's base. **Nightly pass (2026-09-17)**: a third run at 21:45 UTC (`mv_nightly` in `deploy/azure_function_external/function_app.py`, `rehoboam mv-nightly`) re-reads status for every player; nothing else refreshes (no league refresh, no other kind) right after Kickbase's move, scores tonight's forecast and writes tomorrow's from the post-update value — so the site is blank only between 22:00 Berlin and the nightly pass, not until the next morning, and tonight's result is scored the same night. Its `IngestBudget` deadline is clamped to 540s so a slow run can't cross Berlin midnight (22:00 UTC) and key its readings to the wrong day. It writes `session_facts.mode="mv_nightly"`, not `"ingest"`, so rule I7 (`store/session_store.py`) keeps tracking only the twice-daily pass. `player_table`'s `trend_24h_pct` reads Kickbase's own last move (`mv_change`) off the newest status reading directly (migration 011) rather than comparing two `player_status_daily` rows a day apart — the nightly overwrites that row with the post-update value, which would otherwise zero the 24h trend for most of the day; `trend_7d_pct` now prefers `mv_series` over the daily status rows for the same reason. The site shows `Next MV` on Players and Market (`web_mv_forecast`, live until 22:00 Berlin) and the accuracy against "no change" on Calibration & health (`web_mv_accuracy`). Display only: no trading decision reads it. Migration 008 must be applied as admin before its code deploys.
- **Fair value from expected points + the League tab (2026-09-21)**: migration 022 redefines `player_table`'s `fair_price`/`fair_value_gap` — both now read `predicted_ep`, not the season average; `fair_price` comes from its own price-on-points fit (the old one inverted a points-on-price line and overstated extremes by 1/r²); both are fitted and shown for likely starters only (`p_start >= 0.5`), and the price is quoted from 5,000,000 up (below that the measured miss was 2–4×). Display only, like before. Migration 023 adds `web_managers`, `web_manager_matchdays`, `web_manager_transfers` and appends columns to `web_ownership` for `/league`; `league_rank_history` has no season column, so "this season" is every snapshot since the newest season's first kickoff. Both must be applied as admin before the code deploys.
- **Approval gate (2026-08-24)**: a plain squad-improvement buy is no longer executed by the bot. It becomes a *proposal* — recorded in `trade_proposals`, rendered by `notify/render.py` with its full reasoning, and sent by `notify/telegram.py` with Approve/Reject buttons. Approving hits the `telegram_approval` HTTP trigger, which re-validates against live prices, runs `services/safety_gate.check_buy`, and only then bids. **Everything else stays autonomous**: profit trading, trade pairs, emergency squad fill and lineup setting are untouched, deliberately — pairs sell before they bid, and an empty lineup slot is -100.
- `auto` command: Unified aggressive trading — single EP pipeline call, trade pairs compete with plain buys ranked by EP, matchday-aware aggressiveness (aggressive 5+d, moderate 2-4d, locked 0-1d), trend-aware profit selling, up to 10 trades/session (15 aggressive). Plain improvement buys now propose rather than execute (see above).
- `services/safety_gate.py`: the last thing between a decision and real money. Pure, so it can be exhaustively tested. Collects ALL failing reasons rather than short-circuiting, and treats `known_player_ids` as a security boundary — a forged webhook callback naming an unknown player cannot spend. Since REH-100 it guards **every** buy, not only the approved one: `ExecutionService.buy` takes a required `BuyGate` (trade pairs, profit flips, emergency fill) and `league_compliance` gates its re-bid. Two refusal policies differ deliberately — emergency fill walks down its ranked list rather than fielding nobody, while a trade pair is pre-flighted *before* its irreversible sell. `BuyGate.spendable_budget` is the allowance the phase permits, not the wallet balance: `_compute_flip_budget` hands the aggressive phase `current_budget + max_debt` on purpose, and passing the raw balance would delete that strategy.
- `status` command: Read-only diagnostic — runs the full EP pipeline in dry-run mode so you can preview what `auto` would do. Genuinely read-only: `_propose_buy` short-circuits on `dry_run` after rendering, so a diagnostic run neither writes a proposal row nor sends a Telegram message.
- `SmartBidding.calculate_ep_bid`: the only bidding path. Tier+demand+trend stack with optional learned overbid override from `auction_outcomes` (REH-30 fix).
- `BidLearner`: writes `auction_outcomes`, `flip_outcomes`, `matchday_outcomes` (REH-20), `predicted_eps` (REH-20), `team_value_history` (REH-23), `league_rank_history` (REH-24), `matchday_lineup_results` (REH-25), `player_mv_history` (REH-26). Scorer self-calibration loop fully wired via REH-20.
- Double gameweek awareness: EP calculator supports DGW multiplier (1.8x), DGW detection wired through `MatchupAnalyzer.detect_double_gameweek` + `is_dgw_team`.
- Budget-at-kickoff safety: 24-48h buffer + matchday-locked phase prevents trading 0-1 days before match.
- Structured logging (PR #27): all bidding decisions + override applications written to `logs/rehoboam.log` with rotating file handler.

### Strategic Priorities (in order of impact)

1. **Learning loops** (REH-32 through REH-37) — foundation tables backfilled and populated as of 2026-05-09 via REH-39 (flips + lineups + rank history) and REH-40 (full per-flip MV trajectories). Calibration tickets are now unblocked: REH-32 (loss-cut + profit-take ladder, locked design in ticket body), REH-33 (sell-timing peak-MV regret), REH-35 (lineup quality), REH-37 (rank-trajectory regression). REH-34 stays blocked (forward-only buy annotations).
1. **Double gameweek exploitation** — buy 7-10 days before a DGW. DGW detection is wired; a planning recommender that surfaces upcoming DGWs is not.
1. **Budget safety** — Hard block on going negative before kickoff is in place; could tighten the 48h+ early warning.
1. **Squad size enforcement** — At 15/15, the trade table (sell→buy swap pairs) is shown instead of plain buy recommendations.

## Common Commands

The project uses `uv` for dependency management. Install uv (`brew install uv` or `curl -LsSf https://astral.sh/uv/install.sh | sh`), then everything below runs through it.

```bash
# Install dependencies (creates .venv automatically; .python-version pins 3.12)
uv sync --extra dev

# Run the CLI (login, auto, status,
# backfill-history, backfill-mv-history, enrich-corpus, backtest-baseline are exposed)
uv run rehoboam --help
uv run rehoboam login              # Test credentials + list leagues
uv run rehoboam status             # Read-only: show squad + dry-run what auto would do
uv run rehoboam status -v          # Verbose: DEBUG-level decision logs to stderr
uv run rehoboam auto --dry-run     # Simulate one trading session
uv run rehoboam auto               # Live trading session
uv run rehoboam auto --aggressive  # Up to 15 trades, lower EP threshold, +50% spend
uv run rehoboam backfill-history --dry-run   # Preview historical foundation-table backfill (REH-39)
uv run rehoboam backfill-history             # Replay KICKBASE history → flip_outcomes + matchday_lineup_results + league_rank_history
uv run rehoboam backfill-mv-history          # Backfill player_mv_history trajectories for all flipped players (REH-40)
uv run rehoboam enrich-corpus --dry-run      # Preview v2 training-corpus sweep size
uv run rehoboam enrich-corpus                # League-wide sweep; writes to the store (v2 scorer training data)
uv run rehoboam backtest-baseline            # Reproduce the season-average regret baseline weeks 2-3 must beat
uv run rehoboam ingest                       # One budgeted ingestion pass — what func-rehoboam-external runs at 05:00/17:00 UTC
uv run rehoboam mv-nightly                   # Status-only pass right after the ~22:00 MV update — what func-rehoboam-external runs at 21:45 UTC
uv run rehoboam migrate                      # Apply store migrations to DATABASE_URL (idempotent)
uv run rehoboam db-bootstrap                 # Create the rehoboam_bot role + grants (once per project)
uv run rehoboam import-sqlite                # Copy logs/*.db into the store; safe to re-run
uv run rehoboam corpus-pull                  # Materialise the corpus + the replay's learning tables into logs/*.db for replay/backtest
uv run rehoboam calibrate            # Report finished matchdays (what func-rehoboam-external does after each ingest)
uv run rehoboam calibrate --backfill-day 3   # Leak-free predictions + report for a finished matchday, kept apart from the gate
uv run rehoboam players [--position --owner --sort --limit]   # Base XI's player table from the store, plus predicted EP
uv run rehoboam market                                 # Newest market snapshot with sellers/owners and predicted EP
uv run rehoboam backfill-league                        # Every manager's full transfer history, 25/page, idempotent
uv run rehoboam backtest-mv                            # Replay the market-value forecast over the stored daily series (--momentum/--cap lists)

# Code quality
uv run black rehoboam/                        # Format code
uv run ruff check rehoboam/ --fix             # Lint and auto-fix
uv run bandit -r rehoboam/ -c pyproject.toml  # Security scan
uv run mypy rehoboam/ --ignore-missing-imports # Type check

# Testing
uv run pytest                                              # Run all tests
uv run pytest tests/test_ep_bidding.py                     # Single file
uv run pytest tests/test_scoring/                          # Whole subpackage
uv run pytest tests/test_ep_bidding.py::TestEPBidTiers     # Single class
uv run pytest -m "not slow"                                # Skip slow tests
uv run pytest --cov=rehoboam --cov-report=html             # Coverage report

# Dependency maintenance
uv lock                              # Refresh uv.lock (after editing pyproject.toml)
bash scripts/sync-azure-deps.sh      # Regenerate deploy/azure_function/requirements.txt

# Pre-commit hooks
uv run pre-commit install
uv run pre-commit run --all-files

# Azure deployment (Bicep-based, REH-48)
bash deploy/deploy.sh                  # provision + publish both function apps
bash deploy/deploy.sh infra --what-if  # preview Bicep changes (run before first migration)
bash deploy/deploy.sh infra            # just Bicep deploy
bash deploy/deploy.sh code trading     # publish trading function only
bash deploy/deploy.sh code external    # publish the ingestion app

# The dashboard (web/, Next.js on Vercel — see docs/superpowers/specs/2026-09-16-dashboard-design.md)
cd web && npm run dev          # local dashboard against DATABASE_URL
cd web && npm test             # Vitest unit tests
cd web && npm run typecheck    # tsc --noEmit
```

## Store workflow (data foundation PR B2)

The bot's state lives in Supabase Postgres, schema `rehoboam`. Local runs and
prod use the same database through the same bot-role `DATABASE_URL` — there
is no file to fetch or push.

```bash
# Inspect: any psql client works against DATABASE_URL
psql "$DATABASE_URL"

# Schema changes: add store/migrations/NNN_<name>.sql, then apply it as the
# admin (DATABASE_ADMIN_URL) BEFORE deploying code that depends on it
uv run rehoboam migrate

# Offline tools (replay, backtest) need local SQLite files, not a live connection
uv run rehoboam corpus-pull   # writes logs/training_corpus.db + logs/bid_learning.db
```

A local `status` run writes its learning snapshots (predicted EPs, rank
history, MV history) into the live tables by design. `import-sqlite` remains
only for the SQLite-era files — nothing produces new ones.

## Architecture

### Core Components

**API Layer** (`api.py`, `kickbase_client.py`):

- `KickbaseV4Client`: Low-level HTTP client for KICKBASE API v4
- `KickbaseAPI`: Higher-level wrapper used by the rest of the application
- Data classes: `User`, `League`, `Player`, `MarketPlayer`

**Trading System** (`trader.py`, `auto_trader.py`):

- `Trader`: Per-call EP pipeline (`get_ep_recommendations`, `get_ep_recommendations_with_trends`, `find_profit_opportunities`) — stateless, takes a league per call. `next_kickoff` — schedule first, `/myeleven` cross-check.
- `AutoTrader`: Session orchestrator. `run_full_session(league)` is the single entry point used by both the CLI `auto` command and the Azure Function timer trigger.

**EP Scoring Pipeline** (`scoring/models.py`, `scoring/v2/adapter.py`, `scoring/collector.py`, `scoring/decision.py`):

- `score_player_v2(PlayerData) -> PlayerScore`: the live scoring path (REH-55). Returns **real Kickbase matchday points**, not the old 0-100 index — `EP = Σ_status P(status) × rate(player, status)` over the fitted models in `scoring/v2/coefficients.json`.
- `DataCollector`: Assembles player data from pre-fetched API data, flags missing fields
- `DecisionEngine`: Buy/sell/lineup decisions via marginal EP gain calculation and sell plans
- `PlayerScore`: The ONE number driving all decisions. v2 zeroes the v1 decomposition fields (consistency, fixture difficulty, form bonuses) — they are display-only remnants, so do not reason from them.
- Data quality grading (A-F) penalizes unreliable predictions
- `scoring/scorer.py`'s v1 `score_player` still exists and is still tested, but **no production code calls it** after REH-55. REH-20's position calibration multiplier went with it: it was fitted against the 0-100 index and must not be applied to real points.

**Threshold scale — read before touching any EP constant.** Every EP threshold is now in real points: `min_ep_upgrade_threshold` 40.0, `min_expected_points_to_buy` 35.0, bid tiers 70/53/43. These are **pre-season estimates** derived 2026-07-31 from a synthetic mid-table squad (`derive-thresholds` measured n=0 — no purchasable listings exist before 2026-08-28), and every one is a `Settings` field so it can be re-tuned from `.env` mid-season without a deploy. Re-run `rehoboam derive-thresholds` once the market repopulates.

**Learning System** (`bid_learner.py`, `learning/tracker.py`, `activity_feed_learner.py`):

- `BidLearner`: the store's writer + reader for all learning tables (one transaction per call) — auction outcomes, flip outcomes, matchday outcomes (REH-20), predicted EPs (REH-20), team value history (REH-23), league rank history (REH-24), matchday lineup results (REH-25), player MV history (REH-26).
- `LearningTracker`: Lifecycle wrapper around BidLearner — pending bids → resolve_auctions → record_outcome; tracked_purchases → record_flip_outcome.
- `ActivityFeedLearner`: League transfers + market value snapshot events from the activity feed for competitor and demand signals.

**CLI** (`cli.py`):

- Typer-based CLI: `login`, `auto`, `status`, `backfill-history`, `backfill-mv-history`, `enrich-corpus`, `backtest-baseline`. Global `--verbose`/`-v` flag toggles DEBUG-level console logging (the rotating file handler at `logs/rehoboam.log` is always DEBUG).
- Rich console output for formatted tables and status.

**Training corpus + backtest harness** (`enrichment/`, `backtest/`) — week 1 of the v2 rebuild (`docs/superpowers/specs/2026-07-29-rehoboam-v2-design.md`):

- `enrichment/corpus.py`'s `TrainingCorpus`: durable, non-expiring SQLite store — `player_universe`, `player_match_history`, `mv_series`, `sweep_progress` — deliberately separate from `value_history.py`'s 6h-TTL `performance_cache`. Lives at `logs/training_corpus.db`, deliberately **not** part of the store's live operating tables — it's training data, not bot operating state.
- `enrichment/sweep.py`'s `run_sweep`: resumable, throttled, league-wide sweep (`rehoboam enrich-corpus`) — tolerant of per-player failure, `sweep_progress` tracks per-player completion so a rerun only retries what's missing. `--refetch-performance` forces a one-off re-fetch of already-complete players (e.g. after a parsing bug fix) without touching MV-series resumability.
- `backtest/`: a *tuning* instrument, not a verdict — `harness.run_backtest` replays matchday-by-matchday using only pre-matchday data (`snapshot.matches_before`, leakage-tested), scoring via `metrics.spearman` + `metrics.lineup_regret` against `baselines.season_average_baseline` (the model weeks 2-3 must beat). `squad_reconstruction.squad_on_matchday` rebuilds squad membership from flip hold-windows ∪ fielded lineups — medium fidelity, see spec §6.1 for the sensitivity caveats. `baseline_driver.run_baseline` is the committed composition behind `rehoboam backtest-baseline`.

**The store** (`store/`) — PR B1 of the data foundation (`docs/superpowers/specs/2026-09-11-data-foundation-design.md` §1):

- `store/__init__.py`'s `connect()`: psycopg 3 to Supabase Postgres through the **transaction pooler** (port 6543, IPv4), `prepare_threshold=None` because the pooler rejects prepared statements, dict rows. Every statement schema-qualifies `rehoboam.<table>`; `public` stays empty.
- `store/migrate.py`: numbered SQL files under `store/migrations/`, each applied once in its own transaction and recorded in `rehoboam.schema_migrations`. `001_schema.sql` is the SQLite schema translated (epoch doubles kept, identity ids that accept explicit values, `api_cache` with `jsonb` replacing the two JSON caches).
- `store/import_sqlite.py`: `COPY` into a temp table, then `INSERT … ON CONFLICT DO NOTHING`; re-running adds nothing. `store/corpus_pull.py` writes the corpus back into a local SQLite file, because the replay scans it in a loop; it writes with `INSERT OR REPLACE`, so a re-pull **rewrites** existing rows — corpus rows are not immutable (a `player_match_history` placeholder becomes the real result once the match finishes). It also writes the three learning tables the replay reads — `flip_outcomes`, `matchday_lineup_results`, `league_rank_history` — into `logs/bid_learning.db` (`--learning-out`).
- Tests under `tests/store/` run against a real PostgreSQL (`pytest-postgresql`: local `postgresql@17` binaries, or CI's service container via `TEST_PG_HOST`); they skip with a message when neither exists locally, and hard-fail when `CI` is set so a green CI can never mean "never ran". On macOS: `brew install postgresql@17`; if `initdb` cannot find its share files (a keg-only install), symlink `share/postgresql@17` and `lib/postgresql@17` from the keg into `/opt/homebrew/opt/postgresql@17/`.
- PR B2 (2026-09-13): `BidLearner`, `ActivityFeedLearner` and `ValueHistoryCache` are Postgres clients; the Function app and the `auto`/`status` commands call `store.ensure_ready()` first and refuse to run without a migrated store; the blob sync is gone. Tests share one PostgreSQL: `store_dsn` is a fresh migrated database, and an autouse fixture pins `DATABASE_URL` so no test can reach the real project.
- **Ingestion (PR C1, 2026-09-14)**: `func-rehoboam-external` runs `enrichment/ingest.run_ingestion` twice a day into `store/corpus_store.CorpusStore` — universe, `player_status_daily` (status + lineup probability per player per day), match history, MV series — stalest player first, every stale kind for that player (status before every session via `INGEST_STATUS_STALE_AFTER_HOURS`=10, performance daily, MV weekly), within `INGEST_DEADLINE_SECONDS` / `INGEST_MAX_REQUESTS`, and exports every table as gzip CSV to the Blob container on Sunday 03:00 UTC (`store/export.py`). The trading session still fetches per player; PR C2 switches it to read the store first.
- **Session facts + integrity (PR D, 2026-09-15)**: every run of either app writes `rehoboam.session_facts` (`store/session_store.py`); the trading session ends with `services/integrity.check_integrity` (I1–I7, pure), failures go to `integrity_failures`, the board, and one Telegram message per session when any rule fails; the next kickoff comes from `/v4/competitions/1/matchdays` (`kickoff.py`, `Trader.next_kickoff`) with `/myeleven` as the cross-check; in `full` mode a failing I3 sets `EPSessionContext.session_refusal`, which the buy gate reports and the emergency fill ignores.
- **League-wide calibration (PR E, 2026-09-15)**: every session writes `rehoboam.predictions` for every live player from store rows (`scoring/store_scorer.py`, the same `compose_ep` as the live path; squad/market keep the API path and record `live_ep` beside it); the ingestion run clears fetch stamps for finished matchdays, re-reads them, and writes `calibration_rows` + one `calibration_reports` row per matchday (`enrichment/calibrate.py`, metrics in `services/calibration.py`, baseline scored on the same rows) with the gate verdict in `gate` and one Telegram message; the gate is a verdict — trading resumes by changing `TRADING_MODE`. `predicted_eps`/`matchday_outcomes`/`reconcile_finished_matchdays` stay until PR F.
- **League state (G1, 2026-09-16)**: migration `005_league_state.sql`'s six tables — `market_listings`, `manager_squads` (append-only, keyed by `snapshot_at`; readers take the newest); `managers`, `fixtures`, `teams` (upserted in place by their own id); `league_table` (one row per `season, day_number, team_id`, accumulating by matchday) — and migration `006_player_table.sql`'s `rehoboam.player_table` view, Base XI's player-table columns plus our `predicted_ep`, start probability and fair value. The trading session writes the market, the managers and every squad from what it already fetched — no new API call in a session. The ingestion app runs a league refresh once per run before its per-player loop, about 35 requests (market, ranking, 14 squads, page-0 transfers, fixtures, table, clubs older than a week).

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
1. Step 2a — learning snapshots: `LearningTracker.reconcile_finished_matchdays` (REH-20), `snapshot_predictions` (REH-20, until PR F) and `_write_league_predictions` (PR E; feeds `predictions_written` and rule I5), `record_team_value_snapshot` (REH-23), `record_player_mv_snapshot` (REH-26). `Trader` itself writes `record_league_rank_snapshot` (REH-24) + `record_matchday_lineup_result` (REH-25) inside its existing /ranking try block.
1. Steps 3-7 — lineup / matchday-locked / sell phase / squad optimization / unified trade phase. Bids placed via `api.buy_player`, sells via `api.sell_player_instant` or `api.sell_player`.
1. Step 8 — `_set_optimal_lineup` finalizes the starting 11, and `_finish_facts` records the session and runs the integrity check.

## Configuration

Settings loaded from `.env` (see `.env.example`) via Pydantic BaseSettings — see `rehoboam/config.py:Settings` for the full field list and defaults. Notable knobs:

- `KICKBASE_EMAIL`, `KICKBASE_PASSWORD`: Credentials
- `MIN_SELL_PROFIT_PCT`, `MAX_LOSS_PCT`: Trade-side thresholds (defaults 15.0 / -15.0)
- `MIN_EP_UPGRADE_THRESHOLD`: Minimum marginal EP gain to recommend a buy (default 5.0)
- `DRY_RUN`: Safety flag (default: true)
- `TRADING_MODE`: `full` or `lineup_only` (default `full`). In `lineup_only` a session sets the lineup, runs the emergency fill and records learning, and skips every sell and buy phase *except the emergency fill and the league's Top-5 forced sale*. Prod is switched to `lineup_only` with this PR's deploy (app setting first, then code) while the data foundation (spec `docs/superpowers/specs/2026-09-11-data-foundation-design.md`) is rebuilt.

A few legacy `Settings` fields (`min_value_score_to_buy`, `min_buy_value_increase_pct`) are defined but no live code reads them after the value-score bidding path was deleted in PR #34 — candidates for a future cleanup PR.

Module-level constants in `config.py`:

- `POSITION_MINIMUMS`: Minimum players per position (GK:1, DEF:3, MID:2, FW:1)
- `MAX_LINEUP_PROB_FOR_BUY`: Skip players with Kickbase lineup probability above this (default 3 — only starter/rotation, never bench-risk)

## Testing Notes

- Tests use mock credentials (`KICKBASE_EMAIL=test@example.com`)
- CI runs on Python 3.10, 3.11, 3.12
- Test markers: `slow`, `integration`
- `tests/test_scoring_v2/test_store_scorer.py` asserts the store scorer equals `score_player_v2` on the same history — keep it green when touching either.

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

*Historical snapshot of the pre-v2 bot. `ExpectedPointsCalc` (`expected_points.py`) and `value_calculator.py` were deleted in REH-55; scoring now runs through `scoring/v2/`. Kept because the reasoning still explains why the rebuild was needed.*

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
