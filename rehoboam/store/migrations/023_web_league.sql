-- The League tab (2026-09-21): the managers we play against, from what the
-- store already holds. Views only -- no table, no column, no new API call.
-- Plain SQL like 007; `refresh_grants` gives the bot role select on them.
--
-- `league_rank_history` carries no season column and still holds last
-- season's backfill (REH-39), so "this season" is every snapshot taken since
-- the newest season's first kickoff. Measured on the live store: last
-- season's rows end 2026-08-21, this season's fixtures start 2026-08-28.
--
-- A manager who left the league stays in `league_rank_history` but not in
-- `managers`; every view below starts from `managers`, so he never appears.

-- 1. One row per manager: the newest standing, his squad, his dealing.
create or replace view rehoboam.web_managers as
with season_start as (
    select min(kickoff) as at from rehoboam.fixtures
    where season = (select max(season) from rehoboam.fixtures)
),
standing as (
    select distinct on (r.manager_id) r.manager_id, r.day_number, r.rank_overall,
        r.total_points, r.rank_matchday, r.matchday_points, r.team_value
    from rehoboam.league_rank_history r, season_start s
    where r.snapshot_at >= s.at
    order by r.manager_id, r.snapshot_at desc
),
profile as (
    select distinct on (manager_id) manager_id, transfer_pnl, matchday_wins
    from rehoboam.manager_profile_history
    order by manager_id, snapshot_at desc
),
squad_ranked as (
    select o.manager_id, o.market_value, o.predicted_ep, o.on_market,
        row_number() over (
            partition by o.manager_id order by o.predicted_ep desc nulls last
        ) as ep_rank
    from rehoboam.web_ownership o
),
squad as (
    select manager_id,
        count(*)::int as squad_size,
        sum(market_value)::bigint as squad_value,
        -- The eleven highest expected scores he owns, whatever their
        -- positions: an upper bound on his next matchday, not his lineup.
        round(sum(predicted_ep) filter (where ep_rank <= 11)::numeric, 1) as top11_ep,
        count(*) filter (where on_market)::int as on_market
    from squad_ranked
    group by manager_id
),
dealing as (
    select manager_id,
        count(*) filter (where transfer_type = 1)::int as buys_7d,
        count(*) filter (where transfer_type = 2)::int as sells_7d
    from rehoboam.manager_transfers
    where transfer_dt::timestamptz >= now() - interval '7 days'
    group by manager_id
)
select m.manager_id, m.name, m.is_self,
    st.day_number, st.rank_overall, st.total_points,
    st.rank_matchday, st.matchday_points,
    (max(st.total_points) over () - st.total_points) as points_behind_leader,
    st.team_value,
    sq.squad_size, sq.squad_value, sq.top11_ep, sq.on_market,
    p.transfer_pnl, p.matchday_wins,
    coalesce(d.buys_7d, 0) as buys_7d,
    coalesce(d.sells_7d, 0) as sells_7d
from rehoboam.managers m
left join standing st on st.manager_id = m.manager_id
left join squad sq on sq.manager_id = m.manager_id
left join profile p on p.manager_id = m.manager_id
left join dealing d on d.manager_id = m.manager_id;

-- 2. One row per manager per matchday of this season: the newest snapshot
--    taken for that day, so a day re-read after late corrections shows the
--    corrected figures.
create or replace view rehoboam.web_manager_matchdays as
with season_start as (
    select min(kickoff) as at from rehoboam.fixtures
    where season = (select max(season) from rehoboam.fixtures)
)
select distinct on (r.manager_id, r.day_number)
    r.manager_id, r.day_number, r.matchday_points, r.rank_matchday,
    r.total_points, r.rank_overall
from rehoboam.league_rank_history r
join rehoboam.managers m on m.manager_id = r.manager_id, season_start s
where r.snapshot_at >= s.at
order by r.manager_id, r.day_number, r.snapshot_at desc;

-- 3. Every transfer a current manager has made, newest first by reader's
--    `order by`. Kickbase's `tty`: 1 is a buy, 2 a sell (confirmed
--    2026-05-09); the price is always positive, the type carries direction.
create or replace view rehoboam.web_manager_transfers as
select t.manager_id, t.transfer_dt::timestamptz as transfer_at,
    t.player_id, coalesce(p.name, t.player_name) as player_name,
    p.team, p.position,
    case t.transfer_type when 1 then 'buy' when 2 then 'sell' end as kind,
    t.transfer_price as price,
    p.market_value as market_value_now
from rehoboam.manager_transfers t
join rehoboam.managers m on m.manager_id = t.manager_id
left join rehoboam.web_players p on p.player_id = t.player_id;

-- 4. `web_ownership` gains what the manager panel's squad table shows
--    beside the name. Appended only, so `create or replace` is enough and
--    its two existing readers (`selfName`, `managers`) are untouched.
create or replace view rehoboam.web_ownership as
with newest as (
    select manager_id, max(snapshot_at) as at
    from rehoboam.manager_squads
    group by manager_id
)
select s.manager_id, m.name as manager, m.is_self, s.player_id,
    p.name as player_name, p.team, p.position,
    s.market_value, s.gain_loss, s.on_market,
    p.predicted_ep, p.p_start, s.snapshot_at,
    p.points, p.avg_points, p.availability, p.image_path, p.crest_path
from rehoboam.manager_squads s
join newest n on n.manager_id = s.manager_id and n.at = s.snapshot_at
join rehoboam.managers m on m.manager_id = s.manager_id
left join rehoboam.web_players p on p.player_id = s.player_id;
