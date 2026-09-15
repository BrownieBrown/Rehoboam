# PR E: league-wide predictions, calibration reports, and the gate

Parent: `docs/superpowers/specs/2026-09-11-data-foundation-design.md` §4
(League-wide calibration) and §6 (the bar). Rollout row E. This spec
refines §4 against what PRs B–D actually built and records the rulings
where the implementation departs from the parent's wording.

## Why now

The calibration loop still has n ≈ 10: `predicted_eps` holds squad scores
only, `reconcile_finished_matchdays` pairs them per player, and the two
readers of the result have no live caller. M2 (Spearman of predicted versus
actual over all players, per matchday) is unmeasurable, so the trading gate
has nothing to read, and M1 (our lineup regret) is not measured at all.
Since PR C1 the store holds, for every player in the league, per-match
points and status (`player_match_history`), a daily injury status and lineup
probability (`player_status_daily`), and position and team
(`player_universe`). Those are every input `compose_ep` needs. Scoring the
whole league from local rows is arithmetic, and `replay/driver.py` already
does exactly that for the season replay.

## What ships

1. Every trading session scores every live player from store rows and
   writes `predictions` (n ≈ 460 a session, seconds, no API calls).
1. The ingestion app detects finished matchdays from the competition
   schedule, refreshes the rows still dated before the final whistle, joins
   predictions to actuals into `calibration_rows`, and writes one
   `calibration_reports` row per matchday with a like-for-like baseline and
   the gate verdict, then sends one Telegram message.
1. `rehoboam calibrate` runs the same code from the CLI, and
   `--backfill-day N` produces leak-free predictions for finished matchdays
   so three real reports exist before the PR merges.

