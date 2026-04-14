"""Tests for EP-based bidding — calculate_ep_bid()."""

from rehoboam.bidding_strategy import BidRecommendation, SmartBidding
from rehoboam.scoring.models import SellPlan, SellPlanEntry


class TestEPBidTiers:
    def test_must_have_tier(self):
        bidding = SmartBidding()
        result = bidding.calculate_ep_bid(
            asking_price=10_000_000,
            market_value=12_000_000,
            expected_points=80.0,
            marginal_ep_gain=25.0,
            confidence=0.8,
            current_budget=20_000_000,
            sell_plan=None,
        )
        assert result.recommended_bid > 0
        assert result.overbid_pct >= 10.0

    def test_no_improvement_no_bid(self):
        bidding = SmartBidding()
        result = bidding.calculate_ep_bid(
            asking_price=10_000_000,
            market_value=12_000_000,
            expected_points=20.0,
            marginal_ep_gain=0,
            confidence=0.5,
            current_budget=20_000_000,
            sell_plan=None,
        )
        assert result.recommended_bid == 0

    def test_marginal_tier_conservative(self):
        bidding = SmartBidding()
        result = bidding.calculate_ep_bid(
            asking_price=5_000_000,
            market_value=5_000_000,
            expected_points=35.0,
            marginal_ep_gain=3.0,
            confidence=0.6,
            current_budget=10_000_000,
            sell_plan=None,
        )
        assert result.recommended_bid > 0
        assert result.overbid_pct < 15.0

    def test_strong_upgrade_tier(self):
        bidding = SmartBidding()
        result = bidding.calculate_ep_bid(
            asking_price=8_000_000,
            market_value=8_000_000,
            expected_points=60.0,
            marginal_ep_gain=15.0,
            confidence=0.75,
            current_budget=20_000_000,
            sell_plan=None,
        )
        assert result.recommended_bid > 0
        # strong_upgrade tier gets +6% bonus, total overbid should be > marginal tier
        assert result.overbid_pct > 0

    def test_solid_upgrade_tier(self):
        bidding = SmartBidding()
        result = bidding.calculate_ep_bid(
            asking_price=6_000_000,
            market_value=6_000_000,
            expected_points=45.0,
            marginal_ep_gain=7.0,
            confidence=0.65,
            current_budget=15_000_000,
            sell_plan=None,
        )
        assert result.recommended_bid > 0
        assert result.marginal_ep_gain == 7.0

    def test_marginal_ep_gain_stored_in_result(self):
        bidding = SmartBidding()
        result = bidding.calculate_ep_bid(
            asking_price=5_000_000,
            market_value=5_000_000,
            expected_points=40.0,
            marginal_ep_gain=12.0,
            confidence=0.7,
            current_budget=15_000_000,
            sell_plan=None,
        )
        assert result.marginal_ep_gain == 12.0


class TestBudgetCeiling:
    def test_bid_within_budget(self):
        bidding = SmartBidding()
        result = bidding.calculate_ep_bid(
            asking_price=15_000_000,
            market_value=15_000_000,
            expected_points=60.0,
            marginal_ep_gain=15.0,
            confidence=0.8,
            current_budget=20_000_000,
            sell_plan=None,
        )
        assert result.recommended_bid <= 20_000_000

    def test_bid_with_sell_plan_extends_budget(self):
        plan = SellPlan(
            players_to_sell=[SellPlanEntry("s1", "Sell Player", 10_000_000, 20.0, False)],
            total_recovery=10_000_000,
            net_budget_after=5_000_000,
            is_viable=True,
            ep_impact=-20.0,
            reasoning="Sell bench player",
        )
        bidding = SmartBidding()
        result = bidding.calculate_ep_bid(
            asking_price=12_000_000,
            market_value=12_000_000,
            expected_points=70.0,
            marginal_ep_gain=20.0,
            confidence=0.9,
            current_budget=5_000_000,
            sell_plan=plan,
        )
        assert result.recommended_bid > 0
        assert result.sell_plan is not None

    def test_budget_ceiling_with_sell_plan_is_combined(self):
        """Budget ceiling should be current_budget + sell_plan.total_recovery."""
        plan = SellPlan(
            players_to_sell=[SellPlanEntry("s1", "Sell Player", 8_000_000, 15.0, False)],
            total_recovery=8_000_000,
            net_budget_after=3_000_000,
            is_viable=True,
            ep_impact=-15.0,
            reasoning="Sell for budget",
        )
        bidding = SmartBidding()
        result = bidding.calculate_ep_bid(
            asking_price=5_000_000,
            market_value=5_000_000,
            expected_points=55.0,
            marginal_ep_gain=18.0,
            confidence=0.8,
            current_budget=2_000_000,
            sell_plan=plan,
        )
        # Budget ceiling = 2M + 8M = 10M; bid should not exceed that
        assert result.recommended_bid <= 10_000_000


