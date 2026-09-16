# The Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A private, read-only website over the store — Players, Squad & lineup, Market, Calibration & health — so Marco stops depending on Telegram to see what the bot knows.

**Architecture:** Six read-only SQL views in the `rehoboam` schema (one numbered migration in the bot repo) are the only contract. A fresh Next.js App Router project in `web/` replaces the dead Vite app there; every page is a server component that queries those views over the Supabase transaction pooler as `rehoboam_bot`. Supabase Auth (magic link, one user, sign-ups off) gates every route in middleware. Nothing writes.

**Tech Stack:** PostgreSQL views; Next.js 15 (App Router, React 19, TypeScript); `postgres` (porsager) 3.x; `@supabase/ssr` + `@supabase/supabase-js`; Tailwind CSS v4; Vitest; Playwright; pytest against a real PostgreSQL.

**Spec:** `docs/superpowers/specs/2026-09-16-dashboard-design.md`

## Global Constraints

- **The site never writes.** No INSERT, UPDATE, DELETE, no API route that mutates, no form that posts anywhere but Supabase Auth. A reviewer seeing a write in `web/` should reject the task.
- **The site never names a table.** Every query reads `rehoboam.web_*`. A new number the site needs becomes a migration in the bot repo first.
- **Views are plain SQL** — no functions, no `security definer`, no materialized views. Every statement schema-qualifies `rehoboam.<name>`.
- **`DATABASE_URL` never carries a `NEXT_PUBLIC_` prefix** and never reaches a client component. `web/scripts/check-secrets.mjs` fails the build if the pooler host or that variable name appears in `.next/static`.
- **Money is exact.** Market values, asks and budgets render as full integers with thousands separators (`65,089,670`), never `65.1 M`. This is a standing user preference, not a style choice.
- **Colors, exactly:** background `#0e1116`, surface `#11161c`, sidebar `#0b0e12`, border `#1c222b`, border-strong `#242b35`, text `#e6e9ef`, text-dim `#c3c9d3`, muted `#8b93a1`, accent `#e8b04a`, positive `#5cc98a`, negative `#e06c6c`; position tags GK `#7c8cf0`, DEF `#4fb3a8`, MID `#d9a441`, FW `#e07070` on the same hue at 16% alpha. Dark only — no light mode, no theme toggle.
- **Type:** IBM Plex Sans via `next/font/google`, tabular numerals on every numeric cell (`font-variant-numeric: tabular-nums`), 44 px table rows, 40 px table headers, 6 px radii on chips and nav, 8 px on cards.
- **Every page is a server component** with `export const revalidate = 300`. No client-side data fetching, no SWR, no React Query.
- **Python side:** line length 100; run `.venv/bin/black`, `.venv/bin/ruff`, `.venv/bin/pytest` directly — a session hook blocks the `uv run` wrapper. Format only your own hunks in existing Python files.
- **Never read, print or edit `.env`.** `web/.env.example` is fine. Tests use `store_dsn` (a fresh migrated PostgreSQL); never connect to a non-test database; never run a `rehoboam` CLI command.
- **Never commit to `main`.** Commit on the current branch. Every commit message ends with exactly these two lines:
  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_014Qwogi5GPPQJrhoKiZPvPw
  ```

## File Structure

**Bot repo (Python/SQL):**

| File                                          | Responsibility                                   |
| --------------------------------------------- | ------------------------------------------------ |
| `rehoboam/store/migrations/007_web_views.sql` | The six views. The site's entire contract.       |
| `tests/store/test_web_views.py`               | Column lists and the four semantics that matter. |

**The site (`web/`, all new — the existing 28 tracked files are deleted in Task 2):**

| File                                                                                           | Responsibility                                                           |
| ---------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| `web/src/lib/db.ts`                                                                            | The one Postgres client. Nothing else opens a connection.                |
| `web/src/lib/queries.ts`                                                                       | One exported async function per page's data need. All SQL lives here.    |
| `web/src/lib/format.ts`                                                                        | Pure formatters: money, percent, signed, countdown, integrity sentences. |
| `web/src/lib/supabase.ts`                                                                      | Server and middleware Supabase clients (auth only).                      |
| `web/src/lib/auth.ts`                                                                          | `requireSession()` — asserts a session or redirects.                     |
| `web/src/middleware.ts`                                                                        | Route gate.                                                              |
| `web/src/app/layout.tsx`                                                                       | Shell: font, sidebar, header.                                            |
| `web/src/app/page.tsx`                                                                         | Players (the index route).                                               |
| `web/src/app/squad/page.tsx`                                                                   | Squad & lineup.                                                          |
| `web/src/app/market/page.tsx`                                                                  | Market.                                                                  |
| `web/src/app/health/page.tsx`                                                                  | Calibration & health.                                                    |
| `web/src/app/login/page.tsx`, `web/src/app/auth/callback/route.ts`                             | Magic-link sign-in.                                                      |
| `web/src/components/Sidebar.tsx`, `StatusHeader.tsx`, `DataTable.tsx`, `Cell.tsx`, `Chips.tsx` | The shell and the one table component every page uses.                   |
| `web/scripts/check-secrets.mjs`                                                                | Build-time guard.                                                        |

______________________________________________________________________

### Task 1: Migration 007 — the six views

**Files:**

- Create: `rehoboam/store/migrations/007_web_views.sql`
- Create: `tests/store/test_web_views.py`
- Modify: `tests/store/test_migrate.py` (the simulated-migration filename constant, currently `007_...`; bump it to `008_simulated.sql` so it cannot collide)

**Interfaces:**

- Consumes: `rehoboam.player_table` (migration 006), `player_universe`, `market_listings`, `manager_squads`, `managers`, `predictions`, `session_facts`, `integrity_failures`, `calibration_reports`, `tracked_purchases`.
- Produces: views `rehoboam.web_players`, `web_squad`, `web_session_summary`, `web_market`, `web_ownership`, `web_calibration`. Task 2 onward reads only these.

Facts you need, already verified against production:

- `player_table` already carries `team` (the club name, 588/588 populated) — do **not** join `teams` again.

- `player_table.owner` prefers the manager's name and only says `market` for a listing with no owner, so a manager's listed player is indistinguishable there. `web_players` therefore adds a real `listed` boolean.

- `calibration_reports.gate` is NULL on every current row and the non-backfill rows have `n = 0`. The views must not filter those out; the pages render them as "not yet reported".

- `integrity_failures.rule` values in use: I1–I7.

- [ ] **Step 1: Write the migration**

Create `rehoboam/store/migrations/007_web_views.sql`:

```sql
-- The dashboard's contract: six read-only views. The site reads nothing else.
-- Plain SQL, no functions, no security definer; `refresh_grants` in the
-- migrate runner grants the bot role on views as well as tables.

-- 1. Every player, Base XI's columns plus ours, with a real "listed" flag.
--    `player_table` already resolves the club name and ownership per manager.
create or replace view rehoboam.web_players as
with listed as (
    select player_id from rehoboam.market_listings
    where snapshot_at = (select max(snapshot_at) from rehoboam.market_listings)
)
select p.player_id, p.name, p.team, u.team_id, p.position, p.market_value,
    p.trend_24h_pct, p.trend_7d_pct, p.points, p.avg_points, p.median_points,
    p.points_per_million, p.points_prev, p.avg_points_prev,
    p.appearances, p.appearances_prev, p.starts, p.starts_prev,
    p.owner, p.predicted_ep, p.p_start, p.fair_value_gap,
    (l.player_id is not null) as listed
from rehoboam.player_table p
join rehoboam.player_universe u on u.player_id = p.player_id
left join listed l on l.player_id = p.player_id;

-- 2. Our squad as the newest real session saw it, with the eleven it chose.
--    A dry run (a local `status`) must never redefine "the lineup", so the
--    session is the newest non-dry-run row from the trading app.
create or replace view rehoboam.web_squad as
with latest as (
    select session_id, legal_formation, budget, sellable_value, next_kickoff,
        started_at, cost_basis_missing
    from rehoboam.session_facts
    where app = 'function' and dry_run = 0
    order by started_at desc
    limit 1
)
select l.session_id, l.legal_formation, l.budget, l.sellable_value,
    l.next_kickoff, l.started_at as session_started_at, l.cost_basis_missing,
    pr.player_id, p.name, p.team, pr.position, p.market_value,
    p.points, p.avg_points, p.owner,
    pr.predicted_ep, pr.live_ep, pr.in_best_11,
    (pr.p_status ->> '5')::double precision as p_start,
    tp.buy_price as cost_basis,
    case when tp.buy_price is not null then p.market_value - tp.buy_price end as gain_loss
from latest l
join rehoboam.predictions pr on pr.session_id = l.session_id and pr.owned
left join rehoboam.web_players p on p.player_id = pr.player_id
left join rehoboam.tracked_purchases tp on tp.player_id = pr.player_id;

-- 3. Every run of either app, with its integrity rules and the ingest
--    counters flattened out of `extra`. Unordered and unlimited on purpose:
--    the caller orders by started_at and takes what it needs.
create or replace view rehoboam.web_session_summary as
select f.session_id, f.app, f.mode, f.dry_run, f.started_at, f.duration_s, f.phase,
    f.next_kickoff, f.legal_formation, f.budget, f.sellable_value,
    f.cost_basis_missing, f.predictions_written, f.lineup_result,
    f.errors, f.error_text,
    coalesce(i.rules, array[]::text[]) as integrity_rules,
    (f.extra -> 'league_state' ->> 'squads')::int as league_state_squads,
    (f.extra ->> 'requests')::int as requests,
    (f.extra ->> 'failed')::int as failed,
    (f.extra ->> 'status_written')::int as status_written,
    (f.extra ->> 'performance_fetched')::int as performance_fetched,
    (f.extra ->> 'transfers_fetched')::int as transfers_fetched,
    (f.extra ->> 'universe_size')::int as universe_size,
    f.extra ->> 'stopped_by' as stopped_by,
    (f.extra -> 'league' ->> 'failed')::int as league_failed,
    (f.extra -> 'league' ->> 'teams')::int as league_teams,
    (f.extra -> 'league' ->> 'fixtures')::int as league_fixtures,
    (f.extra -> 'league' ->> 'listings')::int as league_listings,
    (f.extra -> 'league' ->> 'squads')::int as league_squads,
    f.extra -> 'calibration' -> 'settled' as calibration_settled,
    f.extra -> 'calibration' -> 'reported' as calibration_reported
from rehoboam.session_facts f
left join (
    select session_id, array_agg(distinct rule order by rule) as rules
    from rehoboam.integrity_failures
    group by session_id
) i on i.session_id = f.session_id;

-- 4. The newest listing snapshot, priced against what we think a player scores.
create or replace view rehoboam.web_market as
with newest as (
    select max(snapshot_at) as at from rehoboam.market_listings
),
us as (
    select manager_id from rehoboam.managers where is_self order by manager_id limit 1
)
select l.snapshot_at, l.player_id, p.name, p.team, p.position,
    l.ask, l.market_value, l.mv_trend, l.offer_count, l.our_bid,
    l.listed_at, l.expires_at, l.status, l.lineup_probability,
    coalesce(m.name, 'Kickbase') as seller,
    (l.seller_id is not null and l.seller_id = (select manager_id from us)) as is_ours,
    p.predicted_ep, p.p_start, p.fair_value_gap, p.points, p.avg_points
from rehoboam.market_listings l
join newest n on l.snapshot_at = n.at
left join rehoboam.web_players p on p.player_id = l.player_id
left join rehoboam.managers m on m.manager_id = l.seller_id;

-- 5. Who owns what, from each manager's OWN newest snapshot — never the
--    global newest, or one partial write blanks every other manager.
create or replace view rehoboam.web_ownership as
with newest as (
    select manager_id, max(snapshot_at) as at
    from rehoboam.manager_squads
    group by manager_id
)
select s.manager_id, m.name as manager, m.is_self, s.player_id,
    p.name as player_name, p.team, p.position,
    s.market_value, s.gain_loss, s.on_market,
    p.predicted_ep, p.p_start, s.snapshot_at
from rehoboam.manager_squads s
join newest n on n.manager_id = s.manager_id and n.at = s.snapshot_at
join rehoboam.managers m on m.manager_id = s.manager_id
left join rehoboam.web_players p on p.player_id = s.player_id;

