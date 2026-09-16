-- G1: what is on the market, who owns whom, the fixtures, the table, the clubs.
create table if not exists rehoboam.market_listings (
    snapshot_at        double precision not null,
    player_id          text not null,
    ask                bigint not null,
    market_value       bigint,
    mv_trend           integer,
    seller_id          text,
    offer_count        integer,
    our_bid            bigint,
    listed_at          double precision,
    expires_at         double precision,
    status             integer,
    lineup_probability integer,
    source             text not null,
    primary key (snapshot_at, player_id)
);
create index if not exists idx_market_listings_player on rehoboam.market_listings (player_id, snapshot_at);

create table if not exists rehoboam.managers (
    manager_id  text primary key,
    league_id   text not null,
    name        text not null,
    is_self     boolean not null default false,
    updated_at  double precision not null
);

create table if not exists rehoboam.manager_squads (
    snapshot_at   double precision not null,
    manager_id    text not null,
    player_id     text not null,
    market_value  bigint,
    gain_loss     bigint,
    on_market     boolean,
    source        text not null,
    primary key (snapshot_at, manager_id, player_id)
);
create index if not exists idx_manager_squads_player on rehoboam.manager_squads (player_id, snapshot_at);
create index if not exists idx_manager_squads_manager on rehoboam.manager_squads (manager_id, snapshot_at);

create table if not exists rehoboam.fixtures (
    match_id      text primary key,
    season        text not null,
    day_number    integer not null,
    kickoff       double precision not null,
    home_team_id  text not null,
    away_team_id  text not null,
    home_goals    integer,
    away_goals    integer,
    status        integer not null,
    updated_at    double precision not null
);
create index if not exists idx_fixtures_day on rehoboam.fixtures (season, day_number);

create table if not exists rehoboam.league_table (
    season          text not null,
    day_number      integer not null,
    team_id         text not null,
    place           integer not null,
    previous_place  integer,
    points          integer,
    played          integer,
    goal_difference integer,
    updated_at      double precision not null,
    primary key (season, day_number, team_id)
);

create table if not exists rehoboam.teams (
    team_id     text primary key,
    name        text not null,
    short_name  text,
    updated_at  double precision not null
);
