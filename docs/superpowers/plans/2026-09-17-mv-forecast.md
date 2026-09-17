# Market-Value Forecast Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Forecast every player's next daily market-value update, score each forecast against the real update, and show both on the dashboard.

**Architecture:** The ingestion app already fetches every player's details twice a day; it starts storing Kickbase's last-update change (`tfhmvt`) and, after each run, scores yesterday's forecasts and writes today's (`run_mv_forecast`). The rule is pure Python (`services/mv_forecast.py`). Migration 008 adds the column, the `mv_forecasts` table, and views the site reads (`web_mv_forecast`, `web_mv_accuracy`, two new columns on `web_players`/`web_market`).

**Tech Stack:** Python 3.10+ with psycopg 3 and Typer; PostgreSQL (Supabase); pytest with pytest-postgresql; Next.js 15 App Router with postgres.js 3.4.9 and Vitest.

**Spec:** `docs/superpowers/specs/2026-09-17-mv-forecast-design.md`

## Global Constraints

- Work in the worktree `/Users/marco/dev/rehoboam/.claude/worktrees/mv-forecast` on branch `marcobraun2013/mv-forecast`. Never commit to `main`. Never push.
- Never read, print or edit any `.env` or `web/.env.local` file. Tests never reach the real database: `store_dsn` points `DATABASE_URL` at a throwaway PostgreSQL.
- Every commit message ends with exactly these two lines, after a blank line:
  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_014Qwogi5GPPQJrhoKiZPvPw
  ```
- Do not run `black` on whole files (the repo is not black-clean). `uv run ruff check <files you touched>` must pass.
- The pre-commit hook runs `mdformat` on Markdown and may rewrite a file; if a commit fails with "files were modified by this hook", `git add` the file again and repeat the same commit.
- Migrations only append view columns (`create or replace view` keeps every existing column, in order, with its type).
- `METHOD = "momentum-v1"`; defaults `MV_FORECAST_MOMENTUM = 0.9`, `MV_FORECAST_CAP = 0.20`; fetch cutoff 21:45 Europe/Berlin; the live forecast day is `((now() at time zone 'Europe/Berlin') + interval '2 hours')::date`.
- Web text rule: every sentence that explains a number must be true in every case that produces it. Negative numbers use U+2212 via the existing `format.ts` helpers. `grep -rn 'text-\[#' web/src` and `grep -rn '"-"' web/src` must stay empty.
- Python test command: `uv run pytest <paths> -q`. Web commands run from `web/`: `npx vitest run`, `npm run typecheck`, `npm run lint`.

______________________________________________________________________

### Task 1: Migration 008 and the stored last-update change

**Files:**

- Create: `rehoboam/store/migrations/008_mv_forecast.sql`
- Modify: `rehoboam/enrichment/rows.py` (`status_row`)
- Modify: `rehoboam/store/corpus_store.py` (`record_status_daily`, `status_on`)
- Modify: `tests/test_enrichment/test_rows.py`, `tests/store/test_corpus_store.py`, `tests/store/test_migrate.py`, `tests/store/test_web_views.py`

**Interfaces:**

- Produces: column `rehoboam.player_status_daily.mv_change bigint`; table `rehoboam.mv_forecasts` (columns below); views `rehoboam.web_mv_forecast`, `rehoboam.web_mv_accuracy`; `web_players` and `web_market` gain `next_mv_change` (bigint) and `next_mv_pct` (numeric, percent, two decimals).

- Produces: `status_row(...)` returns an extra key `"mv_change"` (int or None) read from `details["tfhmvt"]`; `CorpusStore.status_on` returns `mv_change` too.

- [ ] **Step 1: Write the failing tests**

In `tests/test_enrichment/test_rows.py`, replace `test_status_row_reads_st_prob_mv_tid` and extend the missing-fields test:

```python
def test_status_row_reads_st_prob_mv_tid_and_the_last_mv_change():
    row = rows.status_row(
        "p1",
        date(2026, 9, 14),
        {"st": 0, "prob": 1, "mv": 1_000_000, "tid": 7, "tfhmvt": -25_000},
        123.0,
    )
    assert row == {
        "player_id": "p1",
        "day": date(2026, 9, 14),
        "status": 0,
        "lineup_probability": 1,
        "market_value": 1_000_000,
        "mv_change": -25_000,
        "team_id": "7",
        "fetched_at": 123.0,
    }


def test_status_row_tolerates_missing_fields():
    row = rows.status_row("p1", date(2026, 9, 14), {}, 1.0)
    assert (
        row["status"],
        row["lineup_probability"],
        row["market_value"],
        row["mv_change"],
        row["team_id"],
    ) == (
        None,
        None,
        None,
        None,
        None,
    )
```

In `tests/store/test_corpus_store.py`, add:

```python
def test_status_daily_stores_the_last_mv_change_and_a_refetch_replaces_it(store_dsn):
    store = CorpusStore(dsn=store_dsn)
    _universe(store, "p1")
    day = date(2026, 9, 14)
    store.record_status_daily("p1", day, {"mv": 5_000_000, "tfhmvt": 100_000}, 10.0)
    assert store.status_on("p1", day)["mv_change"] == 100_000
    store.record_status_daily("p1", day, {"mv": 5_000_000}, 20.0)
    assert store.status_on("p1", day)["mv_change"] is None
```

In `tests/store/test_migrate.py`:

- add to `EXPECTED_TABLES`, after the `# dashboard views (task 1)` block:
  ```python
      # market-value forecast (008)
      "mv_forecasts",
      "web_mv_forecast",
      "web_mv_accuracy",
  ```
- in `test_migrate_creates_every_table_in_the_rehoboam_schema`, append `"008_mv_forecast.sql",` after `"007_web_views.sql",`.
- change every `{1, 2, 3, 4, 5, 6, 7}` to `{1, 2, 3, 4, 5, 6, 7, 8}` (three places).
- in the two tests that write a simulated file, change `"008_simulated.sql"` to `"009_simulated.sql"` and each comment above it to:
  ```python
  # store_dsn already has versions 1-8 applied from the real
  # migrations dir; use 009 so this simulated file is genuinely new.
  ```
- add:
  ```python
  def test_migrate_applies_008_with_the_mv_change_column(blank_dsn):
      with connect(blank_dsn) as conn:
          applied = migrate(conn)
          col = conn.execute(
              "select data_type from information_schema.columns where table_schema = %s "
              "and table_name = 'player_status_daily' and column_name = 'mv_change'",
              (SCHEMA,),
          ).fetchone()
      assert "008_mv_forecast.sql" in applied
      assert col is not None and col["data_type"] == "bigint"
  ```

In `tests/store/test_web_views.py`, add at the end:

```python
def _berlin_live_day(dsn):
    """The day web_mv_forecast shows, computed with the database's own clock."""
    return _rows(
        dsn,
        "select ((now() at time zone 'Europe/Berlin') + interval '2 hours')::date as d",
    )[0]["d"]


def _forecast(dsn, player_id, target_day, *, change, pct, scored=False):
    with connect(dsn) as conn:
        conn.execute(
            "insert into rehoboam.mv_forecasts (player_id, target_day, made_at, method, "
            "base_mv, last_change, predicted_change, predicted_pct, scored_at, outcome, "
            "actual_change, actual_pct) values (%s, %s, %s, 'momentum-v1', 10000000, "
            "100000, %s, %s, %s, %s, %s, %s)",
            (
                player_id,
                target_day,
                NOW,
                change,
                pct,
                NOW if scored else None,
                "scored" if scored else None,
                change if scored else None,
                pct if scored else None,
            ),
        )


def test_web_mv_forecast_shows_only_the_live_days_unscored_forecast(store_dsn):
    from datetime import timedelta

    _players(store_dsn)
    live = _berlin_live_day(store_dsn)
    _forecast(store_dsn, "a", live, change=90_000, pct=0.009)
    _forecast(store_dsn, "a", live - timedelta(days=1), change=5, pct=0.5)
    _forecast(store_dsn, "b", live, change=-18_000, pct=-0.009, scored=True)
    rows = _rows(store_dsn, "select * from rehoboam.web_mv_forecast")
    assert [(r["player_id"], r["target_day"]) for r in rows] == [("a", live)]
    assert float(rows[0]["predicted_pct"]) == 0.9  # percent, two decimals
    players = {
        r["player_id"]: r
        for r in _rows(store_dsn, "select * from rehoboam.web_players")
    }
    assert players["a"]["next_mv_change"] == 90_000
    assert float(players["a"]["next_mv_pct"]) == 0.9
    assert (
        players["b"]["next_mv_change"] is None and players["b"]["next_mv_pct"] is None
    )


def test_web_market_carries_the_listed_players_forecast(store_dsn):
    _players(store_dsn)
    _forecast(store_dsn, "a", _berlin_live_day(store_dsn), change=-45_000, pct=-0.0045)
    LeagueStore(dsn=store_dsn).write_listings(
        [
            {
                "snapshot_at": NOW,
                "player_id": "a",
                "ask": 10_000_000,
                "market_value": 10_000_000,
                "mv_trend": 2,
                "seller_id": None,
                "offer_count": 0,
                "our_bid": None,
                "listed_at": None,
                "expires_at": NOW + 3600,
                "status": 0,
                "lineup_probability": 1,
                "source": "test",
            }
        ]
    )
    row = _rows(store_dsn, "select * from rehoboam.web_market")[0]
    assert row["next_mv_change"] == -45_000
    assert float(row["next_mv_pct"]) == -0.45


def test_web_mv_accuracy_counts_hits_misses_and_unscorable_rows(store_dsn):
    from datetime import date

    day = date(2026, 9, 16)
    with connect(store_dsn) as conn:
        for pid, pred, actual, outcome in (
            ("a", 100, 80, "scored"),  # right direction, miss 20 € / 0.2 pp
            ("b", 100, -50, "scored"),  # wrong direction, miss 150 € / 1.5 pp
            ("c", 0, 30, "scored"),  # flat forecast: not directional
            ("d", 100, None, "unscorable"),
        ):
            conn.execute(
                "insert into rehoboam.mv_forecasts (player_id, target_day, made_at, method, "
                "base_mv, last_change, predicted_change, predicted_pct, scored_at, outcome, "
                "actual_change, actual_pct) values (%s, %s, 1, 'momentum-v1', 10000, 100, "
                "%s, %s, 2, %s, %s, %s)",
                (
                    pid,
                    day,
                    pred,
                    pred / 10000,
                    outcome,
                    actual,
                    None if actual is None else actual / 10000,
                ),
            )
        # an unscored row never counts
        conn.execute(
            "insert into rehoboam.mv_forecasts (player_id, target_day, made_at, method, "
            "base_mv, last_change, predicted_change, predicted_pct) "
            "values ('e', %s, 1, 'momentum-v1', 10000, 100, 100, 0.01)",
            (day,),
        )
    (row,) = _rows(store_dsn, "select * from rehoboam.web_mv_accuracy")
    assert row["target_day"] == day
    assert (
        row["scored"],
        row["unscorable"],
        row["directional"],
        row["direction_hits"],
    ) == (
        3,
        1,
        2,
        1,
    )
    # misses: |0.8-1.0|=0.2, |-0.5-1.0|=1.5, |0.3-0|=0.3 pp -> mean 0.67
    assert float(row["mae_pct"]) == 0.67
    # no change: 0.8, 0.5, 0.3 pp -> mean 0.53
    assert float(row["baseline_mae_pct"]) == 0.53
    assert row["mae_eur"] == 67  # (20 + 150 + 30) / 3 = 66.67
    assert row["baseline_mae_eur"] == 53  # (80 + 50 + 30) / 3 = 53.33
```

