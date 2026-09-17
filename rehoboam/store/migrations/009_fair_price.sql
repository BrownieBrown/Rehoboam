-- A fair PRICE in euros beside the fair-value gap in points (2026-09-17).
-- `fair_value_gap` answers "how many points above or below his price does he
-- score"; this answers "what would his scoring be worth at his position's
-- going rate". Same per-position regression, read the other way round.
-- Views only append columns, so the running site and bot keep working.

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
mv_1d_status as (
    -- Daily series: nearly every player gets a row every day (status is
    -- refreshed on a 10h window), so this is a genuine 24h reference point.
    select distinct on (player_id) player_id, market_value
    from rehoboam.player_status_daily
    where market_value is not null and day <= current_date - 1
    order by player_id, day desc
),
mv_7d_status as (
    select distinct on (player_id) player_id, market_value
    from rehoboam.player_status_daily
    where market_value is not null and day <= current_date - 7
    order by player_id, day desc
),
mv_1d_series as (
    -- Fallback only: mv_series refreshes weekly per player, so its newest
    -- point before the cutoff can be several days older than the label says.
    select distinct on (player_id) player_id, market_value
    from rehoboam.mv_series
    where snapshot_at <= extract(epoch from now()) - 86400
    order by player_id, snapshot_at desc
),
mv_7d_series as (
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
        coalesce(d1s.market_value, d1e.market_value) as mv_1d,
        coalesce(d7s.market_value, d7e.market_value) as mv_7d
    from rehoboam.player_universe u
    left join rehoboam.teams t on t.team_id = u.team_id
    left join mv_now n on n.player_id = u.player_id
    left join hist h on h.player_id = u.player_id
    left join owner o on o.player_id = u.player_id
    left join listed l on l.player_id = u.player_id
    left join pred pr on pr.player_id = u.player_id
    left join mv_1d_status d1s on d1s.player_id = u.player_id
    left join mv_1d_series d1e on d1e.player_id = u.player_id
    left join mv_7d_status d7s on d7s.player_id = u.player_id
    left join mv_7d_series d7e on d7e.player_id = u.player_id
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
    round((b.avg_points - (f.intercept + f.slope * b.market_value / 1e6))::numeric, 1) as fair_value_gap,
    -- The market value at which this position's price-to-points line would
    -- expect his average: mv = (avg - intercept) / slope. Null without an
    -- average, without a rising line (a flat or falling one prices nothing),
    -- or when the line puts him below zero.
    case
        when f.slope > 0 and b.avg_points is not null
             and (b.avg_points - f.intercept) / f.slope > 0
        then round(((b.avg_points - f.intercept) / f.slope * 1e6)::numeric)::bigint
    end as fair_price
from base b
left join fair f on f.position = b.position;

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
    fc.predicted_pct as next_mv_pct,
    p.fair_price
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
    p.next_mv_change, p.next_mv_pct,
    p.fair_price
from rehoboam.market_listings l
join newest n on l.snapshot_at = n.at
left join rehoboam.web_players p on p.player_id = l.player_id
left join rehoboam.managers m on m.manager_id = l.seller_id;