Out of scope, deliberately: switching the squad and market scoring to the
store (the parent's "C2"), deleting `predicted_eps` / `matchday_outcomes` /
`reconcile_finished_matchdays` (PR F), any change to the lineup decision, and
recalibrating the scorer.

## 1. Predictions from store rows

**Module.** `rehoboam/scoring/store_scorer.py`, pure. It takes rows, not a
connection:

```python
@dataclass(frozen=True)
class StoredPlayer:
    player_id: str
    position: str           # "Goalkeeper" | "Defender" | "Midfielder" | "Forward"
    team_id: str | None
    market_value: int | None
    live_status: int | None          # player_status_daily.status, None when no row
    lineup_probability: int | None   # stored, not used by the model
    matches: list[dict]              # player_match_history rows, oldest first

@dataclass(frozen=True)
class StoredPrediction:
    player_id: str
    predicted_ep: float
    p_status: dict[int, float]       # availability vector actually used
    rate: float                      # Σ_s p(s) × rate(s) / Σ_s p(s), s in PLAYED_STATUSES
    prev_status: int | None
    data_grade: str                  # "A" when the rate model has a player coefficient, else "C"

def score_stored(player: StoredPlayer, *, now: datetime,
                 max_status_age_days: float, availability, rate) -> StoredPrediction
```

`score_stored` mirrors `replay/driver.py:134-176` and the live adapter:
`prev_status_from_history` over `(match_date, status)` pairs with the same
`max_status_age_days` `Settings` value the live path uses,
`recent_played_share` reimplemented over rows (statuses 3 and 5 over 1, 3,
4, 5 in the most recent season with at least five rows), `availability_probs`
with `live_status`, then `compose_ep`. No DGW multiplier (the Bundesliga has
none; a rescheduled double would show up as a bias in the report, which is
the right place to notice it). No team strength, no opponent, no notes.

**Scope.** Every player in `player_universe` whose `player_status_daily` row
is at most 48 hours old. Players with an older row or none are skipped and
counted (`predictions_skipped_stale`), because a prediction without a live
status is a different model. Players without a position are skipped and
counted.

**The `predictions` table** (migration `004_calibration.sql`):

| column         | type             | meaning                                                                       |
| -------------- | ---------------- | ----------------------------------------------------------------------------- |
| `session_id`   | text             | the trading session (or `backfill-md<N>`)                                     |
| `player_id`    | text             |                                                                               |
| `season`       | text             | as in `player_match_history` (e.g. `2026/2027`)                               |
| `day_number`   | integer          | the matchday the prediction is for                                            |
| `kickoff`      | double precision | that matchday's first kickoff, epoch                                          |
| `predicted_at` | double precision | epoch                                                                         |
| `predicted_ep` | double precision |                                                                               |
| `p_status`     | jsonb            | `{"1": p, "3": p, "4": p, "5": p}`                                            |
| `rate`         | double precision |                                                                               |
| `prev_status`  | integer          | nullable                                                                      |
| `live_status`  | integer          | nullable                                                                      |
| `position`     | text             |                                                                               |
| `team_id`      | text             | nullable                                                                      |
| `owned`        | boolean          | in our squad at prediction time                                               |
| `listed`       | boolean          | on the market at prediction time                                              |
| `in_best_11`   | boolean          | the eleven the session chose (`lineup_map` top eleven, legal)                 |
| `live_ep`      | double precision | nullable: the API-path `PlayerScore.expected_points` for owned/listed players |
| `data_grade`   | text             |                                                                               |
| `app`          | text             | `function` / `cli`                                                            |
| `dry_run`      | boolean          |                                                                               |
| `backfill`     | boolean          | written by `--backfill-day`, never by a session                               |

Primary key `(session_id, player_id)`; index `(season, day_number, predicted_at)`. Grants as for every other table (`rehoboam_bot` gets select,
insert, update).

**Store module.** `rehoboam/store/calibration_store.py` with
`CalibrationStore(dsn=None)`:

- `stored_players(*, season, since, status_day) -> list[StoredPlayer]`:
  three bulk queries (universe, latest status rows for `status_day` and the
  day before, match rows with `match_date >= since`), assembled in Python.
  `since` is `now − 400 days`, enough for `prev_status` and the played share.
- `write_predictions(rows: list[dict]) -> int`: one `executemany` upsert in
  one transaction.
- `last_predictions_before(*, season, day_number, kickoff, backfill) -> dict[str, dict]`: per player, the row with the greatest `predicted_at < kickoff`, restricted to `backfill = false` (or `= true` for a backfill
  report).
- `actuals_for(*, season, day_number) -> list[dict]`: match rows for that
  matchday with `player_universe.position` joined.
- `write_calibration(rows, report) -> None`, `report_for(season, day_number) -> dict | None`, `recent_reports(season, n) -> list[dict]`,
  `integrity_clean_since(epoch) -> bool` (no `integrity_failures` row newer
  than `epoch` from a non-dry-run session).
- `players_needing_final_rows(*, season, day_number, whistle) -> list[str]`: players with a row for that matchday whose
  `performance_fetched_at < whistle`.

**Session wiring** (`auto_trader.py`, step 2a, next to
`snapshot_predictions`, which keeps running until PR F):

```python
written = self._write_league_predictions(ctx, nk)  # best effort, try/except
self._facts.predictions_written = int(written)
```

`_write_league_predictions` needs the matchday number, so `NextKickoff`
gains `day_number: int | None`, filled by `next_kickoff_from_matchdays` from
the schedule day group (`/myeleven` has no number; `None` then, and the
session skips league predictions with a logged reason — rule I5 then fails,
which is correct: no kickoff, no matchday to predict for). Owned, listed and
best-eleven flags come from `ctx.squad`, `ep_result["market_players"]` and
`ep_result["lineup_map"]`; `live_ep` from `squad_scores` and
`market_scores`. Rule I5 (`predictions were written this session`) now reads
this count. `status` (dry run) writes rows too, with `dry_run = true`, as
every other learning snapshot does; the calibration join does not care, the
squad-regret join does (below).

## 2. Actuals, rows, reports

**Where.** `rehoboam/enrichment/calibrate.py`, called by the ingestion
app's `ingest` timer after `run_ingestion`, and by `rehoboam calibrate`.
Pure metric code lives in `rehoboam/services/calibration.py` and is tested
exhaustively in the style of `test_safety_gate`.

**Finished matchdays.** `finished_matchdays(schedule_payload) -> list[FinishedMatchday(day_number, first_kickoff, last_kickoff)]`: day groups
in which every fixture has `st == 2` and at least one fixture exists, from
the same `get_competition_matchdays` response `Trader.next_kickoff` reads.
`whistle = last_kickoff + 3 h`.

**Readiness, and the refresh that gets there.** A matchday is reportable
when no `calibration_reports` row exists for it and
`players_needing_final_rows(...)` is empty. When it is not empty, the
ingestion run, before its main loop, clears `performance_fetched_at` for
those players so the loop (stalest first, within the same budget) re-reads
them; after the loop it checks again and reports if ready. A matchday still
not ready 72 hours after the whistle is reported anyway with
`n_stale_rows` recorded, so one player whose fetch keeps failing cannot
block the gate forever.

**Rows.** For each player with a match row for `(season, day_number)`:
`actual_points = points`, `minutes`, `status`; prediction = the last one
before `first_kickoff` (any app, any dry-run flag, `backfill = false`).
Players without a prediction are written with `predicted_ep = null` and
counted in `n_unpredicted`; players who did not play are ordinary rows with
actual 0 — the model priced their absence, so the miss is real.

`calibration_rows`: `season, day_number, player_id, session_id (nullable), predicted_ep (nullable), live_ep (nullable), baseline_ep, actual_points, minutes, status, position, team_id, owned, in_best_11, prev_status, live_status, backfill`; primary key `(season, day_number, player_id, backfill)`.

**Baseline on the same rows.** `baseline_ep` is
`season_average_baseline(matches before first_kickoff)` for that player,
computed from the same match rows — the population the gate compares
against is the same players on the same matchday. The offline
`rehoboam backtest-baseline` scores squads of fifteen and is not the number
to beat; this is the one departure from the parent spec's wording, and it is
what "measured, not assumed" requires.

**Report.** One `calibration_reports` row per `(season, day_number, backfill)`:

| column                                              | meaning                                                                                                                                                                                               |
| --------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `computed_at`, `n`, `n_unpredicted`, `n_stale_rows` |                                                                                                                                                                                                       |
| `mae`, `bias`                                       | mean absolute error, mean (predicted − actual)                                                                                                                                                        |
| `spearman`, `baseline_spearman`                     | over every row with a prediction                                                                                                                                                                      |
| `top11_regret`, `baseline_top11_regret`             | best legal eleven from the whole league by actual minus by predicted (baseline) points, `select_best_eleven` both times                                                                               |
| `squad_regret`                                      | our fielded eleven (owned rows with `in_best_11` from the last non-dry-run session before kickoff) versus the best legal eleven from owned players by actual points; null when no such session exists |
| `live_spearman`, `live_n`                           | Spearman of `live_ep` versus actual over owned/listed rows: the C2 evidence                                                                                                                           |
| `by_position`                                       | jsonb `{pos: {n, mae, bias, spearman}}`                                                                                                                                                               |
| `by_status`                                         | jsonb keyed by `live_status` at prediction (`0` healthy, other codes as stored, `null`)                                                                                                               |
| `worst`                                             | jsonb, three largest `abs(predicted − actual)` with player id, predicted, actual, position                                                                                                            |
| `gate`                                              | jsonb, below                                                                                                                                                                                          |
| `telegram_sent`                                     | boolean                                                                                                                                                                                               |

Metrics reuse `backtest/metrics.spearman` and `select_best_eleven`; MAE and
bias are new one-liners in `services/calibration.py`.

**The gate.** Computed when the report is written, from real (non-backfill)
reports:

```json
{"spearman_ok": true, "regret_ok": false,
 "consecutive_ok": 1, "required": 3,
 "integrity_clean_days": 0.0, "required_clean_days": 7,
 "passes": false}
```

`spearman_ok` is `spearman >= baseline_spearman`; `regret_ok` is
`top11_regret < baseline_top11_regret`; `consecutive_ok` counts the trailing
run of reports where both hold; `integrity_clean_days` is the time since the
last `integrity_failures` row from a non-dry-run session. `passes` is
`consecutive_ok >= 3 and integrity_clean_days >= 7`. The gate is a verdict,
not a switch: trading resumes when Marco changes `TRADING_MODE`, and the
report tells him the moment that is defensible. (I2 and I4 fail every prod
session today for real reasons; the gate stays shut until they are fixed,
which is what it is for.)

**Telegram.** One message per real report, from the ingestion app, through
`notify.send_message` the way the integrity message goes, never for
backfill or dry runs:

```
Rehoboam calibration MD4 2026/2027
n=458 (unpredicted 6)  mae 41.3  bias −2.1
spearman 0.41 (baseline 0.36)  top11 regret 612 (baseline 690)
squad regret 87  live-path spearman 0.44 (n 58)
worst: 3421 Beier pred 71 act 0 · 1188 Kane pred 96 act 198 · …
gate: 1/3 matchdays ok, integrity clean 0.0/7 d — closed
```

`telegram_sent` records the outcome; a failed send is logged and retried on
the next run.

**Session facts.** The ingestion run's `extra` gains
`calibration: {reported: [4], waiting: {4: 17}}`.

## 3. CLI and backfill

`rehoboam calibrate [--day N] [--dry-run]` runs detection and reporting
against `DATABASE_URL`, printing the report table; `--dry-run` computes
without writing or sending.

`rehoboam calibrate --backfill-day N` writes predictions with
`session_id = "backfill-md<N>"`, `backfill = true`, `predicted_at = first_kickoff − 1 s`, scored by `score_stored` from match rows dated before
`first_kickoff` and `live_status = None` (`player_status_daily` starts
2026-09-14, so a backfill has no live status by construction, and says so),
then builds the backfill report. Backfill rows and reports are kept apart by
the `backfill` flag everywhere; the gate never reads them. The PR
description records MD1–MD3 backfill reports as its evidence.

## 4. Errors

Every new call in the trading session is wrapped like the other learning
snapshots: a failure logs with a stack trace, sets nothing else, and rule I5
fails. In the ingestion app, calibration runs after `run_ingestion` inside
its own try/except so a report failure cannot mark the ingest itself as
failed; its errors land in `extra.calibration.error`. Store methods run one
transaction per call, as `connect()` rules.

## 5. Tests

- `tests/test_scoring_v2/test_store_scorer.py`: `score_stored` equals
  `score_player_v2` on the same synthetic history when the payload and rows
  encode the same matches (both paths share `compose_ep`; this test is what
  makes `live_ep` versus `predicted_ep` a measurement of the *inputs*, not of
  two models); stale status, no status, no matches, unknown position.
- `tests/test_calibration.py`: `finished_matchdays` on the kickoff fixture
  (all-finished, partly-finished, empty groups, missing `st`); metrics on
  hand-built rows including ties, zero variance, unpredicted rows, one
  position, the top-eleven regret against a hand-picked eleven; gate
  arithmetic across report sequences; message rendering.
- `tests/store/test_calibration_store.py` against the real Postgres:
  bulk reads assemble the right `StoredPlayer`s, `last_predictions_before`
  picks the latest below kickoff and honours `backfill`, upserts are
  idempotent, readiness query.
- `tests/test_session_facts_wiring.py` additions: a session writes league
  predictions with the flags set, `predictions_written` counts them, I5
  fails when `day_number` is unknown.
- Ingestion app: the detector runs before the loop and clears the right
  players; the report runs after; a report failure leaves `errors = 0` on
  the ingest row.

## 6. Verification before merge

- Suite green on the real Postgres; CI's black, ruff, bandit, deps-sync.
- Migration 004 applied to prod as admin.
- Live dry-run `status` against prod: `predictions` rows ≈ the live
  universe in seconds; `predictions_written` on the session row.
- `rehoboam calibrate --backfill-day 1|2|3` against prod: three backfill
  reports with n ≥ 400, numbers recorded in the PR description.
- `rehoboam calibrate --dry-run` against prod: MD4 not finished, nothing
  written.
- After merge: the first real report after MD4 (kickoff 2026-09-18 18:30
  UTC, last match 2026-09-20; expected from the 2026-09-21 05:00 or 17:00
  UTC run), one Telegram message.

## Rulings

- Column names follow the existing tables (`day_number`, `team_id`), not
  the parent's `matchday` / `team`.
- The baseline the gate compares against is computed on the same rows,
  not taken from `rehoboam backtest-baseline`.
- No DGW multiplier in store predictions.
- Squad and market keep the API scoring path; `live_ep` measures the gap
  and C2 flips on that evidence.
- The gate is a verdict in the report; switching trading on stays a manual
  `TRADING_MODE` change.
- The legacy writers stay until PR F.
