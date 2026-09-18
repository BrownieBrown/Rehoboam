# Player Panel, Players Split View and Market Table Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the full-screen player overlay with one rich player panel docked beside a ranked list on Players and beside the listings table on Market, carrying everything the store knows about a player — availability, both ranks with denominators, his club's league position, the full season record, form with start/sub, his next three fixtures, the market-value moves and chart — plus self-hosted player photos and club crests.

**Architecture:** Still no client JavaScript: every page is a server component and every control is a `<Link>` driven by search params (`?player=`, `?mv=`). The panel is one component (`PlayerPanel`) used by both pages; what differs is how much width the list gives up. New data arrives as four numbered SQL views/migrations applied as admin before the code deploys, read through `web/src/lib/queries.ts`. Images are pulled once by the ingestion app into Supabase Storage and referenced by a stored path.

**Tech Stack:** Next.js 15.5 App Router (server components), postgres.js 3.4.9, Tailwind v4 with `@theme` tokens in `web/src/app/globals.css`, Python 3.12 + psycopg 3 for the store and the ingestion app, vitest for web tests, pytest for Python.

**Spec:** The two approved design canvases —
Players: https://claude.ai/artifact/Lr3W1o7LYT6RXrmRMpSToK
Market: https://claude.ai/artifact/EsVu2TwRZU74Jx3KS9U7xE
and the review findings at `/private/tmp/claude-501/-Users-marco-dev-rehoboam/8018bab4-147e-44b3-b15d-b03d41d0c4c1/scratchpad/panel-review.md`.

**The pixel reference is on disk.** The approved artboards are plain HTML with
every exact value already in them — spacing, type sizes, radii, the token
hex codes, the SVG chart, the two-column split:

- `docs/superpowers/design-refs/players-split.html` — Players: list + panel
- `docs/superpowers/design-refs/market-table.html` — Market: the ten-column table
- `docs/superpowers/design-refs/market-open.html` — Market with a player open

Read the matching artboard before writing any markup and lift the numbers from
it rather than re-deriving them. Two differences from the artboards are
deliberate and required: the real app gets `box-sizing: border-box` from
Tailwind's reset (the artboards set it themselves), and colours must come from
the Tailwind tokens, never the literal hex the artboards use inline.

**Two review findings need no task — the redesign removes what they were
about.** Finding 4 (the season-progress strip omits the current season for a
player with no status-3/5 match yet) and finding 6 (the 24 h tile and the
"last changes" list compute the same quantity two ways and can disagree): the
new panel has neither a season-progress strip nor a last-changes list. Do not
reintroduce either. The 24 h figure has exactly one source, `trend_24h_eur`.

## Global Constraints

- **No client JavaScript.** No `"use client"` outside the existing `Sidebar.tsx`, no event handlers. Every control is a `<Link>`.
- **A hand-edited URL must never break a page.** Unknown `?player=`, unknown `?mv=`, `__proto__`, a player with no history — each renders something sane, never a crash, never a 500.
- **Missing data shows the shared `DASH` constant (U+2014), never a zero and never an empty cell.** Negative numbers route through `num()`/`money()`, which use U+2212, never an ASCII hyphen.
- **No hardcoded colours.** `grep -rn 'text-\[#' web/src` must print nothing. Colours come from the Tailwind tokens in `globals.css`.
- **SQL is parameterised** through postgres.js tagged templates. Any integer interpolated into an expression must be cast: `current_date - ${days}::int`.
- **Migrations are numbered, applied once, and applied as admin BEFORE dependent code deploys.** `ensure_ready()` runs `migrate()` under the bot role, which cannot create views.
- **`create or replace view` may only append columns.** To reorder or drop a column, `drop view ... cascade` then recreate, in the same migration.
- Commit trailers exactly:
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_014Qwogi5GPPQJrhoKiZPvPw`
- Never push. Never commit to main. Never use bare `git stash`. Never read or print any `.env` or `web/.env.local`. Never run `npm run dev` or `next build`. Never write to the production database.

## Verify (every task)

From `web/`: `npx vitest run && npm run typecheck && npm run lint` — only the known `src/lib/db.ts` warning is expected.
From the worktree root: `KICKBASE_EMAIL=test@example.com KICKBASE_PASSWORD=test uv run pytest tests/store/ tests/test_enrichment/ -q`, and `uv run ruff check` on every Python file touched.
`grep -rn 'text-\[#' web/src` and `grep -rn '"-"' web/src` must both print nothing.

## File Structure

| File                                                  | Responsibility                                                                                 |
| ----------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `rehoboam/store/migrations/018_mv_union_guard.sql`    | Stop a zero market value winning a day in `web_player_mv`                                      |
| `rehoboam/store/migrations/019_player_profile_v2.sql` | `web_player_profile`: availability, rank denominators, club league position, 7d trend fallback |
| `rehoboam/store/migrations/020_player_fixtures.sql`   | `web_player_fixtures`: a player's upcoming matches with the opponent's league place            |
| `rehoboam/store/migrations/021_player_images.sql`     | `players.image_path`, `teams.crest_path`, and the two source-hash columns                      |
| `web/src/lib/chart.ts`                                | Pad x as well as y so the extreme dots are never clipped                                       |
| `web/src/lib/availability.ts`                         | Kickbase `st` code → a label and a tone                                                        |
| `web/src/lib/form.ts`                                 | Last five matchdays as played/unplayed entries carrying started-vs-came-on                     |
| `web/src/components/PlayerPanel.tsx`                  | The one player panel, used by both pages                                                       |
| `web/src/components/PlayerList.tsx`                   | The ranked list that Players shows beside the panel                                            |
| `web/src/components/PlayerPhoto.tsx`                  | A player photo or club crest with an initials fallback                                         |
| `web/src/app/(app)/page.tsx`                          | Players: filters, ranked list, docked panel                                                    |
| `web/src/app/(app)/market/page.tsx`                   | Market: ten-column table, narrowing when a player is open                                      |
| `rehoboam/enrichment/images.py`                       | Download, downsample and upload one player photo or crest                                      |
| `rehoboam/store/image_store.py`                       | Read and write the image columns                                                               |

Deleted at the end of Task 8: `web/src/components/PlayerOverlay.tsx` and its test.

______________________________________________________________________

### Task 1: Stop a zero market value winning a day

**Files:**

- Create: `rehoboam/store/migrations/018_mv_union_guard.sql`
- Modify: `tests/store/test_migrate.py`
- Test: `tests/store/test_player_detail_views.py`

**Interfaces:**

- Produces: `rehoboam.web_player_mv` with columns `player_id, day, market_value`, unchanged in name, order and type.

Review finding 2: `017_player_mv_union.sql` filters the `player_status_daily` branch on `market_value is not null` only. A stored `0` therefore wins the day over a real `mv_series` point, floors the chart's scale and fabricates two large moves in "Last changes".

- [ ] **Step 1: Write the failing test**

Add to `tests/store/test_player_detail_views.py`:

```python
def test_web_player_mv_ignores_a_zero_status_reading(store_dsn):
    """A 0 in player_status_daily is a sentinel, not a market value: the
    mv_series point for that day must win instead."""
    with connect(store_dsn) as conn:
        corpus = CorpusStore(conn)
        corpus.upsert_players([{"player_id": "p-zero", "last_name": "Zero"}])
        conn.execute(
            "insert into rehoboam.player_status_daily (player_id, day, market_value)"
            " values (%s, %s, %s)",
            ("p-zero", date(2026, 9, 10), 0),
        )
        corpus.record_mv_series("p-zero", [(_epoch(date(2026, 9, 10)), 4_000_000)])
        rows = conn.execute(
            "select day, market_value from rehoboam.web_player_mv"
            " where player_id = 'p-zero' order by day"
        ).fetchall()
    assert [(r["day"], r["market_value"]) for r in rows] == [
        (date(2026, 9, 10), 4_000_000)
    ]