class TestMarketValueFloor:
    def test_never_bid_below_market_value(self):
        bidding = SmartBidding()
        result = bidding.calculate_ep_bid(
            asking_price=3_000_000,
            market_value=5_000_000,
            expected_points=50.0,
            marginal_ep_gain=10.0,
            confidence=0.7,
            current_budget=10_000_000,
            sell_plan=None,
        )
        assert result.recommended_bid >= 5_000_000

    def test_market_value_floor_is_one_percent_above(self):
        """Market value floor = market_value * 1.01."""
        bidding = SmartBidding()
        result = bidding.calculate_ep_bid(
            asking_price=1_000_000,
            market_value=10_000_000,
            expected_points=50.0,
            marginal_ep_gain=10.0,
            confidence=0.7,
            current_budget=20_000_000,
            sell_plan=None,
        )
        assert result.recommended_bid >= int(10_000_000 * 1.01)


class TestBidRecommendationBackwardCompat:
    def test_max_profitable_bid_alias(self):
        rec = BidRecommendation(
            base_price=5_000_000,
            recommended_bid=6_000_000,
            overbid_amount=1_000_000,
            overbid_pct=20.0,
            reasoning="test",
            budget_ceiling=10_000_000,
            sell_plan=None,
            marginal_ep_gain=15.0,
        )
        assert rec.max_profitable_bid == 10_000_000

    def test_backward_compat_construction(self):
        """Old callers can construct without new fields."""
        rec = BidRecommendation(
            base_price=5_000_000,
            recommended_bid=6_000_000,
            overbid_amount=1_000_000,
            overbid_pct=20.0,
            reasoning="test",
            budget_ceiling=10_000_000,
        )
        assert rec.sell_plan is None
        assert rec.marginal_ep_gain == 0.0

    def test_existing_calculate_bid_still_works(self):
        """calculate_bid() must be fully backward compatible."""
        bidding = SmartBidding()
        result = bidding.calculate_bid(
            asking_price=10_000_000,
            market_value=10_000_000,
            value_score=70.0,
            confidence=0.8,
        )
        # Must still return a BidRecommendation with the alias working
        assert result.max_profitable_bid == result.budget_ceiling
        assert result.sell_plan is None
        assert result.marginal_ep_gain == 0.0

    def test_api_route_field_accessible(self):
        """Simulate api/routes/trading.py accessing recommendation.max_profitable_bid."""
        bidding = SmartBidding()
        result = bidding.calculate_bid(
            asking_price=8_000_000,
            market_value=8_000_000,
            value_score=65.0,
            confidence=0.75,
            trend_change_pct=2.0,
        )
        # This is what api/routes/trading.py:253 does
        max_bid = result.max_profitable_bid
        assert isinstance(max_bid, int)
        assert max_bid > 0


class TestCompetitorAwareBidding:
    """Tests for offer_count and has_aggressive_competitors bid modulation."""

    def _must_have_bid(self, bidding: SmartBidding, **overrides):
        """Shared setup for a must-have tier bid with knobs for competitor args."""
        kwargs = {
            "asking_price": 10_000_000,
            "market_value": 10_000_000,
            "expected_points": 80.0,
            "marginal_ep_gain": 25.0,
            "confidence": 0.8,
            "current_budget": 30_000_000,
            "sell_plan": None,
            "trend_change_pct": 0.0,  # neutral trend, no scaling
        }
        kwargs.update(overrides)
        return bidding.calculate_ep_bid(**kwargs)

    def _marginal_bid(self, bidding: SmartBidding, **overrides):
        """Shared setup for a marginal tier bid."""
        kwargs = {
            "asking_price": 5_000_000,
            "market_value": 5_000_000,
            "expected_points": 30.0,
            "marginal_ep_gain": 3.0,
            "confidence": 0.6,
            "current_budget": 15_000_000,
            "sell_plan": None,
            "trend_change_pct": 0.0,
        }
        kwargs.update(overrides)
        return bidding.calculate_ep_bid(**kwargs)

    def test_no_competition_no_change(self):
        """offer_count=0 should produce the same bid as baseline."""
        bidding = SmartBidding()
        baseline = self._must_have_bid(bidding, offer_count=0)
        assert baseline.recommended_bid > 0

    def test_contested_must_have_bids_higher(self):
        """Heavily contested must-have → higher bid than uncontested."""
        bidding = SmartBidding()
        uncontested = self._must_have_bid(bidding, offer_count=0)
        contested = self._must_have_bid(bidding, offer_count=4)
        assert contested.recommended_bid > uncontested.recommended_bid
        assert "contested" in contested.reasoning

    def test_marginal_contested_skips(self):
        """Marginal EP + 2+ offers → don't feed the auction, skip."""
        bidding = SmartBidding()
        result = self._marginal_bid(bidding, offer_count=2)
        assert result.recommended_bid == 0
        assert "Marginal" in result.reasoning
        assert "skipping" in result.reasoning.lower()

    def test_marginal_uncontested_still_bids(self):
        """Marginal EP + 0 offers → still bid (nothing to avoid)."""
        bidding = SmartBidding()
        result = self._marginal_bid(bidding, offer_count=0)
        assert result.recommended_bid > 0

    def test_solid_upgrade_skipped_when_whales_and_heavy_contest(self):
        """Solid upgrade + 4+ offers + aggressive league → don't outbid whales."""
        bidding = SmartBidding()
        result = bidding.calculate_ep_bid(
            asking_price=8_000_000,
            market_value=8_000_000,
            expected_points=45.0,
            marginal_ep_gain=7.0,  # solid_upgrade tier
            confidence=0.7,
            current_budget=20_000_000,
            sell_plan=None,
            trend_change_pct=0.0,
            offer_count=5,
            has_aggressive_competitors=True,
        )
        assert result.recommended_bid == 0
        assert "whale" in result.reasoning.lower()

    def test_solid_upgrade_bids_when_no_whales(self):
        """Same solid upgrade + offers but no whales → still bids."""
        bidding = SmartBidding()
        result = bidding.calculate_ep_bid(
            asking_price=8_000_000,
            market_value=8_000_000,
            expected_points=45.0,
            marginal_ep_gain=7.0,
            confidence=0.7,
            current_budget=20_000_000,
            sell_plan=None,
            trend_change_pct=0.0,
            offer_count=5,
            has_aggressive_competitors=False,
        )
        assert result.recommended_bid > 0

    def test_must_have_never_skipped_even_with_whales(self):
        """must_have tier is always worth fighting for, regardless of competition."""
        bidding = SmartBidding()
        result = self._must_have_bid(
            bidding,
            offer_count=6,
            has_aggressive_competitors=True,
        )
        assert result.recommended_bid > 0

    def test_contested_bump_survives_falling_trend(self):
        """Regression: contested bump must apply AFTER trend scaling.

        A must_have player with a sharply falling trend (-15%) still needs
        aggressive bidding when contested — the "fight this auction" signal
        shouldn't be dampened by a value dip.
        """
        bidding = SmartBidding()
        falling_uncontested = self._must_have_bid(bidding, offer_count=0, trend_change_pct=-15.0)
        falling_contested = self._must_have_bid(bidding, offer_count=4, trend_change_pct=-15.0)
        # Gap between contested/uncontested should be the full +6% heavy bump,
        # not a trend-dampened fraction of it.
        assert falling_contested.overbid_pct - falling_uncontested.overbid_pct >= 4.0, (
            f"Contested bump was dampened by trend scaling "
            f"(gap: {falling_contested.overbid_pct - falling_uncontested.overbid_pct:.1f}%)"
        )


