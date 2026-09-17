-- The dashboard's contract: six read-only views. The site reads nothing else.
-- Plain SQL, no functions, no security definer; `refresh_grants` in the
-- migrate runner grants the bot role on views as well as tables.

-- 1. Every player, Base XI's columns plus ours, with a real "listed" flag.
--    `player_table` already resolves the club name and ownership per manager.
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
    (l.player_id is not null) as listed
from rehoboam.player_table p
join rehoboam.player_universe u on u.player_id = p.player_id
left join listed l on l.player_id = p.player_id;

-- 2. Our squad as the newest real session saw it, with the eleven it chose.
--    A dry run (a local `status`) must never redefine "the lineup", so the
--    session is the newest non-dry-run row from the trading app. Left join
--    to `predictions`: it is written best-effort in step 2a, after
--    `session_facts` already has a budget and formation, so a session whose
--    roster write failed or wrote zero owned rows must still surface as one
--    row (`player_id is null`), not an empty result the page can't tell
--    apart from "no session has ever run".
create or replace view rehoboam.web_squad as
with latest as (
    select session_id, legal_formation, budget, sellable_value, next_kickoff,
        started_at, cost_basis_missing
    from rehoboam.session_facts
    where app = 'function' and dry_run = 0
    order by started_at desc
    limit 1
)
select l.session_id, l.legal_formation, l.budget, l.sellable_value,
    l.next_kickoff, l.started_at as session_started_at, l.cost_basis_missing,
    pr.player_id, p.name, p.team, pr.position, p.market_value,
    p.points, p.avg_points, p.owner,
    pr.predicted_ep, pr.live_ep, pr.in_best_11,
    (pr.p_status ->> '5')::double precision as p_start,
    tp.buy_price as cost_basis,
    case when tp.buy_price is not null then p.market_value - tp.buy_price end as gain_loss
from latest l
left join rehoboam.predictions pr on pr.session_id = l.session_id and pr.owned
left join rehoboam.web_players p on p.player_id = pr.player_id
left join rehoboam.tracked_purchases tp on tp.player_id = pr.player_id;

-- 3. Every run of either app, with its integrity rules and the ingest
--    counters flattened out of `extra`. Unordered and unlimited on purpose:
--    the caller orders by started_at and takes what it needs.
create or replace view rehoboam.web_session_summary as
select f.session_id, f.app, f.mode, f.dry_run, f.started_at, f.duration_s, f.phase,
    f.next_kickoff, f.legal_formation, f.budget, f.sellable_value,
    f.cost_basis_missing, f.predictions_written, f.lineup_result,
    f.errors, f.error_text,
    coalesce(i.rules, array[]::text[]) as integrity_rules,
    coalesce(i.rule_details, '{}'::jsonb) as integrity_details,
    (f.extra -> 'league_state' ->> 'squads')::int as league_state_squads,
    (f.extra ->> 'requests')::int as requests,
    (f.extra ->> 'failed')::int as failed,
    (f.extra ->> 'status_written')::int as status_written,
    (f.extra ->> 'performance_fetched')::int as performance_fetched,
    (f.extra ->> 'transfers_fetched')::int as transfers_fetched,
    (f.extra ->> 'universe_size')::int as universe_size,
    f.extra ->> 'stopped_by' as stopped_by,
    (f.extra -> 'league' ->> 'failed')::int as league_failed,
    (f.extra -> 'league' ->> 'teams')::int as league_teams,
    (f.extra -> 'league' ->> 'fixtures')::int as league_fixtures,
    (f.extra -> 'league' ->> 'listings')::int as league_listings,
    (f.extra -> 'league' ->> 'squads')::int as league_squads,
    f.extra -> 'calibration' -> 'settled' as calibration_settled,
    f.extra -> 'calibration' -> 'reported' as calibration_reported
from rehoboam.session_facts f
left join (
    select session_id,
        array_agg(distinct rule order by rule) as rules,
        jsonb_object_agg(rule, detail) as rule_details
    from rehoboam.integrity_failures
    group by session_id
) i on i.session_id = f.session_id;

-- 4. The newest listing snapshot, priced against what we think a player scores.
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
    p.predicted_ep, p.p_start, p.fair_value_gap, p.points, p.avg_points
from rehoboam.market_listings l
join newest n on l.snapshot_at = n.at
left join rehoboam.web_players p on p.player_id = l.player_id
left join rehoboam.managers m on m.manager_id = l.seller_id;

-- 5. Who owns what, from each manager's OWN newest snapshot — never the
--    global newest, or one partial write blanks every other manager.
create or replace view rehoboam.web_ownership as
with newest as (
    select manager_id, max(snapshot_at) as at
    from rehoboam.manager_squads
    group by manager_id
)
select s.manager_id, m.name as manager, m.is_self, s.player_id,
    p.name as player_name, p.team, p.position,
    s.market_value, s.gain_loss, s.on_market,
    p.predicted_ep, p.p_start, s.snapshot_at
from rehoboam.manager_squads s
join newest n on n.manager_id = s.manager_id and n.at = s.snapshot_at
join rehoboam.managers m on m.manager_id = s.manager_id
left join rehoboam.web_players p on p.player_id = s.player_id;

-- 6. Every calibration report, live and backfill, including the ones that
--    settled empty (n = 0, gate null) — the page says "not yet reported".
create or replace view rehoboam.web_calibration as
select season, day_number, backfill, computed_at, n, n_unpredicted, n_stale_rows,
    mae, bias, spearman, baseline_spearman, spearman_played,
    top11_regret, baseline_top11_regret, squad_regret,
    live_spearman, live_n, by_position, by_status, worst, gate, telegram_sent
from rehoboam.calibration_reports;
