-- PR C1: the availability history the scorer never had — one row per player per day.
create table if not exists rehoboam.player_status_daily (
    player_id          text not null,
    day                date not null,
    status             integer,          -- Kickbase st: 0 healthy, 1/2/4/256 unavailable
    lineup_probability integer,          -- Kickbase prob: 1 starter … 5 unlikely
    market_value       bigint,
    team_id            text,
    fetched_at         double precision not null,
    primary key (player_id, day)
);
create index if not exists idx_player_status_daily_day on rehoboam.player_status_daily (day);
alter table rehoboam.sweep_progress add column if not exists status_fetched_at double precision;
