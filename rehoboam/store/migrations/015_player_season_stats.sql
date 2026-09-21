-- Player panel (2026-09-18): the owner wants what Kickbase's own player card
-- shows -- goals, assists, cards, minutes and season totals -- alongside the
-- status/MV columns already fetched daily. These seven fields come from the
-- same player-details response `player_status_daily` already stores (`g`,
-- `a`, `y`, `r`, `sec`, `tp`, `ap`, probed live 2026-09-18), so they land on
-- the same row rather than a new table. All null for a player with no
-- appearances this season.
alter table rehoboam.player_status_daily add column if not exists goals integer;
alter table rehoboam.player_status_daily add column if not exists assists integer;
alter table rehoboam.player_status_daily add column if not exists yellow_cards integer;
alter table rehoboam.player_status_daily add column if not exists red_cards integer;
alter table rehoboam.player_status_daily add column if not exists seconds_played integer;
alter table rehoboam.player_status_daily add column if not exists season_points integer;
alter table rehoboam.player_status_daily add column if not exists season_average double precision;
