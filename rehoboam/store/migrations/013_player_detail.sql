-- Player detail overlay (2026-09-18): the owner wants "a window ... with all
-- his stats and market value and basically everything for all seasons we
-- have" behind a click on any player. Three views feed it -- season
-- aggregates, the raw match log (played and missed alike), and daily
-- market-value history -- so the web layer never touches
-- player_match_history or mv_series directly.

-- One row per player per season, counting only matches he was actually in
-- (status 3 came on, 5 started) -- a missed matchday must not drag his
-- averages down.
create or replace view rehoboam.web_player_seasons as
select player_id, season,
    count(*) as appearances,
    count(*) filter (where status = 5) as starts,
    sum(points) as points,
    round(avg(points)::numeric, 1) as avg_points,
    round((percentile_cont(0.5) within group (order by points))::numeric, 1) as median_points,
    max(points) as best_points,
    sum(minutes) as minutes
from rehoboam.player_match_history
where status in (3, 5)
group by player_id, season;

-- Every stored match, played or not, so a missed matchday is visible rather
-- than silently absent from the log.
create or replace view rehoboam.web_player_matches as
select h.player_id, h.season, h.day_number, h.match_date, h.points, h.minutes,
    h.status, h.is_home, t.name as opponent
from rehoboam.player_match_history h
left join rehoboam.teams t on t.team_id = h.opponent_team_id;

create or replace view rehoboam.web_player_mv as
select player_id, (to_timestamp(snapshot_at) at time zone 'UTC')::date as day, market_value
from rehoboam.mv_series;
