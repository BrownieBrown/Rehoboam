"""Tests for rehoboam.enrichment.historical_ids — recovering departed players.

``fetch_universe`` (sweep.py) only sees players currently registered in the
league via ``/lineup/selection``. A backtest replaying a past season needs
every player who was ever actually held, including ones who have since left
the Bundesliga. ``gather_historical_player_ids`` recovers those ids from the
three learning-DB tables that still reference them.
"""

from __future__ import annotations

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
