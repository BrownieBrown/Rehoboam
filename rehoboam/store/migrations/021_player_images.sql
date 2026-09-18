-- Player panel v2 (2026-09-18): image plumbing (task 9). Kickbase hands us a
-- photo path for every player (`pim`, on the player-details payload
-- `status_row` already reads) and a crest path for every club (`tim`, on the
-- team-profile payload `team_row` already reads) -- no new API call is
-- needed for either. `*_source` is that path exactly as Kickbase gives it,
-- so a changed photo/crest is detectable; `*_path` is where our own copy
-- lives once a later task's sync pulls it, and stays null until then.
--
-- The store's "players" table is `player_universe` -- there is no separate
-- `players` table -- so that is where these two columns land.
alter table rehoboam.player_universe add column if not exists image_source text;
alter table rehoboam.player_universe add column if not exists image_path text;
alter table rehoboam.teams add column if not exists crest_source text;
alter table rehoboam.teams add column if not exists crest_path text;

-- Three rulings from earlier tasks in this plan land here too:
--
-- 1. A table column alone never reaches the panel. `web_player_profile` is
--    `select p.* from rehoboam.web_players p`, and Postgres expands `p.*`
--    at CREATE VIEW time -- so `player_table` and `web_players` must select
--    the new `*_path` columns by name, and `web_player_profile` must be
--    dropped and recreated (not `create or replace`, which may only append
--    columns) so its `p.*` expansion picks up whatever `web_players` now
--    has. Nothing selects from `web_player_profile` (019), so the cascade
--    drops nothing beyond the view itself.
--
-- 2. `web_players` gains `availability` (the newest `player_status_daily`
--    status) -- until now only `web_player_profile` carried it, but the
--    Players list's fitness dot needs it on every row, not just the panel's
--    single-row query. `web_player_profile` inherits it through `p.*`, so
--    its own `newest_status` CTE no longer selects `status` for it -- it
--    would otherwise be "availability" twice in one view, which Postgres
--    refuses to create.
--
-- 3. `web_market` gains `trend_24h_eur`, the same `player_status_daily.mv_change`
--    figure `web_player_profile` already shows, so Market can show euros
--    beside the existing percentage. `trend_24h_pct` is untouched -- a later
--    task switches the column over.
--
-- `player_table` and `web_players` only *append* columns below (same names,
-- same order, new ones at the end), so `create or replace view` is safe for
-- both; only `web_player_profile` needs the drop.

