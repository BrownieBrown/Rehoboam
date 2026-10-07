-- matchday_lineup_results keyed by season (2026-10-07).
--
-- The table was keyed by (league_id, day_number) alone, so the writer's
-- "is day 4 already recorded?" found last season's day 4 and skipped every
-- matchday of 2026/27. The season is derived from matchday_date for the rows
-- that predate the column (July 1st is the boundary the fixtures use).

create or replace function rehoboam.season_of_date(matchday_date text)
returns text
language sql
immutable
as $$
    select case
        when extract(month from (matchday_date::timestamptz at time zone 'Europe/Berlin')) >= 7
            then extract(year from (matchday_date::timestamptz at time zone 'Europe/Berlin'))::int::text
                 || '/' ||
                 (extract(year from (matchday_date::timestamptz at time zone 'Europe/Berlin'))::int + 1)::text
        else (extract(year from (matchday_date::timestamptz at time zone 'Europe/Berlin'))::int - 1)::text
             || '/' ||
             extract(year from (matchday_date::timestamptz at time zone 'Europe/Berlin'))::int::text
    end
$$;

alter table rehoboam.matchday_lineup_results add column if not exists season text;

update rehoboam.matchday_lineup_results
   set season = rehoboam.season_of_date(matchday_date)
 where season is null;

alter table rehoboam.matchday_lineup_results alter column season set not null;

alter table rehoboam.matchday_lineup_results drop constraint if exists matchday_lineup_results_pkey;
alter table rehoboam.matchday_lineup_results
    add constraint matchday_lineup_results_pkey primary key (league_id, season, day_number);

-- Writers that predate the column (the SQLite import, raw inserts) still
-- insert without a season: derive it from the date at insert time.
create or replace function rehoboam.lineup_result_season_default()
returns trigger
language plpgsql
as $$
begin
    if new.season is null then
        new.season := rehoboam.season_of_date(new.matchday_date);
    end if;
    return new;
end
$$;

drop trigger if exists lineup_result_season_default on rehoboam.matchday_lineup_results;
create trigger lineup_result_season_default
    before insert on rehoboam.matchday_lineup_results
    for each row execute function rehoboam.lineup_result_season_default();
