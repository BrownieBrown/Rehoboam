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
