# The dashboard: a private, read-only window into the store

*Design, 2026-09-16. Brainstormed with Marco; the visual direction was
chosen on a design canvas
(https://claude.ai/artifact/QngqgCcbCsfVAaMr3X2Y7i).*

## Why

Telegram carries the bot's approvals, alerts and summaries, and Marco
hardly opens it. Since PR G1 (#114) the store holds everything Base XI
shows — every player's points, market value, trends, ownership, the
market, fixtures and the table — plus what only we have: predicted
points per player, start probability, fair value, the calibration
reports and every session's facts. A website over that data is a thin
read layer, and it is the surface Marco will actually look at.

Day one is read-only. Approvals and alerts stay on Telegram until the
site has been in use for a week; moving them is a separate design.

## Scope

Four pages behind a login, one user, desktop first:

1. **Players** — Base XI's table with our three columns appended.
1. **Squad & lineup** — our squad, the best legal eleven the last
   session chose, budget, the session's integrity flags.
1. **Market** — the newest listing snapshot with predicted points, and
   who owns what across the league.
1. **Calibration & health** — per-matchday reports against the
   baseline, the gate verdict, the last thirty runs of both apps.

### Non-goals

- No writes of any kind: no approvals, no lineup submission, no bids.
- No Telegram replacement yet; no notifications from the site.
- No public or league-wide access.
- No mobile layout beyond "does not break"; the tables are desktop work.
- No charts beyond the calibration bar pairs.

## Architecture

- `web/` becomes a fresh **Next.js (App Router, TypeScript)** project,
  replacing the dead Vite app there (its FastAPI backend was deleted in
  b376874). Vercel's root directory is `web/`.
- Pages are **server components**. One module, `web/src/lib/db.ts`,
  opens a `postgres` (porsager) client to the Supabase **transaction
  pooler** (`aws-1-eu-west-1.pooler.supabase.com:6543`) as the existing
  `rehoboam_bot` role, prepared statements off, from `DATABASE_URL` in
  Vercel's environment. Every query is `select … from rehoboam.web_*`;
  the site never names a table.
- **The bot repo owns the views.** They are one numbered migration,
  `rehoboam/store/migrations/007_web_views.sql`, applied as admin like
  005 and 006, so the site and the bot cannot disagree about a column.
  A new number the site needs becomes a migration first.
- **Next.js middleware** checks a Supabase Auth session on every route
  except `/login`; server components assert it again before querying.
- Pages **render on every request**. Each one reads the session cookie
  (`requireSession()`), and Players and Market also read their search
  parameters; either makes a route dynamic in Next 15, so there is no
  route cache and a `revalidate` export would do nothing. Every page load
  queries the views afresh through the pooler, which is acceptable for one
  read-only user. The store changes at 05:00, 08:00, 17:00 and 20:00 UTC;
  nothing is live-polled.
- Nothing in `rehoboam` is exposed through Supabase's Data API (the
  store design's rule stands); no row-level security is needed because
  the browser never holds a database credential.

### Why not the alternatives

A static client app on Supabase's REST API would need the `rehoboam`
schema exposed and row-level security on every view; one wrong grant
leaks predictions to the anon key. A Python data app needs a running
process and a host. Server-rendered Next.js keeps the credential in one
place and reuses SQL we already maintain.

## The data layer (migration `007_web_views.sql`)

Six read-only views in schema `rehoboam`, plain SQL, no functions, no
`security definer`. `refresh_grants` in the migrate runner already
grants the bot role on every table and view, so no new grant statement.

| View                  | Source                                                                                                                                     | What it adds                                                                                                                                                                                                                                                                                                                                                                                                                              |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `web_players`         | `player_table` ⋈ `teams`                                                                                                                   | `club` (the team's `name`); every `player_table` column as is.                                                                                                                                                                                                                                                                                                                                                                            |
| `web_squad`           | `predictions` of the latest non-dry-run `function` session, `owned = true`; ⋈ `player_universe`, `teams`, `tracked_purchases` (cost basis) | `in_best_11`, `predicted_ep`, `p_status`, `position`, `club`, `market_value`, `cost_basis` (null when unknown), `session_id`.                                                                                                                                                                                                                                                                                                             |
| `web_session_summary` | last 30 `session_facts` rows of both apps, ⋈ `integrity_failures` aggregated per session                                                   | `integrity_rules text[]`, and the ingest `extra` flattened: `requests`, `failed`, `status_written`, `performance_fetched`, `transfers_fetched`, `stopped_by`, `league_failed`, `league_teams`, `league_fixtures`, `calibration_settled`, `calibration_reported`. Session rows carry `legal_formation`, `budget`, `sellable_value`, `next_kickoff`, `lineup_result`, `predictions_written`, `errors`, `error_text`, `league_state_squads`. |
| `web_market`          | newest `market_listings` snapshot ⋈ `web_players`, `managers`                                                                              | `ask`, `market_value`, `predicted_ep`, `p_start`, `fair_value_gap`, `seller` (manager name, `Kickbase` when null), `is_ours` (seller = the `is_self` manager), `expires_at`, `offer_count`, `our_bid`, `lineup_probability`.                                                                                                                                                                                                              |
| `web_calibration`     | `calibration_reports`                                                                                                                      | every metric column, `baseline_spearman`, `baseline_top11_regret`, `gate` (jsonb), `worst` (jsonb), `backfill`, `computed_at`; ordered by `day_number`, `backfill`.                                                                                                                                                                                                                                                                       |
| `web_ownership`       | newest **per-manager** `manager_squads` snapshot ⋈ `managers`, `web_players`                                                               | one row per (manager, player): `manager`, `is_self`, `player_name`, `position`, `market_value`, `predicted_ep`, `snapshot_at`. The Market page aggregates squad size, team value and the top three by predicted points per manager in SQL on the site side (`group by` over this view).                                                                                                                                                   |

Semantics that matter, each covered by a test:

- **Newest per manager**, never the global newest `snapshot_at` — the
  per-manager rule from PR G1's review, or a partial write blanks a
  manager for hours.
- **Newest listing snapshot** is `max(snapshot_at)` over
  `market_listings`; the session and the ingest both write one, and the
  newest wins.
- **Latest non-dry-run function session** for the squad, so a local
  `status` dry-run never changes what the site calls "the lineup".
- `web_players` keeps `player_table`'s own rules (ownership per manager,
  daily-series trends) untouched; the join to `teams` is left-outer so a
  club without a profile row still shows the player.

Performance: `web_players` and `web_market` are measured against the
production-sized test fixture (about 600 players, 160 squad rows, 50
listings) before the migration merges; the bar is under one second per
page load through the pooler.

## Access

- **Supabase Auth** on the existing project (`qznixprbyldatdjzorbq`):
  email provider only, **sign-ups disabled**, one user created by hand
  with Marco's address. Sign-in is a magic link; sessions last thirty
  days. *Amended 2026-09-17:* sign-in is the owner's email and password,
  with the magic link kept behind "Forgot your password?"; the owner is
  created in the dashboard with a password and Auto Confirm.
- `web/src/middleware.ts` uses `@supabase/ssr` to read the session
  cookie on every request and redirects anything without one to
  `/login`. Server components call `requireSession()` before any query,
  so a route added later cannot forget the guard.
- Secrets: `DATABASE_URL` (bot role, pooler) and the Supabase anon key.
  The anon key is public by design and used only for the auth
  handshake. `DATABASE_URL` never carries a `NEXT_PUBLIC_` prefix; a
  build step (`scripts/check-secrets.mjs`) fails the build if that
  string or the pooler host appears in the client bundle.
- Failure modes: Supabase Auth unreachable → the login page, never data.
  Postgres unreachable → there is no cached render to fall back to: the
  sidebar shows every fact as unknown, and the page area shows "The store
  is not answering" (`error.tsx`) with a retry button. Never a stack
  trace.

## The pages

All four share one shell: the sidebar (four entries, the "next kickoff ·
lineup set · budget" card), a header with the status line ("588 players
· last session 08:01 UTC · next session 20:00 UTC": the count from
`web_players`, the start of the newest live trading session from
`web_session_summary`), and one table component (sortable header,
tabular numerals, 44 px rows).

### Players

The design canvas as built: chips for position (All, GK, DEF, MID, FW)
and ownership (Any owner, Free agents, My squad), a club select, text
search over name and club, sortable columns, predicted points
descending by default, 50 rows per page. Columns, in Base XI's order
then ours: Player (name over club), Pos, Market value, 24h, 7d, Pts, Ø,
Median, Pts / M€, Apps, Starts, Owner, EP, P(start), Fair. Market
values are full integers with thousands separators, never abbreviated.
Owner shows our name as an amber pill, `free agent` and `on market` in
muted text, other managers plain.

### Squad & lineup

A formation figure (the session's `legal_formation`, e.g. 4-3-3) with
the `in_best_11` players as cards: name, club, predicted points, start
probability. Below it the full squad table with the bench greyed,
`cost_basis` and gain/loss where known (blank, not zero, when unknown),
and the session's integrity rules rendered as sentences (the rule
texts live in `services/integrity.py`; the view carries the codes, the
site carries a code → sentence map that a test keeps in sync with the
Python rule list). Header: budget, sellable value, next kickoff.

### Market

The newest snapshot: Player, Pos, Ask, Market value, Ask vs MV (%),
Seller, Expires in, Offers, EP, P(start), Fair. Sorted by EP by
default; a chip "expiring \< 6 h". Below: the ownership table — one row
per manager: name, squad size, team value, top three by predicted
points — from `web_ownership`, ours first.

### Calibration & health

Top: per matchday, two bar pairs (ours vs baseline) for Spearman and
top-eleven regret, backfill rows marked as such, the gate JSON rendered
as one sentence, the three worst misses per matchday as a small list.
Below: the last thirty runs from `web_session_summary` — time, app,
mode, duration, errors, integrity rules, and for ingest runs status
coverage (`status_written` / universe), requests, `stopped_by`, league
refresh counts. Tonight's deadline stop (371 of 462) must be visible at
a glance in this table.

## Deployment and configuration

- Vercel project linked to the GitHub repo, root directory `web/`, hobby
  tier, region `fra1`. Production deploys from `main`; PRs get preview
  URLs that run against the same store with the same login (the site is
  read-only, so previews are safe).
- Environment on Vercel: `DATABASE_URL`, `NEXT_PUBLIC_SUPABASE_URL`,
  `NEXT_PUBLIC_SUPABASE_ANON_KEY`. `web/.env.example` documents them.
- GitHub Actions: a new `web` job (`npm ci`, `tsc --noEmit`, `next lint`, Vitest) runs on PRs touching `web/**` or
  `rehoboam/store/migrations/**`. The Python jobs are unchanged; the
  view tests run in the existing store test job.
- Migration 007 is applied by hand as admin before the first deploy.

## Testing

- `tests/store/test_web_views.py` (pytest, real Postgres via
  `store_dsn`): every view's column list; newest-per-manager ownership;
  newest listing snapshot; latest non-dry-run session for the squad;
  `web_session_summary` flattening of a real ingest `extra`; the
  integrity code list equals the Python rule list.
- `web/`: Vitest for the pure helpers (number formatting, trend
  colouring, sort comparators, expiry countdown, integrity code map);
  one Playwright smoke test per page against a preview deploy: log in
  with the owner's saved session, page renders, table has rows. The database is
  never mocked in the site's tests; the views are the contract and the
  Python tests own them.

## Rollout

1. Migration 007 (PR in the bot repo, applied as admin).
1. Vercel project, environment, Supabase Auth user, sign-ups off.
1. First deploy from `main`; Marco logs in.
1. Telegram stays on. After a week of use, revisit: approvals on the
   page, alerts off.

## Visual system (from the canvas)

Background `#0e1116`, surface `#11161c`, sidebar `#0b0e12`, border
`#1c222b` / `#242b35`, text `#e6e9ef`, muted `#8b93a1`, accent amber
`#e8b04a` (ours), positive `#5cc98a`, negative `#e06c6c`; position tags
GK `#7c8cf0`, DEF `#4fb3a8`, MID `#d9a441`, FW `#e07070` on 16 % tints.
IBM Plex Sans, tabular numerals, 44 px rows, 40 px headers, 6 px radii
on chips and nav, 8 px on cards. Dark only in this version.

## Rulings and known gaps

- `player_table.p_start` is the scorer's status prior (five discrete
  values), not Kickbase's per-player lineup probability. The site shows
  it as "P(start)" as the view names it; renaming or blending in
  `lineup_probability` is a scoring question, not a site one.
- `teams.short_name` currently holds a timestamp (PR G1 mapped `ts`,
  which is not the short name). The site uses `teams.name`; the fix is
  a separate small PR after a probe of the team profile payload.
- The site is a second cloud holding a database credential. Accepted:
  the role is the bot's own read/write role today; a read-only role
  (`rehoboam_web`) is a follow-up if the site ever leaves one user.
