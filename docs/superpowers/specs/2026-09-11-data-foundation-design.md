# Rehoboam data foundation: one database, league-wide calibration, lineups only until it is measured

Date: 2026-09-11. Status: draft for Marco's review.

Decisions taken on 2026-09-11 while brainstorming this spec:

- The bot runs **lineups only, no trading** while the foundation is rebuilt.
- All four foundation goals are in scope: one live database, session facts with
  an integrity check, league-wide calibration, and an in-season corpus.
- The database is **Supabase Postgres**, chosen over Turso and over keeping
  SQLite in Blob.
- The bar for the whole effort: the finished bot must outperform the current one
  by a clear margin, measured as defined in section 6.

## Why

Measured on 2026-09-11 against prod telemetry and the local state snapshot.

| after matchday 2 | us       | league median | best  |
| ---------------- | -------- | ------------- | ----- |
| rank             | 13 of 14 |               | 1     |
| total points     | 1,246    |               | 2,389 |
| MD1 points       | 590      | 720           | 1,269 |
| MD2 points       | 656      | 882           | 1,629 |

The current bot averages **178 points a matchday below the league median**.

### Where the points went: decisions

- **Squad shape.** Buys are ranked by marginal EP with no formation constraint,
  and `can_fill_starting_eleven` only enforces position minimums. The squad on
  2026-09-11 is 1 GK, 6 DEF, 3 MID, 1 FW. Only five defenders can ever start,
  so the most it can field is ten players in a 5-3-1, which Kickbase rejects
  with `LineupNotEnoughPlayers`. Every prod session since 2026-09-09 20:00 has
  failed to set a lineup. MD3 kicks off 2026-09-12 13:30 UTC.
- **Capital lock.** Since 2026-09-10 every bid is paced to zero because
  EUR 25.3m of open offers, at least one placed manually, exceed the EUR 5.9m
  budget. The board lists eight must-have buys and places none.
- **Negative budget during MD2.** Two auctions resolved on 2026-09-05, the
  budget went negative, and the bot fire-sold Ben Seghir and Maloney.
  Leweling and Jeong were bought on 09-05 and sold on 09-06 at a loss.
- **Approved proposals die on price.** Two approvals failed with
  `UnderpayNotAllowed` because market value rose between proposal and approval.
- **Almost no trading.** 30 of 34 sessions since 2026-08-23 ended with zero
  trades. Season so far: 10 buys for EUR 162.4m, 16 sells for EUR 93.5m,
  transfer P&L EUR -6.6m.

### Where the points went: data

1. **The calibration loop cannot learn.** `predicted_eps` and
   `matchday_outcomes` are written for the squad only: 14 rows this season,
   per-player errors of 50 to 185 points, systematically under-predicting.
1. **`matchday_lineup_results` has no 2026/27 rows.** The writer swallows its
   exception.
1. **Cost basis is unknown for 7 of 11 squad players**, so profit-taking and
   loss-cutting cannot evaluate them.
1. **The training corpus is frozen at 2026-07-31.** Its 14,824 rows for
   2026/27 are schedule placeholders with zero minutes. Newcomers score
   EP 0.0 and are skipped.
1. **Fixture dates vanish between matchdays.** On 08-30, 08-31, 09-06 and
   09-07 `/myeleven` carried no next fixture, so phase detection and the
   kickoff budget guard were both off.
1. **`auction_outcomes` cannot tell outbid from withdrawn**, and the learned
   overbid is fitted on those 35 rows. It pushes bids to +27-35% on a "19% win
   rate" that includes the bot's own cancellations.
1. **The local DB is a hybrid** of an 08-26 Blob snapshot and local writes.
   Every analysis starts with a fetch ritual, and REH-94 records that a
   partial download can replace the learning DB with an empty one.
1. **Trend data for new listings is garbage** (a +1147% trend was logged).

The scorer is `EP = Σ P(status) × rate(status)`. It cannot be recalibrated on
14 rows, and no decision built on top of it can be trusted while the facts it
acts on (fixtures, fieldability, budget, cost basis) are unverified. That is
why the data layer comes first.

## Goals

1. **Lineups only, no trading**, switchable without a deploy, with a
   fieldability check that respects position ceilings so the emergency fill
   can repair a squad like today's.
1. **One live database** that local and prod both read and write. The Blob
   sync, the fetch and push ritual, and the four SQLite state files go.
