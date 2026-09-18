-- Player panel v2 (2026-09-18): the panel now shows his availability, the
-- denominator under each rank, and his club's position in the table, and its
-- 7d move must agree with the one `player_table` (011) has always shown.
-- 016 read the 7d reference point from `mv_series` alone, so a player the
-- weekly sweep had not reached recently showed a dash beside a Players-table
-- number, or an 8-14 day move labelled "1 week"; the daily status series is
-- the same fallback 011 uses. `mv_7d_series`/`mv_7d_status` keep the
-- `market_value > 0` guard 018 added to `web_player_mv` -- a zero written for
-- a player with no market value must not manufacture a trend. Dropped and
-- recreated rather than replaced because the column list grows in the
-- middle, and `create or replace view` may only append columns in Postgres.
-- Nothing else in the schema selects from `web_player_profile`, so the
-- cascade drops nothing beyond the view itself.
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
