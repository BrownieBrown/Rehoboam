-- Fair value from what we EXPECT, not from what he averaged (2026-09-21).
--
-- Migrations 009/012 read a player's season average back through his
-- position's points-on-price line. Measured on the live store after five
-- matchdays, that put 50 of 215 priced players more than 2x away from their
-- market value -- Querfeld, three appearances averaging 145, came out at
-- 24,750,112 against a falling market value of 11,477,966. Three things were
-- wrong, and this fixes each:
--
-- 1. The input. A three-match average is not a level; the market does not
--    believe it and neither does our own scorer (it expects 86 from him, not
--    145). Both columns now read `predicted_ep`, the same number every other
--    decision reads. Put through the OLD line, 86 already gives 11.7 m.
-- 2. The direction. A line fitted as points-on-price and then inverted
--    overstates every extreme by 1/r^2 (r^2 0.37-0.61 here). `fair_price`
--    now comes from its own fit, price-on-points; `fair_value_gap` keeps the
--    points-on-price fit, which is the right way round for ITS question.
-- 3. The population. Fitted over the whole universe, bench players at
--    500,000 and injured stars (high price, no points this week) drag the
--    line and the median miss is 51%. Both fits and both columns are now
--    limited to likely starters, `p_start >= 0.5`: median miss 37-40%
--    outfield, 16% for goalkeepers, and no runaway at the top. A log fit was
--    measured too and rejected -- it priced Olise at 203.7 m.
--
-- 4. The cheap end. A straight line's intercept swamps a small price: under
--    5 m the median miss was 102-378%, against 26-34% from 5 m up (and no
--    2x miss at all above 15 m). So `fair_price` is quoted from 5 m up only.
--    The FIT still includes the cheap starters -- refitting on 5 m+ alone
--    was measured and made the 5-15 m band worse (42% against 34%), because
--    they anchor the low end of the line. `fair_value_gap` is in points, not
--    a ratio, so it keeps covering every likely starter.
--
-- A straight line can still cross zero for the weakest starters (3 of 243
-- measured); those get null, never a negative price.
--
-- `player_table`'s column list is identical -- same names, order and types --
-- so `create or replace` is enough and every view that reads it
-- (`web_players`, `web_market`, `web_player_profile`) is untouched.

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
        -- Points his price usually buys (for the gap, in points).
        regr_slope(predicted_ep, market_value / 1e6) as slope,
        regr_intercept(predicted_ep, market_value / 1e6) as intercept,
        -- Price his expected points usually cost (for the price, in euros).
        -- Its own fit, NOT the line above inverted.
        regr_slope(market_value / 1e6, predicted_ep) as price_slope,
        regr_intercept(market_value / 1e6, predicted_ep) as price_intercept
    from base
    where predicted_ep is not null and market_value is not null and market_value > 0
      and p_start >= 0.5
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
    case
        when b.p_start >= 0.5
        then round((b.predicted_ep - (f.intercept + f.slope * b.market_value / 1e6))::numeric, 1)
    end as fair_value_gap,
    -- What the market usually pays, at his position, for a likely starter we
    -- expect this many points from. Null for anyone not likely to start,
    -- anyone under 5 m (see 4 above), and where the line falls to zero or below.
    case
        when b.p_start >= 0.5 and b.predicted_ep is not null
             and b.market_value >= 5000000
             and f.price_intercept + f.price_slope * b.predicted_ep > 0
        then round(((f.price_intercept + f.price_slope * b.predicted_ep) * 1e6)::numeric)::bigint
    end as fair_price,
    b.image_path,
    b.crest_path
from base b
left join fair f on f.position = b.position;
