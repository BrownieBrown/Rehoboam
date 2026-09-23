-- What it took to win, and what the buy was worth (2026-09-23). Views only --
-- no table, no column, no new API call. Plain SQL like 007 and 023;
-- `refresh_grants` gives the bot role select on them.
--
-- Rival offers are never visible on Kickbase. What IS visible, afterwards, is
-- every transfer: buyer, price, date (`manager_transfers`, five seasons back
-- to 2021-07). Priced against the market value in force that day, each buy
-- says what it took to win that listing -- the evidence `derive-ceilings`
-- needs, seventy times the size of the auctions we entered ourselves.
--
-- The market value in force at a Berlin time t is the daily series point
-- dated (t - 22h): Kickbase publishes the new value at 22:00 Berlin, so a
-- transfer at 10:00 was priced against yesterday's point and one at 22:30
-- against today's. Verified against our own bids' asking prices (Wolfe,
-- Regeer, 2026-09-07..09). `mv_series` points sit at 00:00 UTC of their day.
--
-- `mv_series` starts 2025-07-29 and Kickbase's own history stops at 365
-- days, so `premium_pct` is null for older buys; the points and the resale
-- (`transfer_outcomes`) still reach back to 2021.

-- 1. Every buy, priced against the market value in force.
create or replace view rehoboam.transfer_premiums as
with buys as (
    select league_id,
           manager_id,
           player_id,
           player_name,
           transfer_dt::timestamptz as transferred_at,
           transfer_price           as price
    from rehoboam.manager_transfers
    where transfer_type = 1 and transfer_price > 0
),
aligned as (
    select b.*,
           ((b.transferred_at at time zone 'Europe/Berlin') - interval '22 hours')::date as mv_day,
           (b.transferred_at at time zone 'Europe/Berlin')                              as berlin
    from buys b
)
select a.league_id,
       a.manager_id,
       m.name                                as manager_name,
       coalesce(m.is_self, false)            as is_self,
       a.player_id,
       a.player_name,
       a.transferred_at,
       a.price,
       a.mv_day,
       s.market_value                        as mv_at_transfer,
       case when s.market_value > 0
            then round(100.0 * (a.price - s.market_value) / s.market_value, 2)
       end                                   as premium_pct,
       case when extract(month from a.berlin) >= 7
            then extract(year from a.berlin)::int || '/' || (extract(year from a.berlin)::int + 1)
            else (extract(year from a.berlin)::int - 1) || '/' || extract(year from a.berlin)::int
       end                                   as season
from aligned a
left join rehoboam.managers m
       on m.manager_id = a.manager_id and m.league_id = a.league_id
left join rehoboam.mv_series s
       on s.player_id = a.player_id
      and (to_timestamp(s.snapshot_at) at time zone 'UTC')::date = a.mv_day;

-- 2. What each buy was worth: the same manager's next sale of the player,
--    and the player's points over the five matches that followed the buy.
--    Unplayed fixtures are stored as zero-point placeholders, so only
--    matches already kicked off count.
create or replace view rehoboam.transfer_outcomes as
select p.*,
       s.sold_at,
       s.sell_price,
       case when s.sell_price is not null then s.sell_price - p.price end          as realised_profit,
       case when s.sold_at is not null
            then round((extract(epoch from (s.sold_at - p.transferred_at)) / 86400.0)::numeric, 1)
       end                                                                          as days_held,
       coalesce(h.matches_after, 0)                                                 as matches_after,
       h.points_after,
       h.avg_points_after
from rehoboam.transfer_premiums p
left join lateral (
    select t.transfer_dt::timestamptz as sold_at, t.transfer_price as sell_price
    from rehoboam.manager_transfers t
    where t.league_id = p.league_id
      and t.manager_id = p.manager_id
      and t.player_id = p.player_id
      and t.transfer_type = 2
      and t.transfer_dt::timestamptz > p.transferred_at
    order by t.transfer_dt
    limit 1
) s on true
left join lateral (
    select count(*)                    as matches_after,
           sum(x.points)               as points_after,
           round(avg(x.points), 1)     as avg_points_after
    from (
        select h.points
        from rehoboam.player_match_history h
        where h.player_id = p.player_id
          and h.match_date is not null
          and h.match_date::timestamptz > p.transferred_at
          and h.match_date::timestamptz <= now()
        order by h.match_date
        limit 5
    ) x
) h on true;
