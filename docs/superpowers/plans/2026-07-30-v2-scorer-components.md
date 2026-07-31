# v2 Scorer Components Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the two halves of the v2 scorer — an availability model (REH-52) predicting whether a player is on the pitch, and a rate model (REH-53) predicting what he scores when he is — fitted on held-out-safe historical data and shipped as plain coefficients.

**Architecture:** `EP = Σ_status P(status) × rate(player, status)`. Availability is a shrunk first-order Markov model over Kickbase's per-match `status` field; rate is a league-wide base rate per status multiplied by a per-player quality factor shrunk toward a position prior. Both fit offline, serialise to JSON, and load at import — no ML runtime in the Azure Function.

**Tech Stack:** Python 3.12, `uv`, pytest, SQLite. Standard library only.

## Global Constraints

- **No new runtime dependencies.** No numpy, scipy, pandas, or sklearn. This codebase ships to an Azure Function. All arithmetic is standard library.
- **Train on seasons ≤ 2024/25 only. Never fit on 2025/26.** That season is the held-out set the whole rebuild is judged against; fitting on it silently invalidates every downstream number.
- **Never use `ap`, `tp` or `asp` from the raw payload.** They look like point-in-time player aggregates and are not — `tp` is constant across an entire season (verified: 362 on every matchday for a sampled player), i.e. season-end totals stamped on every row. They leak the outcome.
- **Features are computed from prior matches only.** Every feature row is built by walking a player's history forward; a row for matchday N may see matchdays \< N and nothing else.
- **Do NOT run `black` on whole files.** The repo is not black-clean. Ruff enforces `B905` (`zip` needs `strict=`).
- **Never commit to `main`.** Branch: `feat/reh-52-53-scorer-components`.
- Output is in **real Kickbase points**, never a 0–100 index.

## Prerequisite

A separate in-flight task adds a nullable `status` INTEGER column to
`player_match_history`, populated from the payload's `st`, and re-sweeps the corpus.
**Confirm it has landed before starting Task 1:**

```bash
sqlite3 logs/training_corpus.db "SELECT status, COUNT(*) FROM player_match_history GROUP BY status ORDER BY status;"
```

Expect a large `0`/NULL group (unplayed 2026/27 fixtures) plus meaningful counts at
1, 3, 4 and 5. If `status` does not exist, stop — this plan cannot start.

## Measured facts this plan is built on

Status semantics, from 42,264 payload rows:

| `status` | n      | mean mins | mean pts | reading      |
| -------- | ------ | --------- | -------- | ------------ |
| 1        | 1,898  | 0.0       | 0.0      | not in squad |
| 4        | 10,688 | 1.2       | 1.3      | unused sub   |
| 3        | 8,488  | 18.4      | 18.5     | came on      |
| 5        | 20,449 | 87.1      | 85.0     | started      |

Transition matrix — `P(status | previous status)`, the availability model's core signal:

| prev | n      | →1        | →3        | →4        | →5        |
| ---- | ------ | --------- | --------- | --------- | --------- |
| 1    | 1,853  | **75.4%** | 3.0%      | 19.7%     | 1.9%      |
| 3    | 8,159  | 0.9%      | **54.9%** | 15.7%     | 28.6%     |
| 4    | 10,291 | 1.7%      | 16.0%     | **70.7%** | 11.7%     |
| 5    | 19,748 | 1.1%      | 9.7%      | 7.0%      | **82.3%** |

Within-status points spread — what the rate model must explain:

| status      | n      | mean | sd   | p10 | p50 | p90 |
| ----------- | ------ | ---- | ---- | --- | --- | --- |
| 5 (started) | 20,449 | 85.0 | 65.2 | 13  | 75  | 170 |
| 3 (came on) | 8,488  | 18.5 | 35.9 | −10 | 7   | 58  |

______________________________________________________________________

## File Structure

**Created:**

| File                                    | Responsibility                                           |
| --------------------------------------- | -------------------------------------------------------- |
| `rehoboam/scoring/v2/__init__.py`       | Package marker                                           |
| `rehoboam/scoring/v2/features.py`       | `MatchRow`, `FeatureRow`, leak-free feature construction |
| `rehoboam/scoring/v2/dataset.py`        | Corpus loading + train/holdout split by season           |
| `rehoboam/scoring/v2/availability.py`   | `AvailabilityModel` — fit and predict `P(status)`        |
| `rehoboam/scoring/v2/rate.py`           | `RateModel` — fit and predict points-given-status        |
| `rehoboam/scoring/v2/coefficients.json` | Fitted parameters, committed                             |
| `tests/test_scoring_v2/`                | One test module per source module                        |

**Modified:** `rehoboam/cli.py` (a `fit-scorer` command).

Deliberately **not** in scope: wiring into `score_player` (REH-55) and the
baseline comparison (REH-56). This plan produces the two components and the
means to fit them; integration is a separate reviewable change.

______________________________________________________________________

## Task 1: Feature construction (leak-free)

**Files:**

- Create: `rehoboam/scoring/v2/__init__.py`, `rehoboam/scoring/v2/features.py`
- Create: `tests/test_scoring_v2/__init__.py`, `tests/test_scoring_v2/test_features.py`

**Interfaces:**

- Produces:
  - `MatchRow` frozen dataclass: `player_id: str, season: str, day_number: int, status: int | None, points: int, minutes: int`
  - `FeatureRow` frozen dataclass: `player_id: str, season: str, day_number: int, prev_status: int | None, rolling_minutes_3: float, matches_seen: int, target_status: int | None, target_points: int`
  - `build_feature_rows(matches: list[MatchRow]) -> list[FeatureRow]`
  - `PLAYED_STATUSES: tuple[int, ...] = (1, 3, 4, 5)`

This is the leak boundary for the fitting side, exactly as `matches_before` is for
the backtest side. Every feature on a row describes matches *strictly before* that
row's matchday.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for rehoboam.scoring.v2.features — leak-free feature construction."""

from __future__ import annotations

