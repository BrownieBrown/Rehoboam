"""Tests for matchday outcome tracking and EP accuracy factor."""

import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rehoboam.bid_learner import BidLearner


def _set_outcome_timestamp(db_path: Path, player_id: str, days_ago: float) -> None:
    """Overwrite timestamps for all outcomes of *player_id* to a past date.

    SQLite's CURRENT_TIMESTAMP stores UTC text; we use the same format so the
    decay calculation reads it back correctly.
    """
    ts = (datetime.now(tz=timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE matchday_outcomes SET timestamp = ? WHERE player_id = ?",
            (ts, player_id),
        )
        conn.commit()


class TestMatchdayOutcomeRecording:
    def test_record_and_retrieve(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            learner = BidLearner(db_path=Path(tmpdir) / "test.db")
            learner.record_matchday_outcome(
                player_id="p1",
                player_position="MID",
                matchday_date="2026-03-15",
                predicted_ep=60.0,
                actual_points=55.0,
                was_in_best_11=True,
                opponent_strength="Easy",
            )
            # Should not raise on duplicate (INSERT OR REPLACE)
            learner.record_matchday_outcome(
                player_id="p1",
                player_position="MID",
                matchday_date="2026-03-15",
                predicted_ep=60.0,
                actual_points=55.0,
                was_in_best_11=True,
                opponent_strength="Easy",
            )

    def test_record_multiple_matchdays(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            learner = BidLearner(db_path=Path(tmpdir) / "test.db")
            for i in range(5):
                learner.record_matchday_outcome(
                    player_id="p1",
                    player_position="MID",
                    matchday_date=f"2026-03-{10 + i}",
                    predicted_ep=50.0,
                    actual_points=45.0,
                )
            factor = learner.get_ep_accuracy_factor(player_id="p1")
            assert 0.85 <= factor <= 0.95

    def test_record_with_optional_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            learner = BidLearner(db_path=Path(tmpdir) / "test.db")
            # All optional fields provided
            learner.record_matchday_outcome(
                player_id="p2",
                player_position="FWD",
                matchday_date="2026-03-15",
                predicted_ep=70.0,
                actual_points=80.0,
                was_in_best_11=True,
                opponent_strength="Hard",
                purchase_price=15_000_000,
                marginal_ep_gain_at_purchase=12.5,
            )
            # Confirm no error and factor works
            factor = learner.get_ep_accuracy_factor(player_id="p2")
            # Only 1 matchday, below min_matchdays=3, should fall back or use whatever data
            assert 0.5 <= factor <= 1.0


class TestEPAccuracyFactor:
    def test_returns_1_with_no_data(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            learner = BidLearner(db_path=Path(tmpdir) / "test.db")
            factor = learner.get_ep_accuracy_factor(player_id="unknown")
            assert factor == 1.0

    def test_capped_at_1(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            learner = BidLearner(db_path=Path(tmpdir) / "test.db")
            for i in range(5):
                learner.record_matchday_outcome(
                    player_id="p1",
                    player_position="FWD",
                    matchday_date=f"2026-03-{10 + i}",
                    predicted_ep=30.0,
                    actual_points=50.0,
                )
            factor = learner.get_ep_accuracy_factor(player_id="p1")
            assert factor == 1.0

    def test_floored_at_0_5(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            learner = BidLearner(db_path=Path(tmpdir) / "test.db")
            for i in range(5):
                learner.record_matchday_outcome(
                    player_id="p1",
                    player_position="DEF",
                    matchday_date=f"2026-03-{10 + i}",
                    predicted_ep=80.0,
                    actual_points=10.0,
                )
            factor = learner.get_ep_accuracy_factor(player_id="p1")
            assert factor == 0.5

    def test_position_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            learner = BidLearner(db_path=Path(tmpdir) / "test.db")
            for p in range(5):
                for i in range(5):
                    learner.record_matchday_outcome(
                        player_id=f"mid{p}",
                        player_position="MID",
                        matchday_date=f"2026-03-{10 + i}",
                        predicted_ep=50.0,
                        actual_points=40.0,
                    )
            factor = learner.get_ep_accuracy_factor(
                player_id="new_mid", position="MID", min_matchdays=3
            )
            assert 0.75 <= factor <= 0.85

    def test_no_player_id_no_position(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            learner = BidLearner(db_path=Path(tmpdir) / "test.db")
            # No player_id and no position — should return default 1.0
            factor = learner.get_ep_accuracy_factor()
            assert factor == 1.0

    def test_player_overrides_position(self):
        """Player-specific accuracy should be used when player has sufficient data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            learner = BidLearner(db_path=Path(tmpdir) / "test.db")
            # Position-level: MID scores ~80% of prediction
            for p in range(5):
                for i in range(5):
                    learner.record_matchday_outcome(
                        player_id=f"mid{p}",
                        player_position="MID",
                        matchday_date=f"2026-03-{10 + i}",
                        predicted_ep=50.0,
                        actual_points=40.0,
                    )
            # This specific player is a perfect predictor (actual == predicted)
            for i in range(5):
                learner.record_matchday_outcome(
                    player_id="star_mid",
                    player_position="MID",
                    matchday_date=f"2026-03-{10 + i}",
                    predicted_ep=60.0,
                    actual_points=60.0,
                )
            factor = learner.get_ep_accuracy_factor(
                player_id="star_mid", position="MID", min_matchdays=3
            )
            # Should be 1.0 (capped), not 0.8 from position fallback
            assert factor == 1.0


class TestTimeDecay:
    """Recent matchday outcomes should weigh more than old ones."""

    def test_recent_data_dominates_old_data(self):
        """When a player's recent predictions are accurate but old ones were bad,
        the returned factor should lean toward the recent (accurate) signal."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            learner = BidLearner(db_path=db_path)

            # Old (inaccurate) records: 5 matchdays where actual = 50% of predicted
            for i in range(5):
                learner.record_matchday_outcome(
                    player_id="p_old",
                    player_position="MID",
                    matchday_date=f"2025-10-{10 + i}",
                    predicted_ep=80.0,
                    actual_points=40.0,
                )
            _set_outcome_timestamp(db_path, "p_old", days_ago=180)

            # Recent (accurate) records: 5 matchdays where actual = 95% of predicted
            for i in range(5):
                learner.record_matchday_outcome(
                    player_id="p_old",
                    player_position="MID",
                    matchday_date=f"2026-04-{1 + i}",
                    predicted_ep=80.0,
                    actual_points=76.0,
                )
            # Recent records use CURRENT_TIMESTAMP (= now, age ~0)

            factor = learner.get_ep_accuracy_factor(player_id="p_old", min_matchdays=3)
            # Without decay: raw average would pull toward 0.725
            # With 60-day half-life: 180-day-old rows decay to ~0.125x weight
            # so factor should be ~0.93 — well above 0.8
            assert factor > 0.85, (
                f"Recent accurate data should dominate (got {factor:.3f}); "
                "decay may not be applied."
            )

    def test_identical_data_same_age_gives_same_result_as_unweighted(self):
        """Sanity check: when all rows have age ≈ 0 the decay weight is ≈ 1,
        so the factor matches the old unweighted average."""
        with tempfile.TemporaryDirectory() as tmpdir:
            learner = BidLearner(db_path=Path(tmpdir) / "test.db")
            for i in range(5):
                learner.record_matchday_outcome(
                    player_id="p1",
                    player_position="FWD",
                    matchday_date=f"2026-04-{1 + i}",
                    predicted_ep=50.0,
                    actual_points=45.0,
                )
            factor = learner.get_ep_accuracy_factor(player_id="p1", min_matchdays=3)
            # 45/50 = 0.9 — no decay effect because all rows are fresh
            assert 0.88 <= factor <= 0.92


class TestWonPlayerOutcomeQuality:
    def test_returns_default_with_no_data(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            learner = BidLearner(db_path=Path(tmpdir) / "test.db")
            quality = learner._get_won_player_outcome_quality()
            assert quality == 1.0

    def test_quality_above_1_when_players_outperform(self):
        """Won auction players who score more than predicted → quality > 1 (capped at 1.2)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            learner = BidLearner(db_path=Path(tmpdir) / "test.db")
            from rehoboam.bid_learner import AuctionOutcome

            # Record won auctions
            for i in range(3):
                learner.record_outcome(
                    AuctionOutcome(
                        player_id=f"p{i}",
                        player_name=f"Player {i}",
                        our_bid=10_000_000,
                        asking_price=9_000_000,
                        our_overbid_pct=11.1,
                        won=True,
                        timestamp=1700000000.0 + i,
                    )
                )
                # Players outperformed predictions
                learner.record_matchday_outcome(
                    player_id=f"p{i}",
                    player_position="MID",
                    matchday_date=f"2026-03-{10 + i}",
                    predicted_ep=50.0,
                    actual_points=80.0,
                )
            quality = learner._get_won_player_outcome_quality()
            assert 1.0 <= quality <= 1.2

    def test_quality_below_1_when_players_underperform(self):
        """Won auction players who score less than predicted → quality < 1 (floored at 0.5)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            learner = BidLearner(db_path=Path(tmpdir) / "test.db")
            from rehoboam.bid_learner import AuctionOutcome

            for i in range(3):
                learner.record_outcome(
                    AuctionOutcome(
                        player_id=f"p{i}",
                        player_name=f"Player {i}",
                        our_bid=10_000_000,
                        asking_price=9_000_000,
                        our_overbid_pct=11.1,
                        won=True,
                        timestamp=1700000000.0 + i,
                    )
                )
                learner.record_matchday_outcome(
                    player_id=f"p{i}",
                    player_position="MID",
                    matchday_date=f"2026-03-{10 + i}",
                    predicted_ep=80.0,
                    actual_points=20.0,
                )
            quality = learner._get_won_player_outcome_quality()
            assert 0.5 <= quality < 1.0


class TestEPRecommendedOverbid:
    def test_returns_dict_with_required_keys(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            learner = BidLearner(db_path=Path(tmpdir) / "test.db")
            result = learner.get_ep_recommended_overbid(
                asking_price=10_000_000,
                marginal_ep_gain=15.0,
                market_value=10_000_000,
                budget_ceiling=50_000_000,
            )
            assert "recommended_overbid_pct" in result
            assert "reason" in result
            assert isinstance(result["recommended_overbid_pct"], float)
            assert isinstance(result["reason"], str)

    def test_high_ep_gain_increases_overbid(self):
        """High marginal EP gain should produce higher overbid than low EP gain."""
        with tempfile.TemporaryDirectory() as tmpdir:
            learner = BidLearner(db_path=Path(tmpdir) / "test.db")
            low_ep = learner.get_ep_recommended_overbid(
                asking_price=10_000_000,
                marginal_ep_gain=5.0,
                market_value=10_000_000,
                budget_ceiling=50_000_000,
            )
            high_ep = learner.get_ep_recommended_overbid(
                asking_price=10_000_000,
                marginal_ep_gain=40.0,
                market_value=10_000_000,
                budget_ceiling=50_000_000,
            )
            assert high_ep["recommended_overbid_pct"] >= low_ep["recommended_overbid_pct"]

    def test_budget_ceiling_limits_overbid(self):
        """Budget ceiling must constrain the overbid so we don't exceed budget."""
        with tempfile.TemporaryDirectory() as tmpdir:
            learner = BidLearner(db_path=Path(tmpdir) / "test.db")
            # Tight budget ceiling: only 1M above asking price on a 10M player = 10% max
            result = learner.get_ep_recommended_overbid(
                asking_price=10_000_000,
                marginal_ep_gain=50.0,
                market_value=10_000_000,
                budget_ceiling=11_000_000,
            )
            assert result["recommended_overbid_pct"] <= 10.0

    def test_overbid_pct_is_non_negative(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            learner = BidLearner(db_path=Path(tmpdir) / "test.db")
            result = learner.get_ep_recommended_overbid(
                asking_price=10_000_000,
                marginal_ep_gain=0.0,
                market_value=10_000_000,
                budget_ceiling=50_000_000,
            )
            assert result["recommended_overbid_pct"] >= 0.0