```

`_epoch` is the helper already in that file; if it is absent add:

```python
def _epoch(d: date) -> float:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()
```

- [ ] **Step 2: Run it and watch it fail**

Run: `KICKBASE_EMAIL=test@example.com KICKBASE_PASSWORD=test uv run pytest tests/store/test_player_detail_views.py::test_web_player_mv_ignores_a_zero_status_reading -q`
Expected: FAIL — the row comes back as `market_value == 0`.

- [ ] **Step 3: Write the migration**

`rehoboam/store/migrations/018_mv_union_guard.sql`:

```sql
-- Panel fix (2026-09-18): 017 filtered the daily-status branch on
-- `market_value is not null` only. Kickbase writes 0 for a player with no
-- market value, and `distinct on` prefers the status row for a shared day,
-- so a single 0 floored the chart's scale and invented two large moves in
-- the "last changes" list. Both branches now require a positive value; a
-- day with nothing positive in either source simply has no point, which is
-- what the chart already handles.
create or replace view rehoboam.web_player_mv as
with both_sources as (
    select player_id, day, market_value, 1 as rank_source
    from rehoboam.player_status_daily
    where market_value is not null and market_value > 0
    union all
    select player_id, (to_timestamp(snapshot_at) at time zone 'UTC')::date as day, market_value, 2
    from rehoboam.mv_series
    where market_value is not null and market_value > 0
)
select distinct on (player_id, day) player_id, day, market_value
from both_sources
order by player_id, day, rank_source;
```

- [ ] **Step 4: Run the test and watch it pass**

Run: `KICKBASE_EMAIL=test@example.com KICKBASE_PASSWORD=test uv run pytest tests/store/test_player_detail_views.py -q`
Expected: PASS, and every other test in that file still passes.

- [ ] **Step 5: Update the migration bookkeeping**

In `tests/store/test_migrate.py`: add `018_mv_union_guard.sql` to the applied list, extend the version set through 18, and rename the simulated file in the two bot-role tests to `019_simulated.sql` with its comment.

- [ ] **Step 6: Run the store suite**

Run: `KICKBASE_EMAIL=test@example.com KICKBASE_PASSWORD=test uv run pytest tests/store/ -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add rehoboam/store/migrations/018_mv_union_guard.sql tests/store/
git commit -m "fix(store): a zero market value no longer wins a day in the chart"
```

______________________________________________________________________

### Task 2: Everything the panel's header needs, in one row

**Files:**

- Create: `rehoboam/store/migrations/019_player_profile_v2.sql`
- Modify: `web/src/lib/queries.ts:224-247`, `tests/store/test_migrate.py`
- Test: `tests/store/test_player_profile_view.py`

**Interfaces:**

- Consumes: `rehoboam.web_players`, `rehoboam.player_status_daily`, `rehoboam.mv_series`, `rehoboam.league_table`, `rehoboam.teams`.
- Produces: `rehoboam.web_player_profile` with every `web_players` column plus
  `trend_24h_eur, trend_7d_eur, goals, assists, yellow_cards, red_cards, seconds_played, season_points, season_average, rank_overall, rank_position` (all as today) and the new
  `availability integer, ranked_overall_total integer, ranked_position_total integer, club_place integer, club_points integer, club_goal_difference integer`.
- Produces: the TypeScript type `PlayerProfile` gains the same six fields.

Three things are wrong or missing today. Review finding 5: `trend_7d_eur` reads `mv_series` only, while `player_table` (migration 011) prefers the series and falls back to the daily status row — so the panel dashes where the table shows a number, and routinely reports an 8-14 day move as one week. The panel also needs the availability code, the denominator for each rank, and his club's league position.

`rank_overall` and `rank_position` already exist and keep their meaning. The view must be dropped and recreated rather than replaced, because the column order changes.

- [ ] **Step 1: Write the failing tests**

Add to `tests/store/test_player_profile_view.py`:

```python
def test_trend_7d_falls_back_to_the_daily_status_series(store_dsn):
    """No mv_series point older than a week, but a daily status row there:
    the 7d move is still a number, as player_table has always had it."""
    with connect(store_dsn) as conn:
        corpus = CorpusStore(conn)
        corpus.upsert_players([{"player_id": "p-fb", "last_name": "Fallback"}])
        _status(conn, "p-fb", date.today(), market_value=12_000_000)
        _status(conn, "p-fb", date.today() - timedelta(days=9), market_value=10_000_000)
        row = _profile(conn, "p-fb")
    assert row["trend_7d_eur"] == 2_000_000