1. **Session facts and an integrity check**: every session records the facts
   it acted on and alerts on Telegram when one is missing or contradictory.
1. **League-wide calibration**: predictions for every scored player each
   session, actuals for every player after each matchday, one report per
   matchday, and a measured gate for switching trading back on.
1. **An in-season corpus** refreshed daily by the idle second Function app, so
   newcomers and current-season rates exist.

## Non-goals

- Changing the scorer's model form. Availability × Rate stays. Recalibrating
  it on the new data is the next spec, not this one.
- New external data sources (Understat, ClubElo, OpenLigaDB).
- Any trading-strategy change beyond switching trading off and fixing
  fieldability. The autonomous-wallet spec of 2026-09-05 is paused, not
  replaced; its PRs 1 and 2a stay mergeable and become inert under
  `trading_mode=lineup_only`.
- Telegram directives, approvals, the Data API, auth, or row-level security.
- Rescuing MD3. The 2026-09-12 lineup needs a midfielder or forward bought in
  the app before kickoff. No PR lands in time.

## Design

### 1. The store: Supabase Postgres

**Project.** One Supabase project on the free plan, region Frankfurt, the
nearest to the Function apps in Germany West Central. Marco creates it in the
dashboard so the account and password stay his. All tables live in a dedicated
schema `rehoboam`, owned by a dedicated role `rehoboam_bot` with rights on that
schema only. `public` stays empty and the Data API is never enabled for the
schema, so nothing is reachable from the web and RLS is not required.

**Connection.** The Function apps have IPv4 egress only. Both connect through
the shared pooler in **transaction mode on port 6543**, which Supabase serves
over IPv4 on every plan. Session mode moved to port 5432 on 2026-02-28 and is
not used. Driver: `psycopg[binary]>=3.2` with prepared statements disabled
(`prepare_threshold=None`), which transaction mode requires. Store code is
written as short, self-contained transactions; nothing relies on session
state. The connection string is one Key Vault secret, `database-url`,
surfaced as `DATABASE_URL` on both Function apps via Bicep and set in the
local `.env`. Local and prod use the same URL. A missing `DATABASE_URL` is a
hard error at startup; there is no silent fallback to SQLite.

**Code.** New package `rehoboam/store/`:

- `connect()` — context manager yielding a psycopg connection.
- `migrate()` — applies `rehoboam/store/migrations/NNN_<name>.sql` in order,
  recording each in `schema_migrations`. Run by `rehoboam migrate` and at the
  start of every Function invocation (idempotent, cheap).
- `BidLearner` and `ActivityFeedLearner` rewritten onto it. Translations are
  mechanical: `?` → `%s`; `INSERT OR IGNORE` → `ON CONFLICT DO NOTHING`;
  `INSERT OR REPLACE` → `ON CONFLICT ... DO UPDATE`; `AUTOINCREMENT` →
  `GENERATED ALWAYS AS IDENTITY`; epoch `REAL` → `timestamptz`.
- `value_history.py`'s 6-hour performance and market-value caches become one
  `api_cache(kind, player_id, league_id, fetched_at, payload jsonb)` table,
  read one row at a time. No more 27 MB file downloaded twice a day.

**Table families** (all under `rehoboam`):

| family      | tables                                                                                                                                                                                    | origin                                                             |
| ----------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| operating   | `pending_bids`, `pending_bid_sell_plans`, `auction_outcomes`, `buy_decisions`, `trade_proposals`, `tracked_purchases`, `recently_sold`, `forced_sales`, `flip_outcomes`                   | imported from `bid_learning.db`                                    |
| league      | `league_rank_history`, `manager_profile_history`, `manager_transfers`, `league_transfers`, `market_value_snapshots`, `team_value_history`, `player_mv_history`, `matchday_lineup_results` | imported                                                           |
| corpus      | `player_universe`, `player_status_daily` (new), `player_match_history`, `mv_series`, `player_transfers`, `sweep_progress`                                                                 | imported from `training_corpus.db`, then written by ingestion (§2) |
| calibration | `predictions` (new), `calibration_rows` (new), `calibration_reports` (new); `predicted_eps` and `matchday_outcomes` imported and frozen                                                   | §4                                                                 |
| facts       | `session_facts` (new), `integrity_failures` (new)                                                                                                                                         | §3                                                                 |
| cache       | `api_cache` (new)                                                                                                                                                                         | replaces `player_history.db`                                       |
| meta        | `schema_migrations`                                                                                                                                                                       |                                                                    |