(`LeagueStore.write_listings` takes the row keys that `rehoboam/enrichment/rows.py::market_listing_rows` builds. If the call fails because a key is missing or extra, open `rehoboam/store/league_store.py` and use exactly the keys it reads; do not change the store.)

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_enrichment/test_rows.py tests/store/test_corpus_store.py tests/store/test_migrate.py tests/store/test_web_views.py -q`
Expected: failures on `mv_change` (KeyError / missing column), on `008_mv_forecast.sql` not applied, and on `rehoboam.mv_forecasts` not existing.

- [ ] **Step 3: Write the migration**

Create `rehoboam/store/migrations/008_mv_forecast.sql`:

```sql
-- Market-value forecast (spec 2026-09-17). Kickbase's player details carry
-- `tfhmvt`, the euro change of the last daily update; the ingestion stores it
-- next to the market value it came with. Every run then scores yesterday's
-- forecasts and writes today's. Views only append columns.

alter table rehoboam.player_status_daily add column if not exists mv_change bigint;

create table if not exists rehoboam.mv_forecasts (
    player_id        text not null,
    target_day       date not null,              -- Berlin date of the ~22:00 update
    made_at          double precision not null,
    method           text not null,
    base_mv          bigint not null,            -- market value when forecast
    last_change      bigint not null,            -- tfhmvt when forecast
    predicted_change bigint not null,
    predicted_pct    double precision not null,  -- fraction of base_mv
    scored_at        double precision,
    outcome          text check (outcome in ('scored', 'unscorable')),
    actual_change    bigint,
    actual_pct       double precision,           -- fraction of base_mv
    primary key (player_id, target_day)
);
create index if not exists idx_mv_forecasts_target_day on rehoboam.mv_forecasts (target_day);

-- The forecast for the update still ahead: today's until 22:00 Berlin,
-- tomorrow's from then on (which the morning run writes, so overnight the
-- site shows no forecast rather than one for an update already made).
create or replace view rehoboam.web_mv_forecast as
select f.player_id, f.target_day, f.made_at, f.base_mv, f.predicted_change,
    round((100 * f.predicted_pct)::numeric, 2) as predicted_pct
from rehoboam.mv_forecasts f
where f.scored_at is null
  and f.target_day = ((now() at time zone 'Europe/Berlin') + interval '2 hours')::date;

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
    (l.player_id is not null) as listed,
    fc.predicted_change as next_mv_change,
    fc.predicted_pct as next_mv_pct
from rehoboam.player_table p
join rehoboam.player_universe u on u.player_id = p.player_id
left join listed l on l.player_id = p.player_id
left join rehoboam.web_mv_forecast fc on fc.player_id = p.player_id;

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
    p.predicted_ep, p.p_start, p.fair_value_gap, p.points, p.avg_points,
    p.next_mv_change, p.next_mv_pct
from rehoboam.market_listings l
join newest n on l.snapshot_at = n.at
left join rehoboam.web_players p on p.player_id = l.player_id
left join rehoboam.managers m on m.manager_id = l.seller_id;

-- One row per scored update. `directional` counts forecasts where both the
-- forecast and the real change moved; "no change" is the baseline miss.
create or replace view rehoboam.web_mv_accuracy as
select target_day,
    count(*) filter (where outcome = 'scored') as scored,
    count(*) filter (where outcome = 'unscorable') as unscorable,
    count(*) filter (
        where outcome = 'scored' and predicted_change <> 0 and actual_change <> 0
    ) as directional,
    count(*) filter (
        where outcome = 'scored' and predicted_change <> 0 and actual_change <> 0
          and (predicted_change > 0) = (actual_change > 0)
    ) as direction_hits,
    round((100 * avg(abs(actual_pct - predicted_pct)) filter (where outcome = 'scored'))::numeric, 2)
        as mae_pct,
    round((100 * avg(abs(actual_pct)) filter (where outcome = 'scored'))::numeric, 2)
        as baseline_mae_pct,
    round(avg(abs(actual_change - predicted_change)) filter (where outcome = 'scored'))
        as mae_eur,
    round(avg(abs(actual_change)) filter (where outcome = 'scored'))
        as baseline_mae_eur
from rehoboam.mv_forecasts
where scored_at is not null
group by target_day;
```

- [ ] **Step 4: Store the change**

In `rehoboam/enrichment/rows.py::status_row`, add `mv_change` after `market_value` and extend the docstring:

```python
def status_row(player_id: str, day: date, details: dict, fetched_at: float) -> dict:
    """League player details → one ``player_status_daily`` row.

    ``st`` is the injury/availability status (0 healthy), ``prob`` the lineup
    probability (1 starter … 5 unlikely) — the two fields the scorer never had
    day by day. ``tfhmvt`` is the euro change of the last daily market-value
    update, read with the value it produced. Missing fields stay None rather
    than becoming a fake healthy starter.
    """
    tid = details.get("tid")
    return {
        "player_id": str(player_id),
        "day": day,
        "status": _opt_int(details.get("st")),
        "lineup_probability": _opt_int(details.get("prob")),
        "market_value": _opt_int(details.get("mv")),
        "mv_change": _opt_int(details.get("tfhmvt")),
        "team_id": str(tid) if tid is not None else None,
        "fetched_at": float(fetched_at),
    }
```

In `rehoboam/store/corpus_store.py`, `record_status_daily` writes the new column:

```python
conn.execute(
    """
                INSERT INTO rehoboam.player_status_daily (
                    player_id, day, status, lineup_probability, market_value, mv_change,
                    team_id, fetched_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (player_id, day) DO UPDATE SET
                    status = excluded.status,
                    lineup_probability = excluded.lineup_probability,
                    market_value = excluded.market_value,
                    mv_change = excluded.mv_change,
                    team_id = excluded.team_id,
                    fetched_at = excluded.fetched_at
                """,
    (
        r["player_id"],
        r["day"],
        r["status"],
        r["lineup_probability"],
        r["market_value"],
        r["mv_change"],
        r["team_id"],
        r["fetched_at"],
    ),
)
```

and `status_on` selects it:

```python
                "SELECT player_id, day, status, lineup_probability, market_value, mv_change, "
                "team_id, fetched_at FROM rehoboam.player_status_daily "
                "WHERE player_id = %s AND day = %s",
```

- [ ] **Step 5: Run the tests to see them pass**

Run: `uv run pytest tests/test_enrichment/test_rows.py tests/store/test_corpus_store.py tests/store/test_migrate.py tests/store/test_web_views.py -q`
Expected: all pass. Then run the whole suite: `uv run pytest -q` — expected: everything passes except the two known environment skips.

- [ ] **Step 6: Commit**

```bash
git add rehoboam/store/migrations/008_mv_forecast.sql rehoboam/enrichment/rows.py rehoboam/store/corpus_store.py tests/test_enrichment/test_rows.py tests/store/test_corpus_store.py tests/store/test_migrate.py tests/store/test_web_views.py
git commit -m "feat(store): migration 008 — last MV change, mv_forecasts, forecast views

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014Qwogi5GPPQJrhoKiZPvPw"
```

______________________________________________________________________

### Task 2: The forecast rule

**Files:**

- Create: `rehoboam/services/mv_forecast.py`
- Create: `tests/test_mv_forecast.py`

**Interfaces:**

- Produces (all pure, no I/O):

  - `METHOD: str = "momentum-v1"`, `BERLIN = ZoneInfo("Europe/Berlin")`, `FETCH_CUTOFF = time(21, 45)`
  - `@dataclass(frozen=True) class Forecast: player_id: str; target_day: date; base_mv: int; last_change: int; predicted_change: int; predicted_pct: float`
  - `forecast(player_id: str, market_value: int, last_change: int, target_day: date, *, momentum: float, cap: float) -> Forecast | None`
  - `usable_day(fetched_at: float) -> date | None`
  - `berlin_today(now: float) -> date`
  - `@dataclass(frozen=True) class Score: outcome: str; actual_change: int | None; actual_pct: float | None` with `SCORED = "scored"`, `UNSCORABLE = "unscorable"` module constants
  - `score(base_mv: int, next_market_value: int, next_change: int) -> Score`
  - `@dataclass(frozen=True) class BacktestResult: momentum: float; cap: float; forecasts: int; directional: int; direction_hits: int; mae_pp: float; baseline_mae_pp: float` with property `direction_rate -> float | None`
  - `backtest(series: Iterable[Sequence[int]], *, momentum: float, cap: float) -> BacktestResult`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mv_forecast.py`:

```python
"""The market-value forecast rule (spec 2026-09-17) — pure."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from rehoboam.services.mv_forecast import (
    BERLIN,
    METHOD,
    SCORED,
    UNSCORABLE,
    backtest,
    berlin_today,
    forecast,
    score,
    usable_day,
)

DAY = date(2026, 9, 17)


def _epoch(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=BERLIN).timestamp()


def test_method_name_is_stable():
    assert METHOD == "momentum-v1"


def test_forecast_is_momentum_times_the_last_move():
    # 9,900,000 -> 10,000,000 is +1.0101 %; 0.9 of that on 10,000,000
    f = forecast("p", 10_000_000, 100_000, DAY, momentum=0.9, cap=0.2)
    assert f is not None
    assert f.player_id == "p" and f.target_day == DAY
    assert f.base_mv == 10_000_000 and f.last_change == 100_000
    assert f.predicted_pct == pytest.approx(0.9 * 100_000 / 9_900_000)
    assert f.predicted_change == round(10_000_000 * 0.9 * 100_000 / 9_900_000)


def test_forecast_follows_a_fall():
    f = forecast("p", 9_000_000, -1_000_000, DAY, momentum=0.9, cap=0.2)
    assert f.predicted_pct == pytest.approx(0.9 * -0.1)
    assert f.predicted_change == -810_000


def test_forecast_caps_a_huge_last_move_in_both_directions():
    up = forecast("p", 2_000_000, 1_000_000, DAY, momentum=0.9, cap=0.2)  # +100 %
    assert up.predicted_pct == pytest.approx(0.18)
    assert up.predicted_change == 360_000
    down = forecast("p", 500_000, -500_000, DAY, momentum=0.9, cap=0.2)  # -50 %
    assert down.predicted_pct == pytest.approx(-0.18)
    assert down.predicted_change == -90_000


def test_a_flat_last_update_forecasts_no_change():
    f = forecast("p", 5_000_000, 0, DAY, momentum=0.9, cap=0.2)
    assert f.predicted_pct == 0 and f.predicted_change == 0


@pytest.mark.parametrize(
    "market_value,last_change",
    [(0, 0), (-5, 0), (1_000_000, 1_000_000), (1_000_000, 2_000_000)],
)
def test_no_forecast_without_a_positive_value_before_and_after(
    market_value, last_change
):
    assert forecast("p", market_value, last_change, DAY, momentum=0.9, cap=0.2) is None


def test_usable_day_is_the_berlin_date_before_the_cutoff():
    assert usable_day(_epoch(2026, 9, 17, 7, 0)) == date(2026, 9, 17)
    assert usable_day(_epoch(2026, 9, 17, 21, 44)) == date(2026, 9, 17)
    assert usable_day(_epoch(2026, 9, 17, 0, 5)) == date(2026, 9, 17)


def test_usable_day_refuses_a_fetch_near_or_after_the_update():
    assert usable_day(_epoch(2026, 9, 17, 21, 45)) is None
    assert usable_day(_epoch(2026, 9, 17, 23, 30)) is None


def test_usable_day_follows_berlin_in_winter_too():
    # In January Berlin is UTC+1: 17:00 UTC is 18:00 there, 21:00 UTC is 22:00
    utc_17 = datetime.fromisoformat("2027-01-15T17:00:00+00:00").timestamp()
    assert usable_day(utc_17) == date(2027, 1, 15)
    utc_21 = datetime.fromisoformat("2027-01-15T21:00:00+00:00").timestamp()
    assert usable_day(utc_21) is None


def test_berlin_today_crosses_midnight_before_utc_does():
    utc_2230 = datetime.fromisoformat("2026-09-16T22:30:00+00:00").timestamp()
    assert berlin_today(utc_2230) == date(2026, 9, 17)


def test_score_takes_the_next_change_when_the_base_lines_up():
    s = score(10_000_000, 10_200_000, 200_000)
    assert s.outcome == SCORED
    assert s.actual_change == 200_000
    assert s.actual_pct == pytest.approx(0.02)


def test_score_counts_a_flat_update():
    s = score(10_000_000, 10_000_000, 0)
    assert (s.outcome, s.actual_change, s.actual_pct) == (SCORED, 0, 0.0)


def test_score_refuses_a_reading_whose_base_does_not_match():
    s = score(10_000_000, 10_500_000, 200_000)  # previous value 10,300,000
    assert (s.outcome, s.actual_change, s.actual_pct) == (UNSCORABLE, None, None)


def test_score_refuses_a_non_positive_base():
    assert score(0, 100, 100).outcome == UNSCORABLE


def test_backtest_replays_consecutive_days():
    # +10 %, +10 %, then flat: two forecasts (made on day 1 and day 2)
    series = [[100, 110, 121, 121]]
    r = backtest(series, momentum=1.0, cap=0.5)
    assert r.forecasts == 2
    # day 1: predicted +11 (10 % of 110), actual +11 -> hit, miss 0
    # day 2: predicted +12.1 -> round 12, actual 0 -> flat actual, not directional
    assert r.directional == 1 and r.direction_hits == 1
    assert r.direction_rate == 1.0
    assert r.mae_pp == pytest.approx((0 + 10.0) / 2)
    assert r.baseline_mae_pp == pytest.approx((10.0 + 0) / 2)


def test_backtest_counts_a_wrong_direction():
    r = backtest([[100, 110, 99]], momentum=1.0, cap=0.5)
    assert (r.forecasts, r.directional, r.direction_hits) == (1, 1, 0)
    assert r.direction_rate == 0.0


def test_backtest_of_nothing_is_empty_not_an_error():
    r = backtest([[], [5], [5, 6]], momentum=0.9, cap=0.2)
    assert (r.forecasts, r.directional, r.direction_hits) == (0, 0, 0)
    assert r.direction_rate is None
    assert (r.mae_pp, r.baseline_mae_pp) == (0.0, 0.0)
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_mv_forecast.py -q`
Expected: collection error, `No module named 'rehoboam.services.mv_forecast'`.

- [ ] **Step 3: Write the module**

Create `rehoboam/services/mv_forecast.py`:

```python
"""The market-value forecast (spec docs/superpowers/specs/2026-09-17-mv-forecast-design.md).

Pure: no I/O. Kickbase updates every market value once a day at about 22:00
Berlin time. Over a year of daily values, the next update moved like the last
one, a little weaker: `momentum × last move`, with the last move capped,
missed by a third to a quarter as much as "no change" and had the direction
right about 95 % of the time. What it cannot see is a turn; the scoring below
is what makes a better rule measurable.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

METHOD = "momentum-v1"
BERLIN = ZoneInfo("Europe/Berlin")
#: From this Berlin time on, a reading may be on either side of the update.
FETCH_CUTOFF = time(21, 45)

SCORED = "scored"
UNSCORABLE = "unscorable"


@dataclass(frozen=True)
class Forecast:
    player_id: str
    target_day: date
    base_mv: int
    last_change: int
    predicted_change: int
    predicted_pct: float  # fraction of base_mv


def forecast(
    player_id: str,
    market_value: int,
    last_change: int,
    target_day: date,
    *,
    momentum: float,
    cap: float,
) -> Forecast | None:
    """The next update's change, from the last one. None without a positive
    value both before and after the last update."""
    previous = market_value - last_change
    if market_value <= 0 or previous <= 0:
        return None
    last_pct = last_change / previous
    pct = momentum * max(-cap, min(cap, last_pct))
    return Forecast(
        player_id=str(player_id),
        target_day=target_day,
        base_mv=int(market_value),
        last_change=int(last_change),
        predicted_change=round(market_value * pct),
        predicted_pct=pct,
    )


def berlin_today(now: float) -> date:
    return datetime.fromtimestamp(now, tz=BERLIN).date()


def usable_day(fetched_at: float) -> date | None:
    """The Berlin date whose update a reading precedes, or None when the
    reading was taken so close to (or after) the update that it could be on
    either side of it."""
    local = datetime.fromtimestamp(fetched_at, tz=BERLIN)
    if local.time() >= FETCH_CUTOFF:
        return None
    return local.date()


@dataclass(frozen=True)
class Score:
    outcome: str  # SCORED or UNSCORABLE
    actual_change: int | None
    actual_pct: float | None  # fraction of base_mv


def score(base_mv: int, next_market_value: int, next_change: int) -> Score:
    """Score a forecast with the first reading after its update.

    That reading's value minus its change is the value before the update,
    which must be the forecast's base; otherwise the days did not line up and
    the reading proves nothing about this forecast.
    """
    if base_mv <= 0 or next_market_value - next_change != base_mv:
        return Score(UNSCORABLE, None, None)
    return Score(SCORED, int(next_change), next_change / base_mv)


@dataclass(frozen=True)
class BacktestResult:
    momentum: float
    cap: float
    forecasts: int
    directional: int  # forecast and real change both non-zero
    direction_hits: int
    mae_pp: float  # mean |real − forecast|, percentage points
    baseline_mae_pp: float  # the same for "no change"

    @property
    def direction_rate(self) -> float | None:
        return self.direction_hits / self.directional if self.directional else None


def backtest(
    series: Iterable[Sequence[int]], *, momentum: float, cap: float
) -> BacktestResult:
    """Replay `forecast` over each run of consecutive daily values (oldest first)."""
    n = directional = hits = 0
    miss = baseline = 0.0
    for values in series:
        for i in range(1, len(values) - 1):
            f = forecast(
                "",
                values[i],
                values[i] - values[i - 1],
                date.min,
                momentum=momentum,
                cap=cap,
            )
            if f is None:
                continue
            actual_change = values[i + 1] - values[i]
            actual_pct = actual_change / f.base_mv
            n += 1
            miss += abs(actual_pct - f.predicted_pct)
            baseline += abs(actual_pct)
            if f.predicted_change != 0 and actual_change != 0:
                directional += 1
                hits += (f.predicted_change > 0) == (actual_change > 0)
    return BacktestResult(
        momentum=momentum,
        cap=cap,
        forecasts=n,
        directional=directional,
        direction_hits=hits,
        mae_pp=100 * miss / n if n else 0.0,
        baseline_mae_pp=100 * baseline / n if n else 0.0,
    )
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/test_mv_forecast.py -q`
Expected: all pass. Then `uv run ruff check rehoboam/services/mv_forecast.py tests/test_mv_forecast.py`.

- [ ] **Step 5: Commit**

```bash
git add rehoboam/services/mv_forecast.py tests/test_mv_forecast.py
git commit -m "feat(forecast): the momentum rule, reading usability, scoring and backtest

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014Qwogi5GPPQJrhoKiZPvPw"
```

______________________________________________________________________

### Task 3: The forecast store and the run step

**Files:**

- Create: `rehoboam/store/mv_forecast_store.py`
- Create: `rehoboam/enrichment/mv_forecast.py`
- Create: `tests/store/test_mv_forecast_store.py`
- Create: `tests/test_enrichment/test_mv_forecast_step.py`

**Interfaces:**

- Consumes (Task 2): `forecast`, `score`, `usable_day`, `berlin_today`, `Forecast`, `Score`, `METHOD`, `SCORED`, `UNSCORABLE` from `rehoboam.services.mv_forecast`. (Task 1): table `rehoboam.mv_forecasts`, column `player_status_daily.mv_change`.

