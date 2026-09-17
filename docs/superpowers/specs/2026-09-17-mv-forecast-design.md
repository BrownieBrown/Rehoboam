# Market-value forecast — design

Status: approved in chat 2026-09-17 ("go ahead and do it"). Branch
`marcobraun2013/mv-forecast`, stacked on the dashboard branch (PR #115).

## Why

The owner asked for a market-value (MV) prognosis on the dashboard, like
Base XI's, as "the most helpful" addition. Kickbase updates every market
value once a day at about 22:00 Berlin time, driven mostly by how often
managers across Kickbase buy and sell a player. We cannot see that demand,
but the store holds a year of daily values for every player
(`mv_series`, 191,890 rows, 588 players, strictly one value per day).

Market value helps trading and budget, not matchday points directly, and
the bot trades in `lineup_only` mode today. The forecast is a display and a
measurement; no trading decision reads it in this design.

## What the data says

Measured on `mv_series` (read-only, 2026-09-17), with daily moves capped at
±20 % for the fit:

| Rule            | Direction right | Mean absolute miss                                |
| --------------- | --------------- | ------------------------------------------------- |
| No change       | —               | 1.96 pp (last season), 3.11 pp (since 2026-08-01) |
| 0.9 × last move | ~95 %           | 0.56 pp, 0.79 pp                                  |

- Tomorrow's move ≈ 0.89–0.92 × today's move (r² 0.79–0.84).
- After a rise of more than 1 %, the next update rose 97 % of the time; after
  a fall of more than 1 %, it fell 97 % of the time.
- The rule beats "no change" in every size band except moves of 20 % or
  more, where the uncapped rule is worse (29.97 pp against 22.44 pp) and a
  capped one is better (14.84 pp at a 10 % cap).
- Adding the move from two days ago made every band worse.
- What the rule cannot see is a turn (an injury, a big match). That is the
  job of a later model, which this design makes measurable.

Backtest (2026-09-17, `backtest-mv` code over production): run against the
store's actual daily values, over the full history (190,714 forecasts) and
over this season alone (20,403 forecasts), both picked momentum 0.95 with a
0.30 cap as the lowest average miss — direction right 95.0 % for every pair:

| pair                      | full history miss | this season miss |
| ------------------------- | ----------------- | ---------------- |
| 0.95 / 0.30 (new default) | 0.878 pp          | 1.349 pp         |
| 0.90 / 0.20 (old default) | 0.906 pp          | 1.400 pp         |
| no change                 | 2.382 pp          | 3.693 pp         |

`Settings` defaults moved to `MV_FORECAST_MOMENTUM` = 0.95 and
`MV_FORECAST_CAP` = 0.30 on this evidence.

Kickbase's league player-details response (`GET /v4/leagues/{league}/players/{player}`) carries `tfhmvt`, the euro change of
the last update, and `mvt`, its direction (1 up, 2 down, 0 flat). Probed
live on 2026-09-17 for four listed players: `tfhmvt` equalled the last
difference of each player's MV series exactly. The ingestion already calls
this endpoint for every player twice a day and discards the field.

## Scope

In:

1. Store `tfhmvt` on each daily status row.
1. A forecast per player for the next update, written by every ingestion run.
1. Scoring of each forecast against the update it predicted.
1. A `Next MV` column on Players and Market, sortable.
1. A forecast accuracy section on Calibration & health.
1. `rehoboam backtest-mv`, which re-derives the rule's two constants.

Out: any trading use of the forecast; a model beyond momentum; the squad
page; `player_table` (the CLI view) stays as it is.

## Data

Migration `008_mv_forecast.sql`:

- `player_status_daily.mv_change bigint` — `tfhmvt` from the same response
  as `market_value`. Null on every row written before the migration.

- `mv_forecasts`, one row per player per target update:

  | column                              | meaning                                                                            |
  | ----------------------------------- | ---------------------------------------------------------------------------------- |
  | `player_id`, `target_day`           | primary key; `target_day` is the Berlin date of the ~22:00 update the row predicts |
  | `made_at`                           | epoch of the run that wrote or last rewrote it                                     |
  | `method`                            | `momentum-v1`                                                                      |
  | `base_mv`                           | market value at forecast time (after the previous update)                          |
  | `last_change`                       | `tfhmvt` at forecast time                                                          |
  | `predicted_change`, `predicted_pct` | euros, and a fraction of `base_mv`                                                 |
  | `scored_at`, `outcome`              | null until scored; outcome `scored` or `unscorable`                                |
  | `actual_change`, `actual_pct`       | filled when `outcome = 'scored'`                                                   |

  Index on `target_day`.

- Views (all `create or replace`, columns appended only, so the running
  site and bot keep working):

  - `web_mv_forecast`: unscored forecasts whose `target_day` equals
    `((now() at time zone 'Europe/Berlin') + interval '2 hours')::date` —
    today's date until 22:00 Berlin, tomorrow's from 22:00. Tomorrow's
    forecast does not exist until the morning run, so the page shows a dash
    overnight rather than a forecast for an update that has already
    happened. Columns: `player_id, target_day, made_at, base_mv, predicted_change, predicted_pct` (percent, two decimals).
  - `web_players` gains `next_mv_change` and `next_mv_pct` (left join).
  - `web_market` gains the same two, from `web_players`.
  - `web_mv_accuracy`: one row per scored `target_day` — `scored`,
    `unscorable`, `directional` (both changes non-zero), `direction_hits`,
    `mae_pct`, `baseline_mae_pct` (the miss of "no change"), `mae_eur`,
    `baseline_mae_eur`.

## The rule

`rehoboam/services/mv_forecast.py`, pure:

- `forecast(player_id, market_value, last_change, target_day, *, momentum, cap)`: `previous = market_value − last_change`; no forecast when
  `market_value ≤ 0` or `previous ≤ 0`. `last_pct = last_change / previous`;
  `predicted_pct = momentum × clamp(last_pct, −cap, +cap)`;
  `predicted_change = round(market_value × predicted_pct)`.
- Constants are `Settings` fields, `MV_FORECAST_MOMENTUM` = 0.95 and
  `MV_FORECAST_CAP` = 0.30, so they can be re-tuned from the environment.
- `usable_day(fetched_at)`: the Berlin date of a status fetch when its
  Berlin time is before 21:45, else `None`. A fetch after 21:45 may be on
  either side of the update, so it is used for neither forecasting nor
  scoring. The ingestion runs at 05:00 and 17:00 UTC (07:00/19:00 Berlin in
  summer, 06:00/18:00 in winter) and stops within nine minutes, so its rows
  are always usable. `player_status_daily.day` is keyed by the ingestion
  run's UTC date, not its Berlin one; the two scheduled runs land on the
  same calendar date in both zones all year, but a manual `rehoboam ingest`
  between 22:00 UTC (summer) or 23:00 UTC (winter) and midnight UTC writes
  under the previous day's key and overwrites that day's pre-update reading,
  so avoid running it in that window.
- `score(forecast, next_row)`: `next_row` is the player's status row for
  `target_day + 1`, usable for that day. If `next_row.market_value − next_row.mv_change ≠ base_mv`, the day did not line up: `unscorable`.
  Otherwise `actual_change = next_row.mv_change`, `actual_pct = actual_change / base_mv`. The base check keeps a mis-dated reading from
  ever counting as a hit or a miss.
- `backtest(series, *, momentum, cap)`: replays `forecast` over consecutive
  daily values and reports forecasts made, direction hits among directional
  pairs, mean absolute miss in pp, and the "no change" miss.

## When it runs

`rehoboam/enrichment/mv_forecast.py::run_mv_forecast(store, *, now, momentum, cap)`, called by the ingestion Function after calibration and by
`rehoboam ingest`. It never raises; its outcome (`written`, `scored`,
`unscorable`, `error`) goes into the run's `session_facts.extra` under
`mv_forecast`.

1. Score first. Every unscored forecast with `target_day` before today
   (Berlin): with a usable row for `target_day + 1`, score it; with none and
   `target_day + 1` already past, mark it `unscorable`; otherwise leave it
   (the afternoon run may still read it).
1. Then forecast. Every status row for today with a usable fetch, a market
   value and a `mv_change` gets a forecast for today's update. A second run
   the same day rewrites unscored rows; scored rows are never touched.

## The pages

- Players: `Next MV` after `7d`. Market: `Next MV` after `Market value`.
  The cell shows the signed percent in its tone and the signed euro change
  beneath it in muted text; a dash when there is no live forecast. The
  header carries the hint "Forecast for tonight's ~22:00 update". Sort key
  `next_mv_pct` on both pages.
- Calibration & health: a "Market-value forecast" section above the session
  runs. One sentence over the newest 14 scored updates (weighted by forecasts
  scored), from a tested pure module, then one row per update: date,
  scored, direction right, average miss, the "no change" miss, and the
  average miss in euros. The sentence says the forecast beats, ties or
  loses to "no change" only as the numbers say; with nothing scored it
  says so and when the first score arrives.

## Failure modes

- No `mv_change` yet (rows from before the migration): no forecast, dash.
- A run that stops on its budget: forecasts for the players it reached.
- Status fetch failed for a player: no forecast for that player today.
- Scoring finds no next-day reading: `unscorable` after the day passes;
  the accuracy view counts it separately.
- The forecast step fails: logged, recorded in `extra.mv_forecast.error`,
  the run's other work stands.

## Testing

- Pure: `forecast`, `usable_day` (including 21:44/21:45 Berlin and a
  winter date), `score` (match, mismatch, zero change), `backtest`.
- Store (real PostgreSQL): the status writer stores `mv_change`; forecast
  upsert rewrites unscored rows only; pending selection; outcomes.
- Views: `web_mv_forecast` shows only the live day (computed with the
  database's own clock in the test); `web_players`/`web_market` carry the
  new columns; `web_mv_accuracy` counts hits, misses and unscorable rows.
- Ingestion step: score-then-forecast against a real store with fixed times.
- Web: the sentence module, the cell formatter, the new sort keys.
- Live check (read-only, bot role) after the migration is applied.

## Rollout

1. Apply `008_mv_forecast.sql` to production as the admin before this PR
   merges; the bot role cannot create tables or views. Applying it early is
   harmless: the deployed code ignores the new column, table and view
   columns.
1. This PR merges after #115 (it is stacked on it).
1. The first ingestion after deploy stores `mv_change` and writes the first
   forecasts; the first scores arrive with the next morning's run.