def test_rank_denominators_count_the_ranked_players(store_dsn):
    with connect(store_dsn) as conn:
        corpus = CorpusStore(conn)
        corpus.upsert_players(
            [
                {"player_id": "p-a", "last_name": "A", "position": "Forward"},
                {"player_id": "p-b", "last_name": "B", "position": "Forward"},
                {"player_id": "p-c", "last_name": "C", "position": "Defender"},
                {"player_id": "p-d", "last_name": "D", "position": "Forward"},
            ]
        )
        for pid, pts in (("p-a", 300), ("p-b", 200), ("p-c", 100)):
            _played(conn, pid, points=pts)
        a, c, d = _profile(conn, "p-a"), _profile(conn, "p-c"), _profile(conn, "p-d")
    assert (a["rank_overall"], a["ranked_overall_total"]) == (1, 3)
    assert (a["rank_position"], a["ranked_position_total"]) == (1, 2)
    assert (c["rank_position"], c["ranked_position_total"]) == (1, 1)
    # A player with no points is not ranked and gets no denominator either.
    assert (d["rank_overall"], d["ranked_overall_total"]) == (None, None)


def test_club_league_position_comes_from_the_newest_matchday(store_dsn):
    with connect(store_dsn) as conn:
        LeagueStore(conn).upsert_teams([{"team_id": "t-1", "name": "Freiburg"}])
        CorpusStore(conn).upsert_players(
            [{"player_id": "p-cl", "last_name": "Club", "team_id": "t-1"}]
        )
        conn.execute(
            "insert into rehoboam.league_table"
            " (season, day_number, team_id, place, points, played, goal_difference, updated_at)"
            " values ('2026/2027', 2, 't-1', 5, 4, 2, 1, 0),"
            "        ('2026/2027', 3, 't-1', 1, 9, 3, 9, 0)"
        )
        row = _profile(conn, "p-cl")
    assert (row["club_place"], row["club_points"], row["club_goal_difference"]) == (
        1,
        9,
        9,
    )


def test_availability_is_the_newest_status_code(store_dsn):
    with connect(store_dsn) as conn:
        CorpusStore(conn).upsert_players([{"player_id": "p-av", "last_name": "Av"}])
        _status(conn, "p-av", date.today() - timedelta(days=1), status=0)
        _status(conn, "p-av", date.today(), status=4)
        row = _profile(conn, "p-av")
    assert row["availability"] == 4
```

Add these helpers to the same file if it has none:

```python
def _profile(conn, player_id):
    return conn.execute(
        "select * from rehoboam.web_player_profile where player_id = %s", (player_id,)
    ).fetchone()


def _status(conn, player_id, day, **cols):
    keys = ["player_id", "day", *cols]
    conn.execute(
        f"insert into rehoboam.player_status_daily ({', '.join(keys)})"
        f" values ({', '.join(['%s'] * len(keys))})"
        " on conflict (player_id, day) do update set"
        + ", ".join(f" {k} = excluded.{k}" for k in cols),
        (player_id, day, *cols.values()),
    )


def _played(conn, player_id, points):
    conn.execute(
        "insert into rehoboam.player_match_history"
        " (player_id, season, day_number, points, status, minutes)"
        " values (%s, '2026/2027', 1, %s, 5, 90)",
        (player_id, points),
    )
```

- [ ] **Step 2: Run them and watch them fail**

Run: `KICKBASE_EMAIL=test@example.com KICKBASE_PASSWORD=test uv run pytest tests/store/test_player_profile_view.py -q`
Expected: FAIL — `column "availability" does not exist`, and `trend_7d_eur` is None.

- [ ] **Step 3: Write the migration**

`rehoboam/store/migrations/019_player_profile_v2.sql`:

```sql
-- Player panel v2 (2026-09-18): the panel now shows his availability, the
-- denominator under each rank, and his club's position in the table, and its
-- 7d move must agree with the one `player_table` (011) has always shown.
-- 016 read the 7d reference point from `mv_series` alone, so a player the
-- weekly sweep had not reached recently showed a dash beside a Players-table
-- number, or an 8-14 day move labelled "1 week"; the daily status series is
-- the same fallback 011 uses. Dropped and recreated rather than replaced
-- because the column list grows in the middle.
drop view if exists rehoboam.web_player_profile cascade;
create view rehoboam.web_player_profile as
with newest_status as (
    select distinct on (player_id) player_id, mv_change, goals, assists,
        yellow_cards, red_cards, seconds_played, season_points, season_average,
        status
    from rehoboam.player_status_daily
    order by player_id, day desc
),
mv_7d_series as (
    select distinct on (player_id) player_id, market_value
    from rehoboam.mv_series
    where market_value is not null and market_value > 0
      and snapshot_at <= extract(epoch from now()) - 7 * 86400
    order by player_id, snapshot_at desc
),
mv_7d_status as (
    select distinct on (player_id) player_id, market_value
    from rehoboam.player_status_daily
    where market_value is not null and market_value > 0
      and day <= current_date - 7
    order by player_id, day desc
),
ranked as (
    select player_id,
        rank() over (order by points desc nulls last) as rank_overall,
        rank() over (partition by position order by points desc nulls last) as rank_position,
        count(*) over () as ranked_overall_total,
        count(*) over (partition by position) as ranked_position_total
    from rehoboam.web_players
    where points is not null
),
newest_day as (
    select max(day_number) as day_number from rehoboam.league_table
),
club as (
    select l.team_id, l.place, l.points, l.goal_difference
    from rehoboam.league_table l, newest_day d
    where l.day_number = d.day_number
)
select p.*,
    n.mv_change as trend_24h_eur,
    p.market_value - coalesce(s7.market_value, d7.market_value) as trend_7d_eur,
    n.goals, n.assists, n.yellow_cards, n.red_cards, n.seconds_played,
    n.season_points, n.season_average,
    r.rank_overall, r.rank_position,
    n.status as availability,
    r.ranked_overall_total::int as ranked_overall_total,
    r.ranked_position_total::int as ranked_position_total,
    c.place as club_place,
    c.points as club_points,
    c.goal_difference as club_goal_difference
from rehoboam.web_players p
left join newest_status n on n.player_id = p.player_id
left join mv_7d_series s7 on s7.player_id = p.player_id
left join mv_7d_status d7 on d7.player_id = p.player_id
left join ranked r on r.player_id = p.player_id
left join club c on c.team_id = p.team_id;
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `KICKBASE_EMAIL=test@example.com KICKBASE_PASSWORD=test uv run pytest tests/store/ -q`
Expected: PASS.

- [ ] **Step 5: Extend the TypeScript type**

In `web/src/lib/queries.ts`, add to `PlayerProfile` (after `rank_position`):