**History.** `rehoboam import-sqlite --learning logs/bid_learning.db --corpus logs/training_corpus.db` loads the last Blob fetch once. Idempotent: every
insert is `ON CONFLICT DO NOTHING` on the natural key. Verification is a
per-table row count, SQLite versus Postgres, printed by the command.

**Corpus for offline tools.** Replay and backtest scan 75k rows in a loop and
must not do that over a metered network. A new `rehoboam corpus-pull` writes a
local SQLite file with the corpus tables; the replay and backtest CLIs keep
their `--corpus` path argument and keep working unchanged.

**Insurance.** The free plan's backup policy is not something to depend on. The
ingestion app (§2) exports every table weekly with psycopg `COPY ... TO STDOUT`, one gzip per table, into the existing Blob container. This is the
only remaining use of `azure-storage-blob`; the state sync (`fetch_state`,
`push_state`, `DB_FILES`) and the CLI commands `fetch-azure-state` and
`push-azure-state` are deleted in the same PR that cuts over.

**Deletions.** `azure_blob.py`'s sync, the four `DB_FILES`, `value_tracking.db`
and `market_prices.db` (both empty, no readers), and the `logs/*.db` state
files. The training corpus file stays only as the `corpus-pull` output.

**Pause.** Free projects pause after seven idle days. Two sessions a day keep
the project awake in season. Off-season the ingestion timer keeps running
weekly, which is enough; if the Function apps are stopped entirely, the project
is restored by hand in August.

### 2. Ingestion: the second Function app gets a job

`func-rehoboam-external` is provisioned by Bicep with identity and Key Vault
access but has no code. It gets `deploy/azure_function_external/` with one
timer at **05:00 and 17:00 UTC**, three hours before each trading session.

**Time budget and resumability.** Each run stops after 8 of its 10 minutes.
It processes players in order of oldest `sweep_progress.fetched_at` first, so
an interrupted run continues where it stopped. The 2026-07-29 sweep did 433
players over two endpoints in five minutes; three endpoints over ~450 players
is about eight minutes, so a full pass completes in one or two runs and nothing
is more than a day stale.

**What it writes.**

- `player_universe`: id, name, position, team, current market value.
- `player_status_daily` (new): one row per player per day with Kickbase
  status, market value and lineup probability. This is the availability
  history the scorer has never had; the corpus has per-match status but no
  day-by-day injury or probability signal.
- `player_match_history`: per-match points, minutes, status, home/away,
  opponent. Finished rows are the league-wide actuals §4 consumes.
- `mv_series`: as today.
- `player_transfers`: as today.

**Client hardening.** `kickbase_client.py` has no handling for HTTP 429. The
sweep gets exponential backoff on 429 and 5xx, a per-run request cap, and the
existing throttle. A run that hits the cap ends cleanly and resumes next time.

**Effect on the trading session.** `Trader` reads a candidate's performance
and status from `api_cache` and `player_status_daily` when the row is under 24
hours old and calls the API only for what is missing. The per-player fetch
loop in `trader.py` becomes the fallback. Sessions get faster and the API
budget drops.

**Deploy.** `bash deploy/deploy.sh code external` already exists. Bicep gains
`DATABASE_URL` for both apps and nothing else.

### 3. Session facts and the integrity check

**`session_facts`**: one row per session from either app: `session_id`, `app`,
`mode`, `started_at`, `duration_s`, `phase`, `next_kickoff`,
`next_kickoff_source`, squad counts by position, `fieldable_count`,
`legal_formation`, `budget`, `open_offers_total`, `open_offers_manual`,
`cost_basis_missing`, `predictions_written`, `lineup_result`, `errors`.

**Next kickoff** comes from the competition schedule
(`GET /v4/competitions/1/matchdays`) as the primary source, because it lists
the whole season and never blanks out between matchdays, with `/myeleven` as
the cross-check. The source is recorded on the row. The schedule's kickoff
field is verified against the live API with a probe script before the code
relies on it, per the repo's probe-first rule.

**`services/integrity.py`** is pure: facts in, a list of failures out, tested
exhaustively like `safety_gate`. Rules:

| id  | rule                                                                                                           |
| --- | -------------------------------------------------------------------------------------------------------------- |
| I1  | next kickoff is known                                                                                          |
| I2  | fieldable count is at least 11 after the emergency step, and a legal formation exists                          |
| I3  | budget minus open offers is non-negative, or the deficit is covered by sellable value, before the next kickoff |
| I4  | every owned player has a cost basis                                                                            |
| I5  | predictions were written this session                                                                          |
| I6  | the lineup call succeeded when a kickoff is within 48 hours                                                    |
| I7  | ingestion has completed a pass within the last 36 hours                                                        |

Failures are written to `integrity_failures` and sent as one Telegram message
per session, only when there are failures, and printed on the session board.
The check never blocks lineup setting. In `full` mode, I3 also refuses new
offers; in `lineup_only` mode it only alerts.

### 4. League-wide calibration

**Predictions.** Every trading session scores every player in
`player_universe` whose status row is fresh, not only the squad and the market.
Scoring is pure arithmetic over local rows once §2 exists, so ~450 players cost
seconds. Each session writes `predictions`: `session_id`, `player_id`,
`matchday`, `kickoff`, `predicted_ep`, `p_status` (jsonb), `rate`, `position`,
`team`, `owned`, `listed`, `in_best_11`, `data_grade`.

**Actuals and rows.** After a matchday finishes, detected from
`player_match_history` rows turning finished, the ingestion app joins each
player's last prediction before kickoff to the actual points and writes
`calibration_rows`. Players without a prediction are counted, not dropped.

**Reports.** `calibration_reports` holds one row per matchday: `n`, `mae`,
`bias`, `spearman`, `top11_regret` (best possible eleven from the whole league
by actual points versus by predicted points), and the same by position and by
status bucket. One Telegram message per matchday carries the headline numbers
and the three worst misses. The metric code reuses `backtest/metrics.py`.

**The gate.** Trading switches back on when three consecutive matchday
reports exist and all of: the integrity check has been clean for seven days,
Spearman over all players is at or above the season-average baseline that
`rehoboam backtest-baseline` measures, and `top11_regret` is below that
baseline's. The number to beat is measured, not assumed.

**Supersession.** `predicted_eps` and `matchday_outcomes` are imported and
frozen; their writers and `reconcile_finished_matchdays` are deleted once the
first report exists.

### 5. First PR: lineups only, and fieldability with ceilings

**Mode.** `Settings.trading_mode: Literal["full", "lineup_only"]`, env
`TRADING_MODE`, default `full`. Prod is set to `lineup_only` with the deploy.

**Session under `lineup_only`.** Step 0 (activity feed) runs. Step 1 reconciles
pending bids only; deferred sell plans are skipped and logged. Steps 2 and 2a
run. Step 3, the emergency fill, runs and is the one buy allowed. The locked
branch runs. Steps 4 to 7 (profit sells, squad optimisation, bid compliance,
unified trade phase) are skipped. Step 8 sets the lineup. The session board
prints `mode=lineup_only` and `session-end` carries `mode=`.

**Fieldability.** `can_fill_starting_eleven` computes
`fieldable = Σ min(available_at(pos), max_starters(pos))` and returns ok only
when `fieldable >= 11`, minimums are met, and a legal Kickbase formation exists
for some choice within the ceilings. The legal formation list does not exist in
the code today; it is verified against the app or API before shipping and kept
in `formation.py` as data.

**Emergency basket.** `slots_short` becomes a per-position need derived from
the same ceilings: a 6-DEF squad two short buys a midfielder or forward, never
a seventh defender. `select_emergency_basket` only considers positions whose
ceiling is not saturated.

**Lineup step.** `select_best_eleven` returns a legal eleven or raises. The
lineup step never submits ten names.

**Interplay with the wallet PRs.** This PR branches from `main`. PR #103
(bids are commitments, emergency fill via the gate) is worth merging first
because emergency-fill correctness matters in this mode; PR #104 can merge or
wait, since `trading_mode` makes its buy path inert until trading resumes.

### 6. The bar: what "outperforming by miles" means

Three metrics, each recorded by the foundation itself:

| metric                  | definition                                                                                 | current                                                 | target                                                                  |
| ----------------------- | ------------------------------------------------------------------------------------------ | ------------------------------------------------------- | ----------------------------------------------------------------------- |
| M1 lineup regret        | fielded points minus best possible eleven from the owned squad, per matchday, from actuals | unknown (writer dead); MD3 will carry at least one -100 | at most 50 a matchday by the end of the lineups-only window             |
| M2 calibration          | Spearman of predicted versus actual over all players, per matchday                         | unmeasurable (n=14)                                     | at or above the season-average baseline for three consecutive matchdays |
| M3 points versus league | our matchday points minus the league median, rolling five-matchday mean                    | -178 (MD1 -130, MD2 -226)                               | at least +100 after trading resumes                                     |