create or replace view rehoboam.player_table as
with cur as (
    select max(season) as season from rehoboam.player_match_history
),
prev as (
    select max(season) as season from rehoboam.player_match_history
    where season < (select season from cur)
),
mv_now as (
    select distinct on (player_id) player_id, market_value, mv_change
    from rehoboam.player_status_daily
    where market_value is not null
    order by player_id, day desc
),
hist as (
    select h.player_id,
        sum(h.points) filter (where h.season = c.season and h.status in (3, 5)) as points,
        avg(h.points) filter (where h.season = c.season and h.status in (3, 5)) as avg_points,
        percentile_cont(0.5) within group (
            order by case when h.season = c.season and h.status in (3, 5) then h.points end
        ) as median_points,
        count(*) filter (where h.season = c.season and h.status in (3, 5)) as appearances,
        count(*) filter (where h.season = c.season and h.status = 5) as starts,
        sum(h.points) filter (where h.season = p.season and h.status in (3, 5)) as points_prev,
        avg(h.points) filter (where h.season = p.season and h.status in (3, 5)) as avg_points_prev,
        count(*) filter (where h.season = p.season and h.status in (3, 5)) as appearances_prev,
        count(*) filter (where h.season = p.season and h.status = 5) as starts_prev
    from rehoboam.player_match_history h
    cross join cur c
    left join prev p on true
    group by h.player_id
),
squad_newest as (
    select manager_id, max(snapshot_at) as at
    from rehoboam.manager_squads
    group by manager_id
),
owner as (
    select s.player_id, m.name
    from rehoboam.manager_squads s
    join squad_newest n on n.manager_id = s.manager_id and n.at = s.snapshot_at
    join rehoboam.managers m on m.manager_id = s.manager_id
),
listed as (
    select player_id from rehoboam.market_listings
    where snapshot_at = (select max(snapshot_at) from rehoboam.market_listings)
),
pred as (
    select distinct on (player_id) player_id, predicted_ep, p_status
    from rehoboam.predictions
    where backfill = false
    order by player_id, predicted_at desc
),
mv_7d_status as (
    -- Daily series: nearly every player gets a row every day (status is
    -- refreshed on a 10h window), so this is a genuine 7d reference point.
    select distinct on (player_id) player_id, market_value
    from rehoboam.player_status_daily
    where market_value is not null and day <= current_date - 7
    order by player_id, day desc
),
mv_7d_series as (
    -- Preferred: mv_series is a dated series unaffected by the nightly pass
    -- reshuffling player_status_daily's rows around the ~22:00 update.
    select distinct on (player_id) player_id, market_value
    from rehoboam.mv_series
    where snapshot_at <= extract(epoch from now()) - 7 * 86400
    order by player_id, snapshot_at desc
),
base as (
    select u.player_id,
        coalesce(u.last_name, u.player_id) as name,
        t.name as team,
        u.position,
        coalesce(n.market_value, u.market_value) as market_value,
        n.mv_change,
        h.points, h.avg_points, h.median_points, h.appearances, h.starts,
        h.points_prev, h.avg_points_prev, h.appearances_prev, h.starts_prev,
        coalesce(o.name, case when l.player_id is not null then 'market' else 'Kickbase' end) as owner,
        pr.predicted_ep,
        (pr.p_status ->> '5')::double precision as p_start,
        coalesce(d7e.market_value, d7s.market_value) as mv_7d,
        u.image_path,
        t.crest_path
    from rehoboam.player_universe u
    left join rehoboam.teams t on t.team_id = u.team_id
    left join mv_now n on n.player_id = u.player_id
    left join hist h on h.player_id = u.player_id
    left join owner o on o.player_id = u.player_id
    left join listed l on l.player_id = u.player_id
    left join pred pr on pr.player_id = u.player_id
    left join mv_7d_status d7s on d7s.player_id = u.player_id
    left join mv_7d_series d7e on d7e.player_id = u.player_id
    where u.position is not null
),
fair as (
    select position,
        regr_slope(avg_points, market_value / 1e6) as slope,
        regr_intercept(avg_points, market_value / 1e6) as intercept
    from base
    -- Fitted only on players with a real sample: one loud afternoon must not
    -- tilt what a million euros is said to buy at this position.
    where avg_points is not null and market_value is not null and market_value > 0
      and appearances >= 3
    group by position
)
select b.player_id, b.name, b.team, b.position, b.market_value,
    round((100.0 * b.mv_change / nullif(b.market_value - b.mv_change, 0))::numeric, 2) as trend_24h_pct,
    round((100.0 * (b.market_value - b.mv_7d) / nullif(b.mv_7d, 0))::numeric, 2) as trend_7d_pct,
    b.points,
    round(b.avg_points::numeric, 1) as avg_points,
    round(b.median_points::numeric, 1) as median_points,
    round((b.points / nullif(b.market_value / 1e6, 0))::numeric, 2) as points_per_million,
    b.points_prev,
    round(b.avg_points_prev::numeric, 1) as avg_points_prev,
    b.appearances, b.appearances_prev, b.starts, b.starts_prev,
    b.owner, b.predicted_ep, b.p_start,
    round((b.avg_points - (f.intercept + f.slope * b.market_value / 1e6))::numeric, 1) as fair_value_gap,
    -- The market value at which this position's price-to-points line would
    -- expect his average: mv = (avg - intercept) / slope. Null without an
    -- average, without a rising line (a flat or falling one prices nothing),
    -- or when the line puts him below zero.
    case
        when f.slope > 0 and b.avg_points is not null
             and b.appearances >= 3
             and (b.avg_points - f.intercept) / f.slope > 0
        then round(((b.avg_points - f.intercept) / f.slope * 1e6)::numeric)::bigint
    end as fair_price,
    b.image_path,
    b.crest_path