import pytest

from rehoboam.scoring.v2.features import MatchRow, build_feature_rows


def _m(day: int, status: int | None, points: int, minutes: int) -> MatchRow:
    return MatchRow(
        player_id="1",
        season="2024/2025",
        day_number=day,
        status=status,
        points=points,
        minutes=minutes,
    )


def test_first_match_has_no_previous_status():
    rows = build_feature_rows([_m(1, 5, 80, 90)])
    assert len(rows) == 1
    assert rows[0].prev_status is None
    assert rows[0].matches_seen == 0
    assert rows[0].rolling_minutes_3 == 0.0


def test_prev_status_is_the_immediately_preceding_match():
    rows = build_feature_rows([_m(1, 5, 80, 90), _m(2, 3, 10, 20), _m(3, 4, 0, 0)])
    assert [r.prev_status for r in rows] == [None, 5, 3]


def test_rolling_minutes_uses_only_prior_matches():
    """The row for day 4 must not see day 4's own minutes."""
    rows = build_feature_rows(
        [_m(1, 5, 80, 90), _m(2, 5, 70, 90), _m(3, 5, 60, 90), _m(4, 4, 0, 0)]
    )
    # day 4 sees days 1-3 only: (90+90+90)/3
    assert rows[3].rolling_minutes_3 == pytest.approx(90.0)
    # day 2 sees day 1 only
    assert rows[1].rolling_minutes_3 == pytest.approx(90.0)


def test_rolling_minutes_window_is_capped_at_three():
    rows = build_feature_rows(
        [
            _m(1, 5, 0, 0),
            _m(2, 5, 0, 90),
            _m(3, 5, 0, 90),
            _m(4, 5, 0, 90),
            _m(5, 5, 0, 0),
        ]
    )
    # day 5 sees days 2,3,4 — NOT day 1's zero
    assert rows[4].rolling_minutes_3 == pytest.approx(90.0)


def test_matches_seen_counts_prior_rows():
    rows = build_feature_rows([_m(1, 5, 0, 90), _m(2, 5, 0, 90), _m(3, 5, 0, 90)])
    assert [r.matches_seen for r in rows] == [0, 1, 2]


def test_target_carries_this_row_own_outcome():
    rows = build_feature_rows([_m(1, 5, 80, 90), _m(2, 3, 12, 20)])
    assert [(r.target_status, r.target_points) for r in rows] == [(5, 80), (3, 12)]


def test_rows_are_ordered_by_day_regardless_of_input_order():
    rows = build_feature_rows([_m(3, 5, 0, 90), _m(1, 5, 0, 90), _m(2, 5, 0, 90)])
    assert [r.day_number for r in rows] == [1, 2, 3]


def test_season_boundary_resets_history():
    """A new season starts fresh — last May's form is not last week's form."""
    a = MatchRow("1", "2023/2024", 34, 5, 80, 90)
    b = MatchRow("1", "2024/2025", 1, 5, 70, 90)
    rows = build_feature_rows([a, b])
    assert rows[1].prev_status is None
    assert rows[1].matches_seen == 0


def test_unplayed_fixtures_are_excluded():
    """status 0/None means the match has not been played — it is not a training row."""
    rows = build_feature_rows([_m(1, 5, 80, 90), _m(2, 0, 0, 0), _m(3, None, 0, 0)])
    assert [r.day_number for r in rows] == [1]


def test_empty_input_returns_empty():
    assert build_feature_rows([]) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_scoring_v2/test_features.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rehoboam.scoring.v2'`

- [ ] **Step 3: Create package markers**

`rehoboam/scoring/v2/__init__.py`:

```python
"""v2 scorer: availability × rate, fitted offline, shipped as coefficients."""
```

`tests/test_scoring_v2/__init__.py`: empty file.

- [ ] **Step 4: Implement**

`rehoboam/scoring/v2/features.py`:

```python
"""Leak-free feature construction for the v2 scorer.

Every feature on a row describes matches *strictly before* that row's matchday.
This is the fitting-side equivalent of ``backtest.snapshot.matches_before``: if
it leaks, the model looks excellent offline and fails live, and nothing crashes
to tell you.

Season boundaries reset history deliberately — last May's minutes are not
evidence about this August's.
"""

from __future__ import annotations

from dataclasses import dataclass

# Kickbase per-match status. 1 = not in squad, 3 = came on, 4 = unused sub,
# 5 = started. 0 / None means the fixture has not been played yet.
#
# NOTE: this is NOT the injury `st` from get_player_details, which is a live
# serving-time signal with no historical counterpart.
PLAYED_STATUSES: tuple[int, ...] = (1, 3, 4, 5)

ROLLING_WINDOW = 3


@dataclass(frozen=True)
class MatchRow:
    """One played match, as stored in the training corpus."""

    player_id: str
    season: str
    day_number: int
    status: int | None
    points: int
    minutes: int


@dataclass(frozen=True)
class FeatureRow:
    """One training example: features from the past, target from the present."""

    player_id: str
    season: str
    day_number: int
    prev_status: int | None
    rolling_minutes_3: float
    matches_seen: int
    target_status: int | None
    target_points: int


def build_feature_rows(matches: list[MatchRow]) -> list[FeatureRow]:
    """Turn one player's match history into training rows.

    Args:
        matches: that player's matches, any order. Rows whose ``status`` is not
            a played status are dropped — an unplayed fixture is not evidence.

    Returns:
        One row per played match, ordered by (season, day_number). Features are
        derived only from earlier matches within the same season.
    """
    played = [m for m in matches if m.status in PLAYED_STATUSES]
    played.sort(key=lambda m: (m.season, m.day_number))

    rows: list[FeatureRow] = []
    current_season: str | None = None
    history: list[MatchRow] = []

    for match in played:
        if match.season != current_season:
            current_season = match.season
            history = []

        window = history[-ROLLING_WINDOW:]
        rolling = sum(m.minutes for m in window) / len(window) if window else 0.0

        rows.append(
            FeatureRow(
                player_id=match.player_id,
                season=match.season,
                day_number=match.day_number,
                prev_status=history[-1].status if history else None,
                rolling_minutes_3=rolling,
                matches_seen=len(history),
                target_status=match.status,
                target_points=match.points,
            )
        )
        history.append(match)

    return rows
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_scoring_v2/test_features.py -v`
Expected: PASS (10 tests)