- Produces:

  - `class MvForecastStore(dsn: str | None = None)` with
    - `status_rows(day: date) -> list[dict]` — keys `player_id, market_value, mv_change, fetched_at`; only rows with both values non-null
    - `status_readings(day: date, player_ids: list[str]) -> dict[str, dict]` — same keys, keyed by player id, nulls allowed
    - `upsert_forecasts(forecasts: list[Forecast], *, made_at: float, method: str) -> int`
    - `pending(before: date) -> list[dict]` — keys `player_id, target_day, base_mv`; unscored, `target_day < before`
    - `record_outcomes(outcomes: list[tuple[str, date, Score]], *, scored_at: float) -> int`
    - `daily_series() -> list[list[int]]` (used by Task 5)
  - `@dataclass class MvForecastOutcome: written: int = 0; scored: int = 0; unscorable: int = 0; error: str | None = None`
  - `run_mv_forecast(store, *, now: float, momentum: float, cap: float) -> MvForecastOutcome` — never raises

- [ ] **Step 1: Write the failing store tests**

Create `tests/store/test_mv_forecast_store.py`:

```python
"""MvForecastStore: readings in, forecasts and outcomes out, against a real store."""

from __future__ import annotations

from datetime import date, datetime

from rehoboam.services.mv_forecast import BERLIN, Forecast, Score
from rehoboam.store import connect
from rehoboam.store.corpus_store import CorpusStore
from rehoboam.store.mv_forecast_store import MvForecastStore

D16 = date(2026, 9, 16)
D17 = date(2026, 9, 17)
MORNING_17 = datetime(2026, 9, 17, 7, 0, tzinfo=BERLIN).timestamp()


def _universe(dsn, *ids):
    CorpusStore(dsn=dsn).upsert_players(
        [
            {"player_id": i, "last_name": i, "position": "Forward", "team_id": "3"}
            for i in ids
        ]
    )


def _reading(dsn, pid, day, mv, change, at=MORNING_17):
    details = {"mv": mv} if change is None else {"mv": mv, "tfhmvt": change}
    CorpusStore(dsn=dsn).record_status_daily(pid, day, details, at)


def _forecast(pid, day, base=10_000_000, change=90_000):
    return Forecast(pid, day, base, 100_000, change, change / base)


def _rows(dsn):
    with connect(dsn) as conn:
        return {
            (r["player_id"], r["target_day"]): r
            for r in conn.execute("select * from rehoboam.mv_forecasts").fetchall()
        }


def test_status_rows_returns_only_readings_with_a_value_and_a_change(store_dsn):
    _universe(store_dsn, "a", "b", "c")
    _reading(store_dsn, "a", D17, 10_000_000, 100_000)
    _reading(store_dsn, "b", D17, 5_000_000, None)
    _reading(store_dsn, "c", D16, 5_000_000, 10)
    rows = MvForecastStore(dsn=store_dsn).status_rows(D17)
    assert rows == [
        {
            "player_id": "a",
            "market_value": 10_000_000,
            "mv_change": 100_000,
            "fetched_at": MORNING_17,
        }
    ]


def test_status_readings_keys_by_player_and_keeps_nulls(store_dsn):
    _universe(store_dsn, "a", "b", "c")
    _reading(store_dsn, "a", D17, 10_000_000, 100_000)
    _reading(store_dsn, "b", D17, 5_000_000, None)
    readings = MvForecastStore(dsn=store_dsn).status_readings(D17, ["a", "b", "z"])
    assert set(readings) == {"a", "b"}
    assert readings["b"]["mv_change"] is None


def test_upsert_writes_then_rewrites_only_unscored_rows(store_dsn):
    store = MvForecastStore(dsn=store_dsn)
    written = store.upsert_forecasts(
        [_forecast("a", D17), _forecast("b", D16)], made_at=1.0, method="m"
    )
    assert written == 2
    store.record_outcomes([("b", D16, Score("scored", 50_000, 0.005))], scored_at=2.0)
    store.upsert_forecasts(
        [_forecast("a", D17, change=1), _forecast("b", D16, change=1)],
        made_at=3.0,
        method="m",
    )
    rows = _rows(store_dsn)
    assert (rows[("a", D17)]["predicted_change"], rows[("a", D17)]["made_at"]) == (
        1,
        3.0,
    )
    assert (rows[("b", D16)]["predicted_change"], rows[("b", D16)]["made_at"]) == (
        90_000,
        1.0,
    )


def test_upsert_of_nothing_writes_nothing(store_dsn):
    assert (
        MvForecastStore(dsn=store_dsn).upsert_forecasts([], made_at=1.0, method="m")
        == 0
    )


def test_pending_lists_unscored_forecasts_before_a_day(store_dsn):
    store = MvForecastStore(dsn=store_dsn)
    store.upsert_forecasts(
        [_forecast("a", D16), _forecast("b", D16), _forecast("c", D17)],
        made_at=1.0,
        method="m",
    )
    store.record_outcomes([("b", D16, Score("unscorable", None, None))], scored_at=2.0)
    assert store.pending(before=D17) == [
        {"player_id": "a", "target_day": D16, "base_mv": 10_000_000}
    ]


def test_record_outcomes_fills_the_row_once(store_dsn):
    store = MvForecastStore(dsn=store_dsn)
    store.upsert_forecasts([_forecast("a", D16)], made_at=1.0, method="m")
    assert (
        store.record_outcomes(
            [("a", D16, Score("scored", -20_000, -0.002))], scored_at=5.0
        )
        == 1
    )
    assert (
        store.record_outcomes([("a", D16, Score("scored", 1, 1.0))], scored_at=6.0) == 0
    )
    row = _rows(store_dsn)[("a", D16)]
    assert (
        row["outcome"],
        row["actual_change"],
        row["actual_pct"],
        row["scored_at"],
    ) == (
        "scored",
        -20_000,
        -0.002,
        5.0,
    )


def test_daily_series_splits_each_player_at_gaps(store_dsn):
    _universe(store_dsn, "a", "b")
    corpus = CorpusStore(dsn=store_dsn)
    corpus.record_mv_series(
        "a",
        {
            "it": [
                {"dt": 20000, "mv": 1},
                {"dt": 20001, "mv": 2},
                {"dt": 20003, "mv": 3},
            ]
        },
    )
    corpus.record_mv_series(
        "b", {"it": [{"dt": 20000, "mv": 7}, {"dt": 20001, "mv": 8}]}
    )
    assert MvForecastStore(dsn=store_dsn).daily_series() == [[1, 2], [3], [7, 8]]
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/store/test_mv_forecast_store.py -q`
Expected: `No module named 'rehoboam.store.mv_forecast_store'`.

- [ ] **Step 3: Write the store**

Create `rehoboam/store/mv_forecast_store.py`:

```python
"""Readings in, forecasts and their outcomes out (spec 2026-09-17).

One transaction per call and bulk statements only: a run forecasts every
player it read, about 600, and must not pay one pooler round trip each.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from rehoboam.services.mv_forecast import Forecast, Score


class MvForecastStore:
    def __init__(self, dsn: str | None = None):
        self.dsn = dsn

    def connection(self):
        from rehoboam.store import connect

        return connect(self.dsn)

    def status_rows(self, day: date) -> list[dict[str, Any]]:
        """Every reading for `day` that carries a market value and its last change."""
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT player_id, market_value, mv_change, fetched_at "
                "FROM rehoboam.player_status_daily "
                "WHERE day = %s AND market_value IS NOT NULL AND mv_change IS NOT NULL "
                "ORDER BY player_id",
                (day,),
            ).fetchall()
        return [dict(r) for r in rows]

    def status_readings(
        self, day: date, player_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        if not player_ids:
            return {}
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT player_id, market_value, mv_change, fetched_at "
                "FROM rehoboam.player_status_daily WHERE day = %s AND player_id = ANY(%s)",
                (day, list(player_ids)),
            ).fetchall()
        return {r["player_id"]: dict(r) for r in rows}

    def upsert_forecasts(
        self, forecasts: list[Forecast], *, made_at: float, method: str
    ) -> int:
        """Write forecasts; a second run the same day rewrites a row only while
        it is unscored."""
        if not forecasts:
            return 0
        with self.connection() as conn, conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO rehoboam.mv_forecasts (
                    player_id, target_day, made_at, method, base_mv, last_change,
                    predicted_change, predicted_pct
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (player_id, target_day) DO UPDATE SET
                    made_at = excluded.made_at,
                    method = excluded.method,
                    base_mv = excluded.base_mv,
                    last_change = excluded.last_change,
                    predicted_change = excluded.predicted_change,
                    predicted_pct = excluded.predicted_pct
                WHERE rehoboam.mv_forecasts.scored_at IS NULL
                """,
                [
                    (
                        f.player_id,
                        f.target_day,
                        made_at,
                        method,
                        f.base_mv,
                        f.last_change,
                        f.predicted_change,
                        f.predicted_pct,
                    )
                    for f in forecasts
                ],
            )
        return len(forecasts)

    def pending(self, before: date) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT player_id, target_day, base_mv FROM rehoboam.mv_forecasts "
                "WHERE scored_at IS NULL AND target_day < %s ORDER BY target_day, player_id",
                (before,),
            ).fetchall()
        return [dict(r) for r in rows]

    def record_outcomes(
        self, outcomes: list[tuple[str, date, Score]], *, scored_at: float
    ) -> int:
        """Fill each forecast's outcome once; an already scored row is left alone.
        Returns how many rows were filled."""
        filled = 0
        if not outcomes:
            return 0
        with self.connection() as conn:
            for player_id, target_day, s in outcomes:
                cur = conn.execute(
                    "UPDATE rehoboam.mv_forecasts SET scored_at = %s, outcome = %s, "
                    "actual_change = %s, actual_pct = %s "
                    "WHERE player_id = %s AND target_day = %s AND scored_at IS NULL",
                    (
                        scored_at,
                        s.outcome,
                        s.actual_change,
                        s.actual_pct,
                        player_id,
                        target_day,
                    ),
                )
                filled += cur.rowcount
        return filled

    def daily_series(self) -> list[list[int]]:
        """Each player's stored daily market values, oldest first, split into
        runs of consecutive days."""
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT player_id, snapshot_at, market_value FROM rehoboam.mv_series "
                "ORDER BY player_id, snapshot_at"
            ).fetchall()
        runs: list[list[int]] = []
        last_player = None
        last_day = None
        for r in rows:
            day = int(r["snapshot_at"] // 86400)
            if r["player_id"] != last_player or last_day is None or day != last_day + 1:
                runs.append([])
            runs[-1].append(int(r["market_value"]))
            last_player, last_day = r["player_id"], day
        return runs
```

(`record_outcomes` runs one UPDATE per outcome inside a single connection and transaction; a morning scores at most one day of forecasts, about 600 rows, so this is one round trip per row over one connection. Keep it: `executemany` does not report per-row `rowcount`, and the return value is what tells a caller how many were filled.)

- [ ] **Step 4: Run the store tests to see them pass**

Run: `uv run pytest tests/store/test_mv_forecast_store.py -q`
Expected: all pass.

