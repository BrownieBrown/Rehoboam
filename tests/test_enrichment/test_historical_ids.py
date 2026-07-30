"""Tests for rehoboam.enrichment.historical_ids — recovering departed players.

``fetch_universe`` (sweep.py) only sees players currently registered in the
league via ``/lineup/selection``. A backtest replaying a past season needs
every player who was ever actually held, including ones who have since left
the Bundesliga. ``gather_historical_player_ids`` recovers those ids from the
three learning-DB tables that still reference them.
"""

from __future__ import annotations

import sqlite3

from rehoboam.bid_learner import BidLearner, FlipOutcome
from rehoboam.enrichment.historical_ids import gather_historical_player_ids


def test_gather_historical_player_ids_unions_three_tables(tmp_path):
    learner = BidLearner(db_path=tmp_path / "bid_learning.db")

    learner.record_flip(
        FlipOutcome(
            player_id="1",
            player_name="Flipped Player",
            buy_price=1_000_000,
            sell_price=1_200_000,
            profit=200_000,
            profit_pct=20.0,
            hold_days=10,
            buy_date=1000.0,
            sell_date=2000.0,
        )
    )
    learner.record_player_mv_snapshot(
        [{"player_id": "2", "snapshot_at": 1000.0, "market_value": 5_000_000}]
    )
    learner.record_matchday_lineup_result(
        league_id="L1",
        day_number=1,
        matchday_date="2025-08-23",
        total_points=500,
        lineup_player_ids=["3", "4"],
        lineup_count=11,
        snapshot_at=1000.0,
    )

    ids = gather_historical_player_ids(learner.db_path)

    assert set(ids) == {"1", "2", "3", "4"}


def test_gather_historical_player_ids_dedupes_across_tables(tmp_path):
    learner = BidLearner(db_path=tmp_path / "bid_learning.db")

    learner.record_flip(
        FlipOutcome(
            player_id="1",
            player_name="Same Player",
            buy_price=1_000_000,
            sell_price=1_200_000,
            profit=200_000,
            profit_pct=20.0,
            hold_days=10,
            buy_date=1000.0,
            sell_date=2000.0,
        )
    )
    learner.record_player_mv_snapshot(
        [{"player_id": "1", "snapshot_at": 1000.0, "market_value": 5_000_000}]
    )
    learner.record_matchday_lineup_result(
        league_id="L1",
        day_number=1,
        matchday_date="2025-08-23",
        total_points=500,
        lineup_player_ids=["1", "5"],
        lineup_count=11,
        snapshot_at=1000.0,
    )

    ids = gather_historical_player_ids(learner.db_path)

    assert ids == ["1", "5"]


def test_gather_historical_player_ids_handles_empty_db(tmp_path):
    learner = BidLearner(db_path=tmp_path / "bid_learning.db")

    assert gather_historical_player_ids(learner.db_path) == []


def test_gather_historical_player_ids_skips_malformed_lineup_row(tmp_path):
    """The writer (record_matchday_lineup_result) always json.dumps a real
    list, so a malformed row can only get in via a hand-edit of the DB — but
    CLAUDE.md's own documented debugging workflow is opening the SQLite file
    directly, so this is a realistic path, not a hypothetical. One bad row
    must not take down ids recoverable from the good rows around it."""
    learner = BidLearner(db_path=tmp_path / "bid_learning.db")

    learner.record_matchday_lineup_result(
        league_id="L1",
        day_number=1,
        matchday_date="2025-08-23",
        total_points=500,
        lineup_player_ids=["1", "2"],
        lineup_count=11,
        snapshot_at=1000.0,
    )
    learner.record_matchday_lineup_result(
        league_id="L1",
        day_number=3,
        matchday_date="2025-09-06",
        total_points=480,
        lineup_player_ids=["5", "6"],
        lineup_count=11,
        snapshot_at=3000.0,
    )
    # Hand-corrupt the middle row the way a direct `sqlite3 logs/bid_learning.db`
    # edit could — not valid JSON at all.
    with sqlite3.connect(learner.db_path) as conn:
        conn.execute(
            "INSERT INTO matchday_lineup_results "
            "(league_id, day_number, matchday_date, total_points, "
            "lineup_player_ids, lineup_count, snapshot_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("L1", 2, "2025-08-30", 490, "not valid json{{{", 11, 2000.0),
        )
        conn.commit()

    ids = gather_historical_player_ids(learner.db_path)

    assert set(ids) == {"1", "2", "5", "6"}


def test_gather_historical_player_ids_skips_lineup_row_that_is_not_a_list(tmp_path):
    """Valid JSON that isn't a list (e.g. an object) is a different failure
    mode than invalid JSON text — decoding succeeds, so this must be caught
    separately from the JSONDecodeError path."""
    learner = BidLearner(db_path=tmp_path / "bid_learning.db")

    learner.record_matchday_lineup_result(
        league_id="L1",
        day_number=1,
        matchday_date="2025-08-23",
        total_points=500,
        lineup_player_ids=["1", "2"],
        lineup_count=11,
        snapshot_at=1000.0,
    )
    with sqlite3.connect(learner.db_path) as conn:
        conn.execute(
            "INSERT INTO matchday_lineup_results "
            "(league_id, day_number, matchday_date, total_points, "
            "lineup_player_ids, lineup_count, snapshot_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("L1", 2, "2025-08-30", 490, '{"not": "a list"}', 11, 2000.0),
        )
        conn.commit()

    ids = gather_historical_player_ids(learner.db_path)

    assert set(ids) == {"1", "2"}