from base b
left join fair f on f.position = b.position;

create or replace view rehoboam.web_players as
with listed as (
    select player_id from rehoboam.market_listings
    where snapshot_at = (select max(snapshot_at) from rehoboam.market_listings)
),
newest_status as (
    select distinct on (player_id) player_id, status
    from rehoboam.player_status_daily
    order by player_id, day desc
)
select p.player_id, p.name, p.team, u.team_id, p.position, p.market_value,
    p.trend_24h_pct, p.trend_7d_pct, p.points, p.avg_points, p.median_points,
    p.points_per_million, p.points_prev, p.avg_points_prev,
    p.appearances, p.appearances_prev, p.starts, p.starts_prev,
    p.owner, p.predicted_ep, p.p_start, p.fair_value_gap,
    (l.player_id is not null) as listed,
    fc.predicted_change as next_mv_change,
    fc.predicted_pct as next_mv_pct,
    p.fair_price,
    p.image_path,
    p.crest_path,
    ns.status as availability
from rehoboam.player_table p
join rehoboam.player_universe u on u.player_id = p.player_id
left join listed l on l.player_id = p.player_id
left join rehoboam.web_mv_forecast fc on fc.player_id = p.player_id
left join newest_status ns on ns.player_id = p.player_id;

create or replace view rehoboam.web_market as
with newest as (
    select max(snapshot_at) as at from rehoboam.market_listings
),
us as (
    select manager_id from rehoboam.managers where is_self order by manager_id limit 1
),
newest_status as (
    select distinct on (player_id) player_id, mv_change
    from rehoboam.player_status_daily
    order by player_id, day desc
)
select l.snapshot_at, l.player_id, p.name, p.team, p.position,
    l.ask, l.market_value, l.mv_trend, l.offer_count, l.our_bid,
    l.listed_at, l.expires_at, l.status, l.lineup_probability,
    coalesce(m.name, 'Kickbase') as seller,
    (l.seller_id is not null and l.seller_id = (select manager_id from us)) as is_ours,
    p.predicted_ep, p.p_start, p.fair_value_gap, p.points, p.avg_points,
    p.next_mv_change, p.next_mv_pct,
    p.fair_price,
    p.trend_24h_pct, p.points_per_million,
    ns.mv_change as trend_24h_eur
from rehoboam.market_listings l
join newest n on l.snapshot_at = n.at
left join rehoboam.web_players p on p.player_id = l.player_id
left join rehoboam.managers m on m.manager_id = l.seller_id
left join newest_status ns on ns.player_id = l.player_id;

drop view if exists rehoboam.web_player_profile cascade;
create view rehoboam.web_player_profile as
with newest_status as (
    select distinct on (player_id) player_id, mv_change, goals, assists,
        yellow_cards, red_cards, seconds_played, season_points, season_average
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
newest_season as (
    select max(season) as season from rehoboam.league_table
),
newest_day as (
    select max(l.day_number) as day_number
    from rehoboam.league_table l, newest_season s
    where l.season = s.season
),
club as (
    select l.team_id, l.place, l.points, l.goal_difference
    from rehoboam.league_table l, newest_season s, newest_day d
    where l.season = s.season and l.day_number = d.day_number
)
select p.*,
    n.mv_change as trend_24h_eur,
    p.market_value - coalesce(s7.market_value, d7.market_value) as trend_7d_eur,
    n.goals, n.assists, n.yellow_cards, n.red_cards, n.seconds_played,
    n.season_points, n.season_average,
    r.rank_overall, r.rank_position,
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
