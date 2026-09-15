# The complete database: everything Base XI shows, everything the bot decides from

Follows `docs/superpowers/specs/2026-09-11-data-foundation-design.md` (PRs A–E,
all merged by 2026-09-15) and `2026-09-15-pr-e-calibration-design.md`. Decided
with Marco 2026-09-15: the store should be at least as complete as
[base-xi.de](https://www.base-xi.de) — its player table is the checklist — and
carry player history as far back as Kickbase serves it. This spec details PR
G1 and outlines G2 and G3.

## Why

The store already holds the league-wide player universe, per-match points,
minutes and status back to 2013/14, daily injury status and lineup probability,
a year of market values per player, every completed transfer in the league,
each manager's transfer history and rank, and since PR E the bot's own
predictions and calibration. Two things it never kept are exactly what a
manager looks at before bidding: **what is on the market right now, and who
owns whom**. Sessions fetch both live, use them in memory, and throw them
away. Base XI's table has seventeen columns; fifteen are joins over tables we
have, the other two ("Besitzer", club names) are the gap.

## The checklist (Base XI's player table)

| Column                         | Source after G1                                                                                                                                            |
| ------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Team                           | `teams.name` via `player_universe.team_id`                                                                                                                 |
| Pos                            | `player_universe.position`                                                                                                                                 |
| MW                             | `player_status_daily.market_value` (newest day)                                                                                                            |
| Trend 24h, Trend               | `mv_series` (daily points, 365 days)                                                                                                                       |
| KI-Trend                       | not replicated; ours is `predictions.predicted_ep` for the next matchday (a market-value forecast can be fitted later on `mv_series` + `league_transfers`) |
| Punkte, Ø Pkt, Median, Pkt/Mio | `player_match_history`, current season, statuses 3 and 5 for appearances                                                                                   |
| Punkte Vorsaison, Ø Vorsaison  | same table, previous season                                                                                                                                |
| Einsätze, Einsätze Vorsaison   | rows with status in (3, 5)                                                                                                                                 |
| S11, S11 Vorsaison             | rows with status 5                                                                                                                                         |
| Besitzer                       | `manager_squads` (newest snapshot) → manager name, else "market" if listed, else "Kickbase"                                                                |

Plus three columns Base XI cannot have: `predicted_ep` (next matchday),
`p_start` (from `predictions.p_status`), and `fair_value_gap` (points per
million against the position's regression, the number behind their MW-Graph).

## What Kickbase exposes (survey 2026-09-15, facts)

- **Market** `GET /v4/leagues/{lid}/market`: one call, every listing — player
  id, ask (`prc`), market value, seller user id (`u.i`; absent means Kickbase
  sells), offer count (`ofc`), our own offer (`uop`/`uoid`), listed-at (`dt`).
  Rival bid amounts are never visible. No expiry field is parsed anywhere;
  whether one exists is a probe-first item (Kickbase listings expire after a
  fixed window; if the payload carries it, store it, else derive from `dt`).
- **Ownership** `GET /v4/leagues/{lid}/managers/{mid}/squad`: one call per
  manager, same player shape as our squad. `/ranking` lists the managers.
  `/managers/{mid}/transfer?start=N` pages of 25, newest first, whole season
  reachable. `/players/{pid}/transferHistory` is the per-player chain back to
  2021\. The activity feed carries names only.
- **Fixtures** `GET /v4/competitions/1/matchdays`: every match of the season
  with match id, kickoff, clubs, goals, status. `GET /v4/competitions/1/table`
  the league table. `GET /v4/leagues/{lid}/teams/{tid}/teamprofile` the club
  name, place and record.
- **Per-match events** (goals, assists, cards): not in the performance
  payload. Season totals `g`/`a` sit on the player page; the unprobed
  `/v4/matches/{id}/details` and `/v4/live/eventtypes` are the only plausible
  per-match sources. → G2, after a probe.
- **History depth**: performance returns every season a player has, for any
  id, competition-scoped; players outside our league's universe are fetchable
  by id (details for position); the current universe is enumerated via
  `/lineup/selection` per position (453–462 players). → G3.

## G1: market, ownership, fixtures, the table view

**No new API call in the trading session.** The session already fetches the
market and every manager's squad to build its context; it writes what it
fetched. The ingestion app adds about 35 requests per run to its ~940.

### Tables (migration `005_complete_database.sql`)

```sql
create table rehoboam.market_listings (
    snapshot_at   double precision not null,   -- epoch of the fetch
    player_id     text not null,
    ask           bigint not null,             -- prc
    market_value  bigint,
    seller_id     text,                        -- null = Kickbase
    offer_count   integer,                     -- ofc
    our_bid       bigint,                      -- uop when uoid is ours
    listed_at     double precision,            -- dt
    expires_at    double precision,            -- probe-first; null when unknown
    status        integer,                     -- st at listing time
    source        text not null,               -- session | ingest
    primary key (snapshot_at, player_id)
);
create index on rehoboam.market_listings (player_id, snapshot_at);

create table rehoboam.managers (
    manager_id  text primary key,
    league_id   text not null,
    name        text not null,
    is_self     boolean not null default false,
    updated_at  double precision not null
);

create table rehoboam.manager_squads (
    snapshot_at   double precision not null,
    manager_id    text not null,
    player_id     text not null,
    market_value  bigint,
    source        text not null,               -- session | ingest
    primary key (snapshot_at, manager_id, player_id)
);
create index on rehoboam.manager_squads (player_id, snapshot_at);
create index on rehoboam.manager_squads (manager_id, snapshot_at);

create table rehoboam.fixtures (
    match_id      text primary key,            -- mi
    season        text not null,
    day_number    integer not null,
    kickoff       double precision not null,   -- dt
    home_team_id  text not null,
    away_team_id  text not null,
    home_goals    integer,
    away_goals    integer,
    status        integer not null,            -- st: 0 not started, 2 finished, other in progress
    updated_at    double precision not null
);
create index on rehoboam.fixtures (season, day_number);

create table rehoboam.league_table (
    season      text not null,
    day_number  integer not null,
    team_id     text not null,
    place       integer not null,
    played      integer, won integer, drawn integer, lost integer,
    goals_for   integer, goals_against integer, points integer,
    updated_at  double precision not null,
    primary key (season, day_number, team_id)
);

create table rehoboam.teams (
    team_id     text primary key,
    name        text not null,
    short_name  text,
    updated_at  double precision not null
);
```

Column names for `league_table` and `teams` beyond `team_id`/`name`/`place`
are probe-first; the migration ships with what the probe confirms.

### The view

`rehoboam.player_table` is a SQL view (not materialised; ~600 rows) with the
checklist columns in Base XI's order and our three appended. Season strings
come from `player_match_history.season`; "previous season" is the newest
season title below the current one. `owner` resolves in this order: the
newest `manager_squads` snapshot that lists the player → `managers.name`;
else a `market_listings` row in the newest market snapshot → `market`; else
`Kickbase`. `trend_24h` and `trend_7d` are percentage changes from
`mv_series` at `now − 1 day` / `− 7 days` (nearest earlier point).

### Writers

- `enrichment/rows.py` gains pure builders: `market_listing_rows(payload, snapshot_at, our_user_id)`, `manager_squad_rows(manager_id, payload, snapshot_at)`, `fixture_rows(schedule_payload, season)`, `league_table_rows( payload, season, day_number)`, `team_row(profile_payload)`.
- `store/league_store.py`: `LeagueStore(dsn=None)` with `write_listings`,
  `write_squads`, `upsert_managers`, `upsert_fixtures`, `write_table`,
  `upsert_teams`, `latest_market(...)`, `owner_of(player_ids)`, one
  transaction per call, bulk inserts.
- **Session** (`auto_trader.py`, step 2a, best-effort): `_write_league_state( ctx)` writes the market listings from `ep_result["market_players"]` (the
  `MarketPlayer` objects carry ask, seller, offer count, listed-at — verified
  in the plan against `MarketPlayer.from_dict`) and the manager squads from the
  competitor squads `Trader` already fetched (kept on `ep_result` as
  `competitor_squads: dict[manager_id, list[player]]`, a new key). Our own
  squad is written under our manager id with `is_self`.
- **Ingest** (`enrichment/ingest.py`, before the per-player loop): market (1
  call), ranking (1), every manager's squad (~13), the newest transfer page per
  manager (~13, into the existing `manager_transfers`), schedule (already
  fetched for calibration) → `fixtures`, table (1) → `league_table`, and once
  a week the team profiles (18) → `teams`. All counted in `IngestStats` and
  `session_facts.extra`. The `transfers` kind (`player_transfers`) joins the
  per-player loop with a 7-day window.
- **Backfill** (`rehoboam backfill-league`): every manager's full transfer
  history (13 × ~13 pages once), resumable, controller-run.

### CLI

`rehoboam players [--position] [--owner] [--sort col]` prints the view as a
Rich table; `rehoboam market` prints the newest market snapshot with our
prediction per listing.

### Tests

Pure builders in the `test_safety_gate` style (every field, missing keys,
Kickbase-as-seller, our own offer); `tests/store/test_league_store.py` on the
real PostgreSQL (upserts idempotent, `owner_of` precedence, view columns and
the previous-season logic on seeded seasons); ingest wiring (request counts,
budget accounting, a failing manager call does not stop the loop); session
wiring (listings and squads written from the context, no extra API call —
asserted on a counting client).

### Verification before merge

Live dry-run `status` against prod: `market_listings` rows equal the market
size, `manager_squads` rows ≈ 13 × squad sizes, `player_table` returns every
live player with `owner` filled for every owned player. `rehoboam players`
matches Base XI's numbers for three spot-checked players (points, appearances,
S11, previous season).

## G2: per-match events (after a probe)

Probe `/v4/matches/{id}/details` and `/v4/live/eventtypes` with
`scripts/probe_match_details.py` (read-only). If per-player events are there:
`player_match_events(match_id, player_id, minutes, goals, assists, yellow, red, clean_sheet, points)` written for each finished fixture (9 calls per
matchday); if not: `player_season_stats(player_id, day, goals, assists, …)`
from the player page daily. Either way the scorer refit gets features beyond
points.

## G3: history for every player Kickbase has

`rehoboam backfill-history --season-floor 2013` walks every player id ever
seen in `league_transfers`, `manager_transfers`, `player_transfers`,
`manager_squads` and `market_listings`, fetches competition details
(position), full performance and 365 days of market value, resumable through
`sweep_progress`, budgeted per evening. Players no longer in the league keep
a `left_league_at` on `player_universe` so the session's live filter ignores
them.

## Rulings

- The session writes from what it already fetched; it never adds an API call.
- Snapshots are append-only (`snapshot_at` in the key); the view reads the
  newest. Retention is not a concern at four snapshots a day.
- Rival bid amounts are not observable; overbid learning stays on completed
  transfers (`league_transfers`).
- KI-Trend is not replicated; predicted points replace it and a market-value
  forecast is a later spec.
- `expires_at`, `league_table` and `teams` field names are probe-first.
- Order: G1 (one PR), G2 after its probe, G3 as a CLI backfill. The Base XI
  style dashboard is a separate PR on top of `player_table`.
