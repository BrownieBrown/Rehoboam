-- G1: Base XI's player table as a view over the store, plus our three columns.
create or replace view rehoboam.player_table as
with cur as (
    select max(season) as season from rehoboam.player_match_history
),
prev as (
    select max(season) as season from rehoboam.player_match_history
    where season < (select season from cur)
),
mv_now as (
    select distinct on (player_id) player_id, market_value
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
owner as (
    select s.player_id, m.name
    from rehoboam.manager_squads s
    join rehoboam.managers m on m.manager_id = s.manager_id
    where s.snapshot_at = (select max(snapshot_at) from rehoboam.manager_squads)
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
mv_1d as (
    select distinct on (player_id) player_id, market_value
    from rehoboam.mv_series
    where snapshot_at <= extract(epoch from now()) - 86400
    order by player_id, snapshot_at desc
),
mv_7d as (
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
        h.points, h.avg_points, h.median_points, h.appearances, h.starts,
        h.points_prev, h.avg_points_prev, h.appearances_prev, h.starts_prev,
        coalesce(o.name, case when l.player_id is not null then 'market' else 'Kickbase' end) as owner,
        pr.predicted_ep,
        (pr.p_status ->> '5')::double precision as p_start,
        d1.market_value as mv_1d,
        d7.market_value as mv_7d
    from rehoboam.player_universe u
    left join rehoboam.teams t on t.team_id = u.team_id
    left join mv_now n on n.player_id = u.player_id
    left join hist h on h.player_id = u.player_id
    left join owner o on o.player_id = u.player_id
    left join listed l on l.player_id = u.player_id
    left join pred pr on pr.player_id = u.player_id
    left join mv_1d d1 on d1.player_id = u.player_id
    left join mv_7d d7 on d7.player_id = u.player_id
    where u.position is not null
),
fair as (
    select position,
        regr_slope(avg_points, market_value / 1e6) as slope,
        regr_intercept(avg_points, market_value / 1e6) as intercept
    from base
    where avg_points is not null and market_value is not null and market_value > 0
    group by position
)
select b.player_id, b.name, b.team, b.position, b.market_value,
    round((100.0 * (b.market_value - b.mv_1d) / nullif(b.mv_1d, 0))::numeric, 2) as trend_24h_pct,
    round((100.0 * (b.market_value - b.mv_7d) / nullif(b.mv_7d, 0))::numeric, 2) as trend_7d_pct,
    b.points,
    round(b.avg_points::numeric, 1) as avg_points,
    round(b.median_points::numeric, 1) as median_points,
    round((b.points / nullif(b.market_value / 1e6, 0))::numeric, 2) as points_per_million,
    b.points_prev,
    round(b.avg_points_prev::numeric, 1) as avg_points_prev,
    b.appearances, b.appearances_prev, b.starts, b.starts_prev,
    b.owner, b.predicted_ep, b.p_start,
    round((b.avg_points - (f.intercept + f.slope * b.market_value / 1e6))::numeric, 1) as fair_value_gap
from base b
left join fair f on f.position = b.position;