- [ ] **Step 5: Write the failing run-step tests**

Create `tests/test_enrichment/test_mv_forecast_step.py`:

```python
"""run_mv_forecast: score yesterday's forecasts, then write today's (real store)."""

from __future__ import annotations

from datetime import date, datetime

from rehoboam.enrichment.mv_forecast import MvForecastOutcome, run_mv_forecast
from rehoboam.services.mv_forecast import BERLIN, METHOD
from rehoboam.store import connect
from rehoboam.store.corpus_store import CorpusStore
from rehoboam.store.mv_forecast_store import MvForecastStore

D15 = date(2026, 9, 15)
D16 = date(2026, 9, 16)
D17 = date(2026, 9, 17)


def _at(day, hh, mm=0):
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=BERLIN).timestamp()


def _universe(dsn, *ids):
    CorpusStore(dsn=dsn).upsert_players(
        [
            {"player_id": i, "last_name": i, "position": "Forward", "team_id": "3"}
            for i in ids
        ]
    )


def _reading(dsn, pid, day, mv, change, at):
    CorpusStore(dsn=dsn).record_status_daily(pid, day, {"mv": mv, "tfhmvt": change}, at)


def _forecasts(dsn):
    with connect(dsn) as conn:
        return {
            (r["player_id"], r["target_day"]): r
            for r in conn.execute("select * from rehoboam.mv_forecasts").fetchall()
        }


def _run(dsn, now):
    return run_mv_forecast(MvForecastStore(dsn=dsn), now=now, momentum=0.9, cap=0.2)


def test_a_morning_run_forecasts_today_from_todays_readings(store_dsn):
    _universe(store_dsn, "a", "b")
    _reading(store_dsn, "a", D16, 9_900_000, 50, _at(D16, 7))
    _reading(store_dsn, "a", D17, 10_000_000, 100_000, _at(D17, 7))
    _reading(store_dsn, "b", D17, 5_000_000, -100_000, _at(D17, 7))
    out = _run(store_dsn, _at(D17, 7, 9))
    assert out == MvForecastOutcome(written=2, scored=0, unscorable=0, error=None)
    rows = _forecasts(store_dsn)
    assert set(rows) == {("a", D17), ("b", D17)}
    a = rows[("a", D17)]
    assert (a["method"], a["base_mv"], a["last_change"]) == (
        METHOD,
        10_000_000,
        100_000,
    )
    assert a["predicted_change"] == round(10_000_000 * 0.9 * 100_000 / 9_900_000)
    assert rows[("b", D17)]["predicted_change"] < 0


def test_a_reading_taken_after_the_cutoff_is_not_forecast(store_dsn):
    _universe(store_dsn, "a")
    _reading(store_dsn, "a", D17, 10_000_000, 100_000, _at(D17, 21, 50))
    out = _run(store_dsn, _at(D17, 21, 55))
    assert out.written == 0
    assert _forecasts(store_dsn) == {}


def test_the_next_morning_scores_yesterday_then_forecasts_today(store_dsn):
    _universe(store_dsn, "a", "b")
    _reading(store_dsn, "a", D16, 10_000_000, 100_000, _at(D16, 7))
    _reading(store_dsn, "b", D16, 5_000_000, 50_000, _at(D16, 7))
    _run(store_dsn, _at(D16, 7, 9))
    # the 22:00 update on the 16th: a +80,000, b's reading does not line up
    _reading(store_dsn, "a", D17, 10_080_000, 80_000, _at(D17, 7))
    _reading(store_dsn, "b", D17, 5_000_000, 30_000, _at(D17, 7))
    out = _run(store_dsn, _at(D17, 7, 9))
    assert (out.scored, out.unscorable, out.written, out.error) == (1, 1, 2, None)
    rows = _forecasts(store_dsn)
    a16 = rows[("a", D16)]
    assert (a16["outcome"], a16["actual_change"]) == ("scored", 80_000)
    assert a16["actual_pct"] == 80_000 / 10_000_000
    assert rows[("b", D16)]["outcome"] == "unscorable"
    assert rows[("a", D17)]["base_mv"] == 10_080_000


def test_a_missing_next_day_reading_waits_for_the_afternoon_run(store_dsn):
    _universe(store_dsn, "a")
    _reading(store_dsn, "a", D16, 10_000_000, 100_000, _at(D16, 7))
    _run(store_dsn, _at(D16, 7, 9))
    out = _run(store_dsn, _at(D17, 7, 9))  # no reading for a on the 17th yet
    assert (out.scored, out.unscorable) == (0, 0)
    assert _forecasts(store_dsn)[("a", D16)]["scored_at"] is None


def test_a_forecast_whose_next_day_passed_unread_becomes_unscorable(store_dsn):
    _universe(store_dsn, "a")
    _reading(store_dsn, "a", D15, 10_000_000, 100_000, _at(D15, 7))
    _run(store_dsn, _at(D15, 7, 9))
    out = _run(store_dsn, _at(D17, 7, 9))  # the 16th was never read
    assert (out.scored, out.unscorable) == (0, 1)
    assert _forecasts(store_dsn)[("a", D15)]["outcome"] == "unscorable"


def test_a_late_next_day_reading_is_not_used_to_score(store_dsn):
    _universe(store_dsn, "a")
    _reading(store_dsn, "a", D15, 10_000_000, 100_000, _at(D15, 7))
    _run(store_dsn, _at(D15, 7, 9))
    _reading(store_dsn, "a", D16, 10_080_000, 80_000, _at(D16, 22, 30))
    out = _run(store_dsn, _at(D17, 7, 9))
    assert (out.scored, out.unscorable) == (0, 1)


def test_a_failing_store_is_reported_not_raised():
    class Broken:
        def pending(self, before):
            raise RuntimeError("pooler down")

    out = run_mv_forecast(Broken(), now=_at(D17, 7), momentum=0.9, cap=0.2)
    assert out.written == 0 and out.error == "pooler down"
```

- [ ] **Step 6: Run them to see them fail**

Run: `uv run pytest tests/test_enrichment/test_mv_forecast_step.py -q`
Expected: `No module named 'rehoboam.enrichment.mv_forecast'`.

- [ ] **Step 7: Write the run step**

Create `rehoboam/enrichment/mv_forecast.py`:

```python
"""The ingestion's forecast step (spec 2026-09-17): score, then forecast.

Runs after the per-player loop, so today's readings are in the store. Scoring
comes first because the morning's readings are what score yesterday's
forecasts. The step never raises: a failure is logged and returned, and the
run's other work stands.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from rehoboam.services.mv_forecast import (
    METHOD,
    SCORED,
    UNSCORABLE,
    Score,
    berlin_today,
    forecast,
    score,
    usable_day,
)

logger = logging.getLogger(__name__)


@dataclass
class MvForecastOutcome:
    written: int = 0
    scored: int = 0
    unscorable: int = 0
    error: str | None = None


def _score_pending(store, today: date, now: float) -> tuple[int, int]:
    by_next_day: dict[date, list[dict]] = defaultdict(list)
    for row in store.pending(before=today):
        by_next_day[row["target_day"] + timedelta(days=1)].append(row)

    outcomes: list[tuple[str, date, Score]] = []
    for next_day, rows in sorted(by_next_day.items()):
        readings = store.status_readings(next_day, [r["player_id"] for r in rows])
        for row in rows:
            reading = readings.get(row["player_id"])
            usable = (
                reading is not None
                and reading["market_value"] is not None
                and reading["mv_change"] is not None
                and usable_day(reading["fetched_at"]) == next_day
            )
            if usable:
                result = score(
                    row["base_mv"], reading["market_value"], reading["mv_change"]
                )
            elif next_day < today:
                result = Score(UNSCORABLE, None, None)
            else:
                continue  # today's afternoon run may still read it
            outcomes.append((row["player_id"], row["target_day"], result))

    store.record_outcomes(outcomes, scored_at=now)
    scored = sum(1 for _, _, s in outcomes if s.outcome == SCORED)
    return scored, len(outcomes) - scored


def run_mv_forecast(
    store, *, now: float, momentum: float, cap: float
) -> MvForecastOutcome:
    outcome = MvForecastOutcome()
    try:
        today = berlin_today(now)
        outcome.scored, outcome.unscorable = _score_pending(store, today, now)
        forecasts = []
        for row in store.status_rows(today):
            if usable_day(row["fetched_at"]) != today:
                continue
            f = forecast(
                row["player_id"],
                row["market_value"],
                row["mv_change"],
                today,
                momentum=momentum,
                cap=cap,
            )
            if f is not None:
                forecasts.append(f)
        outcome.written = store.upsert_forecasts(forecasts, made_at=now, method=METHOD)
    except Exception as e:  # noqa: BLE001 — the step reports, the run goes on
        logger.exception("mv forecast step failed")
        outcome.error = str(e)[:500]
    logger.info(
        "mv-forecast written=%d scored=%d unscorable=%d error=%s",
        outcome.written,
        outcome.scored,
        outcome.unscorable,
        outcome.error,
    )
    return outcome
```

- [ ] **Step 8: Run the tests to see them pass**

Run: `uv run pytest tests/test_enrichment/test_mv_forecast_step.py tests/store/test_mv_forecast_store.py -q`
Expected: all pass. Then `uv run ruff check rehoboam/store/mv_forecast_store.py rehoboam/enrichment/mv_forecast.py tests/store/test_mv_forecast_store.py tests/test_enrichment/test_mv_forecast_step.py`.

- [ ] **Step 9: Commit**

```bash
git add rehoboam/store/mv_forecast_store.py rehoboam/enrichment/mv_forecast.py tests/store/test_mv_forecast_store.py tests/test_enrichment/test_mv_forecast_step.py
git commit -m "feat(forecast): the forecast store and the score-then-forecast run step

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014Qwogi5GPPQJrhoKiZPvPw"
```

______________________________________________________________________

### Task 4: Wire the step into both ingestion paths

**Files:**

- Modify: `rehoboam/config.py` (two `Settings` fields after `ingest_transfers_stale_after_hours`)
- Modify: `rehoboam/enrichment/ingest.py` (`facts_for_ingest`)
- Modify: `deploy/azure_function_external/function_app.py` (`ingest`)
- Modify: `rehoboam/cli.py` (`ingest_cmd`)
- Modify: `.env.example`
- Modify: `tests/test_enrichment/test_ingest.py`, `tests/test_cli_ingest.py`, `tests/test_config.py`

**Interfaces:**

- Consumes (Task 3): `run_mv_forecast(store, *, now, momentum, cap) -> MvForecastOutcome`, `MvForecastStore()`.

