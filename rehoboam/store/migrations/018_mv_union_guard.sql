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
