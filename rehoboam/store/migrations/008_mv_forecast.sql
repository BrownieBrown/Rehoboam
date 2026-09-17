-- Market-value forecast (spec 2026-09-17). Kickbase's player details carry
-- `tfhmvt`, the euro change of the last daily update; the ingestion stores it
-- next to the market value it came with. Every run then scores yesterday's
-- forecasts and writes today's. Views only append columns.

alter table rehoboam.player_status_daily add column if not exists mv_change bigint;

create table if not exists rehoboam.mv_forecasts (
    player_id        text not null,
    target_day       date not null,              -- Berlin date of the ~22:00 update
    made_at          double precision not null,
    method           text not null,
    base_mv          bigint not null,            -- market value when forecast
    last_change      bigint not null,            -- tfhmvt when forecast
    predicted_change bigint not null,
    predicted_pct    double precision not null,  -- fraction of base_mv
    scored_at        double precision,
    outcome          text check (outcome in ('scored', 'unscorable')),
    actual_change    bigint,
    actual_pct       double precision,           -- fraction of base_mv
    primary key (player_id, target_day)
);
create index if not exists idx_mv_forecasts_target_day on rehoboam.mv_forecasts (target_day);

-- The forecast for the update still ahead: today's until 22:00 Berlin,
-- tomorrow's from then on (which the morning run writes, so overnight the
-- site shows no forecast rather than one for an update already made).
create or replace view rehoboam.web_mv_forecast as
select f.player_id, f.target_day, f.made_at, f.base_mv, f.predicted_change,
    round((100 * f.predicted_pct)::numeric, 2) as predicted_pct
from rehoboam.mv_forecasts f
where f.scored_at is null
  and f.target_day = ((now() at time zone 'Europe/Berlin') + interval '2 hours')::date;

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
    fc.predicted_pct as next_mv_pct
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
    p.next_mv_change, p.next_mv_pct
from rehoboam.market_listings l
join newest n on l.snapshot_at = n.at
left join rehoboam.web_players p on p.player_id = l.player_id
left join rehoboam.managers m on m.manager_id = l.seller_id;

-- One row per scored update. `directional` counts forecasts where both the
-- forecast and the real change moved; "no change" is the baseline miss.
create or replace view rehoboam.web_mv_accuracy as
select target_day,
    count(*) filter (where outcome = 'scored') as scored,
    count(*) filter (where outcome = 'unscorable') as unscorable,
    count(*) filter (
        where outcome = 'scored' and predicted_change <> 0 and actual_change <> 0
    ) as directional,
    count(*) filter (
        where outcome = 'scored' and predicted_change <> 0 and actual_change <> 0
          and (predicted_change > 0) = (actual_change > 0)
    ) as direction_hits,
    round((100 * avg(abs(actual_pct - predicted_pct)) filter (where outcome = 'scored'))::numeric, 2)
        as mae_pct,
    round((100 * avg(abs(actual_pct)) filter (where outcome = 'scored'))::numeric, 2)
        as baseline_mae_pct,
    round(avg(abs(actual_change - predicted_change)) filter (where outcome = 'scored'))
        as mae_eur,
    round(avg(abs(actual_change)) filter (where outcome = 'scored'))
        as baseline_mae_eur
from rehoboam.mv_forecasts
where scored_at is not null
group by target_day;