- [ ] **Step 6: Commit**

```bash
git add rehoboam/scoring/v2/ tests/test_scoring_v2/
git commit -m "feat(scoring): leak-free feature construction for the v2 scorer"
```

______________________________________________________________________

## Task 2: Corpus loading and the train/holdout split

**Files:**

- Create: `rehoboam/scoring/v2/dataset.py`
- Create: `tests/test_scoring_v2/test_dataset.py`

**Interfaces:**

- Consumes: `MatchRow`, `FeatureRow`, `build_feature_rows` (Task 1)

- Produces:

  - `TRAIN_MAX_SEASON: str = "2024/2025"`
  - `HOLDOUT_SEASON: str = "2025/2026"`
  - `load_match_rows(db_path: Path) -> dict[str, list[MatchRow]]` — player_id → matches
  - `load_positions(db_path: Path) -> dict[str, str]` — player_id → position
  - `split_rows(rows: list[FeatureRow]) -> tuple[list[FeatureRow], list[FeatureRow]]` — (train, holdout)

- [ ] **Step 1: Write the failing test**

```python
"""Tests for rehoboam.scoring.v2.dataset — corpus loading and the season split."""

from __future__ import annotations

import sqlite3

from rehoboam.scoring.v2.dataset import (
    HOLDOUT_SEASON,
    TRAIN_MAX_SEASON,
    load_match_rows,
    load_positions,
    split_rows,
)
from rehoboam.scoring.v2.features import FeatureRow


def _corpus(tmp_path):
    """A minimal corpus with the columns the loader needs."""
    db = tmp_path / "corpus.db"
    with sqlite3.connect(db) as conn:
        conn.executescript("""
            CREATE TABLE player_universe (
                player_id TEXT PRIMARY KEY, first_name TEXT, last_name TEXT,
                position TEXT, team_id TEXT, market_value INTEGER, average_points REAL
            );
            CREATE TABLE player_match_history (
                player_id TEXT NOT NULL, season TEXT NOT NULL, day_number INTEGER NOT NULL,
                match_date TEXT, points INTEGER NOT NULL, minutes INTEGER NOT NULL,
                team_id TEXT, opponent_team_id TEXT, is_home INTEGER NOT NULL DEFAULT 0,
                status INTEGER,
                PRIMARY KEY (player_id, season, day_number)
            );
            """)
        conn.execute(
            "INSERT INTO player_universe VALUES ('1','Jamal','Musiala','Midfielder','2',0,0.0)"
        )
        conn.executemany(
            "INSERT INTO player_match_history "
            "(player_id, season, day_number, points, minutes, status) VALUES (?,?,?,?,?,?)",
            [
                ("1", "2023/2024", 1, 80, 90, 5),
                ("1", "2024/2025", 1, 70, 90, 5),
                ("1", "2025/2026", 1, 60, 90, 5),
            ],
        )
        conn.commit()
    return db


def _fr(season: str) -> FeatureRow:
    return FeatureRow(
        player_id="1",
        season=season,
        day_number=1,
        prev_status=None,
        rolling_minutes_3=0.0,
        matches_seen=0,
        target_status=5,
        target_points=50,
    )


def test_load_match_rows_groups_by_player(tmp_path):
    rows = load_match_rows(_corpus(tmp_path))
    assert set(rows) == {"1"}
    assert len(rows["1"]) == 3
    assert {m.season for m in rows["1"]} == {"2023/2024", "2024/2025", "2025/2026"}


def test_load_match_rows_preserves_status(tmp_path):
    rows = load_match_rows(_corpus(tmp_path))
    assert all(m.status == 5 for m in rows["1"])


def test_load_positions(tmp_path):
    assert load_positions(_corpus(tmp_path)) == {"1": "Midfielder"}


def test_split_puts_2025_26_in_holdout_and_earlier_in_train():
    train, holdout = split_rows([_fr("2023/2024"), _fr("2024/2025"), _fr("2025/2026")])
    assert [r.season for r in train] == ["2023/2024", "2024/2025"]
    assert [r.season for r in holdout] == ["2025/2026"]


def test_split_excludes_seasons_after_the_holdout():
    """2026/2027 fixtures are unplayed — they belong in neither set."""
    train, holdout = split_rows([_fr("2024/2025"), _fr("2026/2027")])
    assert [r.season for r in train] == ["2024/2025"]
    assert holdout == []


def test_split_constants_are_what_the_spec_requires():
    assert TRAIN_MAX_SEASON == "2024/2025"
    assert HOLDOUT_SEASON == "2025/2026"


def test_split_never_leaks_holdout_into_train():
    """The guard. If this ever fails, every downstream number is invalid."""
    rows = [_fr(s) for s in ("2013/2014", "2024/2025", "2025/2026", "2026/2027")]
    train, holdout = split_rows(rows)
    assert all(r.season <= TRAIN_MAX_SEASON for r in train)
    assert all(r.season == HOLDOUT_SEASON for r in holdout)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_scoring_v2/test_dataset.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rehoboam.scoring.v2.dataset'`

- [ ] **Step 3: Implement**

`rehoboam/scoring/v2/dataset.py`:

```python
"""Corpus loading and the train/holdout split.

The split is the project's most important discipline: 2025/26 is the season the
whole rebuild is judged against, so nothing may be fitted on it. Seasons after
the holdout (2026/27 fixtures) are unplayed and belong in neither set.

Kickbase season titles are ``YYYY/YYYY`` and sort correctly under plain string
comparison, which is what the boundaries below rely on.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from rehoboam.scoring.v2.features import FeatureRow, MatchRow

TRAIN_MAX_SEASON = "2024/2025"
HOLDOUT_SEASON = "2025/2026"


def load_match_rows(db_path: Path) -> dict[str, list[MatchRow]]:
    """Load every played match from the corpus, grouped by player."""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("""
            SELECT player_id, season, day_number, status, points, minutes
            FROM player_match_history
            ORDER BY player_id, season, day_number
            """).fetchall()

    by_player: dict[str, list[MatchRow]] = {}
    for player_id, season, day_number, status, points, minutes in rows:
        by_player.setdefault(str(player_id), []).append(
            MatchRow(
                player_id=str(player_id),
                season=season,
                day_number=int(day_number),
                status=status,
                points=int(points),
                minutes=int(minutes),
            )
        )
    return by_player


def load_positions(db_path: Path) -> dict[str, str]:
    """player_id → position, for players whose position is known."""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT player_id, position FROM player_universe WHERE position IS NOT NULL"
        ).fetchall()
    return {str(pid): pos for pid, pos in rows}


def split_rows(rows: list[FeatureRow]) -> tuple[list[FeatureRow], list[FeatureRow]]:
    """Split into (train, holdout) by season.

    Train is everything up to and including ``TRAIN_MAX_SEASON``. Holdout is
    exactly ``HOLDOUT_SEASON``. Later seasons are dropped — they are fixtures,
    not results.
    """
    train = [r for r in rows if r.season <= TRAIN_MAX_SEASON]
    holdout = [r for r in rows if r.season == HOLDOUT_SEASON]
    return train, holdout
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_scoring_v2/test_dataset.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add rehoboam/scoring/v2/dataset.py tests/test_scoring_v2/test_dataset.py
git commit -m "feat(scoring): corpus loading and train/holdout split for v2 fitting"
```

______________________________________________________________________

## Task 3: Availability model (REH-52)

**Files:**

- Create: `rehoboam/scoring/v2/availability.py`
- Create: `tests/test_scoring_v2/test_availability.py`

**Interfaces:**

- Consumes: `FeatureRow`, `PLAYED_STATUSES` (Task 1)
- Produces:
  - `AvailabilityModel` frozen dataclass with `transitions: dict[int, dict[int, float]]`, `prior: dict[int, float]`, `shrinkage_k: float`
  - `AvailabilityModel.predict(prev_status: int | None) -> dict[int, float]`
  - `AvailabilityModel.to_dict() -> dict` / `AvailabilityModel.from_dict(d: dict) -> AvailabilityModel`
  - `fit_availability(rows: list[FeatureRow], *, shrinkage_k: float = 20.0) -> AvailabilityModel`

A shrunk first-order Markov model. Shrinkage matters for the sparse rows — status
1 has only 1,853 transitions against status 5's 19,748, so unsmoothed estimates
for rare states are noisy.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for rehoboam.scoring.v2.availability."""

from __future__ import annotations

import pytest

from rehoboam.scoring.v2.availability import AvailabilityModel, fit_availability
from rehoboam.scoring.v2.features import FeatureRow


def _row(prev: int | None, target: int) -> FeatureRow:
    return FeatureRow(
        player_id="1",
        season="2024/2025",
        day_number=1,
        prev_status=prev,
        rolling_minutes_3=0.0,
        matches_seen=1,
        target_status=target,
        target_points=0,
    )


def test_predictions_are_a_probability_distribution():
    model = fit_availability([_row(5, 5)] * 50 + [_row(5, 3)] * 50)
    probs = model.predict(5)
    assert set(probs) == {1, 3, 4, 5}
    assert sum(probs.values()) == pytest.approx(1.0)
    assert all(0.0 <= p <= 1.0 for p in probs.values())


def test_learns_persistence_from_data():
    """Starters mostly start again — the dominant real-world signal."""
    rows = (
        [_row(5, 5)] * 820 + [_row(5, 3)] * 100 + [_row(5, 4)] * 70 + [_row(5, 1)] * 10
    )
    model = fit_availability(rows, shrinkage_k=0.0)
    probs = model.predict(5)
    assert probs[5] == pytest.approx(0.82, abs=0.01)


def test_shrinkage_pulls_sparse_states_toward_the_prior():
    """One observation of a rare state must not produce a 100% estimate."""
    rows = [_row(5, 5)] * 1000 + [_row(1, 3)]
    model = fit_availability(rows, shrinkage_k=20.0)
    probs = model.predict(1)
    assert probs[3] < 0.5, "a single observation should not dominate"


def test_zero_shrinkage_reproduces_raw_frequencies():
    rows = [_row(3, 5)] * 3 + [_row(3, 3)] * 1
    model = fit_availability(rows, shrinkage_k=0.0)
    assert model.predict(3)[5] == pytest.approx(0.75)


def test_unknown_previous_status_falls_back_to_the_prior():
    """A player's first-ever match has no previous status."""
    rows = [_row(5, 5)] * 80 + [_row(5, 4)] * 20
    model = fit_availability(rows)
    probs = model.predict(None)
    assert sum(probs.values()) == pytest.approx(1.0)
    assert probs[5] > probs[4]


def test_previous_status_never_seen_in_training_falls_back_to_the_prior():
    rows = [_row(5, 5)] * 100
    model = fit_availability(rows)
    assert model.predict(1) == model.predict(None)


def test_empty_training_data_yields_a_uniform_prior():
    model = fit_availability([])
    probs = model.predict(5)
    assert sum(probs.values()) == pytest.approx(1.0)
    assert len(set(probs.values())) == 1


def test_round_trips_through_dict():
    model = fit_availability([_row(5, 5)] * 10 + [_row(5, 3)] * 5)
    restored = AvailabilityModel.from_dict(model.to_dict())
    assert restored.predict(5) == model.predict(5)
    assert restored.predict(None) == model.predict(None)


