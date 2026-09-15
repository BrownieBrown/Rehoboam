-- PR E: league-wide predictions, the rows that pair them with actuals, and one report per matchday.
create table if not exists rehoboam.predictions (
    session_id   text not null,
    player_id    text not null,
    season       text not null,
    day_number   integer not null,
    kickoff      double precision not null,     -- epoch of the next kickoff at prediction time
    predicted_at double precision not null,     -- epoch
    predicted_ep double precision not null,
    p_status     jsonb not null,                -- {"1": p, "3": p, "4": p, "5": p}
    rate         double precision not null,
    prev_status  integer,
    live_status  integer,
    position     text not null,
    team_id      text,
    owned        boolean not null default false,
    listed       boolean not null default false,
    in_best_11   boolean not null default false,
    live_ep      double precision,              -- the API-path PlayerScore for owned/listed players
    data_grade   text not null,
    app          text not null,                 -- function | cli
    dry_run      boolean not null default false,
    backfill     boolean not null default false,
    primary key (session_id, player_id)
);
create index if not exists idx_predictions_matchday
    on rehoboam.predictions (season, day_number, predicted_at);

create table if not exists rehoboam.calibration_rows (
    season        text not null,
    day_number    integer not null,
    player_id     text not null,
    backfill      boolean not null default false,
    session_id    text,
    predicted_ep  double precision,
    live_ep       double precision,
    baseline_ep   double precision not null,
    actual_points integer not null,
    minutes       integer not null,
    status        integer,
    position      text not null,
    team_id       text,
    owned         boolean not null default false,
    in_best_11    boolean not null default false,
    prev_status   integer,
    live_status   integer,
    primary key (season, day_number, player_id, backfill)
);

create table if not exists rehoboam.calibration_reports (
    season                text not null,
    day_number            integer not null,
    backfill              boolean not null default false,
    computed_at           double precision not null,
    n                     integer not null,
    n_unpredicted         integer not null,
    n_stale_rows          integer not null default 0,
    mae                   double precision,
    bias                  double precision,
    spearman              double precision,
    baseline_spearman     double precision,
    spearman_played       double precision,
    top11_regret          double precision,
    baseline_top11_regret double precision,
    squad_regret          double precision,
    live_spearman         double precision,
    live_n                integer not null default 0,
    by_position           jsonb not null,
    by_status             jsonb not null,
    worst                 jsonb not null,
    gate                  jsonb,
    telegram_sent         boolean not null default false,
    primary key (season, day_number, backfill)
);
