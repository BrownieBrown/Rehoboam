-- The Market page reads the same two numbers the Players page shows (2026-09-17):
-- yesterday's move and points per million. They already exist on `web_players`;
-- this appends them to `web_market`, which is the only view the page reads.
-- Columns are appended, so the running site and bot keep working.

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
    p.fair_price,
    p.trend_24h_pct, p.points_per_million
from rehoboam.market_listings l
join newest n on l.snapshot_at = n.at
left join rehoboam.web_players p on p.player_id = l.player_id
left join rehoboam.managers m on m.manager_id = l.seller_id;