def test_rows_without_a_target_status_are_ignored():
    rows = [_row(5, 5)] * 10 + [FeatureRow("1", "2024/2025", 2, 5, 0.0, 1, None, 0)]
    model = fit_availability(rows, shrinkage_k=0.0)
    assert model.predict(5)[5] == pytest.approx(1.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_scoring_v2/test_availability.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`rehoboam/scoring/v2/availability.py`:

```python
"""Availability model — P(status | previous status).

The largest single effect in the game. Measured across the corpus:

    status 1 (not in squad)  mean   0.0 pts
    status 4 (unused sub)    mean   1.3 pts
    status 3 (came on)       mean  18.5 pts
    status 5 (started)       mean  85.0 pts

A ~85-point swing driven purely by whether the player is on the pitch. The v1
scorer expressed this as a ±20 bonus on a 0-100 index.

A first-order Markov model captures most of it — starters start again 82% of the
time, unused subs stay unused 71% of the time. Transition counts are shrunk
toward the marginal prior because the rare states are sparse: status 1 has 1,853
observed transitions against status 5's 19,748.

Note this model is fitted only on *historical* signals. Kickbase's live lineup
probability (`prob`) and injury status have no historical counterpart — Kickbase
does not publish what a player's lineup probability was two seasons ago — so they
cannot be fitted here. They belong at serving time as explicit, documented
overrides.
"""

from __future__ import annotations

from dataclasses import dataclass

from rehoboam.scoring.v2.features import PLAYED_STATUSES, FeatureRow

DEFAULT_SHRINKAGE_K = 20.0


@dataclass(frozen=True)
class AvailabilityModel:
    """Fitted transition probabilities, plus a marginal prior for cold start."""

    transitions: dict[int, dict[int, float]]
    prior: dict[int, float]
    shrinkage_k: float

    def predict(self, prev_status: int | None) -> dict[int, float]:
        """P(status) given the player's previous match status.

        Falls back to the marginal prior when the previous status is unknown
        (a player's first match) or was never observed in training.
        """
        if prev_status is None:
            return dict(self.prior)
        return dict(self.transitions.get(prev_status, self.prior))

    def to_dict(self) -> dict:
        return {
            "transitions": {
                str(prev): {str(nxt): p for nxt, p in row.items()}
                for prev, row in self.transitions.items()
            },
            "prior": {str(s): p for s, p in self.prior.items()},
            "shrinkage_k": self.shrinkage_k,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AvailabilityModel":
        return cls(
            transitions={
                int(prev): {int(nxt): float(p) for nxt, p in row.items()}
                for prev, row in data["transitions"].items()
            },
            prior={int(s): float(p) for s, p in data["prior"].items()},
            shrinkage_k=float(data["shrinkage_k"]),
        )


def fit_availability(
    rows: list[FeatureRow], *, shrinkage_k: float = DEFAULT_SHRINKAGE_K
) -> AvailabilityModel:
    """Fit transition probabilities from feature rows.

    Args:
        rows: training rows. Only those with a ``target_status`` count.
        shrinkage_k: pseudo-count pulling each transition row toward the
            marginal prior. 0.0 gives raw frequencies.
    """
    marginal = dict.fromkeys(PLAYED_STATUSES, 0)
    counts: dict[int, dict[int, int]] = {
        prev: dict.fromkeys(PLAYED_STATUSES, 0) for prev in PLAYED_STATUSES
    }

    for row in rows:
        if row.target_status not in PLAYED_STATUSES:
            continue
        marginal[row.target_status] += 1
        if row.prev_status in PLAYED_STATUSES:
            counts[row.prev_status][row.target_status] += 1

    total = sum(marginal.values())
    if total == 0:
        uniform = 1.0 / len(PLAYED_STATUSES)
        prior = dict.fromkeys(PLAYED_STATUSES, uniform)
        return AvailabilityModel(transitions={}, prior=prior, shrinkage_k=shrinkage_k)

    prior = {s: marginal[s] / total for s in PLAYED_STATUSES}

    transitions: dict[int, dict[int, float]] = {}
    for prev in PLAYED_STATUSES:
        row_total = sum(counts[prev].values())
        if row_total == 0:
            continue
        denominator = row_total + shrinkage_k
        transitions[prev] = {
            s: (counts[prev][s] + shrinkage_k * prior[s]) / denominator
            for s in PLAYED_STATUSES
        }

    return AvailabilityModel(
        transitions=transitions, prior=prior, shrinkage_k=shrinkage_k
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_scoring_v2/test_availability.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add rehoboam/scoring/v2/availability.py tests/test_scoring_v2/test_availability.py
git commit -m "feat(scoring): availability model (REH-52)"
```

______________________________________________________________________

## Task 4: Rate model (REH-53)

**Files:**

- Create: `rehoboam/scoring/v2/rate.py`
- Create: `tests/test_scoring_v2/test_rate.py`

**Interfaces:**

- Consumes: `FeatureRow`, `PLAYED_STATUSES` (Task 1)
- Produces:
  - `RateModel` frozen dataclass with `base_rate: dict[int, float]`, `quality: dict[str, float]`, `position_prior: dict[str, float]`, `shrinkage_k: float`
  - `RateModel.predict(player_id: str, status: int, position: str | None) -> float`
  - `RateModel.to_dict()` / `RateModel.from_dict(d)`
  - `fit_rate(rows: list[FeatureRow], positions: dict[str, str], *, shrinkage_k: float = 5.0) -> RateModel`

`rate = base_rate[status] × quality(player)`. Quality is the player's own
points-per-match relative to the league, shrunk toward his position's average so a
player with three matches is pulled to the prior while one with thirty stands on
his record. This is what makes cold start degrade gracefully instead of needing a
separate code path.

Within-status spread is what this model exists to explain: started is mean 85.0 with
sd 65.2 (p10 13, p90 170).

- [ ] **Step 1: Write the failing test**

```python
"""Tests for rehoboam.scoring.v2.rate."""

from __future__ import annotations

import pytest

from rehoboam.scoring.v2.features import FeatureRow
from rehoboam.scoring.v2.rate import RateModel, fit_rate


def _row(player_id: str, status: int, points: int) -> FeatureRow:
    return FeatureRow(
        player_id=player_id,
        season="2024/2025",
        day_number=1,
        prev_status=5,
        rolling_minutes_3=90.0,
        matches_seen=5,
        target_status=status,
        target_points=points,
    )


POSITIONS = {"star": "Forward", "average": "Forward", "keeper": "Goalkeeper"}


def test_base_rate_is_learned_per_status():
    rows = [_row("average", 5, 80)] * 20 + [_row("average", 3, 18)] * 20
    model = fit_rate(rows, POSITIONS)
    assert model.base_rate[5] == pytest.approx(80.0, abs=1.0)
    assert model.base_rate[3] == pytest.approx(18.0, abs=1.0)


def test_a_better_player_scores_above_the_base_rate():
    rows = [_row("average", 5, 80)] * 30 + [_row("star", 5, 160)] * 30
    model = fit_rate(rows, POSITIONS, shrinkage_k=0.0)
    assert model.predict("star", 5, "Forward") > model.predict("average", 5, "Forward")


def test_shrinkage_pulls_a_thin_record_toward_the_position_prior():
    """One 200-point game must not make a player twice the league's best."""
    rows = [_row("average", 5, 80)] * 100 + [_row("star", 5, 200)]
    model = fit_rate(rows, POSITIONS, shrinkage_k=5.0)
    unshrunk = fit_rate(rows, POSITIONS, shrinkage_k=0.0)
    assert model.predict("star", 5, "Forward") < unshrunk.predict("star", 5, "Forward")


def test_a_long_record_stands_on_its_own():
    """With plenty of evidence, shrinkage barely moves the estimate."""
    rows = [_row("average", 5, 80)] * 100 + [_row("star", 5, 160)] * 100
    shrunk = fit_rate(rows, POSITIONS, shrinkage_k=5.0)
    unshrunk = fit_rate(rows, POSITIONS, shrinkage_k=0.0)
    assert shrunk.predict("star", 5, "Forward") == pytest.approx(
        unshrunk.predict("star", 5, "Forward"), rel=0.05
    )


def test_unknown_player_falls_back_to_the_position_prior():
    rows = [_row("average", 5, 80)] * 30
    model = fit_rate(rows, POSITIONS)
    assert model.predict("never-seen", 5, "Forward") == pytest.approx(
        model.base_rate[5] * model.position_prior["Forward"], rel=0.01
    )


def test_unknown_player_and_unknown_position_falls_back_to_the_base_rate():
    rows = [_row("average", 5, 80)] * 30
    model = fit_rate(rows, POSITIONS)
    assert model.predict("never-seen", 5, None) == pytest.approx(model.base_rate[5])


def test_status_with_no_training_data_returns_zero():
    rows = [_row("average", 5, 80)] * 10
    model = fit_rate(rows, POSITIONS)
    assert model.predict("average", 1, "Forward") == 0.0


def test_predictions_are_in_real_points_not_an_index():
    """The v1 scorer's cardinal sin was a 0-100 index masquerading as points."""
    rows = [_row("star", 5, 160)] * 50
    model = fit_rate(rows, POSITIONS, shrinkage_k=0.0)
    assert model.predict("star", 5, "Forward") == pytest.approx(160.0, abs=5.0)


def test_round_trips_through_dict():
    rows = [_row("average", 5, 80)] * 10 + [_row("star", 5, 160)] * 10
    model = fit_rate(rows, POSITIONS)
    restored = RateModel.from_dict(model.to_dict())
    assert restored.predict("star", 5, "Forward") == pytest.approx(
        model.predict("star", 5, "Forward")
    )


def test_empty_training_data_predicts_zero():
    model = fit_rate([], {})
    assert model.predict("anyone", 5, "Forward") == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_scoring_v2/test_rate.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`rehoboam/scoring/v2/rate.py`:

```python
"""Rate model — points scored, given that the player is in a given state.

    rate = base_rate[status] × quality(player)

``base_rate`` is the league-wide average points for that availability state
(started ≈ 85, came on ≈ 18.5, unused sub ≈ 1.3, not in squad ≈ 0). ``quality``
is the player's own scoring relative to the league, shrunk toward his position's
average.

Shrinkage is what makes cold start work without a special case: a player with
three matches is pulled hard toward the position prior, one with thirty stands on
his own record. The v1 scorer approximated this with a grade-F halving rule.

The defect this replaces: v1's ``base_points = min(avg_pts * 2.0, 40.0)``
saturated at 20 points per game, and 93.1% of Bundesliga players exceed that — so
the one component meant to express player quality was a constant for everyone
worth owning.

Output is in real Kickbase points, never a 0-100 index.
"""

from __future__ import annotations

from dataclasses import dataclass

from rehoboam.scoring.v2.features import PLAYED_STATUSES, FeatureRow

DEFAULT_SHRINKAGE_K = 5.0


@dataclass(frozen=True)
class RateModel:
    """League base rates plus per-player and per-position quality multipliers."""

    base_rate: dict[int, float]
    quality: dict[str, float]
    position_prior: dict[str, float]
    shrinkage_k: float

    def predict(self, player_id: str, status: int, position: str | None) -> float:
        """Expected points for this player in this availability state."""
        base = self.base_rate.get(status, 0.0)
        if base == 0.0:
            return 0.0

        multiplier = self.quality.get(player_id)
        if multiplier is None:
            multiplier = self.position_prior.get(position or "", 1.0)
        return base * multiplier

    def to_dict(self) -> dict:
        return {
            "base_rate": {str(s): r for s, r in self.base_rate.items()},
            "quality": dict(self.quality),
            "position_prior": dict(self.position_prior),
            "shrinkage_k": self.shrinkage_k,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RateModel":
        return cls(
            base_rate={int(s): float(r) for s, r in data["base_rate"].items()},
            quality={str(p): float(q) for p, q in data["quality"].items()},
            position_prior={
                str(p): float(q) for p, q in data["position_prior"].items()
            },
            shrinkage_k=float(data["shrinkage_k"]),
        )


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def fit_rate(
    rows: list[FeatureRow],
    positions: dict[str, str],
    *,
    shrinkage_k: float = DEFAULT_SHRINKAGE_K,
) -> RateModel:
    """Fit base rates and shrunk per-player quality multipliers.

    Args:
        rows: training rows with a ``target_status`` and ``target_points``.
        positions: player_id → position, for the position priors.
        shrinkage_k: pseudo-count pulling a player's quality toward his
            position's prior. 0.0 uses raw per-player averages.
    """
    by_status: dict[int, list[float]] = {s: [] for s in PLAYED_STATUSES}
    per_player: dict[str, list[float]] = {}

    for row in rows:
        if row.target_status not in PLAYED_STATUSES:
            continue
        by_status[row.target_status].append(float(row.target_points))
        # Quality is measured on states where scoring is actually possible.
        if row.target_status in (3, 5):
            per_player.setdefault(row.player_id, []).append(float(row.target_points))

    base_rate = {s: _mean(by_status[s]) for s in PLAYED_STATUSES}

    scoring_reference = _mean([p for s in (3, 5) for p in by_status[s]])
    if scoring_reference == 0.0:
        return RateModel(
            base_rate=base_rate, quality={}, position_prior={}, shrinkage_k=shrinkage_k
        )

    raw_quality = {
        pid: _mean(pts) / scoring_reference for pid, pts in per_player.items()
    }

    by_position: dict[str, list[float]] = {}
    for pid, q in raw_quality.items():
        position = positions.get(pid)
        if position:
            by_position.setdefault(position, []).append(q)
    position_prior = {pos: _mean(qs) for pos, qs in by_position.items()}

    quality: dict[str, float] = {}
    for pid, points in per_player.items():
        n = len(points)
        prior = position_prior.get(positions.get(pid, ""), 1.0)
        quality[pid] = (n * raw_quality[pid] + shrinkage_k * prior) / (n + shrinkage_k)

    return RateModel(
        base_rate=base_rate,
        quality=quality,
        position_prior=position_prior,
        shrinkage_k=shrinkage_k,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_scoring_v2/test_rate.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add rehoboam/scoring/v2/rate.py tests/test_scoring_v2/test_rate.py
git commit -m "feat(scoring): rate model (REH-53)"
```

______________________________________________________________________

## Task 5: `fit-scorer` CLI — fit, persist, and report holdout calibration

**Files:**

- Create: `rehoboam/scoring/v2/coefficients.py`
- Modify: `rehoboam/cli.py`
- Create: `tests/test_scoring_v2/test_coefficients.py`

**Interfaces:**

- Consumes: everything above

- Produces:

  - `COEFFICIENTS_PATH: Path` — `rehoboam/scoring/v2/coefficients.json`
  - `save_coefficients(availability: AvailabilityModel, rate: RateModel, meta: dict, path: Path = COEFFICIENTS_PATH) -> None`
  - `load_coefficients(path: Path = COEFFICIENTS_PATH) -> tuple[AvailabilityModel, RateModel, dict]`
  - CLI command `rehoboam fit-scorer`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for rehoboam.scoring.v2.coefficients — persistence round-trip."""

from __future__ import annotations

import pytest

from rehoboam.scoring.v2.availability import fit_availability
from rehoboam.scoring.v2.coefficients import load_coefficients, save_coefficients
from rehoboam.scoring.v2.features import FeatureRow
from rehoboam.scoring.v2.rate import fit_rate


def _row(pid: str, prev: int | None, status: int, points: int) -> FeatureRow:
    return FeatureRow(
        player_id=pid,
        season="2024/2025",
        day_number=1,
        prev_status=prev,
        rolling_minutes_3=90.0,
        matches_seen=5,
        target_status=status,
        target_points=points,
    )


def test_round_trip_preserves_predictions(tmp_path):
    rows = [_row("a", 5, 5, 80)] * 20 + [_row("b", 5, 3, 20)] * 20
    availability = fit_availability(rows)
    rate = fit_rate(rows, {"a": "Forward", "b": "Midfielder"})

    path = tmp_path / "coefficients.json"
    save_coefficients(availability, rate, {"trained_on": "2024/2025"}, path)
    loaded_av, loaded_rate, meta = load_coefficients(path)

    assert loaded_av.predict(5) == availability.predict(5)
    assert loaded_rate.predict("a", 5, "Forward") == pytest.approx(
        rate.predict("a", 5, "Forward")
    )
    assert meta["trained_on"] == "2024/2025"


def test_saved_file_is_human_readable_json(tmp_path):
    """Coefficients are committed to the repo — a diff must be reviewable."""
    rows = [_row("a", 5, 5, 80)] * 10
    path = tmp_path / "coefficients.json"
    save_coefficients(
        fit_availability(rows), fit_rate(rows, {"a": "Forward"}), {}, path
    )

    text = path.read_text()
    assert "\n" in text, "must be pretty-printed, not one line"
    assert '"availability"' in text
    assert '"rate"' in text


def test_missing_file_raises_a_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_coefficients(tmp_path / "nope.json")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_scoring_v2/test_coefficients.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement persistence**

`rehoboam/scoring/v2/coefficients.py`:

```python
"""Persistence for fitted v2 scorer coefficients.

Coefficients live in a committed JSON file rather than a database because they
ship to the Azure Function with the code, and because a pretty-printed diff makes
a refit reviewable — you can see what moved.
"""

from __future__ import annotations

import json
from pathlib import Path

from rehoboam.scoring.v2.availability import AvailabilityModel
from rehoboam.scoring.v2.rate import RateModel

COEFFICIENTS_PATH = Path(__file__).parent / "coefficients.json"


def save_coefficients(
    availability: AvailabilityModel,
    rate: RateModel,
    meta: dict,
    path: Path = COEFFICIENTS_PATH,
) -> None:
    """Write fitted models to disk, pretty-printed for reviewable diffs."""
    payload = {
        "meta": meta,
        "availability": availability.to_dict(),
        "rate": rate.to_dict(),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def load_coefficients(
    path: Path = COEFFICIENTS_PATH,
) -> tuple[AvailabilityModel, RateModel, dict]:
    """Load fitted models. Raises FileNotFoundError if never fitted."""
    if not path.exists():
        raise FileNotFoundError(
            f"No fitted coefficients at {path}. Run `rehoboam fit-scorer` first."
        )
    payload = json.loads(path.read_text())
    return (
        AvailabilityModel.from_dict(payload["availability"]),
        RateModel.from_dict(payload["rate"]),
        payload.get("meta", {}),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_scoring_v2/test_coefficients.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Add the CLI command**

Append to `rehoboam/cli.py`, following the conventions of the neighbouring
commands (Typer options, Rich table output, lazy imports inside the function):

```python
@app.command("fit-scorer")
def fit_scorer(
    availability_k: float = typer.Option(
        20.0, "--availability-k", help="Shrinkage pseudo-count for the transition model"
    ),
    rate_k: float = typer.Option(
        5.0, "--rate-k", help="Shrinkage pseudo-count for per-player quality"
    ),
):
    """Fit the v2 scorer components and write coefficients.json.

    Trains on seasons up to 2024/25 and reports calibration on the held-out
    2025/26 season. Never fits on the holdout — that season is what the whole
    rebuild is judged against.
    """
    from .enrichment.corpus import TrainingCorpus
    from .scoring.v2.availability import fit_availability
    from .scoring.v2.coefficients import COEFFICIENTS_PATH, save_coefficients
    from .scoring.v2.dataset import (
        HOLDOUT_SEASON,
        TRAIN_MAX_SEASON,
        load_match_rows,
        load_positions,
        split_rows,
    )
    from .scoring.v2.features import build_feature_rows
    from .scoring.v2.rate import fit_rate

    corpus = TrainingCorpus()
    by_player = load_match_rows(corpus.db_path)
    positions = load_positions(corpus.db_path)

    all_rows = []
    for matches in by_player.values():
        all_rows.extend(build_feature_rows(matches))

    train, holdout = split_rows(all_rows)
    console.print(
        f"[cyan]train {len(train):,} rows (≤{TRAIN_MAX_SEASON}) · "
        f"holdout {len(holdout):,} rows ({HOLDOUT_SEASON})[/cyan]"
    )
    if not train:
        console.print("[red]No training rows — is the corpus populated?[/red]")
        raise typer.Exit(1)

    availability = fit_availability(train, shrinkage_k=availability_k)
    rate = fit_rate(train, positions, shrinkage_k=rate_k)

    save_coefficients(
        availability,
        rate,
        {
            "train_max_season": TRAIN_MAX_SEASON,
            "holdout_season": HOLDOUT_SEASON,
            "train_rows": len(train),
            "availability_k": availability_k,
            "rate_k": rate_k,
        },
    )

    table = Table(title="Availability transitions (fitted)")
    table.add_column("prev")
    for s in (1, 3, 4, 5):
        table.add_column(f"→{s}", justify="right")
    for prev in (1, 3, 4, 5):
        probs = availability.predict(prev)
        table.add_row(str(prev), *(f"{probs[s]:.1%}" for s in (1, 3, 4, 5)))
    console.print(table)

    rates = Table(title="Base rate by status (real points)")
    rates.add_column("status")
    rates.add_column("points", justify="right")
    for s in (1, 3, 4, 5):
        rates.add_row(str(s), f"{rate.base_rate.get(s, 0.0):.1f}")
    console.print(rates)

    console.print(f"[dim]Coefficients: {COEFFICIENTS_PATH}[/dim]")
```

- [ ] **Step 6: Verify the command registers and run it for real**

```bash
uv run rehoboam --help          # fit-scorer must appear
uv run rehoboam fit-scorer
```

Expect the fitted transition table to broadly match the measured matrix in this
plan's header (starters staying starters around 82%, unused subs around 71%), and
base rates near 85 for status 5 and 18.5 for status 3.

**If the fitted numbers diverge sharply from those, stop and report** — either the
corpus is not populated as expected or the feature construction is dropping rows.

- [ ] **Step 7: Run the full suite and commit**

```bash
uv run pytest -q
git add rehoboam/scoring/v2/ rehoboam/cli.py tests/test_scoring_v2/
git commit -m "feat(scoring): fit-scorer CLI and coefficient persistence"
```

______________________________________________________________________

## Self-Review

**Spec coverage.** REH-52 (availability) → Task 3, with the corrected input list:
fitted on historical `status`, with live `prob`/injury as serving-time overrides
rather than trained features. REH-53 (rate) → Task 4, including shrinkage toward a
position prior and real-point output. Shared scaffolding → Tasks 1–2. Fitting and
persistence → Task 5.

**Deliberately deferred, with the ticket that owns each:** wiring into
`score_player` and deleting the legacy modules is REH-55; the baseline comparison
and ship decision is REH-56; context multipliers (fixture, home/away, DGW) are
REH-54.

**Known gap this plan does not close.** Task 5 reports fitted parameters but does
not score the holdout. Calibration on 2025/26 — "does a predicted-82%-to-start
player actually start 82% of the time?" — needs the integration from REH-55 to
produce comparable predictions. The `split_rows` holdout is built and unused here
on purpose; REH-56 consumes it. Stated rather than silently skipped.

**Placeholder scan.** No TBDs. Task 5's Step 6 has a stop condition rather than a
vague "check it looks right".

**Type consistency.** `FeatureRow` fields are consumed identically in Tasks 3–5.
`PLAYED_STATUSES` is defined once in `features.py` and imported by both models.
`to_dict`/`from_dict` pairs on both models match what `coefficients.py` reads.
`TrainingCorpus.db_path` is the existing attribute used by `dataset.py` loaders.
