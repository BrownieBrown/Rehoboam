-- Player panel v2 (2026-09-18): his next matches, so the panel can say who
-- he plays, home or away, when, and how good they are. The opponent's place
-- comes from the newest stored matchday of the league table -- the same
-- reference `web_player_profile` (019) uses for his own club -- and is null
-- before the table has any rows. Only fixtures that have not kicked off yet.
--
-- `league_table` is append-only across seasons and never purged, so
-- `max(day_number)` with no season filter would resolve to a *previous*
-- season's higher final day number in the opening matchdays of a new one,
-- pricing the opponent's place from the wrong season -- the same bug 019
-- fixed for `web_player_profile`'s `club` CTE. `newest_season` resolves the
-- season first, the same two-step pattern.
create or replace view rehoboam.web_player_fixtures as
with newest_season as (
    select max(season) as season from rehoboam.league_table
),
newest_day as (
    select max(l.day_number) as day_number
    from rehoboam.league_table l, newest_season s
    where l.season = s.season
),
places as (
    select l.team_id, l.place
    from rehoboam.league_table l, newest_season s, newest_day d
    where l.season = s.season and l.day_number = d.day_number
)
select p.player_id,
    f.season,
    f.day_number,
    f.kickoff,
    (f.home_team_id = p.team_id) as is_home,
    case when f.home_team_id = p.team_id then away.name else home.name end as opponent,
    case when f.home_team_id = p.team_id then ap.place else hp.place end as opponent_place
from rehoboam.web_players p
join rehoboam.fixtures f
  on f.home_team_id = p.team_id or f.away_team_id = p.team_id
left join rehoboam.teams home on home.team_id = f.home_team_id
left join rehoboam.teams away on away.team_id = f.away_team_id
left join places hp on hp.team_id = f.home_team_id
left join places ap on ap.team_id = f.away_team_id
where p.team_id is not null
  and f.kickoff > extract(epoch from now());