```ts
  /** Kickbase's `st` availability code — 0 is fit. `availability.ts` names it. */
  availability: number | null;
  /** How many players carry a rank at all, for the "of N" under each rank. */
  ranked_overall_total: number | null;
  ranked_position_total: number | null;
  /** His club's line in the newest stored matchday of the league table. */
  club_place: number | null;
  club_points: number | null;
  club_goal_difference: number | null;
```

- [ ] **Step 6: Update the migration bookkeeping and run everything**

`tests/store/test_migrate.py`: applied list gains `019_player_profile_v2.sql`, version set through 19, simulated file becomes `020_simulated.sql`.

Run: `KICKBASE_EMAIL=test@example.com KICKBASE_PASSWORD=test uv run pytest tests/store/ -q` and, from `web/`, `npm run typecheck`.
Expected: both PASS.

- [ ] **Step 7: Commit**

```bash
git add rehoboam/store/migrations/019_player_profile_v2.sql tests/store/ web/src/lib/queries.ts
git commit -m "feat(store): availability, rank denominators and his club's league place"
```

______________________________________________________________________

### Task 3: His next three matches

**Files:**

- Create: `rehoboam/store/migrations/020_player_fixtures.sql`
- Modify: `web/src/lib/queries.ts` (after `playerMv`), `tests/store/test_migrate.py`
- Test: `tests/store/test_player_fixtures_view.py`

**Interfaces:**

- Consumes: `rehoboam.fixtures`, `rehoboam.teams`, `rehoboam.league_table`, `rehoboam.web_players`.

- Produces: `rehoboam.web_player_fixtures` with `player_id, season, day_number, kickoff (double precision), is_home (boolean), opponent (text), opponent_place (int)`.

- Produces: `playerFixtures(playerId: string, limit = 3): Promise<PlayerFixture[]>` where
  `PlayerFixture = { season: string; day_number: number; kickoff_at: string | null; is_home: boolean; opponent: string | null; opponent_place: number | null }`.

- [ ] **Step 1: Write the failing test**

Create `tests/store/test_player_fixtures_view.py`:

```python
from datetime import datetime, timedelta, timezone

from rehoboam.store import connect
from rehoboam.store.corpus_store import CorpusStore
from rehoboam.store.league_store import LeagueStore


def _fx(conn, match_id, day_number, home, away, kickoff):
    conn.execute(
        "insert into rehoboam.fixtures (match_id, season, day_number, kickoff,"
        " home_team_id, away_team_id, status, updated_at)"
        " values (%s, '2026/2027', %s, %s, %s, %s, 0, 0)",
        (match_id, day_number, kickoff.timestamp(), home, away),
    )


def test_upcoming_fixtures_carry_the_opponent_and_his_place(store_dsn):
    soon = datetime.now(timezone.utc) + timedelta(days=2)
    later = datetime.now(timezone.utc) + timedelta(days=9)
    past = datetime.now(timezone.utc) - timedelta(days=2)
    with connect(store_dsn) as conn:
        LeagueStore(conn).upsert_teams(
            [
                {"team_id": "t-me", "name": "Freiburg"},
                {"team_id": "t-op", "name": "Frankfurt"},
            ]
        )
        CorpusStore(conn).upsert_players(
            [{"player_id": "p-fx", "last_name": "Fix", "team_id": "t-me"}]
        )
        conn.execute(
            "insert into rehoboam.league_table (season, day_number, team_id, place,"
            " points, played, goal_difference, updated_at)"
            " values ('2026/2027', 3, 't-op', 9, 4, 3, 0, 0)"
        )
        _fx(conn, "m-past", 3, "t-me", "t-op", past)
        _fx(conn, "m-1", 4, "t-op", "t-me", soon)
        _fx(conn, "m-2", 5, "t-me", "t-op", later)
        rows = conn.execute(
            "select * from rehoboam.web_player_fixtures where player_id = 'p-fx'"
            " order by kickoff"
        ).fetchall()
    assert [r["day_number"] for r in rows] == [4, 5]  # the past match is gone
    assert rows[0]["is_home"] is False and rows[0]["opponent"] == "Frankfurt"
    assert rows[0]["opponent_place"] == 9
    assert rows[1]["is_home"] is True


def test_a_player_with_no_club_has_no_fixtures(store_dsn):
    with connect(store_dsn) as conn:
        CorpusStore(conn).upsert_players([{"player_id": "p-none", "last_name": "None"}])
        rows = conn.execute(
            "select * from rehoboam.web_player_fixtures where player_id = 'p-none'"
        ).fetchall()
    assert rows == []
```

- [ ] **Step 2: Run it and watch it fail**

Run: `KICKBASE_EMAIL=test@example.com KICKBASE_PASSWORD=test uv run pytest tests/store/test_player_fixtures_view.py -q`
Expected: FAIL — `relation "rehoboam.web_player_fixtures" does not exist`.

- [ ] **Step 3: Write the migration**

`rehoboam/store/migrations/020_player_fixtures.sql`:

```sql
-- Player panel v2 (2026-09-18): his next matches, so the panel can say who
-- he plays, home or away, when, and how good they are. The opponent's place
-- comes from the newest stored matchday of the league table — the same
-- reference `web_player_profile` uses for his own club — and is null before
-- the table has any rows. Only fixtures that have not kicked off yet.
create or replace view rehoboam.web_player_fixtures as
with newest_day as (
    select max(day_number) as day_number from rehoboam.league_table
),
places as (
    select l.team_id, l.place
    from rehoboam.league_table l, newest_day d
    where l.day_number = d.day_number
)
select p.player_id,
    f.season,
    f.day_number,
    f.kickoff,
    (f.home_team_id = p.team_id) as is_home,
    case when f.home_team_id = p.team_id then away.name else home.name end as opponent,
    case when f.home_team_id = p.team_id then ap.place else hp.place end as opponent_place
from rehoboam.web_players p
join rehoboam.fixtures f
  on f.home_team_id = p.team_id or f.away_team_id = p.team_id
left join rehoboam.teams home on home.team_id = f.home_team_id
left join rehoboam.teams away on away.team_id = f.away_team_id
left join places hp on hp.team_id = f.home_team_id
left join places ap on ap.team_id = f.away_team_id
where p.team_id is not null
  and f.kickoff > extract(epoch from now());
```

- [ ] **Step 4: Run the test and watch it pass**

Run: `KICKBASE_EMAIL=test@example.com KICKBASE_PASSWORD=test uv run pytest tests/store/test_player_fixtures_view.py -q`
Expected: PASS.

