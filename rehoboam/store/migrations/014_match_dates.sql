-- The fixture list runs to the end of the season (2026-09-18): `player_match_history`
-- holds every matchday, including ones not yet played, as rows with a real
-- future date, status 0, points 0, minutes 0. `web_player_matches` had no
-- way to tell those apart from the past, so ordering it "newest first" by
-- season/day_number surfaced next May's fixtures ahead of the matches he
-- actually played. This appends a real timestamp, parsed only when the
-- stored text looks like one -- malformed text (or none) gives null rather
-- than raising -- so the web layer can filter to the past itself. Column
-- appended, so the running site and bot keep working.
create or replace view rehoboam.web_player_matches as
select h.player_id, h.season, h.day_number, h.match_date, h.points, h.minutes,
    h.status, h.is_home, t.name as opponent,
    case when h.match_date ~ '^\d{4}-\d{2}-\d{2}T' then h.match_date::timestamptz end as match_at
from rehoboam.player_match_history h
left join rehoboam.teams t on t.team_id = h.opponent_team_id;