class TestDGWBidding:
    """Tests for is_dgw flag in calculate_ep_bid()."""

    def test_dgw_floors_confidence(self):
        """DGW player bid should reflect higher confidence even when the
        caller passes a low confidence (e.g. from small games_played sample).
        """
        bidding = SmartBidding()
        non_dgw = bidding.calculate_ep_bid(
            asking_price=10_000_000,
            market_value=10_000_000,
            expected_points=80.0,
            marginal_ep_gain=25.0,
            confidence=0.5,
            current_budget=30_000_000,
            trend_change_pct=0.0,
            is_dgw=False,
        )
        # Same inputs but DGW: confidence gets floored at 0.9, triggering the
        # +5% confidence bonus branch → higher overbid.
        dgw = bidding.calculate_ep_bid(
            asking_price=10_000_000,
            market_value=10_000_000,
            expected_points=80.0,
            marginal_ep_gain=25.0,
            confidence=0.5,
            current_budget=30_000_000,
            trend_change_pct=0.0,
            is_dgw=True,
        )
        assert dgw.overbid_pct > non_dgw.overbid_pct
        assert "DGW" in dgw.reasoning

    def test_non_dgw_no_dgw_in_reasoning(self):
        bidding = SmartBidding()
        result = bidding.calculate_ep_bid(
            asking_price=10_000_000,
            market_value=10_000_000,
            expected_points=80.0,
            marginal_ep_gain=25.0,
            confidence=0.8,
            current_budget=30_000_000,
            trend_change_pct=0.0,
        )
        assert "DGW" not in result.reasoning


class TestAggressiveCompetitorsHelper:
    """Tests for ActivityFeedLearner.has_aggressive_competitors()."""

    def test_no_data_returns_false(self, tmp_path):
        from rehoboam.activity_feed_learner import ActivityFeedLearner

        learner = ActivityFeedLearner(db_path=tmp_path / "test.db")
        assert learner.has_aggressive_competitors() is False

    def test_high_threat_detected(self, tmp_path):
        """A manager with many purchases + high avg price → threat_score > 100."""
        import sqlite3
        import time

        from rehoboam.activity_feed_learner import ActivityFeedLearner

        db_path = tmp_path / "test.db"
        learner = ActivityFeedLearner(db_path=db_path)

        # Manually seed league_transfers with a "whale" manager
        with sqlite3.connect(db_path) as conn:
            for i in range(10):
                conn.execute(
                    """
                    INSERT INTO league_transfers (
                        activity_id, player_id, player_name, buyer_name,
                        transfer_price, transfer_type, timestamp, processed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"act_{i}",
                        f"p{i}",
                        f"Player {i}",
                        "Whale",
                        15_000_000,
                        1,
                        "2026-04-01T10:00:00Z",
                        time.time(),
                    ),
                )
            conn.commit()

        assert learner.has_aggressive_competitors() is True
