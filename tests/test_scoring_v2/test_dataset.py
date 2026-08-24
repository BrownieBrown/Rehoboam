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
        conn.executescript(
            """
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
            """
        )
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


def test_split_trains_through_2025_26_and_holds_out_the_current_season():
    """2025/26 validated the rebuild, then had to be folded back in.

    Serving a model fitted only to 2024/25 meant every player whose record
    began in 2025/26 had no fitted quality and fell back to a position prior.
    """
    train, holdout = split_rows([_fr("2023/2024"), _fr("2024/2025"), _fr("2025/2026")])
    assert [r.season for r in train] == ["2023/2024", "2024/2025", "2025/2026"]
    assert holdout == []


def test_split_excludes_seasons_after_the_holdout():
    """Anything past the holdout belongs in neither set."""
    train, holdout = split_rows([_fr("2024/2025"), _fr("2027/2028")])
    assert [r.season for r in train] == ["2024/2025"]
    assert holdout == []


def test_the_current_season_is_the_holdout_not_training_data():
    """The in-progress season must never be trained on, even once results land."""
    train, holdout = split_rows([_fr("2025/2026"), _fr("2026/2027")])
    assert [r.season for r in train] == ["2025/2026"]
    assert [r.season for r in holdout] == ["2026/2027"]


def test_split_constants_are_what_the_spec_requires():
    assert TRAIN_MAX_SEASON == "2025/2026"
    assert HOLDOUT_SEASON == "2026/2027"


def test_split_never_leaks_holdout_into_train():
    """The guard. If this ever fails, every downstream number is invalid."""
    rows = [_fr(s) for s in ("2013/2014", "2024/2025", "2025/2026", "2026/2027")]
    train, holdout = split_rows(rows)
    assert all(r.season <= TRAIN_MAX_SEASON for r in train)
    assert all(r.season == HOLDOUT_SEASON for r in holdout)
