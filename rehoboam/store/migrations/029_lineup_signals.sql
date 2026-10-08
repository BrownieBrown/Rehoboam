-- Lineup signals (2026-10-08): what the scorer reads about who will be on the
-- pitch, and the two outside sources that cross-check it. Grants come from
-- `refresh_grants` in the migrate runner.

-- 1. A prediction records the Kickbase lineup-probability code it saw
--    (1 starter … 5 unlikely) and whether ligainsider's predicted eleven named
--    the player (null = no prediction for his club). The calibration rows copy
--    both, so the per-code bias can be read off the report's rows.
alter table rehoboam.predictions add column if not exists lineup_probability integer;
alter table rehoboam.predictions add column if not exists predicted_xi boolean;
alter table rehoboam.calibration_rows add column if not exists lineup_probability integer;
alter table rehoboam.calibration_rows add column if not exists predicted_xi boolean;

-- 2. ligainsider's "Voraussichtliche Aufstellung" per club and matchday. One
--    row per named player: the eleven (`in_xi`) and the alternatives the page
--    lists beside them. Re-fetched Thursday and Friday; the newest wins.
create table if not exists rehoboam.predicted_lineups (
    source            text not null,
    season            text not null,
    day_number        integer not null,
    team_id           text not null,
    player_name       text not null,
    player_id         text,
    in_xi             boolean not null,
    slot              integer,
    source_updated_at text,
    fetched_at        double precision not null,
    primary key (source, season, day_number, team_id, player_name)
);
create index if not exists predicted_lineups_player_idx
    on rehoboam.predicted_lineups (season, day_number, player_id);

-- 3. Understat per-player season totals (xG, xA, shots, key passes …), one
--    snapshot per day fetched so per-week deltas can be read later. Display
--    and fitting data; nothing in trading reads it yet.
create table if not exists rehoboam.understat_player_stats (
    season        text not null,
    understat_id  text not null,
    day           date not null,
    player_name   text not null,
    team_title    text not null,
    player_id     text,
    position      text,
    games         integer,
    time_played   integer,
    goals         integer,
    assists       integer,
    shots         integer,
    key_passes    integer,
    npg           integer,
    yellow_cards  integer,
    red_cards     integer,
    xg            double precision,
    xa            double precision,
    npxg          double precision,
    xg_chain      double precision,
    xg_buildup    double precision,
    fetched_at    double precision not null,
    primary key (season, understat_id, day)
);
create index if not exists understat_player_stats_player_idx
    on rehoboam.understat_player_stats (season, player_id, day);
