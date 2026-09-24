-- Where a player stands at his position, league-wide (2026-09-24).
--
-- The sell loop knew profit against cost and nothing about quality, which is
-- why 16 m of a top-six defender and 500 k of a benchwarmer were treated the
-- same way. Marco's rule: "sell more often if not a top player, and don't
-- overpay". The ranking that matches that instinct is points per appearance
-- among players who have actually played -- by predicted points this week
-- Castello Jr. (status 2, out) ranks 181 of 210 defenders; by total points 60;
-- by points per appearance about 6. The first two would sell a good player
-- because he is out this week; the third keeps him and still sells the
-- benchwarmers.
--
-- `player_ranks` is its own view over `player_table` so the big view is not
-- re-issued. `avg_points_rank_pos` ranks only players with at least one
-- appearance this season (the rest are null: unknown quality, not bad
-- quality); the code decides how many appearances a HELD player needs before
-- his rank is trusted (`SELL_RANK_MIN_APPEARANCES`). The other three ranks
-- cover everyone at the position. `position_size` is the population, so a
-- rank can be read as a share.
--
-- `web_players` appends two of the ranks (same names, same order, new ones
-- at the end) so `create or replace` is enough; `web_player_profile`'s `p.*`
-- was expanded at its own creation and does not pick them up, which is fine
-- -- the panel does not show them.

create or replace view rehoboam.player_ranks as
select player_id, position, appearances,
    case
        when coalesce(appearances, 0) >= 1
        then rank() over (
            partition by position, (coalesce(appearances, 0) >= 1)
            order by avg_points desc nulls last, player_id
        )
    end as avg_points_rank_pos,
    rank() over (partition by position order by points desc nulls last, player_id) as points_rank_pos,
    rank() over (partition by position order by predicted_ep desc nulls last, player_id) as ep_rank_pos,
    rank() over (partition by position order by market_value desc nulls last, player_id) as mv_rank_pos,
    count(*) over (partition by position) as position_size
from rehoboam.player_table;

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
    ns.status as availability,
    r.avg_points_rank_pos,
    r.ep_rank_pos
from rehoboam.player_table p
join rehoboam.player_universe u on u.player_id = p.player_id
left join listed l on l.player_id = p.player_id
left join rehoboam.web_mv_forecast fc on fc.player_id = p.player_id
left join newest_status ns on ns.player_id = p.player_id
left join rehoboam.player_ranks r on r.player_id = p.player_id;