M1 is what this spec delivers directly: the -100s and the ten-man lineups stop.
M2 is what this spec makes measurable and gates on. M3 is the outcome. Moving
M3 from -178 to +100 is roughly 280 points a matchday, which over the remaining
season is the gap between 13th place and the top third. It depends on
recalibrating the scorer on the league-wide data this spec produces, and that
recalibration is the next spec. This one makes it possible and makes its
result visible; it does not claim to deliver M3 on its own.

## Verification

- Pure modules (`integrity`, fieldability, basket, calibration metrics) get
  exhaustive unit tests in the style of `test_safety_gate`.
- The store gets tests against a real Postgres: a `postgres:17` service
  container in CI, migrations applied from scratch, an import from a fixture
  SQLite file, and row-count assertions. Local runs use the same container or
  the Supabase project's shadow database.
- `import-sqlite` prints per-table counts for SQLite and Postgres; the PR
  description records them.
- Every PR that touches the API or decisions runs a live `--dry-run` against
  prod before merge, as the repo's smoke rule requires.
- PR A changes a decision path (fieldability) and therefore runs the season
  replay before shipping.
- First ingestion pass: ~450 players in `player_universe`, one
  `player_status_daily` row per player, non-zero finished rows in
  `player_match_history` for MD1 to MD3.
- First calibration report after the first full matchday post-deploy, with
  `n` of at least 400.

## Rollout

| PR  | scope                                                                                                                        | prod change                                  |
| --- | ---------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------- |
| A   | `trading_mode`, fieldability with ceilings, per-position emergency basket, legal eleven or raise                             | `TRADING_MODE=lineup_only`                   |
| B   | `rehoboam/store/`, migrations, learner rewrite, `api_cache`, `import-sqlite`, cut-over, Blob sync deleted, CLAUDE.md updated | `DATABASE_URL` on both apps; import run once |
| C   | ingestion app, corpus tables, backoff, weekly COPY export                                                                    | external app deployed                        |
| D   | `session_facts`, `integrity`, Telegram alert, competition-schedule kickoff                                                   | none                                         |
| E   | league-wide predictions, `calibration_rows`, reports, gate message                                                           | none — shipped 2026-09-15 (PR E)             |
| F   | `corpus-pull`, legacy calibration writers deleted, `value_tracking` and `market_prices` removed                              | none                                         |

A ships first and alone. B and C follow together. D and E follow. F lands
after the first report. Each PR is one Linear issue and one branch, per the
repo's habit.

## Risks

- **Supabase free tier changes or the project pauses.** Everything is plain
  SQL and the weekly COPY export lands in Blob; moving to any Postgres is a
  restore. Off-season keepalive is the weekly ingestion run.
- **Kickbase rate-limits the sweep.** Backoff, a per-run cap, and two runs a
  day at the pace the July sweep already used without incident.
- **The 10-minute Function limit.** Time budget and resumability; a full pass
  may take two runs and that is acceptable.
- **SQLite semantics drift on import.** `INSERT OR REPLACE` deletes and
  re-inserts, which is not an upsert; identity columns renumber. The import is
  verified by counts and by spot checks of the operating tables.
- **Egress.** Per-row reads keep monthly egress near 100 MB against a 5 GB
  allowance.
- **Prepared statements.** Transaction-mode pooling rejects them; the store
  sets `prepare_threshold=None` and the CI container tests through a pooler
  where possible.
- **Two CLAUDE.md versions.** `main` still describes the approval gate; PR B
  rewrites the current-state paragraph.
- **MD3.** Out of scope and unrescued by any PR; a manual buy before
  2026-09-12 13:30 UTC is the only fix.

## Judgement calls open to revision

- The legal formation list and the competition schedule's kickoff field are
  both probe-first items; the spec assumes both exist as the app shows them.
- Whether `api_cache` is still needed once `player_status_daily` and
  `player_match_history` are fresh daily. Kept for one release as a fallback.
- Whether the gate's Spearman threshold should be the season-average baseline
  or something stricter. Start with the measured baseline; tighten after the
  first three reports.