- [ ] **Step 5: Add the query**

In `web/src/lib/queries.ts`, after `playerMv`:

```ts
export type PlayerFixture = {
  season: string;
  day_number: number;
  /** ISO-8601 text, not a JS Date — the same reason `playerMatches` does it. */
  kickoff_at: string | null;
  is_home: boolean;
  opponent: string | null;
  opponent_place: number | null;
};

/** His next `limit` matches, soonest first. Empty for a player with no club,
 * and for one whose club has no upcoming fixture stored. */
export async function playerFixtures(playerId: string, limit = 3): Promise<PlayerFixture[]> {
  return sql<PlayerFixture[]>`
    select season, day_number, is_home, opponent, opponent_place,
      to_char(to_timestamp(kickoff) at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as kickoff_at
    from rehoboam.web_player_fixtures
    where player_id = ${playerId}
    order by kickoff asc
    limit ${limit}::int
  `;
}
```

- [ ] **Step 6: Update the bookkeeping and run everything**

`tests/store/test_migrate.py`: applied list gains `020_player_fixtures.sql`, version set through 20, `EXPECTED_TABLES` gains `web_player_fixtures`, simulated file becomes `021_simulated.sql`.

Run: `KICKBASE_EMAIL=test@example.com KICKBASE_PASSWORD=test uv run pytest tests/store/ -q` and `npm run typecheck` from `web/`.

- [ ] **Step 7: Commit**

```bash
git add rehoboam/store/migrations/020_player_fixtures.sql tests/store/ web/src/lib/queries.ts
git commit -m "feat(store): a player's next matches, with the opponent's league place"
```

______________________________________________________________________

### Task 4: The chart stops clipping its own dots

**Files:**

- Modify: `web/src/lib/chart.ts`
- Test: `web/src/lib/chart.test.ts`

**Interfaces:**

- Produces: `chart(points, width, height, pad = 6)` where **both** x and y are inset by `pad`, so `high.x` and `low.x` are never 0 or `width`.

Review finding 1: only y is padded, so the `<circle r="4">` at the first and last point is half outside the viewBox. A monotone series — the common shape — clips both at once. The review also found the tests never assert `area`, never assert `high.x`/`low.x`, and check a flat series only via `ys.size === 1`, so a line pinned to the top edge would pass.

- [ ] **Step 1: Write the failing tests**

Add to `web/src/lib/chart.test.ts`:

```ts
it("insets x as well as y so the extreme dots are not clipped", () => {
  const c = chart([{ day: "2026-01-01", market_value: 1 }, { day: "2026-01-11", market_value: 9 }], 100, 50)!;
  expect(c.low.x).toBeCloseTo(6, 1);
  expect(c.high.x).toBeCloseTo(94, 1);
  expect(c.low.y).toBeCloseTo(44, 1);
  expect(c.high.y).toBeCloseTo(6, 1);
});

it("closes the area path along the bottom of the box", () => {
  const c = chart([{ day: "2026-01-01", market_value: 1 }, { day: "2026-01-11", market_value: 9 }], 100, 50)!;
  expect(c.area.startsWith("M")).toBe(true);
  expect(c.area.endsWith("Z")).toBe(true);
  expect(c.area).toContain(c.line.slice(1));
  expect(c.area).toContain("50");
});

it("draws a flat series through the middle, not along an edge", () => {
  const c = chart(
    [{ day: "2026-01-01", market_value: 7 }, { day: "2026-01-05", market_value: 7 }],
    100,
    50,
  )!;
  const ys = [...c.line.matchAll(/,([\d.]+)/g)].map((m) => Number(m[1]));
  expect(new Set(ys).size).toBe(1);
  expect(ys[0]).toBeCloseTo(25, 1);
});
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd web && npx vitest run src/lib/chart.test.ts`
Expected: FAIL — `c.low.x` is 0 and `c.high.x` is 100.

- [ ] **Step 3: Inset x in the implementation**

In `web/src/lib/chart.ts`, where x is computed from the day span, replace the full-width mapping with a padded one:

```ts
  const usableWidth = width - pad * 2;
  const x = (t: number) => Math.round((pad + ((t - firstT) / spanT) * usableWidth) * 10) / 10;
```

Keep the existing y mapping. Where the area path closes along the bottom, close it at `height` and at the padded first/last x, so the fill sits under the line rather than under the box:

```ts
  const area = `${line} L${x(lastT)},${height} L${x(firstT)},${height} Z`;
```

Update the doc comment above `chart` so it says both axes are inset by `pad`.

- [ ] **Step 4: Run the tests and watch them pass**

Run: `cd web && npx vitest run src/lib/chart.test.ts`
Expected: PASS, and every existing chart test still passes.

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/chart.ts web/src/lib/chart.test.ts
git commit -m "fix(web): inset the chart on both axes so its dots are never clipped"
```

______________________________________________________________________

### Task 5: Availability and form as tested pure functions

**Files:**

- Create: `web/src/lib/availability.ts`, `web/src/lib/availability.test.ts`, `web/src/lib/form.ts`, `web/src/lib/form.test.ts`
- Modify: `web/src/lib/queries.ts` (`playerMatches` doc comment only)

**Interfaces:**

- Produces: `availability(code: number | null): { label: string; tone: Tone }`.
- Produces: `formEntries(matches: PlayerMatch[], count = 5): FormEntry[]` where
  `FormEntry = { day_number: number; season: string; points: number | null; role: "started" | "came on" | "did not play" }`, oldest first.

Review finding 3: today's form strip filters by date, so a matchday he was an unused substitute or out of the squad prints `0` — indistinguishable from playing and scoring nothing. Status carries the truth: 5 started, 3 came on, 4 unused sub, 1 not in squad, 0 not played.

The availability codes seen in production today are 0, 1, 2, 4 and 16. Only 0 (fit) and 4 (out) are established in this codebase — `rehoboam/h2h.py` treats `{4, 256}` as out and `matchup_analyzer.py` treats 0 as healthy. Everything else is named "unavailable" rather than guessed at; a follow-up probe can split 1, 2 and 16 apart later.

- [ ] **Step 1: Write the failing tests**

`web/src/lib/availability.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { availability } from "./availability";

describe("availability", () => {
  it("calls 0 fit, in a positive tone", () => {
    expect(availability(0)).toEqual({ label: "Fit", tone: "positive" });
  });

  it("calls the codes this codebase knows are out unavailable", () => {
    expect(availability(4)).toEqual({ label: "Out", tone: "negative" });
    expect(availability(256)).toEqual({ label: "Out", tone: "negative" });
  });

  it("names any other non-zero code without guessing at it", () => {
    expect(availability(1)).toEqual({ label: "Unavailable", tone: "negative" });
    expect(availability(16)).toEqual({ label: "Unavailable", tone: "negative" });
  });

  it("says nothing when the store has no code for him", () => {
    expect(availability(null)).toEqual({ label: "Unknown", tone: "neutral" });
  });
});
```

`web/src/lib/form.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { formEntries } from "./form";

const m = (day_number: number, status: number | null, points: number | null) => ({
  season: "2026/2027",
  day_number,
  match_date: null,
  points,
  minutes: null,
  status,
  is_home: null,
  opponent: null,
  match_at: null,
});

describe("formEntries", () => {
  it("returns the newest matchdays oldest-first", () => {
    const out = formEntries([m(3, 5, 253), m(2, 5, 272), m(1, 5, 234)], 3);
    expect(out.map((e) => e.day_number)).toEqual([1, 2, 3]);
  });

  it("separates a start from a substitute appearance", () => {
    expect(formEntries([m(1, 5, 90)], 1)[0].role).toBe("started");
    expect(formEntries([m(1, 3, 40)], 1)[0].role).toBe("came on");
  });

  it("does not report points for a matchday he was not on the pitch", () => {
    for (const status of [4, 1, 0, null]) {
      const [entry] = formEntries([m(1, status, 0)], 1);
      expect(entry.role).toBe("did not play");
      expect(entry.points).toBeNull();
    }
  });

  it("pads to `count` so the strip keeps its shape for a new player", () => {
    expect(formEntries([m(1, 5, 120)], 5)).toHaveLength(5);
  });

  it("never mutates its input", () => {
    const input = [m(1, 5, 10), m(2, 5, 20)];
    const copy = JSON.parse(JSON.stringify(input));
    formEntries(input, 2);
    expect(input).toEqual(copy);
  });
});
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd web && npx vitest run src/lib/availability.test.ts src/lib/form.test.ts`
Expected: FAIL — neither module exists.

- [ ] **Step 3: Write the two modules**

`web/src/lib/availability.ts`:

```ts
import type { Tone } from "./format";

/** Kickbase's `st` code. 0 is fit; `rehoboam/h2h.py` treats 4 and 256 as out
 * and nothing in this codebase has established what 1, 2 and 16 mean, so
 * they are reported as unavailable rather than guessed at. */
export function availability(code: number | null): { label: string; tone: Tone } {
  if (code === null) return { label: "Unknown", tone: "neutral" };
  if (code === 0) return { label: "Fit", tone: "positive" };
  if (code === 4 || code === 256) return { label: "Out", tone: "negative" };
  return { label: "Unavailable", tone: "negative" };
}
```

`web/src/lib/form.ts`:

```ts
import type { PlayerMatch } from "./queries";

export type FormEntry = {
  season: string;
  day_number: number;
  /** Null whenever he was not on the pitch: a stored 0 for an unused
   * substitute is Kickbase's placeholder, not a score. */
  points: number | null;
  role: "started" | "came on" | "did not play";
};

/** The newest `count` matchdays, oldest first, padded with empty entries so
 * the strip keeps its shape. Status is the authority on whether he played:
 * 5 started, 3 came on, everything else did not. */
export function formEntries(matches: PlayerMatch[], count = 5): FormEntry[] {
  const newest = matches.slice(0, count).reverse();
  const entries: FormEntry[] = newest.map((m) => {
    const role = m.status === 5 ? "started" : m.status === 3 ? "came on" : "did not play";
    return {
      season: m.season,
      day_number: m.day_number,
      points: role === "did not play" ? null : m.points,
      role,
    };
  });
  while (entries.length < count) {
    entries.unshift({ season: "", day_number: 0, points: null, role: "did not play" });
  }
  return entries;
}
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `cd web && npx vitest run src/lib/availability.test.ts src/lib/form.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/availability.ts web/src/lib/availability.test.ts web/src/lib/form.ts web/src/lib/form.test.ts
git commit -m "feat(web): availability and form as tested pure functions"
```

______________________________________________________________________

### Task 6: The player panel

**Files:**

- Create: `web/src/components/PlayerPanel.tsx`
- Modify: none yet — Tasks 7 and 8 mount it.

**Interfaces:**

- Consumes: `playerProfile`, `playerMatches`, `playerFixtures`, `playerMv` from `queries.ts`; `chart` from `chart.ts`; `formEntries` from `form.ts`; `availability` from `availability.ts`; `rangeDays`, `rangeLabel`, `RANGES` from `mv-range.ts`; `hrefFor` from `query-href.ts`; `money`, `num`, `signedMoney`, `pct`, `DASH`, `POSITION` from `format.ts`; `Pill`.
- Produces:
  ```ts
  export async function PlayerPanel({ playerId, basePath, params, listing }: {
    playerId: string;
    basePath: string;
    params: Record<string, string | undefined>;
    /** Market passes the one thing it adds; Players passes nothing. */
    listing?: { seller: string; expiresAt: number | null } | null;
  }): Promise<React.ReactElement | null>
  ```

Renders `null` when `playerProfile` finds no such player, so a hand-edited `?player=` cannot break either page.

Layout, top to bottom, matching the approved canvas:

1. **Identity** — photo (Task 10 swaps in the real one; until then `PlayerPhoto` renders initials), name, availability badge, position pill, club, owner, and a Close link on the right.
1. **Listing strip** — only when `listing` is passed: "Listed by <seller> · closes in <countdown>", in the accent tone.
1. **Ranks** — three cells in one bordered row: `Rank overall` `<n> of <N>`, `Rank, <position plural>` `<n> of <N>`, `Club in the table` `<place> · <points> pts · GD <±n>`. A player with no rank reads `DASH` and the label "not ranked".
1. **Four tiles** — Market value, Expected points, Ø points, Points per million.
1. **Two columns.** Left: `This season` (Matches, Started, Minutes, Ø min, Goals, Assists, Yellow, Red) and `Form — last five matchdays` (each cell: matchday number, points in tone, and `START`/`SUB`/`—`). Right: `Next three matches` (matchday, H/A, opponent, opponent's place, kickoff) and `Market value moves` (Last 24 h, Last week, Tonight forecast, Fair value).
1. **Chart** — the range links (`RANGES`, keeping every other param via `hrefFor`), the area+line SVG with a dot on the high and the low, and the low/high figures with their dates beneath. When `chart` returns null: one muted line, "Not enough market-value history to draw a line."

Minutes come from `seconds_played / 60`, rounded. Ø minutes divides by `appearances` and shows `DASH` when `appearances` is null or 0 — never a division by zero.

- [ ] **Step 1: Write the component**

Build it from the tokens already in `globals.css` (`bg-surface`, `bg-bg`, `border-border`, `text-text`, `text-text-dim`, `text-muted`, `text-accent`, `text-positive`, `text-negative`). Fetch with one `Promise.all` after the profile null-check, exactly as the old overlay did:

```tsx
  const profile = await playerProfile(playerId);
  if (!profile) return null;
  const days = rangeDays(params.mv);
  const [matches, fixtures, mv] = await Promise.all([
    playerMatches(playerId, 5),
    playerFixtures(playerId, 3),
    playerMv(playerId, days),
  ]);
```

- [ ] **Step 2: Typecheck and lint**

Run: `cd web && npm run typecheck && npm run lint`
Expected: clean but for the known `db.ts` warning.

- [ ] **Step 3: Commit**

```bash
git add web/src/components/PlayerPanel.tsx
git commit -m "feat(web): one player panel, with everything the store knows about him"
```

______________________________________________________________________

### Task 7: Players becomes a list and a docked panel

**Files:**

- Create: `web/src/components/PlayerList.tsx`
- Modify: `web/src/app/(app)/page.tsx`

**Interfaces:**

- Consumes: `PlayerPanel` from Task 6, `players`/`playerCount` from `queries.ts`.
- Produces: `PlayerList({ rows, selected, basePath, params })` — a bordered list where each row is a link to `hrefFor(basePath, params, { player: row.player_id })`, carrying a position-coloured left rule, the photo, the name with an availability dot, the position and club, and on the right the sorted-by figure over the market value.

The page keeps its filter chips, its club/search form and its paging. The table and the full-screen overlay both go. The sort links move into a small "Ranked by …" header above the list, offering the same `PLAYER_SORTS` keys the table used, so no sort is lost.

Below 1280 px the panel stacks under the list rather than beside it (`flex-col xl:flex-row`), so the page still works on a laptop.

- [ ] **Step 1: Write `PlayerList`**

One file, server component, no client JavaScript; the selected row carries `bg-accent/8` and an accent rule.

- [ ] **Step 2: Rewrite the page body**

Replace the `DataTable` block with:

```tsx
      <div className="flex min-h-0 flex-1 flex-col gap-6 px-6 pb-6 xl:flex-row">
        <PlayerList rows={rows} selected={params.player} basePath="/" params={params} />
        {params.player ? (
          <PlayerPanel playerId={params.player} basePath="/" params={params} />
        ) : (
          <EmptyPanel />
        )}
      </div>
```

`EmptyPanel` is a small local component: a bordered `bg-surface` box with one muted line, "Pick a player to see everything we know about him."

- [ ] **Step 3: Typecheck, lint and test**

Run: `cd web && npx vitest run && npm run typecheck && npm run lint`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add web/src/components/PlayerList.tsx "web/src/app/(app)/page.tsx"
git commit -m "feat(web): Players is a ranked list beside a docked panel"
```

______________________________________________________________________

### Task 8: Market drops the ask and makes room for the panel

**Files:**

- Modify: `web/src/app/(app)/market/page.tsx`, `web/src/lib/queries.ts` (`MarketRow`, `MARKET_SORTS`, `market()`)
- Delete: `web/src/components/PlayerOverlay.tsx` and its test

**Interfaces:**

- Produces: `MarketRow` without `ask`; `MARKET_SORTS` without `"ask"`.

The owner's instruction: "remove ask everywhere for now." Market value is the price column. The Fair price column's sub-line compares against market value, not the removed ask.

Ten columns when nothing is open — Player, Pos, Market value, Fair value, 24 h, Tonight, EP, Pts / M, Seller, Expires — grouped under four band labels: Who / What he costs / What he scores / The listing. A countdown under six hours renders in the accent tone.

When `?player=` is set the table drops to Player, Market value, EP and Expires and the panel takes the rest, so the rows stay where they were rather than being covered.

- [ ] **Step 1: Drop `ask` from the query layer**

Remove the field from `MarketRow`, remove `"ask"` from `MARKET_SORTS`, and if `market()` sorts by a whitelist, make sure the default is still `predicted_ep`. Leave `rehoboam.web_market`'s own `ask` column alone — the view is shared, and nothing else needs a migration.

- [ ] **Step 2: Rewrite the page**

Two column sets chosen by `params.player`, the band header above them, and the panel mounted with its listing strip:

```tsx
        {params.player ? (
          <PlayerPanel
            playerId={params.player}
            basePath="/market"
            params={params}
            listing={listingFor(rows, params.player)}
          />
        ) : null}
```

`listingFor` is a small local helper returning `{ seller, expiresAt }` for the open row, or `null` when the open player is not in the current listing set.

- [ ] **Step 3: Delete the overlay**

```bash
git rm web/src/components/PlayerOverlay.tsx
git rm web/src/components/PlayerOverlay.test.tsx 2>/dev/null || true
```

Then `grep -rn "PlayerOverlay" web/src` must print nothing.

- [ ] **Step 4: Typecheck, lint and test**

Run: `cd web && npx vitest run && npm run typecheck && npm run lint`
Expected: PASS. Also `grep -rn 'text-\[#' web/src` and `grep -rn '"-"' web/src` print nothing.

- [ ] **Step 5: Commit**

```bash
git add -A web/src
git commit -m "feat(web): Market drops the ask and gives the panel its room"
```

______________________________________________________________________

### Task 9: Store the image the API already hands us

**Files:**

- Create: `rehoboam/store/migrations/021_player_images.sql`
- Modify: `rehoboam/enrichment/rows.py` (`status_row`), `rehoboam/store/corpus_store.py` (`record_status_daily`, `upsert_players`), `rehoboam/store/league_store.py` (`upsert_teams`)
- Test: `tests/test_enrichment/test_rows.py`, `tests/store/test_corpus_store.py`

**Interfaces:**

- Produces: `players.image_source` (text, the `pim` path as Kickbase gives it) and `players.image_path` (text, where our copy lives); `teams.crest_source` and `teams.crest_path`.

Probed live on 2026-09-18: `/v4/leagues/{lid}/players/{pid}` returns `pim` (player photo, PNG) and `tim` (club crest, SVG) as CDN-relative paths like `content/file/<hash>.png`, served from `https://kickbase.b-cdn.net/` without authentication. The market listing payload carries `pim` too. No new API call is needed.

- [ ] **Step 1: Write the failing tests**

Extend the two existing `status_row` tests in `tests/test_enrichment/test_rows.py` (the exact-dict one and the missing-fields one) with `"image_source": "content/file/abc.png"` from `pim`, and `None` when `pim` is absent. Extend the round-trip test in `tests/store/test_corpus_store.py` the same way.

- [ ] **Step 2: Run them and watch them fail**

Run: `KICKBASE_EMAIL=test@example.com KICKBASE_PASSWORD=test uv run pytest tests/test_enrichment/test_rows.py tests/store/test_corpus_store.py -q`
Expected: FAIL on the missing key.

- [ ] **Step 3: Write the migration and the writers**

`rehoboam/store/migrations/021_player_images.sql`:

```sql
-- Player panel v2 (2026-09-18): Kickbase hands us a photo path for every
-- player (`pim`) and a crest path for every club (`tim`) in payloads the
-- ingestion already fetches. `*_source` is that path as given, so a changed
-- photo is detectable; `*_path` is where our own copy lives once the sync
-- has pulled it, and stays null until then.
alter table rehoboam.players add column if not exists image_source text;
alter table rehoboam.players add column if not exists image_path text;
alter table rehoboam.teams add column if not exists crest_source text;
alter table rehoboam.teams add column if not exists crest_path text;
```

`status_row` gains `"image_source": details.get("pim")`; `record_status_daily` carries it into `players.image_source` on conflict; `upsert_teams` carries `tim` into `teams.crest_source`.

- [ ] **Step 4: Run the tests and watch them pass**

Run: `KICKBASE_EMAIL=test@example.com KICKBASE_PASSWORD=test uv run pytest tests/store/ tests/test_enrichment/ -q`
Expected: PASS.

- [ ] **Step 5: Bookkeeping, ruff, commit**

`tests/store/test_migrate.py`: applied list gains `021_player_images.sql`, version set through 21, simulated file becomes `022_simulated.sql`.

```bash
uv run ruff check rehoboam/enrichment/rows.py rehoboam/store/corpus_store.py rehoboam/store/league_store.py
git add rehoboam/ tests/
git commit -m "feat(store): record the photo and crest paths Kickbase gives us"
```

______________________________________________________________________

### Task 10: Pull the images in, and show them

**Files:**

- Create: `rehoboam/enrichment/images.py`, `tests/test_enrichment/test_images.py`, `web/src/components/PlayerPhoto.tsx`, `web/src/components/PlayerPhoto.test.tsx`
- Modify: `rehoboam/enrichment/ingest.py`, `rehoboam/config.py`, `web/src/components/PlayerPanel.tsx`, `web/src/components/PlayerList.tsx`, `web/src/app/(app)/market/page.tsx`

**Interfaces:**

- Produces: `sync_images(store, *, client, limit: int, now: float) -> dict[str, int]` returning `{"players": n, "teams": n, "skipped": n, "failed": n}`. Never raises: a failure is counted and logged, exactly as `run_mv_forecast` does.
- Produces: `PlayerPhoto({ src, name, size })` and `ClubCrest({ src, club, size })` — an `<img>` when `src` is set, otherwise initials on the position-neutral surface. Never a broken-image icon.

Measured on 2026-09-18: the original photos are ~170 KB; `sips -Z 160` brings them to 15-18 KB with no visible loss at the sizes shown, so the whole league is roughly 9 MB rather than ~100 MB. Crests are SVG, 1-24 KB, and need no resizing.

Storage is a public Supabase Storage bucket named `kickbase`, in the project the dashboard already reads. **This task needs one new Function-app setting, `SUPABASE_STORAGE_KEY`; the code must no-op cleanly when it is unset** so it can merge before the owner adds it.

- [ ] **Step 1: Write the failing test**

`tests/test_enrichment/test_images.py` — with a fake client, assert that: a player whose `image_source` is unchanged and whose `image_path` is set is skipped; a player with a new `image_source` is downloaded, resized and uploaded, and `image_path` is written; a download failure increments `failed` and leaves `image_path` null without raising; and `sync_images` returns zeros and does nothing at all when the storage key is absent.

- [ ] **Step 2: Run it and watch it fail**

Run: `KICKBASE_EMAIL=test@example.com KICKBASE_PASSWORD=test uv run pytest tests/test_enrichment/test_images.py -q`
Expected: FAIL — no module.

- [ ] **Step 3: Write `sync_images` and wire it into the ingestion**

Resize with Pillow (already an indirect dependency; add it explicitly to `pyproject.toml` if `uv run python -c "import PIL"` fails). Budget it like every other ingestion kind: stalest first, capped by `limit`, inside the existing deadline. Call it after the per-player loop in `run_ingestion`.

- [ ] **Step 4: Run the test and watch it pass**

Run: `KICKBASE_EMAIL=test@example.com KICKBASE_PASSWORD=test uv run pytest tests/test_enrichment/ -q`
Expected: PASS.

- [ ] **Step 5: Write `PlayerPhoto` and mount it**

The fallback is the point: initials on `bg-bg` inside the same circle, so a missing photo reads as a deliberate placeholder. Mount it in `PlayerPanel` (66 px), `PlayerList` (34 px) and the Market table (30 px), with `ClubCrest` at 13-16 px beside every club name.

- [ ] **Step 6: Run everything**

Run: `cd web && npx vitest run && npm run typecheck && npm run lint`, and the full Python suite from the root.
Expected: PASS (2 known Python skips).

- [ ] **Step 7: Commit**

```bash
git add rehoboam/ tests/ web/src pyproject.toml
git commit -m "feat: our own copies of the player photos and club crests"
```

______________________________________________________________________

## Controller steps, after the last task

1. Apply migrations 018-021 to production **as admin**, in order, before anything deploys.
1. Re-run the panel live check against production as the bot role; extend it to cover `playerFixtures` and the new profile columns.
1. Ask the owner to add `SUPABASE_STORAGE_KEY` to the Function app and create the public `kickbase` bucket; until then the sync no-ops and every photo renders as initials.
1. Push to PR #117 and update the body.