-- 6. Every calibration report, live and backfill, including the ones that
--    settled empty (n = 0, gate null) — the page says "not yet reported".
create or replace view rehoboam.web_calibration as
select season, day_number, backfill, computed_at, n, n_unpredicted, n_stale_rows,
    mae, bias, spearman, baseline_spearman, spearman_played,
    top11_regret, baseline_top11_regret, squad_regret,
    live_spearman, live_n, by_position, by_status, worst, gate, telegram_sent
from rehoboam.calibration_reports;
```

- [ ] **Step 2: Teach `tests/store/test_migrate.py` about migration 007**

That file asserts three things a new migration changes. `_tables()` reads `information_schema.tables`, which in PostgreSQL **includes views** — that is why `player_table` is already in `EXPECTED_TABLES`. So:

1. `test_migrate_creates_every_table_in_the_rehoboam_schema`: add `"007_web_views.sql"` to the expected `applied` list, after `"006_player_table.sql"`.
1. `test_migrate_is_idempotent`: change `applied_versions(conn) == {1, 2, 3, 4, 5, 6}` to `{1, 2, 3, 4, 5, 6, 7}`.
1. `EXPECTED_TABLES` (top of the file): add the six view names — `web_players`, `web_squad`, `web_session_summary`, `web_market`, `web_ownership`, `web_calibration`.

Run `.venv/bin/pytest tests/store/test_migrate.py -q` after Step 1: it must pass. If it fails on the relation set, you missed a view name.

- [ ] **Step 3: Write the failing tests**

Create `tests/store/test_web_views.py`:

```python
"""The dashboard's six views (migration 007)."""

from __future__ import annotations

import time

from rehoboam.services.calibration import CalibrationReport
from rehoboam.services.session_facts import IntegrityFailure, SessionFacts
from rehoboam.store import connect
from rehoboam.store.calibration_store import CalibrationStore
from rehoboam.store.corpus_store import CorpusStore
from rehoboam.store.league_store import LeagueStore
from rehoboam.store.session_store import SessionStore

NOW = time.time()
SEASON = "2026/2027"


def _players(dsn):
    CorpusStore(dsn=dsn).upsert_players(
        [
            {
                "player_id": "a",
                "first_name": None,
                "last_name": "Alpha",
                "position": "Midfielder",
                "team_id": "7",
                "market_value": 10_000_000,
                "average_points": 50.0,
            },
            {
                "player_id": "b",
                "first_name": None,
                "last_name": "Beta",
                "position": "Defender",
                "team_id": "8",
                "market_value": 2_000_000,
                "average_points": 10.0,
            },
        ]
    )
    LeagueStore(dsn=dsn).upsert_teams(
        [
            {"team_id": "7", "name": "Bayern", "short_name": "FCB", "updated_at": NOW},
            {"team_id": "8", "name": "Schalke", "short_name": "S04", "updated_at": NOW},
        ]
    )


def _managers(dsn):
    LeagueStore(dsn=dsn).upsert_managers(
        [
            {
                "manager_id": "me",
                "league_id": "1",
                "name": "Brownie",
                "is_self": True,
                "updated_at": NOW,
            },
            {
                "manager_id": "rival",
                "league_id": "1",
                "name": "Rival",
                "is_self": False,
                "updated_at": NOW,
            },
        ]
    )


def _rows(dsn, sql):
    with connect(dsn) as conn:
        return conn.execute(sql).fetchall()


def test_web_players_carries_the_columns_the_site_reads(store_dsn):
    _players(store_dsn)
    rows = {
        r["player_id"]: r
        for r in _rows(store_dsn, "select * from rehoboam.web_players")
    }
    assert set(rows) == {"a", "b"}
    for key in (
        "name",
        "team",
        "team_id",
        "position",
        "market_value",
        "owner",
        "predicted_ep",
        "p_start",
        "fair_value_gap",
        "listed",
    ):
        assert key in rows["a"], key
    assert rows["a"]["team"] == "Bayern" and rows["a"]["team_id"] == "7"
    assert rows["a"]["listed"] is False


def test_web_players_flags_a_listed_player_its_owner_still_owns(store_dsn):
    _players(store_dsn)
    _managers(store_dsn)
    league = LeagueStore(dsn=store_dsn)
    league.write_squads(
        [
            {
                "snapshot_at": NOW,
                "manager_id": "rival",
                "player_id": "a",
                "market_value": 10_000_000,
                "gain_loss": 0,
                "on_market": True,
                "source": "test",
            }
        ]
    )
    league.write_listings(
        [
            {
                "snapshot_at": NOW,
                "player_id": "a",
                "ask": 11_000_000,
                "market_value": 10_000_000,
                "mv_trend": 1,
                "seller_id": "rival",
                "offer_count": 0,
                "our_bid": None,
                "listed_at": NOW,
                "expires_at": NOW + 3600,
                "status": 0,
                "lineup_probability": 1,
                "source": "test",
            }
        ]
    )
    row = {
        r["player_id"]: r
        for r in _rows(store_dsn, "select * from rehoboam.web_players")
    }["a"]
    # `player_table.owner` says "Rival"; only `listed` tells the page it is for sale.
    assert row["owner"] == "Rival" and row["listed"] is True


def test_web_ownership_takes_each_managers_own_newest_snapshot(store_dsn):
    _players(store_dsn)
    _managers(store_dsn)
    league = LeagueStore(dsn=store_dsn)
    league.write_squads(
        [
            {
                "snapshot_at": NOW,
                "manager_id": "me",
                "player_id": "a",
                "market_value": 10_000_000,
                "gain_loss": 0,
                "on_market": False,
                "source": "t",
            },
            {
                "snapshot_at": NOW,
                "manager_id": "rival",
                "player_id": "b",
                "market_value": 2_000_000,
                "gain_loss": 0,
                "on_market": False,
                "source": "t",
            },
        ]
    )
    # A later snapshot that contains only our own squad must not blank the rival.
    league.write_squads(
        [
            {
                "snapshot_at": NOW + 60,
                "manager_id": "me",
                "player_id": "a",
                "market_value": 10_500_000,
                "gain_loss": 0,
                "on_market": False,
                "source": "t",
            },
        ]
    )
    rows = {
        r["player_id"]: r
        for r in _rows(store_dsn, "select * from rehoboam.web_ownership")
    }
    assert rows["a"]["manager"] == "Brownie" and rows["a"]["is_self"] is True
    assert rows["a"]["market_value"] == 10_500_000
    assert (
        rows["b"]["manager"] == "Rival"
    ), "the rival's older snapshot still owns their player"


def test_web_market_reads_only_the_newest_snapshot_and_names_the_seller(store_dsn):
    _players(store_dsn)
    _managers(store_dsn)
    league = LeagueStore(dsn=store_dsn)
    old = {
        "snapshot_at": NOW,
        "player_id": "a",
        "ask": 1,
        "market_value": 10_000_000,
        "mv_trend": 0,
        "seller_id": None,
        "offer_count": 0,
        "our_bid": None,
        "listed_at": NOW,
        "expires_at": NOW + 10,
        "status": 0,
        "lineup_probability": 1,
        "source": "t",
    }
    league.write_listings([old])
    league.write_listings(
        [
            {**old, "snapshot_at": NOW + 60, "ask": 11_000_000, "seller_id": "me"},
            {
                **old,
                "snapshot_at": NOW + 60,
                "player_id": "b",
                "ask": 2_500_000,
                "seller_id": "rival",
                "market_value": 2_000_000,
            },
        ]
    )
    rows = {
        r["player_id"]: r for r in _rows(store_dsn, "select * from rehoboam.web_market")
    }
    assert set(rows) == {"a", "b"}, "the older snapshot is gone"
    assert rows["a"]["ask"] == 11_000_000
    assert rows["a"]["seller"] == "Brownie" and rows["a"]["is_ours"] is True
    assert rows["b"]["seller"] == "Rival" and rows["b"]["is_ours"] is False


def test_web_market_calls_an_unowned_listing_kickbase(store_dsn):
    _players(store_dsn)
    LeagueStore(dsn=store_dsn).write_listings(
        [
            {
                "snapshot_at": NOW,
                "player_id": "a",
                "ask": 9,
                "market_value": 10_000_000,
                "mv_trend": 0,
                "seller_id": None,
                "offer_count": 0,
                "our_bid": None,
                "listed_at": NOW,
                "expires_at": NOW + 10,
                "status": 0,
                "lineup_probability": 1,
                "source": "t",
            },
        ]
    )
    row = _rows(store_dsn, "select * from rehoboam.web_market")[0]
    assert row["seller"] == "Kickbase" and row["is_ours"] is False


def _facts(session_id, *, app="function", dry_run=False, started_at=NOW, **kw):
    return SessionFacts(
        session_id=session_id,
        app=app,
        mode=kw.pop("mode", "lineup_only"),
        dry_run=dry_run,
        started_at=started_at,
        **kw,
    )


def _prediction(session_id, player_id, *, owned=True, in_best_11=True, ep=100.0):
    return {
        "session_id": session_id,
        "player_id": player_id,
        "season": SEASON,
        "day_number": 4,
        "kickoff": NOW + 86400,
        "predicted_at": NOW,
        "predicted_ep": ep,
        "p_status": {"1": 0.1, "5": 0.8},
        "rate": 1.0,
        "prev_status": 1,
        "live_status": 1,
        "position": "Midfielder",
        "team_id": "7",
        "owned": owned,
        "listed": False,
        "in_best_11": in_best_11,
        "live_ep": ep,
        "data_grade": "A",
        "app": "function",
        "dry_run": False,
        "backfill": False,
    }


def test_web_squad_uses_the_newest_real_session_not_a_dry_run(store_dsn):
    _players(store_dsn)
    sessions = SessionStore(dsn=store_dsn)
    calib = CalibrationStore(dsn=store_dsn)
    sessions.record(
        _facts("real", started_at=NOW, legal_formation="4-3-3", budget=1_725_739)
    )
    sessions.record(_facts("dry", app="cli", dry_run=True, started_at=NOW + 600))
    calib.write_predictions([_prediction("real", "a"), _prediction("dry", "b")])
    rows = _rows(store_dsn, "select * from rehoboam.web_squad")
    assert [r["player_id"] for r in rows] == ["a"]
    assert rows[0]["session_id"] == "real"
    assert rows[0]["legal_formation"] == "4-3-3" and rows[0]["budget"] == 1_725_739
    assert rows[0]["in_best_11"] is True and rows[0]["p_start"] == 0.8
    assert rows[0]["cost_basis"] is None and rows[0]["gain_loss"] is None


def test_web_squad_carries_cost_basis_and_gain_when_the_purchase_is_known(store_dsn):
    _players(store_dsn)
    SessionStore(dsn=store_dsn).record(_facts("real", legal_formation="4-3-3"))
    CalibrationStore(dsn=store_dsn).write_predictions([_prediction("real", "a")])
    with connect(store_dsn) as conn:
        conn.execute(
            "insert into rehoboam.tracked_purchases "
            "(player_id, player_name, buy_price, buy_date, source) "
            "values ('a', 'Alpha', 8000000, %s, 'test')",
            (NOW,),
        )
    row = _rows(store_dsn, "select * from rehoboam.web_squad")[0]
    assert row["cost_basis"] == 8_000_000
    assert row["gain_loss"] == 2_000_000


def test_web_session_summary_flattens_an_ingest_run_and_its_rules(store_dsn):
    sessions = SessionStore(dsn=store_dsn)
    sessions.record(
        _facts(
            "ingest1",
            app="external",
            mode="ingest",
            extra={
                "requests": 1185,
                "failed": 0,
                "status_written": 371,
                "performance_fetched": 371,
                "transfers_fetched": 371,
                "universe_size": 462,
                "stopped_by": "deadline",
                "league": {
                    "failed": 0,
                    "teams": 24,
                    "fixtures": 306,
                    "listings": 48,
                    "squads": 159,
                },
                "calibration": {"settled": [3], "reported": [], "waiting": {}},
            },
        )
    )
    sessions.record(_facts("sess1", extra={"league_state": {"squads": 158}}))
    sessions.record_failures(
        "sess1", [IntegrityFailure("I4", "2 owned player(s) without a cost basis")]
    )
    rows = {
        r["session_id"]: r
        for r in _rows(store_dsn, "select * from rehoboam.web_session_summary")
    }
    ingest = rows["ingest1"]
    assert ingest["requests"] == 1185 and ingest["status_written"] == 371
    assert ingest["universe_size"] == 462 and ingest["stopped_by"] == "deadline"
    assert ingest["league_teams"] == 24 and ingest["league_failed"] == 0
    assert ingest["calibration_settled"] == [3]
    assert ingest["integrity_rules"] == []
    session = rows["sess1"]
    assert session["integrity_rules"] == ["I4"]
    assert session["league_state_squads"] == 158
    assert session["requests"] is None, "a trading session has no ingest counters"


