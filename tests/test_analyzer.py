"""Tests for market analyzer"""

import pytest
from unittest.mock import Mock
from rehoboam.analyzer import MarketAnalyzer, PlayerAnalysis


def create_mock_market_player(
    first_name="John",
    last_name="Doe",
    position="Striker",
    price=1000000,
    market_value=1200000,
    points=50,
    average_points=5.5,
):
    """Helper to create a mock market player"""
    player = Mock()
    player.first_name = first_name
    player.last_name = last_name
    player.position = position
    player.price = price
    player.market_value = market_value
    player.points = points
    player.average_points = average_points
    return player


def create_mock_owned_player(
    first_name="Jane",
    last_name="Smith",
    position="Midfielder",
    market_value=2000000,
    points=80,
    average_points=7.5,
):
    """Helper to create a mock owned player"""
    player = Mock()
    player.first_name = first_name
    player.last_name = last_name
    player.position = position
    player.market_value = market_value
    player.points = points
    player.average_points = average_points
    return player


class TestMarketAnalyzer:
    """Test the MarketAnalyzer class"""

    def test_initialization(self):
        """Test analyzer initializes with correct parameters"""
        analyzer = MarketAnalyzer(
            min_buy_value_increase_pct=15.0,
            min_sell_profit_pct=10.0,
            max_loss_pct=-5.0,
        )

        assert analyzer.min_buy_value_increase_pct == 15.0
        assert analyzer.min_sell_profit_pct == 10.0
        assert analyzer.max_loss_pct == -5.0

    def test_analyze_market_player_buy_recommendation(self):
        """Test that undervalued players get BUY recommendation"""
        analyzer = MarketAnalyzer(
            min_buy_value_increase_pct=10.0,
            min_sell_profit_pct=5.0,
            max_loss_pct=-3.0,
        )

        # Player priced at 1M but worth 1.5M (50% undervalued)
        player = create_mock_market_player(
            price=1000000,
            market_value=1500000,
        )

        analysis = analyzer.analyze_market_player(player)

        assert analysis.recommendation == "BUY"
        assert analysis.value_change_pct == 50.0
        assert analysis.confidence > 0.0
        assert "higher than current price" in analysis.reason

    def test_analyze_market_player_skip_recommendation(self):
        """Test that overvalued players get SKIP recommendation"""
        analyzer = MarketAnalyzer(
            min_buy_value_increase_pct=10.0,
            min_sell_profit_pct=5.0,
            max_loss_pct=-3.0,
        )

        # Player priced at 1.5M but only worth 1M (overvalued)
        player = create_mock_market_player(
            price=1500000,
            market_value=1000000,
        )

        analysis = analyzer.analyze_market_player(player)

        assert analysis.recommendation == "SKIP"
        assert analysis.value_change_pct < 0
        assert "overvalued" in analysis.reason

    def test_analyze_market_player_hold_recommendation(self):
        """Test that slightly undervalued players get HOLD recommendation"""
        analyzer = MarketAnalyzer(
            min_buy_value_increase_pct=10.0,
            min_sell_profit_pct=5.0,
            max_loss_pct=-3.0,
        )

        # Player priced at 1M but worth 1.05M (5% undervalued, below threshold)
        player = create_mock_market_player(
            price=1000000,
            market_value=1050000,
        )

        analysis = analyzer.analyze_market_player(player)

        assert analysis.recommendation == "HOLD"
        assert 0 < analysis.value_change_pct < 10.0

    def test_analyze_owned_player_sell_for_profit(self):
        """Test owned player gets SELL when profit target reached"""
        analyzer = MarketAnalyzer(
            min_buy_value_increase_pct=10.0,
            min_sell_profit_pct=5.0,
            max_loss_pct=-3.0,
        )

        player = create_mock_owned_player(market_value=1500000)
        purchase_price = 1000000  # Bought for 1M, now worth 1.5M (50% profit)

        analysis = analyzer.analyze_owned_player(player, purchase_price)

        assert analysis.recommendation == "SELL"
        assert analysis.value_change_pct == 50.0
        assert "Profit target reached" in analysis.reason

    def test_analyze_owned_player_sell_for_loss(self):
        """Test owned player gets SELL when stop-loss triggered"""
        analyzer = MarketAnalyzer(
            min_buy_value_increase_pct=10.0,
            min_sell_profit_pct=5.0,
            max_loss_pct=-3.0,
        )

        player = create_mock_owned_player(market_value=900000)
        purchase_price = 1000000  # Bought for 1M, now worth 900K (-10% loss)

        analysis = analyzer.analyze_owned_player(player, purchase_price)

        assert analysis.recommendation == "SELL"
        assert analysis.value_change_pct == -10.0
        assert "Stop-loss triggered" in analysis.reason

    def test_analyze_owned_player_hold(self):
        """Test owned player gets HOLD when within acceptable range"""
        analyzer = MarketAnalyzer(
            min_buy_value_increase_pct=10.0,
            min_sell_profit_pct=5.0,
            max_loss_pct=-3.0,
        )

        player = create_mock_owned_player(market_value=1020000)
        purchase_price = 1000000  # Bought for 1M, now worth 1.02M (2% profit)

        analysis = analyzer.analyze_owned_player(player, purchase_price)

        assert analysis.recommendation == "HOLD"
        assert 0 < analysis.value_change_pct < 5.0

    def test_find_best_opportunities(self):
        """Test finding best trading opportunities"""
        analyzer = MarketAnalyzer(
            min_buy_value_increase_pct=10.0,
            min_sell_profit_pct=5.0,
            max_loss_pct=-3.0,
        )

        # Create analyses for different players
        analyses = [
            analyzer.analyze_market_player(
                create_mock_market_player(first_name="Player1", price=1000000, market_value=1500000)
            ),  # 50% gain - best
            analyzer.analyze_market_player(
                create_mock_market_player(first_name="Player2", price=1000000, market_value=1200000)
            ),  # 20% gain - second best
            analyzer.analyze_market_player(
                create_mock_market_player(first_name="Player3", price=1000000, market_value=900000)
            ),  # Overvalued - skip
            analyzer.analyze_market_player(
                create_mock_market_player(first_name="Player4", price=1000000, market_value=1100000)
            ),  # 10% gain - just at threshold
        ]

        opportunities = analyzer.find_best_opportunities(analyses, top_n=2)

        assert len(opportunities) == 2
        # First should be the 50% gain
        assert opportunities[0].player.first_name == "Player1"
        assert opportunities[0].value_change_pct == 50.0
        # Second should be the 20% gain
        assert opportunities[1].player.first_name == "Player2"
        assert opportunities[1].value_change_pct == 20.0

    def test_find_best_opportunities_no_buys(self):
        """Test when no BUY opportunities exist"""
        analyzer = MarketAnalyzer(
            min_buy_value_increase_pct=10.0,
            min_sell_profit_pct=5.0,
            max_loss_pct=-3.0,
        )

        # All players are overvalued or below threshold
        analyses = [
            analyzer.analyze_market_player(
                create_mock_market_player(price=1000000, market_value=900000)
            ),  # Overvalued
            analyzer.analyze_market_player(
                create_mock_market_player(price=1000000, market_value=1050000)
            ),  # Below threshold
        ]

        opportunities = analyzer.find_best_opportunities(analyses, top_n=10)

        assert len(opportunities) == 0