- Produces: `Settings.mv_forecast_momentum: float = 0.9`, `Settings.mv_forecast_cap: float = 0.20`; `facts_for_ingest(stats, *, app, session_id, calibration=None, mv_forecast: dict | None = None)` puts `mv_forecast` into `extra["mv_forecast"]` when given.

- [ ] **Step 1: Write the failing tests**

In `tests/test_enrichment/test_ingest.py`, add after `test_facts_for_ingest_maps_stats_into_extra_and_zeroes_errors`:

```python
def test_facts_for_ingest_carries_the_forecast_step_outcome():
    stats = IngestStats(status_written=1, started_at=1.0, duration_s=2.0)
    step = {"written": 3, "scored": 2, "unscorable": 1, "error": None}
    facts = facts_for_ingest(stats, app="external", session_id="s", mv_forecast=step)
    assert facts.extra["mv_forecast"] == step
    assert (
        "mv_forecast"
        not in facts_for_ingest(stats, app="external", session_id="s").extra
    )
```

In `tests/test_cli_ingest.py`, in **both** `fake_settings = SimpleNamespace(...)` blocks add:

```python
        mv_forecast_momentum=0.9,
        mv_forecast_cap=0.2,
```

and at the end of `test_ingest_records_a_session_facts_row` add:

```python
    assert row["extra"]["mv_forecast"] == {
        "written": 0,
        "scored": 0,
        "unscorable": 0,
        "error": None,
    }
    assert "mv_forecast" in result.output
```

In `tests/test_config.py` (its tests set the credentials with `monkeypatch.setenv` and build `Settings()`), add:

```python
def test_mv_forecast_settings_default_to_the_measured_rule(monkeypatch):
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "testpassword")
    monkeypatch.delenv("MV_FORECAST_MOMENTUM", raising=False)
    monkeypatch.delenv("MV_FORECAST_CAP", raising=False)
    s = Settings()
    assert (s.mv_forecast_momentum, s.mv_forecast_cap) == (0.9, 0.2)


def test_mv_forecast_settings_read_the_environment(monkeypatch):
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "testpassword")
    monkeypatch.setenv("MV_FORECAST_MOMENTUM", "0.85")
    monkeypatch.setenv("MV_FORECAST_CAP", "0.15")
    s = Settings()
    assert (s.mv_forecast_momentum, s.mv_forecast_cap) == (0.85, 0.15)
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_enrichment/test_ingest.py tests/test_cli_ingest.py tests/test_config.py -q`
Expected: `facts_for_ingest() got an unexpected keyword argument 'mv_forecast'`, missing settings attributes, and the CLI assertion on `extra["mv_forecast"]`.

- [ ] **Step 3: Add the settings**

In `rehoboam/config.py`, directly after the `ingest_transfers_stale_after_hours` field:

```python
    mv_forecast_momentum: float = Field(
        default=0.9,
        description=(
            "The next market-value update is forecast as this multiple of the last "
            "one (spec 2026-09-17). Re-derive with `rehoboam backtest-mv`. "
            "Env: MV_FORECAST_MOMENTUM."
        ),
    )
    mv_forecast_cap: float = Field(
        default=0.20,
        description=(
            "The last update's move is capped at ± this fraction before the multiple "
            "is applied. Env: MV_FORECAST_CAP."
        ),
    )
```

In `.env.example`, after the `# INGEST_TRANSFERS_STALE_AFTER_HOURS=168.0` line:

```
# Market-value forecast (2026-09-17): the next daily update is forecast as
# MOMENTUM x the last update's move, that move capped at +/- CAP.
# Re-derive both with `uv run rehoboam backtest-mv`.
# MV_FORECAST_MOMENTUM=0.9
# MV_FORECAST_CAP=0.20
```

- [ ] **Step 4: Carry the outcome in the session facts**

In `rehoboam/enrichment/ingest.py`, change `facts_for_ingest`'s signature and body:

```python
def facts_for_ingest(
    stats: IngestStats,
    *,
    app: str,
    session_id: str,
    calibration: dict | None = None,
    mv_forecast: dict | None = None,
) -> SessionFacts:
```

add one paragraph to its docstring, after the `calibration` paragraph:

```
    `mv_forecast`, when given, is the forecast step's `MvForecastOutcome` as a
    dict -- like calibration, it never affects `errors`.
```

and after `if calibration is not None: extra["calibration"] = calibration`:

```python
if mv_forecast is not None:
    extra["mv_forecast"] = mv_forecast
```

- [ ] **Step 5: Call the step from the Function**

In `deploy/azure_function_external/function_app.py::ingest`, between the calibration block (ending `logging.info("calibration-end %s", calibration)`) and the `try: SessionStore().record(...)` block, insert:

```python
        mv_forecast = None
        try:
            from rehoboam.enrichment.mv_forecast import run_mv_forecast
            from rehoboam.store.mv_forecast_store import MvForecastStore

            mv_forecast = asdict(
                run_mv_forecast(
                    MvForecastStore(),
                    now=time.time(),
                    momentum=settings.mv_forecast_momentum,
                    cap=settings.mv_forecast_cap,
                )
            )
            logging.info("mv-forecast-end %s", mv_forecast)
        except Exception:
            logging.warning("mv forecast: step failed", exc_info=True)
```

and pass it on:

```python
facts_for_ingest(
    stats,
    app="external",
    session_id=session_id,
    calibration=calibration,
    mv_forecast=mv_forecast,
)
```

- [ ] **Step 6: Call the step from the CLI**

In `rehoboam/cli.py::ingest_cmd`:

- add to the function's imports: `from dataclasses import asdict`, `from .enrichment.mv_forecast import run_mv_forecast`, `from .store.mv_forecast_store import MvForecastStore`.
- replace the block

```python
try:
    SessionStore().record(facts_for_ingest(stats, app="cli", session_id=session_id))
except Exception:
    logger.error("session_facts write failed", exc_info=True)
```

with

```python
    mv_outcome = run_mv_forecast(
        MvForecastStore(),
        now=time.time(),
        momentum=settings.mv_forecast_momentum,
        cap=settings.mv_forecast_cap,
    )
    try:
        SessionStore().record(
            facts_for_ingest(
                stats, app="cli", session_id=session_id, mv_forecast=asdict(mv_outcome)
            )
        )
    except Exception:
        logger.error("session_facts write failed", exc_info=True)
```

- after `table.add_row("duration_s", f"{stats.duration_s:.0f}")`, add:

```python
table.add_row(
    "mv_forecast",
    f"written {mv_outcome.written} · scored {mv_outcome.scored} · "
    f"unscorable {mv_outcome.unscorable}"
    + (f" · error {mv_outcome.error}" if mv_outcome.error else ""),
)
```

- [ ] **Step 7: Run the tests to see them pass**

Run: `uv run pytest tests/test_enrichment/test_ingest.py tests/test_cli_ingest.py tests/test_config.py -q`
Expected: all pass. Then `uv run pytest -q` (whole suite) and `uv run ruff check rehoboam/config.py rehoboam/enrichment/ingest.py rehoboam/cli.py deploy/azure_function_external/function_app.py tests/test_enrichment/test_ingest.py tests/test_cli_ingest.py tests/test_config.py`.

Also run `bash scripts/sync-azure-deps.sh` and confirm `git status` shows no change under `deploy/` other than `function_app.py` (no new dependency is added; this proves it).

- [ ] **Step 8: Commit**

```bash
git add rehoboam/config.py rehoboam/enrichment/ingest.py deploy/azure_function_external/function_app.py rehoboam/cli.py .env.example tests/test_enrichment/test_ingest.py tests/test_cli_ingest.py tests/test_config.py
git commit -m "feat(ingest): score and write market-value forecasts after every run

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014Qwogi5GPPQJrhoKiZPvPw"
```

______________________________________________________________________

### Task 5: `rehoboam backtest-mv`

**Files:**

- Modify: `rehoboam/cli.py` (new command, placed directly after `backtest_baseline`)
- Modify: `tests/store/test_cli.py`

**Interfaces:**

- Consumes (Task 2): `backtest(series, *, momentum, cap) -> BacktestResult`. (Task 3): `MvForecastStore().daily_series()`.

- Produces: CLI command `backtest-mv` with options `--momentum` and `--cap`, each a comma-separated list of floats.

- [ ] **Step 1: Write the failing tests**

Append to `tests/store/test_cli.py`:

```python
def _mv_series(dsn):
    from rehoboam.store.corpus_store import CorpusStore

    corpus = CorpusStore(dsn=dsn)
    corpus.upsert_players(
        [{"player_id": "a", "last_name": "A", "position": "Forward", "team_id": "3"}]
    )
    corpus.record_mv_series(
        "a",
        {
            "it": [
                {"dt": 20000, "mv": 100},
                {"dt": 20001, "mv": 110},
                {"dt": 20002, "mv": 121},
            ]
        },
    )


def test_backtest_mv_prints_one_row_per_combination_and_the_best(store_dsn):
    _mv_series(store_dsn)
    result = runner.invoke(
        app, ["backtest-mv", "--momentum", "1.0,0.5", "--cap", "0.5"]
    )
    assert result.exit_code == 0, result.output
    assert "Market-value forecast backtest" in result.output
    assert "1.00" in result.output and "0.50" in result.output
    # momentum 1.0 forecasts +11 for a real +11: no miss, so it is the best
    assert "Lowest miss: momentum 1.00, cap 0.50" in result.output


def test_backtest_mv_rejects_a_bad_list_without_a_traceback(store_dsn):
    result = runner.invoke(app, ["backtest-mv", "--momentum", "fast"])
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "--momentum" in result.output


def test_backtest_mv_on_an_empty_store_says_so(store_dsn):
    result = runner.invoke(app, ["backtest-mv"])
    assert result.exit_code == 0, result.output
    assert "No daily market values" in result.output
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/store/test_cli.py -q -k backtest_mv`
Expected: `No such command 'backtest-mv'`.

- [ ] **Step 3: Write the command**

In `rehoboam/cli.py`, after the `backtest_baseline` function:

```python
def _float_list(raw: str, flag: str) -> list[float]:
    try:
        values = [float(part) for part in raw.split(",") if part.strip()]
    except ValueError:
        values = []
    if not values:
        console.print(
            f"[red]{flag} takes a comma-separated list of numbers, e.g. 0.8,0.9[/red]"
        )
        raise typer.Exit(code=1)
    return values


@app.command("backtest-mv")
def backtest_mv(
    momentum: str = typer.Option("0.8,0.85,0.9,0.95,1.0", "--momentum"),
    cap: str = typer.Option("0.1,0.15,0.2,0.3", "--cap"),
):
    """Replay the market-value forecast over every stored daily series (read-only)."""
    from .services.mv_forecast import backtest
    from .store.mv_forecast_store import MvForecastStore

    momenta = _float_list(momentum, "--momentum")
    caps = _float_list(cap, "--cap")
    _ensure_store()
    series = MvForecastStore().daily_series()
    results = [backtest(series, momentum=m, cap=c) for m in momenta for c in caps]
    if not results or results[0].forecasts == 0:
        console.print("No daily market values in the store to replay.")
        return

    table = Table(title="Market-value forecast backtest")
    columns = (
        "Momentum",
        "Cap",
        "Forecasts",
        "Direction right",
        "Miss (pp)",
        "No change (pp)",
    )
    for column in columns:
        table.add_column(column, justify="right")
    for r in results:
        rate = "—" if r.direction_rate is None else f"{100 * r.direction_rate:.1f}%"
        table.add_row(
            f"{r.momentum:.2f}",
            f"{r.cap:.2f}",
            f"{r.forecasts:,}",
            rate,
            f"{r.mae_pp:.3f}",
            f"{r.baseline_mae_pp:.3f}",
        )
    console.print(table)
    best = min(results, key=lambda r: r.mae_pp)
    console.print(
        f"Lowest miss: momentum {best.momentum:.2f}, cap {best.cap:.2f} "
        f"({best.mae_pp:.3f} pp against {best.baseline_mae_pp:.3f} pp for no change)."
    )
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/store/test_cli.py -q`
Expected: all pass. Then `uv run ruff check rehoboam/cli.py tests/store/test_cli.py`.

- [ ] **Step 5: Commit**

```bash
git add rehoboam/cli.py tests/store/test_cli.py
git commit -m "feat(cli): backtest-mv replays the market-value forecast over the store

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014Qwogi5GPPQJrhoKiZPvPw"
```

______________________________________________________________________

### Task 6: `Next MV` on Players and Market

**Files:**

- Create: `web/src/lib/next-mv.ts`, `web/src/lib/next-mv.test.ts`
- Create: `web/src/components/NextMv.tsx`
- Modify: `web/src/components/DataTable.tsx` (optional `hint` on a column)
- Modify: `web/src/lib/queries.ts` (`PlayerRow`, `PLAYER_SORTS`, `MarketRow`, `MARKET_SORTS`)
- Modify: `web/src/app/(app)/page.tsx`, `web/src/app/(app)/market/page.tsx`

**Interfaces:**

- Consumes (Task 1): view columns `next_mv_change`, `next_mv_pct` (percent, two decimals) on `web_players` and `web_market`.

- Produces: `nextMv(pct: number | null, change: number | null): { pct: string; change: string; tone: Tone } | null`; `NEXT_MV_HINT: string`; `<NextMvCell pct change />`; `Column<T>.hint?: string`.

- [ ] **Step 1: Write the failing test**

Create `web/src/lib/next-mv.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { NEXT_MV_HINT, nextMv } from "./next-mv";

describe("nextMv", () => {
  it("formats a rise with its euro change", () => {
    expect(nextMv(2.1, 820_000)).toEqual({ pct: "+2.10%", change: "+820,000", tone: "positive" });
  });

  it("formats a fall with a real minus sign", () => {
    expect(nextMv(-0.45, -45_000)).toEqual({ pct: "−0.45%", change: "−45,000", tone: "negative" });
  });

  it("takes its tone from the euro change when the percent rounds to zero", () => {
    expect(nextMv(0, 1_234)).toEqual({ pct: "0.00%", change: "+1,234", tone: "positive" });
    expect(nextMv(0, 0)).toEqual({ pct: "0.00%", change: "0", tone: "neutral" });
  });

  it("returns null when either half is missing", () => {
    expect(nextMv(null, 5)).toBeNull();
    expect(nextMv(1, null)).toBeNull();
    expect(nextMv(null, null)).toBeNull();
  });

  it("names the update the forecast is for", () => {
    expect(NEXT_MV_HINT).toBe("Forecast for tonight's ~22:00 market-value update");
  });
});
```

- [ ] **Step 2: Run it to see it fail**

Run (from `web/`): `npx vitest run src/lib/next-mv.test.ts`
Expected: fails to resolve `./next-mv`.

- [ ] **Step 3: Write the helper, the cell and the column hint**

Create `web/src/lib/next-mv.ts`:

```ts
import { signedMoney, signedPct, type Tone } from "./format";

/** The header hint on every `Next MV` column. */
export const NEXT_MV_HINT = "Forecast for tonight's ~22:00 market-value update";

/**
 * The two halves of a `Next MV` cell. The tone follows the euro change: a
 * small move can round to 0.00 % and still be a rise or a fall.
 */
export function nextMv(
  pct: number | null,
  change: number | null,
): { pct: string; change: string; tone: Tone } | null {
  if (pct === null || change === null) return null;
  const money = signedMoney(change);
  return { pct: signedPct(pct).text, change: money.text, tone: money.tone };
}
```

Create `web/src/components/NextMv.tsx`:

```tsx
import { DASH, type Tone } from "@/lib/format";
import { nextMv } from "@/lib/next-mv";

const TONE: Record<Tone, string> = {
  positive: "text-positive",
  negative: "text-negative",
  neutral: "text-muted",
};

/** Percent over euros, or a dash when there is no forecast for the next update. */
export function NextMvCell({ pct, change }: { pct: number | null; change: number | null }) {
  const out = nextMv(pct, change);
  if (!out) return <span className="text-muted">{DASH}</span>;
  return (
    <div className="flex flex-col items-end gap-0.5">
      <span className={TONE[out.tone]}>{out.pct}</span>
      <span className="text-xs text-muted">{out.change}</span>
    </div>
  );
}
```

In `web/src/components/DataTable.tsx`, add to `Column<T>`:

```ts
  /** Shown as the header's tooltip. */
  hint?: string;
```

and give the `<th>` a `title={c.hint}` attribute.

- [ ] **Step 4: Add the columns and sort keys**

In `web/src/lib/queries.ts`:

- in `PlayerRow`, after `listed: boolean;` add:
  ```ts
    /** Forecast for the next market-value update: euros, and percent with two decimals. */
    next_mv_change: number | null;
    next_mv_pct: number | null;
  ```
- in `PLAYER_SORTS`, insert `"next_mv_pct",` after `"trend_7d_pct",`.
- in `MarketRow`, after `avg_points: number | null;` add the same two fields with the same comment.
- in `MARKET_SORTS`, insert `"next_mv_pct",` after `"market_value",`.

In `web/src/app/(app)/page.tsx` (Players): import `NextMvCell` from `@/components/NextMv` and `NEXT_MV_HINT` from `@/lib/next-mv`, and insert after the `trend_7d_pct` column:

```tsx
    {
      key: "next_mv_pct",
      label: "Next MV",
      hint: NEXT_MV_HINT,
      cell: (p) => <NextMvCell pct={p.next_mv_pct} change={p.next_mv_change} />,
    },
```

In `web/src/app/(app)/market/page.tsx`: the same two imports, and insert after the `market_value` column:

```tsx
    {
      key: "next_mv_pct", label: "Next MV", hint: NEXT_MV_HINT,
      cell: (r) => <NextMvCell pct={r.next_mv_pct} change={r.next_mv_change} />,
    },
```

- [ ] **Step 5: Run the checks**

Run (from `web/`): `npx vitest run && npm run typecheck && npm run lint`
Expected: all tests pass, no type errors, lint shows only the known `src/lib/db.ts` "Unused eslint-disable directive" warning. Then from the worktree root: `grep -rn 'text-\[#' web/src; grep -rn '"-"' web/src` — both print nothing.

- [ ] **Step 6: Commit**

```bash
git add web/src/lib/next-mv.ts web/src/lib/next-mv.test.ts web/src/components/NextMv.tsx web/src/components/DataTable.tsx web/src/lib/queries.ts "web/src/app/(app)/page.tsx" "web/src/app/(app)/market/page.tsx"
git commit -m "feat(web): Next MV column on Players and Market

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014Qwogi5GPPQJrhoKiZPvPw"
```

______________________________________________________________________

### Task 7: Forecast accuracy on Calibration & health

**Files:**

- Create: `web/src/lib/mv-accuracy.ts`, `web/src/lib/mv-accuracy.test.ts`
- Modify: `web/src/lib/queries.ts` (`MvAccuracyRow`, `mvAccuracy`)
- Modify: `web/src/app/(app)/health/page.tsx`

**Interfaces:**

- Consumes (Task 1): view `rehoboam.web_mv_accuracy`. Existing: `compare` from `web/src/lib/calibration.ts`; `DataTable`, `money`, `num`, `pct`, `DASH` from `format.ts`.

- Produces: `MvAccuracyRow`; `mvAccuracy(limit?: number): Promise<MvAccuracyRow[]>`; `summarize(rows): AccuracySummary`; `accuracySentence(summary): string`; `directionRight(row): string`.

- [ ] **Step 1: Write the failing test**

Create `web/src/lib/mv-accuracy.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { accuracySentence, directionRight, summarize } from "./mv-accuracy";

const row = (o: Partial<Parameters<typeof summarize>[0][number]>) => ({
  scored: 0,
  directional: 0,
  direction_hits: 0,
  mae_pct: null,
  baseline_mae_pct: null,
  ...o,
});

describe("summarize", () => {
  it("weights each update by how many forecasts it scored", () => {
    const s = summarize([
      row({ scored: 300, directional: 200, direction_hits: 190, mae_pct: 0.5, baseline_mae_pct: 2 }),
      row({ scored: 100, directional: 50, direction_hits: 40, mae_pct: 1.3, baseline_mae_pct: 2.4 }),
    ]);
    expect(s).toEqual({ updates: 2, scored: 400, maePct: 0.7, baselineMaePct: 2.1, directionRate: 0.92 });
  });

  it("skips updates with nothing scored", () => {
    const s = summarize([row({ scored: 0 }), row({ scored: 10, mae_pct: 1, baseline_mae_pct: 1 })]);
    expect(s.updates).toBe(1);
    expect(s.directionRate).toBeNull();
  });

  it("is empty when nothing is scored", () => {
    expect(summarize([])).toEqual({ updates: 0, scored: 0, maePct: null, baselineMaePct: null, directionRate: null });
  });
});

describe("accuracySentence", () => {
  it("says nothing is scored yet, and when scores arrive", () => {
    expect(accuracySentence(summarize([]))).toBe(
      "No forecast has been scored yet. Each forecast is scored by the first data run after its update, usually the next morning.",
    );
  });

  it("says the forecast beats no change only when its miss is smaller", () => {
    const s = { updates: 14, scored: 7000, maePct: 0.7, baselineMaePct: 2.1, directionRate: 0.953 };
    expect(accuracySentence(s)).toBe(
      'Over the last 14 updates (7000 forecasts), the forecast missed by 0.70 points of percent on average, better than "no change" at 2.10. It called the direction right 95% of the time.',
    );
  });

  it("says so when it ties or loses", () => {
    const tie = { updates: 1, scored: 1, maePct: 1, baselineMaePct: 1, directionRate: null };
    expect(accuracySentence(tie)).toBe(
      'Over the last update (1 forecast), the forecast missed by 1.00 points of percent on average, the same as "no change" at 1.00.',
    );
    const loss = { updates: 2, scored: 5, maePct: 3, baselineMaePct: 1, directionRate: 0 };
    expect(accuracySentence(loss)).toBe(
      'Over the last 2 updates (5 forecasts), the forecast missed by 3.00 points of percent on average, worse than "no change" at 1.00. It called the direction right 0% of the time.',
    );
  });
});

describe("directionRight", () => {
  it("is a share of the directional forecasts, or a dash", () => {
    expect(directionRight({ directional: 200, direction_hits: 190 })).toBe("95% of 200");
    expect(directionRight({ directional: 0, direction_hits: 0 })).toBe("—");
  });
});
```