def test_web_calibration_keeps_an_empty_settled_report(store_dsn):
    # `write_calibration` takes the report object, not loose metrics: it calls
    # `report.as_row()` and writes rows + report in one transaction.
    empty = CalibrationReport(
        n=0,
        n_unpredicted=0,
        n_stale_rows=0,
        mae=None,
        bias=None,
        spearman=None,
        baseline_spearman=None,
        spearman_played=None,
        top11_regret=None,
        baseline_top11_regret=None,
        squad_regret=None,
        live_spearman=None,
        live_n=0,
    )
    CalibrationStore(dsn=store_dsn).write_calibration(
        season=SEASON,
        day_number=1,
        backfill=False,
        rows=[],
        report=empty,
        gate=None,
        computed_at=NOW,
    )
    rows = _rows(store_dsn, "select * from rehoboam.web_calibration")
    assert len(rows) == 1
    assert rows[0]["n"] == 0 and rows[0]["gate"] is None
    assert rows[0]["backfill"] is False
```

These call shapes were verified against the store on 2026-09-16: `SessionStore.record_failures(session_id, [IntegrityFailure(rule, detail)])`, `CalibrationStore.write_calibration(season=, day_number=, backfill=, rows=, report=CalibrationReport(...), gate=, computed_at=)`, and `SessionFacts.dry_run` is a **bool**. If a signature has moved since, read the store module and adapt the call — never change the store to fit the test.

- [ ] **Step 4: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/store/test_web_views.py -q`
Expected: every test FAILS with `psycopg.errors.UndefinedTable: relation "rehoboam.web_players" does not exist` (or similar) because migration 007 has not been written yet — run this step BEFORE Step 1 if you prefer strict TDD; either order is fine as long as you see red before green.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/store/test_web_views.py tests/store/test_migrate.py tests/store/test_player_table.py -q`
Expected: PASS, output pristine.

- [ ] **Step 6: Measure the two heavy views**

The spec's bar is under one second per page load through the pooler. With the test database seeded by the tests above the row counts are trivial, so measure shape, not speed: run `explain (analyze, buffers) select * from rehoboam.web_players` and `... from rehoboam.web_market` against `store_dsn` in a scratch script and paste the plan lines into your report. Flag any sequential scan over `player_match_history` that is not already inherited from `player_table`.

- [ ] **Step 7: Commit**

```bash
git add rehoboam/store/migrations/007_web_views.sql tests/store/test_web_views.py tests/store/test_migrate.py
git commit -m "feat(store): the dashboard's six read-only views"
```

______________________________________________________________________

### Task 2: The Next.js scaffold, the one database client, and the secret guard

**Files:**

- Delete: every tracked file under `web/` (28 of them — the February Vite app whose FastAPI backend was deleted in b376874) plus the untracked `web/dist/`, `web/node_modules/`, `web/public/favicon.svg`
- Create: `web/package.json`, `web/tsconfig.json`, `web/next.config.mjs`, `web/.gitignore`, `web/.env.example`, `web/vitest.config.ts`
- Create: `web/src/app/globals.css`, `web/src/lib/db.ts`, `web/scripts/check-secrets.mjs`
- Create: `web/src/lib/db.test.ts`
- Modify: `.gitignore` (the repo root already ignores `web/node_modules/`, `web/dist/`, `web/.env`, `web/.env.local`; add `web/.next/`)

**Interfaces:**

- Produces: `sql` — a `postgres` (porsager) tagged-template client exported from `web/src/lib/db.ts`, and `dsn()` (exported for the test) which reads `process.env.DATABASE_URL` and throws a named error when it is missing. Tasks 3–8 import `sql` and nothing else for data.

Why porsager `postgres` and not `pg`: it is the tagged-template client, its `prepare: false` option is exactly what the Supabase transaction pooler needs (the pooler rejects prepared statements — the same reason `store/__init__.py` sets `prepare_threshold=None`), and it pools per process, which suits Vercel's serverless functions.

- [ ] **Step 1: Delete the dead app**

```bash
git rm -r --quiet web
rm -rf web
mkdir -p web/src/app web/src/lib web/src/components web/scripts
```

Deleting it whole is deliberate: its pages (Portfolio, Analytics, Account) map to a backend that no longer exists, and keeping any of it would mean reading two conventions at once.

- [ ] **Step 2: Write the project files**

`web/package.json`:

```json
{
  "name": "rehoboam-web",
  "private": true,
  "version": "1.0.0",
  "scripts": {
    "dev": "next dev",
    "build": "next build && node scripts/check-secrets.mjs",
    "start": "next start",
    "lint": "next lint",
    "typecheck": "tsc --noEmit",
    "test": "vitest run"
  },
  "dependencies": {
    "next": "^15.1.0",
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "postgres": "^3.4.5",
    "server-only": "^0.0.1",
    "@supabase/supabase-js": "^2.47.0",
    "@supabase/ssr": "^0.5.2"
  },
  "devDependencies": {
    "typescript": "^5.7.0",
    "@types/node": "^22.10.0",
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0",
    "eslint": "^9.17.0",
    "eslint-config-next": "^15.1.0",
    "tailwindcss": "^4.0.0",
    "@tailwindcss/postcss": "^4.0.0",
    "postcss": "^8.4.49",
    "vitest": "^2.1.8"
  }
}
```

`web/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["dom", "dom.iterable", "ES2022"],
    "allowJs": false,
    "skipLibCheck": true,
    "strict": true,
    "noEmit": true,
    "esModuleInterop": true,
    "module": "esnext",
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "jsx": "preserve",
    "incremental": true,
    "plugins": [{ "name": "next" }],
    "paths": { "@/*": ["./src/*"] }
  },
  "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx", ".next/types/**/*.ts"],
  "exclude": ["node_modules"]
}
```

`web/next.config.mjs`:

```javascript
/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The Postgres driver must not be bundled into the edge/client graph.
  serverExternalPackages: ["postgres"],
};

export default nextConfig;
```

`web/postcss.config.mjs`:

```javascript
export default { plugins: { "@tailwindcss/postcss": {} } };
```

`web/.gitignore`:

```
node_modules/
.next/
.env
.env.local
next-env.d.ts
```

`web/.env.example`:

```
# The bot role's pooler URL. Server-side only — never NEXT_PUBLIC_.
DATABASE_URL=postgresql://rehoboam_bot.<ref>:<password>@aws-1-eu-west-1.pooler.supabase.com:6543/postgres
# Supabase Auth. The anon key is public by design and used only for sign-in.
NEXT_PUBLIC_SUPABASE_URL=https://<ref>.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=<anon key>
```

`web/src/app/globals.css` — the whole colour system as Tailwind v4 theme tokens, copied verbatim from the plan's Global Constraints:

```css
@import "tailwindcss";

@theme {
  --color-bg: #0e1116;
  --color-surface: #11161c;
  --color-sidebar: #0b0e12;
  --color-border: #1c222b;
  --color-border-strong: #242b35;
  --color-text: #e6e9ef;
  --color-text-dim: #c3c9d3;
  --color-muted: #8b93a1;
  --color-accent: #e8b04a;
  --color-positive: #5cc98a;
  --color-negative: #e06c6c;
  --color-gk: #7c8cf0;
  --color-def: #4fb3a8;
  --color-mid: #d9a441;
  --color-fw: #e07070;
}

html, body {
  background: var(--color-bg);
  color: var(--color-text);
  margin: 0;
  -webkit-font-smoothing: antialiased;
}

.tnum { font-variant-numeric: tabular-nums; }
```

- [ ] **Step 3: Write the failing test for the database module**

`web/src/lib/db.test.ts`:

```typescript
import { describe, expect, it, afterEach } from "vitest";
import { dsn } from "./db";

const original = process.env.DATABASE_URL;
afterEach(() => {
  process.env.DATABASE_URL = original;
});

describe("dsn", () => {
  it("returns DATABASE_URL when it is set", () => {
    process.env.DATABASE_URL = "postgresql://user:pw@host:6543/postgres";
    expect(dsn()).toBe("postgresql://user:pw@host:6543/postgres");
  });

  it("throws a named error when DATABASE_URL is missing", () => {
    delete process.env.DATABASE_URL;
    expect(() => dsn()).toThrow(/DATABASE_URL is not set/);
  });

  it("refuses a URL that was exposed to the client", () => {
    delete process.env.DATABASE_URL;
    process.env.NEXT_PUBLIC_DATABASE_URL = "postgresql://leaked";
    expect(() => dsn()).toThrow(/DATABASE_URL is not set/);
    delete process.env.NEXT_PUBLIC_DATABASE_URL;
  });
});
```

`web/vitest.config.ts`:

```typescript
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
```

- [ ] **Step 4: Run it to see it fail**

Run: `cd web && npm install && npx vitest run`
Expected: FAIL — `Failed to resolve import "./db"`.

- [ ] **Step 5: Write `web/src/lib/db.ts`**

```typescript
import "server-only";
import postgres from "postgres";

/** The bot role's pooler URL. Server-side only; never a NEXT_PUBLIC_ variable. */
export function dsn(): string {
  const url = process.env.DATABASE_URL;
  if (!url) {
    throw new Error(
      "DATABASE_URL is not set. The site reads the store as the rehoboam_bot role " +
        "through the Supabase transaction pooler; set it in the Vercel project.",
    );
  }
  return url;
}

declare global {
  // eslint-disable-next-line no-var
  var __rehoboamSql: ReturnType<typeof postgres> | undefined;
}

/**
 * One client per process, reused across requests.
 *
 * `prepare: false` is not optional: the Supabase transaction pooler rejects
 * prepared statements, which is the same reason the Python store passes
 * `prepare_threshold=None`. `max: 3` keeps a serverless fleet from exhausting
 * the pooler's connection budget; the site is read-only and low-traffic.
 */
export const sql =
  globalThis.__rehoboamSql ??
  postgres(dsn(), {
    prepare: false,
    max: 3,
    idle_timeout: 20,
    connect_timeout: 10,
    transform: { undefined: null },
  });

if (process.env.NODE_ENV !== "production") globalThis.__rehoboamSql = sql;
```

`import "server-only"` makes any accidental import from a client component a build error. Add `"server-only": "^0.0.1"` to dependencies.

- [ ] **Step 6: Write the build-time secret guard**

`web/scripts/check-secrets.mjs`:

```javascript
// Fails the build if a server secret reached the client bundle.
import { readdir, readFile } from "node:fs/promises";
import { join } from "node:path";

const NEEDLES = ["DATABASE_URL", "pooler.supabase.com", "rehoboam_bot"];
const ROOT = ".next/static";

async function* files(dir) {
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) yield* files(path);
    else yield path;
  }
}

const hits = [];
for await (const path of files(ROOT)) {
  if (!/\.(js|mjs|json|css)$/.test(path)) continue;
  const text = await readFile(path, "utf8");
  for (const needle of NEEDLES) if (text.includes(needle)) hits.push(`${needle} in ${path}`);
}

