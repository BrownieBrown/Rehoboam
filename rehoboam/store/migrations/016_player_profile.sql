-- Player panel (2026-09-18): one row per player carrying everything the
-- panel's header needs -- season stats, the market-value move in euros
-- rather than percent, and his rank -- so the page runs one query, not six.
-- Built on `web_players` (never re-derives its columns): the newest
-- `player_status_daily` row supplies the season stats and the 24h move
-- Kickbase itself reports (`mv_change`); the newest `mv_series` point at or
-- before seven days ago supplies the 7d move in euros; `rank()` over players
-- who have actually played gives ties the same rank and leaves the rest
-- null, via a CTE + left join so a player with no points still appears
-- exactly once.
create or replace view rehoboam.web_player_profile as
with newest_status as (
    select distinct on (player_id) player_id, mv_change, goals, assists,
        yellow_cards, red_cards, seconds_played, season_points, season_average
    from rehoboam.player_status_daily
    order by player_id, day desc
),
mv_7d as (
    select distinct on (player_id) player_id, market_value
    from rehoboam.mv_series
    where snapshot_at <= extract(epoch from now()) - 7 * 86400
    order by player_id, snapshot_at desc
),
ranked as (
    select player_id,
        rank() over (order by points desc nulls last) as rank_overall,
        rank() over (partition by position order by points desc nulls last) as rank_position
    from rehoboam.web_players
    where points is not null
)
select p.*,
    n.mv_change as trend_24h_eur,
    p.market_value - m7.market_value as trend_7d_eur,
    n.goals, n.assists, n.yellow_cards, n.red_cards, n.seconds_played,
    n.season_points, n.season_average,
    r.rank_overall, r.rank_position
from rehoboam.web_players p
left join newest_status n on n.player_id = p.player_id
left join mv_7d m7 on m7.player_id = p.player_id
left join ranked r on r.player_id = p.player_id;