- [ ] **Step 2: Run it to see it fail**

Run (from `web/`): `npx vitest run src/lib/mv-accuracy.test.ts`
Expected: fails to resolve `./mv-accuracy`.

- [ ] **Step 3: Write the module**

Create `web/src/lib/mv-accuracy.ts`:

```ts
import { compare } from "./calibration";
import { DASH, num } from "./format";

export type AccuracyInput = {
  scored: number;
  directional: number;
  direction_hits: number;
  mae_pct: number | null;
  baseline_mae_pct: number | null;
};

export type AccuracySummary = {
  updates: number;
  scored: number;
  maePct: number | null;
  baselineMaePct: number | null;
  directionRate: number | null;
};

const round2 = (n: number) => Math.round(n * 100) / 100;

/**
 * One summary over several updates, each weighted by how many forecasts it
 * scored. Rounded to the two decimals the page prints, so the verdict below
 * compares exactly the numbers a reader sees.
 */
export function summarize(rows: AccuracyInput[]): AccuracySummary {
  const used = rows.filter((r) => r.scored > 0 && r.mae_pct !== null && r.baseline_mae_pct !== null);
  const scored = used.reduce((n, r) => n + r.scored, 0);
  if (scored === 0) {
    return { updates: 0, scored: 0, maePct: null, baselineMaePct: null, directionRate: null };
  }
  const mae = used.reduce((n, r) => n + (r.mae_pct as number) * r.scored, 0) / scored;
  const baseline = used.reduce((n, r) => n + (r.baseline_mae_pct as number) * r.scored, 0) / scored;
  const directional = used.reduce((n, r) => n + r.directional, 0);
  const hits = used.reduce((n, r) => n + r.direction_hits, 0);
  return {
    updates: used.length,
    scored,
    maePct: round2(mae),
    baselineMaePct: round2(baseline),
    directionRate: directional > 0 ? round2(hits / directional) : null,
  };
}

/** The section's one sentence. Its verdict is exactly what the two numbers say. */
export function accuracySentence(s: AccuracySummary): string {
  if (s.scored === 0 || s.maePct === null || s.baselineMaePct === null) {
    return "No forecast has been scored yet. Each forecast is scored by the first data run after its update, usually the next morning.";
  }
  const updates = s.updates === 1 ? "the last update" : `the last ${s.updates} updates`;
  const forecasts = s.scored === 1 ? "1 forecast" : `${s.scored} forecasts`;
  const outcome = compare(s.maePct, s.baselineMaePct, "lower");
  const verdict = outcome === "beats" ? "better than" : outcome === "ties" ? "the same as" : "worse than";
  const direction =
    s.directionRate === null
      ? ""
      : ` It called the direction right ${Math.round(s.directionRate * 100)}% of the time.`;
  return `Over ${updates} (${forecasts}), the forecast missed by ${num(s.maePct, 2)} points of percent on average, ${verdict} "no change" at ${num(s.baselineMaePct, 2)}.${direction}`;
}

/** "95% of 200": hits among the forecasts where both the forecast and the update moved. */
export function directionRight(r: { directional: number; direction_hits: number }): string {
  if (r.directional === 0) return DASH;
  return `${Math.round((100 * r.direction_hits) / r.directional)}% of ${r.directional}`;
}
```

(The test's "95%" case: `directionRate` 0.953 prints `Math.round(95.3)` = 95.)

- [ ] **Step 4: Add the query**

In `web/src/lib/queries.ts`, after `calibration()`:

```ts
export type MvAccuracyRow = {
  /** YYYY-MM-DD, the Berlin date of the update. */
  target_day: string;
  scored: number;
  unscorable: number;
  directional: number;
  direction_hits: number;
  mae_pct: number | null;
  baseline_mae_pct: number | null;
  mae_eur: number | null;
  baseline_mae_eur: number | null;
};

/** The newest scored updates first. The date is text: postgres.js would make a `date` a JS Date. */
export async function mvAccuracy(limit = 14): Promise<MvAccuracyRow[]> {
  return sql<MvAccuracyRow[]>`
    select to_char(target_day, 'YYYY-MM-DD') as target_day, scored, unscorable,
           directional, direction_hits, mae_pct, baseline_mae_pct, mae_eur, baseline_mae_eur
    from rehoboam.web_mv_accuracy
    order by target_day desc
    limit ${limit}
  `;
}
```

- [ ] **Step 5: Add the section to the page**

In `web/src/app/(app)/health/page.tsx`:

- add `mvAccuracy` and `type MvAccuracyRow` to the existing `@/lib/queries` import; add `money` to the existing `@/lib/format` import; import `accuracySentence, directionRight, summarize` from `@/lib/mv-accuracy`.

- fetch it with the others:

  ```tsx
  const [calibrationRows, sessionRows, mvRows] = await Promise.all([
    calibration(),
    sessions(30),
    mvAccuracy(),
  ]);
  ```

- define the columns next to the session columns:

  ```tsx
  const pp = (n: number | null) => (n === null ? DASH : `${num(n, 2)} pp`);
  const mvColumns: Column<MvAccuracyRow>[] = [
    { key: "target_day", label: "Update", align: "left", sortable: false, cell: (r) => r.target_day },
    { key: "scored", label: "Scored", sortable: false, cell: (r) => num(r.scored) },
    { key: "unscorable", label: "Unscorable", sortable: false, cell: (r) => num(r.unscorable) },
    { key: "direction", label: "Direction right", sortable: false, cell: (r) => directionRight(r) },
    { key: "mae_pct", label: "Avg miss", sortable: false, cell: (r) => pp(r.mae_pct) },
    { key: "baseline_mae_pct", label: "No change", sortable: false, cell: (r) => pp(r.baseline_mae_pct) },
    { key: "mae_eur", label: "Avg miss (€)", sortable: false, cell: (r) => money(r.mae_eur) },
    { key: "baseline_mae_eur", label: "No change (€)", sortable: false, cell: (r) => money(r.baseline_mae_eur) },
  ];
  ```

- between the "Calibration by matchday" `<div>` and the "Session runs" `<div>`, add:

  ```tsx
        <div>
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-[0.08em] text-muted">
            Market-value forecast
          </h2>
          <p className="mb-3 text-sm text-text-dim">{accuracySentence(summarize(mvRows))}</p>
          {mvRows.length > 0 ? (
            <DataTable columns={mvColumns} rows={mvRows} sort="" dir="desc" basePath="/health" />
          ) : null}
        </div>
  ```

- [ ] **Step 6: Run the checks**

Run (from `web/`): `npx vitest run && npm run typecheck && npm run lint`
Expected: all pass (lint: only the known `db.ts` warning). From the worktree root: `grep -rn 'text-\[#' web/src; grep -rn '"-"' web/src` print nothing.

- [ ] **Step 7: Commit**

```bash
git add web/src/lib/mv-accuracy.ts web/src/lib/mv-accuracy.test.ts web/src/lib/queries.ts "web/src/app/(app)/health/page.tsx"
git commit -m "feat(web): market-value forecast accuracy on Calibration & health

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014Qwogi5GPPQJrhoKiZPvPw"
```

______________________________________________________________________

### Task 8: Documentation

**Files:**

- Modify: `CLAUDE.md`

- Modify: `web/README.md`

- [ ] **Step 1: CLAUDE.md**

- In "Current state", after the "The dashboard (2026-09-16)" bullet, add:

  ```markdown
  - **Market-value forecast (2026-09-17)**: the ingestion stores Kickbase's last-update change (`tfhmvt` → `player_status_daily.mv_change`) and, after every run, `enrichment/mv_forecast.run_mv_forecast` scores yesterday's forecasts against the morning's readings and writes today's into `rehoboam.mv_forecasts` (rule in `services/mv_forecast.py`: `MV_FORECAST_MOMENTUM` × the last move, capped at ± `MV_FORECAST_CAP`; a reading taken from 21:45 Berlin on is used for neither). A score counts only when the reading's value minus its change equals the forecast's base. The site shows `Next MV` on Players and Market (`web_mv_forecast`, live until 22:00 Berlin) and the accuracy against "no change" on Calibration & health (`web_mv_accuracy`). Display only: no trading decision reads it. Migration 008 must be applied as admin before its code deploys.
  ```

- In the dashboard bullet, change "reading the six `rehoboam.web_*` views from migration 007" to "reading the `rehoboam.web_*` views (migrations 007 and 008)".

- In "Common Commands", after the `backfill-league` line, add:

  ```bash
  uv run rehoboam backtest-mv                            # Replay the market-value forecast over the stored daily series (--momentum/--cap lists)
  ```

- [ ] **Step 2: web/README.md**

Open the file and update every place that says the site reads "six views" or lists the views, so it names `web_mv_forecast` and `web_mv_accuracy` too and says migration 008 must be applied as admin before a deploy that reads them (same wording style as the existing 007 note). Add one short paragraph under the page descriptions (or, if there is none, after the auth section) saying: Players and Market show `Next MV`, the forecast for tonight's ~22:00 update (percent over euros, a dash when there is none, overnight included); Calibration & health shows how it has scored against "no change".

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md web/README.md
git commit -m "docs: the market-value forecast

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014Qwogi5GPPQJrhoKiZPvPw"
```

(If the hook reformats a Markdown file, `git add` it again and repeat the commit.)