if (hits.length) {
  console.error("Server secrets reached the client bundle:\n" + hits.join("\n"));
  process.exit(1);
}
console.log(`check-secrets: clean (${NEEDLES.length} patterns)`);
```

- [ ] **Step 7: Run the tests and the build**

Run: `cd web && npx vitest run && npm run typecheck`
Expected: 3 tests PASS, typecheck clean.

The `npm run build` step needs a `DATABASE_URL` to import `db.ts`, and there is no page to render yet — leave the full build to Task 4, where the first page exists. If you want the guard exercised now, run `node scripts/check-secrets.mjs` against an empty `.next/static` and confirm it prints `clean`.

- [ ] **Step 8: Commit**

```bash
git add -A web .gitignore
git commit -m "feat(web): replace the dead Vite app with a Next.js scaffold and one pooled client"
```

______________________________________________________________________

### Task 3: Sign-in — middleware, the login page, the callback

**Files:**

- Create: `web/src/lib/supabase.ts`, `web/src/lib/auth.ts`, `web/src/middleware.ts`
- Create: `web/src/app/login/page.tsx`, `web/src/app/login/actions.ts`, `web/src/app/auth/callback/route.ts`
- Create: `web/src/lib/auth.test.ts`

**Interfaces:**

- Consumes: nothing from earlier tasks except the project scaffold.

- Produces: `requireSession(): Promise<Session>` from `web/src/lib/auth.ts` — every page calls it first; `createServerClient()` and `createMiddlewareClient(request, response)` from `web/src/lib/supabase.ts`.

- [ ] **Step 1: Write the Supabase clients**

`web/src/lib/supabase.ts`:

```typescript
import { createServerClient as createSSRClient, type CookieOptions } from "@supabase/ssr";
import { cookies } from "next/headers";
import type { NextRequest, NextResponse } from "next/server";

function env() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !key) throw new Error("NEXT_PUBLIC_SUPABASE_URL / _ANON_KEY are not set");
  return { url, key };
}

/** For server components and route handlers. */
export async function createServerClient() {
  const { url, key } = env();
  const store = await cookies();
  return createSSRClient(url, key, {
    cookies: {
      getAll: () => store.getAll(),
      setAll: (list) => {
        try {
          list.forEach(({ name, value, options }) => store.set(name, value, options));
        } catch {
          // A server component cannot set cookies; the middleware refreshes them.
        }
      },
    },
  });
}

/** For middleware, where cookies ride on the response. */
export function createMiddlewareClient(request: NextRequest, response: NextResponse) {
  const { url, key } = env();
  return createSSRClient(url, key, {
    cookies: {
      getAll: () => request.cookies.getAll(),
      setAll: (list: { name: string; value: string; options: CookieOptions }[]) =>
        list.forEach(({ name, value, options }) => response.cookies.set(name, value, options)),
    },
  });
}
```

- [ ] **Step 2: Write the route gate**

`web/src/middleware.ts`:

```typescript
import { NextResponse, type NextRequest } from "next/server";
import { createMiddlewareClient } from "@/lib/supabase";

const PUBLIC = ["/login", "/auth/callback"];

