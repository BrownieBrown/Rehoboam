-- Panel fix round 1 (2026-09-19): `web_player_mv` (013) read only `mv_series`,
-- which the ingestion refreshes about weekly per player -- so the panel's own
-- "Market value" tile (today's figure, from `web_players`/`player_table`) and
-- its chart could disagree by days, and the "last changes" list built from
-- that same chart series was stale for the same reason. `player_status_daily`
-- carries a `market_value` for every player every ingestion writes a status
-- row for -- daily, not weekly -- so the two sources together are a complete,
-- current series; `distinct on` picks the daily status row over the weekly
-- series point whenever both cover the same day (rank_source 1 beats 2), and
-- falls back to the series point for the days only it has. Same three
-- columns, same order, so nothing downstream (web/src/lib/queries.ts's
-- `playerMv`) needs to change.
create or replace view rehoboam.web_player_mv as
with both_sources as (
    select player_id, day, market_value, 1 as rank_source
    from rehoboam.player_status_daily
    where market_value is not null
    union all
    select player_id, (to_timestamp(snapshot_at) at time zone 'UTC')::date as day, market_value, 2
    from rehoboam.mv_series
)
select distinct on (player_id, day) player_id, day, market_value
from both_sources
order by player_id, day, rank_source;
