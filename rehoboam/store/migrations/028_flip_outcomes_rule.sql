-- flip_outcomes: the intent the player was bought with and the rule that
-- sold him (2026-10-07), so a win rate per exit rule can be read off the
-- table instead of guessed. Rows that predate the columns stay null.
alter table rehoboam.flip_outcomes add column if not exists intent text;
alter table rehoboam.flip_outcomes add column if not exists exit_rule text;