export async function middleware(request: NextRequest) {
  const response = NextResponse.next({ request });
  const supabase = createMiddlewareClient(request, response);
  const {
    data: { user },
  } = await supabase.auth.getUser();

  const path = request.nextUrl.pathname;
  const isPublic = PUBLIC.some((p) => path === p || path.startsWith(p + "/"));

  if (!user && !isPublic) {
    const to = request.nextUrl.clone();
    to.pathname = "/login";
    to.searchParams.set("next", path);
    return NextResponse.redirect(to);
  }
  if (user && path === "/login") {
    const to = request.nextUrl.clone();
    to.pathname = "/";
    to.search = "";
    return NextResponse.redirect(to);
  }
  return response;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
```

`getUser()` rather than `getSession()`: it revalidates the token against Supabase instead of trusting a cookie the browser could have forged.

- [ ] **Step 3: Write the per-page assertion**

`web/src/lib/auth.ts`:

```typescript
import "server-only";
import { redirect } from "next/navigation";
import { createServerClient } from "./supabase";

/**
 * Every page calls this before it queries. The middleware already redirects an
 * anonymous request, so this is the second lock: a route added later that the
 * matcher somehow misses still cannot reach the store.
 */
export async function requireSession() {
  const supabase = await createServerClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");
  return user;
}
```

- [ ] **Step 4: Write the login page and the callback**

`web/src/app/login/actions.ts`:

```typescript
"use server";

import { headers } from "next/headers";
import { createServerClient } from "@/lib/supabase";

export async function sendMagicLink(_prev: { message: string }, form: FormData) {
  const email = String(form.get("email") ?? "").trim();
  if (!email) return { message: "Enter the address the account was created with." };

  const supabase = await createServerClient();
  const origin = (await headers()).get("origin") ?? "";
  const { error } = await supabase.auth.signInWithOtp({
    email,
    options: { emailRedirectTo: `${origin}/auth/callback`, shouldCreateUser: false },
  });
  // The same answer either way: never reveal whether an address has an account.
  return { message: error ? "Check your inbox." : "Check your inbox." };
}
```

`shouldCreateUser: false` is what keeps the site to one user even if sign-ups are ever re-enabled in the Supabase dashboard.

`web/src/app/login/page.tsx`:

```tsx
"use client";

import { useActionState } from "react";
import { sendMagicLink } from "./actions";

export default function LoginPage() {
  const [state, action, pending] = useActionState(sendMagicLink, { message: "" });
  return (
    <main className="flex min-h-screen items-center justify-center bg-bg">
      <form action={action} className="w-80 rounded-lg border border-border bg-surface p-6">
        <h1 className="mb-1 text-lg font-bold text-text">Rehoboam</h1>
        <p className="mb-5 text-sm text-muted">Sign in with a link sent to your inbox.</p>
        <input
          type="email"
          name="email"
          required
          autoComplete="email"
          placeholder="you@example.com"
          className="mb-3 h-10 w-full rounded-md border border-border-strong bg-bg px-3 text-sm text-text outline-none focus:border-accent"
        />
        <button
          type="submit"
          disabled={pending}
          className="h-10 w-full rounded-md bg-accent text-sm font-semibold text-[#14110a] disabled:opacity-60"
        >
          {pending ? "Sending…" : "Send the link"}
        </button>
        {state.message ? <p className="mt-3 text-sm text-muted">{state.message}</p> : null}
      </form>
    </main>
  );
}
```

`web/src/app/auth/callback/route.ts`:

```typescript
import { NextResponse, type NextRequest } from "next/server";
import { createServerClient } from "@/lib/supabase";

export async function GET(request: NextRequest) {
  const code = request.nextUrl.searchParams.get("code");
  const next = request.nextUrl.searchParams.get("next") ?? "/";
  if (code) {
    const supabase = await createServerClient();
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (!error) return NextResponse.redirect(new URL(next, request.nextUrl.origin));
  }
  return NextResponse.redirect(new URL("/login?error=link", request.nextUrl.origin));
}
```

- [ ] **Step 5: Write the failing test for the public-route list**

`web/src/lib/auth.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { isPublicPath } from "./auth-paths";

describe("isPublicPath", () => {
  it("lets the login page and the auth callback through", () => {
    expect(isPublicPath("/login")).toBe(true);
    expect(isPublicPath("/auth/callback")).toBe(true);
    expect(isPublicPath("/auth/callback?code=abc")).toBe(true);
  });

  it("gates every data page", () => {
    for (const path of ["/", "/squad", "/market", "/health", "/loginx", "/auth"]) {
      expect(isPublicPath(path)).toBe(false);
    }
  });
});
```

Run it, watch it fail, then extract the list into `web/src/lib/auth-paths.ts`:

```typescript
const PUBLIC = ["/login", "/auth/callback"];

/** Shared by the middleware and its test; `path` may carry a query string. */
export function isPublicPath(path: string): boolean {
  const clean = path.split("?")[0];
  return PUBLIC.some((p) => clean === p || clean.startsWith(p + "/"));
}
```

and have `middleware.ts` import `isPublicPath` instead of its own inline list. Note `/loginx` must be false and `/auth` alone must be false — that is what the `startsWith(p + "/")` shape buys.

- [ ] **Step 6: Run the tests**

Run: `cd web && npx vitest run && npm run typecheck`
Expected: PASS (6 tests now).

- [ ] **Step 7: Commit**

```bash
git add web/src
git commit -m "feat(web): magic-link sign-in, gated in middleware and again per page"
```

______________________________________________________________________

### Task 4: The shell, the formatters, and the one table component

**Files:**

- Create: `web/src/lib/format.ts`, `web/src/lib/format.test.ts`
- Create: `web/src/lib/integrity.ts`, `web/src/lib/integrity.test.ts`
- Create: `web/src/components/Sidebar.tsx`, `web/src/components/StatusHeader.tsx`, `web/src/components/DataTable.tsx`, `web/src/components/Pill.tsx`
- Create: `web/src/app/layout.tsx`, `web/src/app/error.tsx`
- Create: `web/src/lib/queries.ts` (with the two shell queries only; Tasks 5–8 append to it)

**Interfaces:**

- Consumes: `sql` from Task 2, `requireSession` from Task 3.

- Produces:

  - `money(n: number | null): string` — `65,089,670`, `—` for null. Never abbreviated.
  - `signedPct(n: number | null): { text: string; tone: Tone }` — `+2.00%` / `−0.77%` with `−` U+2212, tone `positive` | `negative` | `neutral`.
  - `signed(n: number | null, digits?: number): { text: string; tone: Tone }`
  - `num(n: number | null, digits?: number): string`
  - `countdown(epochSeconds: number | null, now?: number): string` — `24.5 h`, `48 min`, `expired`, `—`.
  - `ago(epochSeconds: number | null, now?: number): string` — `08:01 UTC`, plus `3 h ago`.
  - `POSITION = { Goalkeeper: {short: "GK", color: "gk"}, … }`
  - `integritySentence(rule: string, detail: string): string` from `integrity.ts`.
  - `<DataTable columns={…} rows={…} sort={…} dir={…} basePath={…} query={…} />` — the sortable table every page uses.
  - `<Pill tone="accent" | "muted" | "plain" | "gk" | "def" | "mid" | "fw">`.
  - `<Sidebar facts={ShellFacts} current={string} />` — the four entries and the kickoff card.
  - `<StatusHeader title={string} subtitle?={string} right?={React.ReactNode} />` — Task 7 relies on `subtitle`, Task 6 on `right`.

- [ ] **Step 1: Write the failing formatter tests**

`web/src/lib/format.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { ago, countdown, money, num, signed, signedPct } from "./format";

describe("money", () => {
  it("writes the exact figure with separators", () => {
    expect(money(65089670)).toBe("65,089,670");
    expect(money(500000)).toBe("500,000");
    expect(money(0)).toBe("0");
  });
  it("never abbreviates", () => {
    expect(money(65089670)).not.toMatch(/M|m|k/);
  });
  it("shows an em dash for nothing", () => {
    expect(money(null)).toBe("—");
  });
});

describe("signedPct", () => {
  it("marks a rise positive and a fall negative", () => {
    expect(signedPct(2)).toEqual({ text: "+2.00%", tone: "positive" });
    expect(signedPct(-0.77)).toEqual({ text: "−0.77%", tone: "negative" });
  });
  it("treats exactly zero as neutral", () => {
    expect(signedPct(0)).toEqual({ text: "0.00%", tone: "neutral" });
  });
  it("uses a real minus sign, not a hyphen", () => {
    expect(signedPct(-1).text.startsWith("−")).toBe(true);
  });
  it("shows an em dash for nothing", () => {
    expect(signedPct(null)).toEqual({ text: "—", tone: "neutral" });
  });
});

describe("signed", () => {
  it("rounds to the digits asked for", () => {
    expect(signed(-109.04, 1)).toEqual({ text: "−109.0", tone: "negative" });
    expect(signed(67.44, 1)).toEqual({ text: "+67.4", tone: "positive" });
  });
});

describe("num", () => {
  it("formats plain numbers and blanks null", () => {
    expect(num(202, 1)).toBe("202.0");
    expect(num(null)).toBe("—");
  });
});

describe("countdown", () => {
  const now = 1_000_000;
  it("counts hours down to one decimal", () => {
    expect(countdown(now + 88200, now)).toBe("24.5 h");
  });
  it("switches to minutes under an hour", () => {
    expect(countdown(now + 2880, now)).toBe("48 min");
  });
  it("says expired in the past and blanks a manager listing", () => {
    expect(countdown(now - 1, now)).toBe("expired");
    expect(countdown(null, now)).toBe("—");
  });
});

describe("ago", () => {
  it("names the UTC clock time and the distance", () => {
    const t = Date.UTC(2026, 8, 16, 8, 1, 13) / 1000;
    expect(ago(t, t + 3 * 3600)).toBe("08:01 UTC · 3 h ago");
  });
});
```

- [ ] **Step 2: Write the failing integrity test**

`web/src/lib/integrity.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { RULES, integritySentence } from "./integrity";

describe("integritySentence", () => {
  it("turns a rule code into something a person reads", () => {
    expect(integritySentence("I4", "2 owned player(s) without a cost basis")).toBe(
      "Two squad players have no purchase price, so profit and loss cannot be judged for them.",
    );
    expect(integritySentence("I2", "only 10 fieldable after the emergency step")).toBe(
      "The squad cannot field a legal eleven.",
    );
  });

  it("falls back to the raw detail for a rule it does not know", () => {
    expect(integritySentence("I9", "something new")).toBe("I9: something new");
  });

  it("covers every rule the bot can raise", () => {
    expect(Object.keys(RULES).sort()).toEqual(["I1", "I2", "I3", "I4", "I5", "I6", "I7"]);
  });
});
```

The sentence for I4 counts in words only up to the count in the detail — read `rehoboam/services/integrity.py` for each rule's meaning and write one sentence per rule that says what it means for Marco, not what the code checked. The last test is the one that matters: it fails the day the bot grows an I8 and nobody told the site.

- [ ] **Step 3: Run both to see them fail**

Run: `cd web && npx vitest run`
Expected: FAIL — `Failed to resolve import "./format"` and `"./integrity"`.

- [ ] **Step 4: Write `format.ts`**

```typescript
export type Tone = "positive" | "negative" | "neutral";

const DASH = "—";
const MINUS = "−";

const groups = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

/** Exact euros with thousands separators. Never abbreviated — a standing rule. */
export function money(n: number | null | undefined): string {
  if (n === null || n === undefined) return DASH;
  return groups.format(n);
}

export function num(n: number | null | undefined, digits = 0): string {
  if (n === null || n === undefined) return DASH;
  return n.toFixed(digits);
}

function tone(n: number): Tone {
  return n > 0 ? "positive" : n < 0 ? "negative" : "neutral";
}

function withSign(n: number, body: string): string {
  if (n > 0) return `+${body}`;
  if (n < 0) return `${MINUS}${body}`;
  return body;
}

export function signed(n: number | null | undefined, digits = 1): { text: string; tone: Tone } {
  if (n === null || n === undefined) return { text: DASH, tone: "neutral" };
  return { text: withSign(n, Math.abs(n).toFixed(digits)), tone: tone(n) };
}

export function signedPct(n: number | null | undefined): { text: string; tone: Tone } {
  if (n === null || n === undefined) return { text: DASH, tone: "neutral" };
  return { text: withSign(n, `${Math.abs(n).toFixed(2)}%`), tone: tone(n) };
}

/** Time left on a Kickbase listing. A manager's listing has no expiry. */
export function countdown(epoch: number | null | undefined, now = Date.now() / 1000): string {
  if (epoch === null || epoch === undefined) return DASH;
  const left = epoch - now;
  if (left <= 0) return "expired";
  if (left < 3600) return `${Math.round(left / 60)} min`;
  return `${(left / 3600).toFixed(1)} h`;
}

/** "08:01 UTC · 3 h ago" — the store's clock is UTC, so the page's is too. */
export function ago(epoch: number | null | undefined, now = Date.now() / 1000): string {
  if (epoch === null || epoch === undefined) return DASH;
  const d = new Date(epoch * 1000);
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  const past = Math.max(0, now - epoch);
  const distance =
    past < 3600 ? `${Math.round(past / 60)} min ago` : `${Math.round(past / 3600)} h ago`;
  return `${hh}:${mm} UTC · ${distance}`;
}

export const POSITION: Record<string, { short: string; token: string }> = {
  Goalkeeper: { short: "GK", token: "gk" },
  Defender: { short: "DEF", token: "def" },
  Midfielder: { short: "MID", token: "mid" },
  Forward: { short: "FW", token: "fw" },
};
```

- [ ] **Step 5: Write `integrity.ts`**

```typescript
/**
 * One sentence per integrity rule the bot can raise. The last test in
 * integrity.test.ts asserts this map matches `rehoboam/services/integrity.py`,
 * so a new rule in the bot fails the site's suite rather than showing a code.
 */
export const RULES: Record<string, (detail: string) => string> = {
  I1: () => "The session could not tell when the next match kicks off.",
  I2: () => "The squad cannot field a legal eleven.",
  I3: () => "The session refused to trade because a fact it needs was missing.",
  I4: (detail) => {
    const n = Number(detail.match(/^\d+/)?.[0] ?? 0);
    const word = ["No", "One", "Two", "Three", "Four", "Five"][n] ?? String(n);
    const plural = n === 1 ? "player has" : "players have";
    return `${word} squad ${plural} no purchase price, so profit and loss cannot be judged for them.`;
  },
  I5: () => "The session wrote no predictions, so calibration has nothing from it.",
  I6: () => "The lineup was not set although kickoff is close.",
  I7: () => "The ingestion app has not finished a run recently.",
};

export function integritySentence(rule: string, detail: string): string {
  const write = RULES[rule];
  return write ? write(detail) : `${rule}: ${detail}`;
}
```

Read `rehoboam/services/integrity.py` and correct any sentence whose meaning does not match the rule — the wording above is the plan's best reading, the Python is the truth.

- [ ] **Step 6: Write the shell**

`web/src/components/Pill.tsx`:

```tsx
const TONES: Record<string, string> = {
  accent: "bg-accent text-[#14110a]",
  muted: "text-muted",
  plain: "text-text-dim",
  gk: "bg-gk/15 text-gk",
  def: "bg-def/15 text-def",
  mid: "bg-mid/15 text-mid",
  fw: "bg-fw/15 text-fw",
};

export function Pill({ tone = "plain", children }: { tone?: string; children: React.ReactNode }) {
  const style = TONES[tone] ?? TONES.plain;
  const box = tone === "muted" || tone === "plain" ? "" : "px-2 rounded-[4px]";
  return (
    <span className={`inline-flex h-[22px] items-center text-xs font-semibold ${box} ${style}`}>
      {children}
    </span>
  );
}
```

`web/src/components/DataTable.tsx` — one server component, sorting by link (`?sort=col&dir=desc`) so no client JavaScript is needed:

```tsx
import Link from "next/link";

export type Column<T> = {
  key: string;
  label: string;
  align?: "left" | "right";
  sortable?: boolean;
  cell: (row: T) => React.ReactNode;
};

export function DataTable<T>({
  columns,
  rows,
  sort,
  dir,
  basePath,
  query,
}: {
  columns: Column<T>[];
  rows: T[];
  sort: string;
  dir: "asc" | "desc";
  basePath: string;
  query?: Record<string, string | undefined>;
}) {
  function href(key: string) {
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(query ?? {})) if (v) params.set(k, v);
    params.set("sort", key);
    params.set("dir", key === sort && dir === "desc" ? "asc" : "desc");
    return `${basePath}?${params.toString()}`;
  }

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface">
      <table className="w-full border-collapse">
        <thead>
          <tr>
            {columns.map((c) => (
              <th
                key={c.key}
                className={`h-10 whitespace-nowrap border-b border-border-strong px-3 text-[11px] font-semibold uppercase tracking-[0.08em] ${
                  c.align === "left" ? "text-left" : "text-right"
                } ${c.key === sort ? "text-accent" : "text-muted"}`}
              >
                {c.sortable === false ? (
                  c.label
                ) : (
                  <Link href={href(c.key)} className="hover:text-text">
                    {c.label}
                    {c.key === sort ? (dir === "desc" ? " ▾" : " ▴") : ""}
                  </Link>
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {columns.map((c) => (
                <td
                  key={c.key}
                  className={`tnum h-11 whitespace-nowrap border-b border-border px-3 ${
                    c.align === "left" ? "text-left" : "text-right"
                  }`}
                >
                  {c.cell(row)}
                </td>
              ))}
            </tr>
          ))}
          {rows.length === 0 ? (
            <tr>
              <td colSpan={columns.length} className="h-24 text-center text-sm text-muted">
                Nothing here yet.
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>
    </div>
  );
}
```

`web/src/components/Sidebar.tsx` — the four entries and the kickoff card; mark the current route with `usePathname` in a small client component, or compare against a `current` prop passed from the layout (prefer the prop: it keeps the shell server-rendered).

`web/src/components/StatusHeader.tsx` — the title, the count line ("588 players · store updated 08:01 UTC · next session 20:00 UTC") built from `ago()` and the shell query below, and an optional right slot.

`web/src/app/layout.tsx`:

```tsx
import type { Metadata } from "next";
import { IBM_Plex_Sans } from "next/font/google";
import "./globals.css";
import { Sidebar } from "@/components/Sidebar";
import { shellFacts } from "@/lib/queries";

const plex = IBM_Plex_Sans({ subsets: ["latin"], weight: ["400", "500", "600", "700"] });

export const metadata: Metadata = { title: "Rehoboam", description: "The bot's own dashboard" };

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const facts = await shellFacts();
  return (
    <html lang="en">
      <body className={`${plex.className} flex min-h-screen bg-bg text-text`}>
        <Sidebar facts={facts} />
        <main className="flex min-w-0 flex-1 flex-col">{children}</main>
      </body>
    </html>
  );
}
```

The layout renders on the login page too, where there is no session — `shellFacts()` must therefore return nulls rather than throw when the query fails or no session exists. Guard it: catch, log, return `{ lastSession: null, nextKickoff: null, ... }`.

- [ ] **Step 7: Write the shell query**

`web/src/lib/queries.ts` (Tasks 5–8 append their own functions here):

```typescript
import "server-only";
import { sql } from "./db";

export type ShellFacts = {
  players: number | null;
  lastSessionAt: number | null;
  nextKickoff: number | null;
  formation: string | null;
  budget: number | null;
  lineupResult: string | null;
  stale: boolean;
};

/**
 * The sidebar and header need these on every route, including the login page
 * where there is no session — so this never throws; a failure renders dashes.
 */
export async function shellFacts(): Promise<ShellFacts> {
  const empty: ShellFacts = {
    players: null, lastSessionAt: null, nextKickoff: null,
    formation: null, budget: null, lineupResult: null, stale: true,
  };
  try {
    const [session] = await sql<
      { started_at: number; next_kickoff: number | null; legal_formation: string | null;
        budget: number | null; lineup_result: string | null }[]
    >`
      select started_at, next_kickoff, legal_formation, budget, lineup_result
      from rehoboam.web_session_summary
      where app = 'function' and dry_run = 0
      order by started_at desc
      limit 1
    `;
    const [{ count }] = await sql<{ count: number }[]>`
      select count(*)::int as count from rehoboam.web_players
    `;
    if (!session) return { ...empty, players: count };
    return {
      players: count,
      lastSessionAt: session.started_at,
      nextKickoff: session.next_kickoff,
      formation: session.legal_formation,
      budget: session.budget,
      lineupResult: session.lineup_result,
      stale: Date.now() / 1000 - session.started_at > 14 * 3600,
    };
  } catch (error) {
    console.error("shellFacts failed", error);
    return empty;
  }
}
```

- [ ] **Step 8: Write the failure boundary the spec requires**

A page whose query throws must show a banner, never a stack trace. `web/src/app/error.tsx`:

```tsx
"use client";

export default function Error({ error, reset }: { error: Error; reset: () => void }) {
  return (
    <div className="m-6 rounded-lg border border-border bg-surface p-6">
      <h2 className="mb-1 text-base font-semibold text-text">The store is not answering</h2>
      <p className="mb-4 text-sm text-muted">
        The page could not read the database. Nothing is broken in the bot: this view is
        read-only, and the numbers reappear as soon as the store answers again.
      </p>
      <button
        onClick={reset}
        className="h-9 rounded-md border border-border-strong px-3 text-sm text-text-dim"
      >
        Try again
      </button>
    </div>
  );
}
```

Next.js renders this for any error thrown by a page in the same segment. The digest is logged server-side; the page shows no message from the exception, so a connection string can never reach the browser through an error.

- [ ] **Step 9: Run everything**

Run: `cd web && npx vitest run && npm run typecheck && npm run lint`
Expected: all tests PASS; typecheck and lint clean. `npm run build` still needs a page — Task 5 adds it.

- [ ] **Step 10: Commit**

```bash
git add web/src
git commit -m "feat(web): the shell, the formatters and the table every page uses"
```

______________________________________________________________________

### Task 5: The Players page

**Files:**

- Create: `web/src/app/page.tsx`, `web/src/components/Filters.tsx`
- Create: `web/src/lib/sort.ts`, `web/src/lib/sort.test.ts`
- Modify: `web/src/lib/queries.ts` (append `players()`, `selfName()`, `clubs()`)

**Interfaces:**

- Consumes: `sql`, `requireSession`, `DataTable`, `Pill`, `money`/`num`/`signedPct`/`POSITION`.
- Produces: `players(opts)`, `selfName()`, `clubs()` in `queries.ts`; `sortKey(raw, allowed, fallback)` and `sortDir(raw)` in `sort.ts`, reused by Tasks 6-8.

This is the page the design canvas shows. Columns, in Base XI's order then ours: Player (name over club), Pos, Market value, 24h, 7d, Pts, Ø, Median, Pts / M€, Apps, Starts, Owner, EP, P(start), Fair.

- [ ] **Step 1: Write the failing sort-guard test**

`web/src/lib/sort.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { sortDir, sortKey } from "./sort";

const ALLOWED = ["name", "market_value", "predicted_ep"];

describe("sortKey", () => {
  it("passes an allowed column through", () => {
    expect(sortKey("market_value", ALLOWED, "predicted_ep")).toBe("market_value");
  });
  it("falls back for anything else - the value reaches SQL as an identifier", () => {
    expect(sortKey("drop table", ALLOWED, "predicted_ep")).toBe("predicted_ep");
    expect(sortKey(undefined, ALLOWED, "predicted_ep")).toBe("predicted_ep");
    expect(sortKey("market_value; --", ALLOWED, "predicted_ep")).toBe("predicted_ep");
  });
});

describe("sortDir", () => {
  it("accepts only asc and desc", () => {
    expect(sortDir("asc")).toBe("asc");
    expect(sortDir("desc")).toBe("desc");
    expect(sortDir("sideways")).toBe("desc");
    expect(sortDir(undefined)).toBe("desc");
  });
});
```

- [ ] **Step 2: Run it to see it fail**

Run: `cd web && npx vitest run src/lib/sort.test.ts`
Expected: FAIL - `Failed to resolve import "./sort"`.

- [ ] **Step 3: Write `web/src/lib/sort.ts`**

```typescript
/**
 * A sort column is an SQL identifier, not a value, so it can never be a bound
 * parameter. This whitelist is the only thing standing between a query string
 * and the database - every page passes its own allowed list.
 */
export function sortKey(raw: string | undefined, allowed: string[], fallback: string): string {
  return raw && allowed.includes(raw) ? raw : fallback;
}

export function sortDir(raw: string | undefined): "asc" | "desc" {
  return raw === "asc" ? "asc" : "desc";
}
```

- [ ] **Step 4: Append the queries**

In `web/src/lib/queries.ts`:

```typescript
export type PlayerRow = {
  player_id: string; name: string; team: string | null; team_id: string | null;
  position: string; market_value: number | null;
  trend_24h_pct: number | null; trend_7d_pct: number | null;
  points: number | null; avg_points: number | null; median_points: number | null;
  points_per_million: number | null; appearances: number | null; starts: number | null;
  owner: string; predicted_ep: number | null; p_start: number | null;
  fair_value_gap: number | null; listed: boolean;
};

export const PLAYER_SORTS = [
  "name", "position", "market_value", "trend_24h_pct", "trend_7d_pct", "points",
  "avg_points", "median_points", "points_per_million", "appearances", "starts",
  "owner", "predicted_ep", "p_start", "fair_value_gap",
];

/** Our own manager name, for the "my squad" filter and the amber owner pill. */
export async function selfName(): Promise<string | null> {
  const [row] = await sql<{ manager: string }[]>`
    select manager from rehoboam.web_ownership where is_self limit 1
  `;
  return row?.manager ?? null;
}

export async function clubs(): Promise<string[]> {
  const rows = await sql<{ team: string }[]>`
    select distinct team from rehoboam.web_players where team is not null order by team
  `;
  return rows.map((r) => r.team);
}

export async function players(opts: {
  sort: string; dir: "asc" | "desc";
  position?: string; owner?: "mine" | "free";
  club?: string; q?: string; limit?: number;
}): Promise<PlayerRow[]> {
  // "mine" needs our manager name; when there is none, the filter matches nothing.
  const mine = opts.owner === "mine" ? ((await selfName()) ?? "") : null;
  const free = opts.owner === "free";
  const like = opts.q ? `%${opts.q}%` : null;
  return sql<PlayerRow[]>`
    select * from rehoboam.web_players
    where (${opts.position ?? null}::text is null or position = ${opts.position ?? null})
      and (${opts.club ?? null}::text is null or team = ${opts.club ?? null})
      and (${like}::text is null or name ilike ${like} or team ilike ${like})
      and (${mine}::text is null or owner = ${mine})
      and (${free} = false or owner = 'Kickbase')
    order by ${sql.unsafe(opts.sort)} ${opts.dir === "asc" ? sql`asc` : sql`desc`} nulls last,
             player_id asc
    limit ${opts.limit ?? 50}
  `;
}
```

`sql.unsafe(opts.sort)` is safe **only** because `opts.sort` came through `sortKey()` against `PLAYER_SORTS`. Never pass a raw search param to it. Every other value is a bound parameter.

- [ ] **Step 5: Write the page**

`web/src/app/page.tsx`:

```tsx
import { requireSession } from "@/lib/auth";
import { clubs, players, selfName, PLAYER_SORTS, type PlayerRow } from "@/lib/queries";
import { sortDir, sortKey } from "@/lib/sort";
import { DataTable, type Column } from "@/components/DataTable";
import { Filters } from "@/components/Filters";
import { Pill } from "@/components/Pill";
import { StatusHeader } from "@/components/StatusHeader";
import { money, num, signed, signedPct, POSITION, type Tone } from "@/lib/format";

export const revalidate = 300;

const TONE: Record<Tone, string> = {
  positive: "text-positive",
  negative: "text-negative",
  neutral: "text-muted",
};

function Trend({ pct, value }: { pct?: number | null; value?: number | null }) {
  const out = pct !== undefined ? signedPct(pct) : signed(value ?? null, 1);
  return <span className={TONE[out.tone]}>{out.text}</span>;
}

export default async function PlayersPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  await requireSession();
  const params = await searchParams;
  const sort = sortKey(params.sort, PLAYER_SORTS, "predicted_ep");
  const dir = sortDir(params.dir);
  const owner = params.owner === "mine" || params.owner === "free" ? params.owner : undefined;

  const [rows, clubList, me] = await Promise.all([
    players({ sort, dir, position: params.position, owner, club: params.club, q: params.q }),
    clubs(),
    selfName(),
  ]);

  const columns: Column<PlayerRow>[] = [
    {
      key: "name", label: "Player", align: "left",
      cell: (p) => (
        <div className="flex flex-col gap-0.5">
          <span className="text-sm font-semibold text-text">{p.name}</span>
          <span className="text-xs text-muted">{p.team ?? "-"}</span>
        </div>
      ),
    },
    {
      key: "position", label: "Pos", align: "left",
      cell: (p) => {
        const pos = POSITION[p.position] ?? { short: p.position, token: "plain" };
        return <Pill tone={pos.token}>{pos.short}</Pill>;
      },
    },
    { key: "market_value", label: "Market value", cell: (p) => money(p.market_value) },
    { key: "trend_24h_pct", label: "24h", cell: (p) => <Trend pct={p.trend_24h_pct} /> },
    { key: "trend_7d_pct", label: "7d", cell: (p) => <Trend pct={p.trend_7d_pct} /> },
    { key: "points", label: "Pts", cell: (p) => <b className="text-text">{num(p.points)}</b> },
    { key: "avg_points", label: "Avg", cell: (p) => num(p.avg_points, 1) },
    { key: "median_points", label: "Median", cell: (p) => num(p.median_points, 1) },
    { key: "points_per_million", label: "Pts / M", cell: (p) => num(p.points_per_million, 2) },
    { key: "appearances", label: "Apps", cell: (p) => num(p.appearances) },
    { key: "starts", label: "Starts", cell: (p) => num(p.starts) },
    {
      key: "owner", label: "Owner", align: "left",
      cell: (p) =>
        p.owner === me ? (
          <Pill tone="accent">{p.owner}</Pill>
        ) : p.owner === "Kickbase" ? (
          <span className="text-sm text-muted">free agent</span>
        ) : (
          <span className="text-sm text-text-dim">
            {p.owner}
            {p.listed ? <span className="text-muted"> - listed</span> : null}
          </span>
        ),
    },
    {
      key: "predicted_ep", label: "EP",
      cell: (p) => <b className="text-[15px] text-text">{num(p.predicted_ep, 0)}</b>,
    },
    {
      key: "p_start", label: "P(start)",
      cell: (p) => (p.p_start === null ? "-" : `${Math.round(p.p_start * 100)}%`),
    },
    { key: "fair_value_gap", label: "Fair", cell: (p) => <Trend value={p.fair_value_gap} /> },
  ];

  return (
    <>
      <StatusHeader title="Players" />
      <Filters clubs={clubList} params={params} />
      <div className="px-6 pb-6">
        <DataTable columns={columns} rows={rows} sort={sort} dir={dir} basePath="/" query={params} />
      </div>
    </>
  );
}
```

`web/src/components/Filters.tsx` is a server component too: the position and ownership chips are `<Link>`s that set or clear one search param while keeping the others, and the club select plus the search box sit in a `<form method="get">` with hidden inputs carrying the current sort. No client JavaScript on this page.

- [ ] **Step 6: Run the tests and the build**

Run: `cd web && npx vitest run && npm run typecheck && npm run lint && npm run build`
Expected: tests PASS, build succeeds, `check-secrets: clean`.

- [ ] **Step 7: Look at it against the real store**

`DATABASE_URL=<the bot URL> npm run dev`, sign in, open `/`. Check: 50 rows; Olise first at EP 215; market values carry separators and no `M`; your own name renders as an amber pill; clicking `Market value` re-sorts and the other filters survive in the URL.

- [ ] **Step 8: Commit**

```bash
git add web/src
git commit -m "feat(web): the players page"
```

______________________________________________________________________

### Task 6: Squad & lineup

**Files:**

- Create: `web/src/app/squad/page.tsx`, `web/src/components/Formation.tsx`
- Modify: `web/src/lib/queries.ts` (append `squad()`)
- Modify: `rehoboam/store/migrations/007_web_views.sql` and `tests/store/test_web_views.py` (integrity details, see Step 1)

**Interfaces:**

- Consumes: `web_squad`, `web_session_summary`, `DataTable`, `integritySentence`.

- Produces: `squad(): Promise<SquadRow[]>` and `latestSessionRules(): Promise<{rule, detail}[]>` in `queries.ts`.

- [ ] **Step 1: Carry the integrity detail text in the view**

The page prints a sentence per integrity rule, and a sentence needs the rule's `detail` (I4's count comes from it). `web_session_summary` currently aggregates only the rule codes, and the Global Constraints forbid the site from reading `integrity_failures` directly. So widen the view.

In `rehoboam/store/migrations/007_web_views.sql`, change the integrity sub-select to:

```sql
left join (
    select session_id,
        array_agg(distinct rule order by rule) as rules,
        jsonb_object_agg(rule, detail) as rule_details
    from rehoboam.integrity_failures
    group by session_id
) i on i.session_id = f.session_id
```

and add to that view's select list:

```sql
    coalesce(i.rule_details, '{}'::jsonb) as integrity_details,
```

Then extend the existing `test_web_session_summary_flattens_an_ingest_run_and_its_rules` in `tests/store/test_web_views.py`:

```python
    assert session["integrity_details"] == {"I4": "2 owned player(s) without a cost basis"}
    assert ingest["integrity_details"] == {}
```

Run: `.venv/bin/pytest tests/store/test_web_views.py -q` - RED first (the column does not exist), then GREEN after the migration change. Migration 007 has not shipped to production yet, so editing it in place is correct; do not add an 008.

- [ ] **Step 2: Append the queries**

```typescript
export type SquadRow = {
  session_id: string; legal_formation: string | null; budget: number | null;
  sellable_value: number | null; next_kickoff: number | null; session_started_at: number;
  cost_basis_missing: number | null;
  player_id: string; name: string | null; team: string | null; position: string;
  market_value: number | null; points: number | null; avg_points: number | null;
  owner: string | null; predicted_ep: number | null; live_ep: number | null;
  in_best_11: boolean; p_start: number | null;
  cost_basis: number | null; gain_loss: number | null;
};

export async function squad(): Promise<SquadRow[]> {
  return sql<SquadRow[]>`
    select * from rehoboam.web_squad
    order by in_best_11 desc, predicted_ep desc nulls last, player_id asc
  `;
}

/** The integrity rules the newest real session raised, as rule + detail pairs. */
export async function latestSessionRules(): Promise<{ rule: string; detail: string }[]> {
  const [row] = await sql<{ integrity_details: Record<string, string> }[]>`
    select integrity_details from rehoboam.web_session_summary
    where app = 'function' and dry_run = 0
    order by started_at desc
    limit 1
  `;
  return Object.entries(row?.integrity_details ?? {}).map(([rule, detail]) => ({ rule, detail }));
}
```

- [ ] **Step 3: Write `web/src/components/Formation.tsx`**

```tsx
import { num } from "@/lib/format";
import type { SquadRow } from "@/lib/queries";

/**
 * The eleven the session actually chose, laid out by its formation string
 * ("4-3-3" = 4 defenders, 3 midfielders, 3 forwards, keeper on top). No pitch
 * graphic: rows of cards read better at a glance and survive any formation.
 */
export function Formation({ formation, eleven }: { formation: string | null; eleven: SquadRow[] }) {
  const byPosition = (name: string) => eleven.filter((p) => p.position === name);
  const counts = (formation ?? "").split("-").map((n) => Number(n));
  const rows: { label: string; players: SquadRow[] }[] = [
    { label: "GK", players: byPosition("Goalkeeper").slice(0, 1) },
    { label: "DEF", players: byPosition("Defender").slice(0, counts[0] || 99) },
    { label: "MID", players: byPosition("Midfielder").slice(0, counts[1] || 99) },
    { label: "FW", players: byPosition("Forward").slice(0, counts[2] || 99) },
  ];

  return (
    <div className="flex flex-col gap-3">
      {rows.map((row) => (
        <div key={row.label} className="flex flex-wrap justify-center gap-3">
          {row.players.map((p) => (
            <div
              key={p.player_id}
              className="flex w-40 flex-col gap-1 rounded-lg border border-border bg-surface p-3"
            >
              <span className="text-sm font-semibold text-text">{p.name}</span>
              <span className="text-xs text-muted">{p.team ?? "-"}</span>
              <span className="tnum text-lg font-bold text-text">{num(p.predicted_ep, 0)}</span>
              <span className="tnum text-xs text-muted">
                {p.p_start === null ? "-" : `${Math.round(p.p_start * 100)}% to start`}
              </span>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 4: Write the page**

`web/src/app/squad/page.tsx` renders, in order:

1. `StatusHeader` with title `Squad & lineup` and a right slot: `Budget {money(budget)}` and `Sellable {money(sellable_value)}` from the first row.
1. `<Formation formation={...} eleven={rows.filter(r => r.in_best_11)} />`.
1. The integrity block: `latestSessionRules()` mapped through `integritySentence(rule, detail)`, one line each with a leading bullet, inside `rounded-lg border border-border bg-surface p-4`. Render nothing when the list is empty.
1. A `DataTable` of the whole squad with these columns: Player (name over club, align left), Pos (`Pill`), EP (`num(predicted_ep, 0)`, bold), P(start) (`Math.round(p_start * 100)%`), Market value (`money`), Cost basis (`money`), Gain/loss (`signed(gain_loss, 0)` coloured by tone), Pts (`num`), Avg (`num(avg_points, 1)`), In XI (`in_best_11 ? "yes" : "-"`). Bench rows get `opacity-60` - pass a `rowClass` prop through `DataTable` (add it: `rowClass?: (row: T) => string`).

Empty case: when `squad()` returns nothing, render "No completed session has recorded a squad yet." in place of the formation and the table.

`cost_basis` and `gain_loss` render `-` when null, never `0` - a zero would read as "bought for nothing", and two of our players genuinely have no recorded purchase.

- [ ] **Step 5: Run everything**

Run: `cd web && npx vitest run && npm run typecheck && npm run build` and `.venv/bin/pytest tests/store/test_web_views.py -q`
Expected: all PASS.

- [ ] **Step 6: Check it against the bot**

Open `/squad` against the real store and compare with `rehoboam status`: eleven players, formation `4-3-3`, budget `1,725,739`, exactly two players showing `-` for cost basis, and one integrity line about purchase prices.

- [ ] **Step 7: Commit**

```bash
git add web/src rehoboam/store/migrations/007_web_views.sql tests/store/test_web_views.py
git commit -m "feat(web): squad and lineup, with the session's integrity flags in words"
```

______________________________________________________________________

### Task 7: Market

**Files:**

- Create: `web/src/app/market/page.tsx`
- Modify: `web/src/lib/queries.ts` (append `market()` and `managers()`)

**Interfaces:**

- Consumes: `web_market`, `web_ownership`, `DataTable`, `countdown`, `money`, `signedPct`.
- Produces: `market(opts)` and `managers()` in `queries.ts`.

Two tables on one page: the newest listing snapshot on top, the league's ownership underneath.

- [ ] **Step 1: Append the queries**

```typescript
export type MarketRow = {
  snapshot_at: number; player_id: string; name: string | null; team: string | null;
  position: string | null; ask: number; market_value: number | null; mv_trend: number | null;
  offer_count: number | null; our_bid: number | null; listed_at: number | null;
  expires_at: number | null; status: number | null; lineup_probability: number | null;
  seller: string; is_ours: boolean; predicted_ep: number | null; p_start: number | null;
  fair_value_gap: number | null; points: number | null; avg_points: number | null;
};

export const MARKET_SORTS = [
  "name", "position", "ask", "market_value", "seller", "expires_at",
  "offer_count", "predicted_ep", "p_start", "fair_value_gap",
];

export async function market(opts: {
  sort: string; dir: "asc" | "desc"; expiringHours?: number;
}): Promise<MarketRow[]> {
  const cutoff = opts.expiringHours ? Date.now() / 1000 + opts.expiringHours * 3600 : null;
  return sql<MarketRow[]>`
    select * from rehoboam.web_market
    where (${cutoff}::double precision is null
           or (expires_at is not null and expires_at <= ${cutoff}))
    order by ${sql.unsafe(opts.sort)} ${opts.dir === "asc" ? sql`asc` : sql`desc`} nulls last,
             player_id asc
  `;
}

export type ManagerRow = {
  manager_id: string; manager: string; is_self: boolean;
  squad_size: number; team_value: number | null; top: string[];
};

/** One row per manager, from each manager's own newest squad snapshot. */
export async function managers(): Promise<ManagerRow[]> {
  return sql<ManagerRow[]>`
    with ranked as (
      select manager_id, manager, is_self, player_name, market_value, predicted_ep,
             row_number() over (
               partition by manager_id order by predicted_ep desc nulls last, player_id
             ) as rank
      from rehoboam.web_ownership
    )
    select manager_id, manager, is_self,
        count(*)::int as squad_size,
        sum(market_value)::bigint as team_value,
        array_remove(array_agg(case when rank <= 3 then player_name end
                               order by rank), null) as top
    from ranked
    group by manager_id, manager, is_self
    order by is_self desc, team_value desc nulls last
  `;
}
```

- [ ] **Step 2: Write the page**

`web/src/app/market/page.tsx`:

```tsx
import { requireSession } from "@/lib/auth";
import { managers, market, MARKET_SORTS, type MarketRow } from "@/lib/queries";
import { sortDir, sortKey } from "@/lib/sort";
import { DataTable, type Column } from "@/components/DataTable";
import { Pill } from "@/components/Pill";
import { StatusHeader } from "@/components/StatusHeader";
import { ago, countdown, money, num, signed, signedPct, POSITION, type Tone } from "@/lib/format";
import Link from "next/link";

export const revalidate = 300;

const TONE: Record<Tone, string> = {
  positive: "text-positive",
  negative: "text-negative",
  neutral: "text-muted",
};

export default async function MarketPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  await requireSession();
  const params = await searchParams;
  const sort = sortKey(params.sort, MARKET_SORTS, "predicted_ep");
  const dir = sortDir(params.dir);
  const expiring = params.expiring === "6" ? 6 : undefined;

  const [rows, managerRows] = await Promise.all([
    market({ sort, dir, expiringHours: expiring }),
    managers(),
  ]);

  const columns: Column<MarketRow>[] = [
    {
      key: "name", label: "Player", align: "left",
      cell: (r) => (
        <div className="flex flex-col gap-0.5">
          <span className="text-sm font-semibold text-text">{r.name ?? r.player_id}</span>
          <span className="text-xs text-muted">{r.team ?? "-"}</span>
        </div>
      ),
    },
    {
      key: "position", label: "Pos", align: "left",
      cell: (r) => {
        const pos = POSITION[r.position ?? ""] ?? { short: r.position ?? "-", token: "plain" };
        return <Pill tone={pos.token}>{pos.short}</Pill>;
      },
    },
    { key: "ask", label: "Ask", cell: (r) => money(r.ask) },
    { key: "market_value", label: "Market value", cell: (r) => money(r.market_value) },
    {
      key: "over", label: "Ask vs MV", sortable: false,
      cell: (r) => {
        if (!r.market_value) return "-";
        const out = signedPct(((r.ask - r.market_value) / r.market_value) * 100);
        return <span className={TONE[out.tone]}>{out.text}</span>;
      },
    },
    {
      key: "seller", label: "Seller", align: "left",
      cell: (r) =>
        r.is_ours ? <Pill tone="accent">{r.seller}</Pill> : (
          <span className="text-sm text-text-dim">{r.seller}</span>
        ),
    },
    { key: "expires_at", label: "Expires in", cell: (r) => countdown(r.expires_at) },
    { key: "offer_count", label: "Offers", cell: (r) => num(r.offer_count) },
    {
      key: "predicted_ep", label: "EP",
      cell: (r) => <b className="text-[15px] text-text">{num(r.predicted_ep, 0)}</b>,
    },
    {
      key: "p_start", label: "P(start)",
      cell: (r) => (r.p_start === null ? "-" : `${Math.round(r.p_start * 100)}%`),
    },
    {
      key: "fair_value_gap", label: "Fair",
      cell: (r) => {
        const out = signed(r.fair_value_gap, 1);
        return <span className={TONE[out.tone]}>{out.text}</span>;
      },
    },
  ];

  const snapshotAt = rows[0]?.snapshot_at ?? null;

  return (
    <>
      <StatusHeader
        title="Market"
        subtitle={`${rows.length} listings - snapshot ${ago(snapshotAt)}`}
      />
      <div className="flex items-center gap-2 px-6 py-4">
        <Link
          href="/market"
          className={`inline-flex h-8 items-center rounded-md px-3 text-[13px] font-semibold ${
            expiring ? "border border-border-strong text-text-dim" : "bg-text text-bg"
          }`}
        >
          All
        </Link>
        <Link
          href="/market?expiring=6"
          className={`inline-flex h-8 items-center rounded-md px-3 text-[13px] font-semibold ${
            expiring ? "bg-text text-bg" : "border border-border-strong text-text-dim"
          }`}
        >
          Expiring under 6 h
        </Link>
      </div>
      <div className="px-6 pb-6">
        <DataTable
          columns={columns} rows={rows} sort={sort} dir={dir}
          basePath="/market" query={params}
        />
      </div>
      <div className="px-6 pb-8">
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-[0.08em] text-muted">
          Who owns what
        </h2>
        <div className="overflow-hidden rounded-lg border border-border bg-surface">
          <table className="w-full border-collapse">
            <thead>
              <tr className="text-[11px] uppercase tracking-[0.08em] text-muted">
                <th className="h-10 border-b border-border-strong px-3 text-left">Manager</th>
                <th className="h-10 border-b border-border-strong px-3 text-right">Squad</th>
                <th className="h-10 border-b border-border-strong px-3 text-right">Team value</th>
                <th className="h-10 border-b border-border-strong px-3 text-left">
                  Top three by predicted points
                </th>
              </tr>
            </thead>
            <tbody>
              {managerRows.map((m) => (
                <tr key={m.manager_id}>
                  <td className="h-11 border-b border-border px-3 text-left">
                    {m.is_self ? (
                      <Pill tone="accent">{m.manager}</Pill>
                    ) : (
                      <span className="text-sm text-text-dim">{m.manager}</span>
                    )}
                  </td>
                  <td className="tnum h-11 border-b border-border px-3 text-right">
                    {m.squad_size}
                  </td>
                  <td className="tnum h-11 border-b border-border px-3 text-right">
                    {money(m.team_value)}
                  </td>
                  <td className="h-11 border-b border-border px-3 text-left text-sm text-muted">
                    {m.top.join(", ") || "-"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
```

`StatusHeader` gains an optional `subtitle` prop for this page; add it in this task.

- [ ] **Step 3: Run the suite and the build**

Run: `cd web && npx vitest run && npm run typecheck && npm run lint && npm run build`
Expected: PASS.

- [ ] **Step 4: Check it against the store**

Open `/market`: the listing count matches `select count(*) from rehoboam.web_market`; sellers show real manager names with `Kickbase` for unowned listings; expiry counts down only for Kickbase listings (a manager's listing has none, so it shows `-`); the ownership table lists 13 real managers plus the Kickbase CPU account with a squad of zero excluded by the join.

- [ ] **Step 5: Commit**

```bash
git add web/src
git commit -m "feat(web): the market and who owns what"
```

______________________________________________________________________

### Task 8: Calibration & health

**Files:**

- Create: `web/src/app/health/page.tsx`, `web/src/components/BarPair.tsx`
- Modify: `web/src/lib/queries.ts` (append `calibration()` and `sessions()`)

**Interfaces:**

- Consumes: `web_calibration`, `web_session_summary`, `integritySentence`.
- Produces: `calibration()` and `sessions(limit)` in `queries.ts`.

Current production state this page must survive: every live report has `n = 0` and `gate = null`, and only the backfill rows carry metrics. It must say so rather than render blanks.

- [ ] **Step 1: Append the queries**

```typescript
export type CalibrationRow = {
  season: string; day_number: number; backfill: boolean; computed_at: number;
  n: number; n_unpredicted: number; n_stale_rows: number;
  mae: number | null; bias: number | null;
  spearman: number | null; baseline_spearman: number | null; spearman_played: number | null;
  top11_regret: number | null; baseline_top11_regret: number | null;
  squad_regret: number | null; live_spearman: number | null; live_n: number;
  worst: { player_id: string; position: string; predicted: number; actual: number }[];
  gate: Record<string, unknown> | null;
};

export async function calibration(): Promise<CalibrationRow[]> {
  return sql<CalibrationRow[]>`
    select * from rehoboam.web_calibration
    order by day_number asc, backfill asc
  `;
}

export type SessionRow = {
  session_id: string; app: string; mode: string; dry_run: number;
  started_at: number; duration_s: number; errors: number; error_text: string;
  lineup_result: string | null; predictions_written: number | null;
  integrity_rules: string[]; integrity_details: Record<string, string>;
  league_state_squads: number | null;
  requests: number | null; failed: number | null; status_written: number | null;
  universe_size: number | null; stopped_by: string | null;
  league_teams: number | null; league_fixtures: number | null; league_failed: number | null;
};

export async function sessions(limit = 30): Promise<SessionRow[]> {
  return sql<SessionRow[]>`
    select * from rehoboam.web_session_summary
    order by started_at desc
    limit ${limit}
  `;
}
```

- [ ] **Step 2: Write `web/src/components/BarPair.tsx`**

```tsx
/**
 * Ours against the baseline on one scale. Higher is better for Spearman,
 * lower is better for regret - `betterIs` decides which bar is coloured well.
 */
export function BarPair({
  label, ours, baseline, betterIs, digits = 2,
}: {
  label: string;
  ours: number | null;
  baseline: number | null;
  betterIs: "higher" | "lower";
  digits?: number;
}) {
  if (ours === null || baseline === null) {
    return (
      <div className="flex flex-col gap-1">
        <span className="text-xs text-muted">{label}</span>
        <span className="text-sm text-muted">not yet reported</span>
      </div>
    );
  }
  const max = Math.max(Math.abs(ours), Math.abs(baseline), 1e-9);
  const win = betterIs === "higher" ? ours > baseline : ours < baseline;
  const bar = (value: number, tone: string) => (
    <div className="flex items-center gap-2">
      <div className="h-2 flex-1 overflow-hidden rounded-sm bg-border">
        <div className={`h-full ${tone}`} style={{ width: `${(Math.abs(value) / max) * 100}%` }} />
      </div>
      <span className="tnum w-16 text-right text-xs text-text-dim">{value.toFixed(digits)}</span>
    </div>
  );
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-baseline justify-between">
        <span className="text-xs text-muted">{label}</span>
        <span className={`text-xs font-semibold ${win ? "text-positive" : "text-negative"}`}>
          {win ? "beats the baseline" : "loses to the baseline"}
        </span>
      </div>
      {bar(ours, win ? "bg-positive" : "bg-negative")}
      {bar(baseline, "bg-muted")}
    </div>
  );
}
```

- [ ] **Step 3: Write the page**

`web/src/app/health/page.tsx` renders:

1. `StatusHeader title="Calibration & health"`.
1. One card per matchday from `calibration()`, newest first, grouped so a matchday with both a live and a backfill row shows the live one and marks the backfill "leak-free backfill". Each card: the matchday number; `BarPair` for Spearman (`spearman` vs `baseline_spearman`, `betterIs: "higher"`) and for top-eleven regret (`top11_regret` vs `baseline_top11_regret`, `betterIs: "lower"`, `digits: 0`); a line `n = 478 - 45 stale rows excluded - MAE 42.2 - bias -0.98` through `num()`; the gate as one sentence (`gate === null ? "No gate verdict yet." : sentence built from its fields - read services/calibration.py's gate_verdict for the shape and render its keys, never JSON`); and the three `worst` entries as `player_id - predicted X, actual Y`.
   A row with `n === 0` renders the card title plus "Settled with no predictions - this matchday finished before the bot was writing them." and nothing else.
1. A runs table from `sessions(30)`: Time (`ago(started_at)`), App (`app` + `mode`, with `dry run` in muted text when `dry_run === 1`), Duration (`num(duration_s, 0)` + ` s`), Result (`lineup_result` for trading sessions; for ingest rows `status_written / universe_size` plus `stopped_by` in `text-negative` when it is `deadline`), Requests (`num(requests)`), League (`league_teams`/`league_fixtures` when present), Errors (`errors`, red when above zero), Integrity (the `integrity_rules` joined, each with `title={integritySentence(rule, integrity_details[rule] ?? "")}`).

Tonight's ingest run must be legible in that table at a glance: `371 / 462` with `deadline` in red.

- [ ] **Step 4: Run the suite and the build**

Run: `cd web && npx vitest run && npm run typecheck && npm run lint && npm run build`
Expected: PASS.

- [ ] **Step 5: Check it against the store**

Open `/health`: three backfill cards (matchday 1 losing on regret, 2 and 3 winning), three live cards saying "settled with no predictions", and the runs table showing the 17:00 UTC ingest stopped by its deadline.

- [ ] **Step 6: Commit**

```bash
git add web/src
git commit -m "feat(web): calibration against the baseline, and both apps' health"
```

______________________________________________________________________

### Task 9: CI, the smoke test, and the docs

**Files:**

- Create: `web/playwright.config.ts`, `web/e2e/smoke.spec.ts`
- Modify: `web/package.json` (add `@playwright/test`, an `e2e` script)
- Modify: `.github/workflows/ci.yml` (a `web` job)
- Modify: `CLAUDE.md` (a dashboard section and the commands)
- Create: `web/README.md`

**Interfaces:**

- Consumes: everything from Tasks 2-8.

- Produces: a CI job that gates the site, and the run book for deploying it.

- [ ] **Step 1: Add the CI job**

In `.github/workflows/ci.yml`, after the existing `lint` job, add:

```yaml
  web:
    name: Web
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '22'
          cache: npm
          cache-dependency-path: web/package-lock.json
      - name: Install
        working-directory: web
        run: npm ci
      - name: Typecheck
        working-directory: web
        run: npm run typecheck
      - name: Lint
        working-directory: web
        run: npm run lint
      - name: Unit tests
        working-directory: web
        run: npm test
```

The existing workflow has no `paths` filter, so this job runs on every push - that is fine and cheaper than getting the filter wrong. The build step is deliberately not in CI: `next build` needs `DATABASE_URL` to import the query modules, and CI has no store. Vercel's own build is the gate for that.

- [ ] **Step 2: Write the smoke test**

`web/e2e/smoke.spec.ts`:

```typescript
import { expect, test } from "@playwright/test";

/**
 * Runs against a deployed preview with a storage state captured by a signed-in
 * browser (see README). It proves the pages render real rows, not that the
 * numbers are right - the Python view tests own the numbers.
 */
const PAGES = [
  { path: "/", heading: "Players" },
  { path: "/squad", heading: "Squad & lineup" },
  { path: "/market", heading: "Market" },
  { path: "/health", heading: "Calibration & health" },
];

for (const page of PAGES) {
  test(`${page.heading} renders`, async ({ page: browser }) => {
    await browser.goto(page.path);
    await expect(browser.getByRole("heading", { name: page.heading })).toBeVisible();
    await expect(browser.locator("table tbody tr").first()).toBeVisible();
  });
}

test("an anonymous visitor is sent to the login page", async ({ browser: b }) => {
  const anonymous = await b.newContext({ storageState: { cookies: [], origins: [] } });
  const page = await anonymous.newPage();
  await page.goto("/");
  await expect(page).toHaveURL(/\/login/);
  await anonymous.close();
});
```

`web/playwright.config.ts` reads `BASE_URL` from the environment, sets `use.storageState: "e2e/.auth.json"`, and has no web server (the target is a deploy).

- [ ] **Step 3: Write `web/README.md`**

Cover, in this order: what the site is and that it is read-only; the three environment variables and where they come from; `npm run dev` locally; how to capture `e2e/.auth.json` by signing in once with `npx playwright codegen`; that the views live in the bot repo and a new column means a new migration; and that a `status` dry-run writes real market and squad rows, so the site's numbers can move when you run the bot locally.

- [ ] **Step 4: Update `CLAUDE.md`**

Add to the Common Commands block:

```bash
# The dashboard (web/, Next.js on Vercel — see docs/superpowers/specs/2026-09-16-dashboard-design.md)
cd web && npm run dev          # local dashboard against DATABASE_URL
cd web && npm test             # Vitest unit tests
cd web && npm run typecheck    # tsc --noEmit
```

And one bullet in the Current state list:

```markdown
- **The dashboard (2026-09-16)**: `web/` is a Next.js app on Vercel — a private, read-only view of the store (Players, Squad & lineup, Market, Calibration & health) behind a Supabase Auth magic link. Every page is a server component reading the six `rehoboam.web_*` views from migration 007 over the pooler as `rehoboam_bot`; the site never names a table and never writes. Telegram keeps approvals and alerts.
```

- [ ] **Step 5: Run every check**

Run: `cd web && npm ci && npm run typecheck && npm run lint && npm test` and `.venv/bin/pytest tests/store/ -q`
Expected: PASS. Report the counts.

- [ ] **Step 6: Commit**

```bash
git add web .github/workflows/ci.yml CLAUDE.md
git commit -m "ci(web): typecheck, lint and unit tests for the dashboard; smoke tests and docs"
```

______________________________________________________________________

## Controller-run steps after the plan

These need Marco's credentials or touch production; an implementer never does them.

1. Apply migration 007 to production as admin (through `store.migrate.migrate` with the Settings admin DSN, as for 004-006), then confirm the six views answer: `select count(*) from rehoboam.web_players` and friends.
1. Create the Supabase Auth user: Authentication → Users → add Marco's address; then Providers → Email, disable sign-ups; Site URL and redirect URLs set to the Vercel production domain and `https://*-<project>.vercel.app/auth/callback` for previews.
1. `vercel login`, then `vercel link` from `web/`, set the three environment variables for Production and Preview, and deploy.
1. Sign in, walk the four pages, and compare the Players table against `rehoboam players` and the squad against `rehoboam status`.
1. Open the PR, merge, let CI run; Vercel deploys production from `main`.
1. After a week of use, decide whether approvals move onto the site and alerts come off Telegram - a separate design.
