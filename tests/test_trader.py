"""Tests for trader logic"""

import pytest
from unittest.mock import Mock, MagicMock, patch
from io import StringIO
from rehoboam.trader import Trader
from rehoboam.config import Settings
from rehoboam.analyzer import PlayerAnalysis


@pytest.fixture
def mock_settings(monkeypatch):
    """Create mock settings"""
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "testpassword")

    settings = Settings()
    settings.dry_run = True
    settings.min_sell_profit_pct = 5.0
    settings.max_loss_pct = -3.0
    settings.min_buy_value_increase_pct = 10.0
    settings.max_player_cost = 5000000
    settings.reserve_budget = 1000000

    return settings


@pytest.fixture
def mock_api():
    """Create a mock API"""
    api = Mock()
    return api


@pytest.fixture
def trader(mock_api, mock_settings):
    """Create a Trader instance"""
    return Trader(mock_api, mock_settings)


def create_mock_market_player(first_name, last_name, price, market_value):
    """Helper to create mock market player"""
    player = Mock()
    player.first_name = first_name
    player.last_name = last_name
    player.position = "Striker"
    player.price = price
    player.market_value = market_value
    player.points = 50
    player.average_points = 5.0
    return player


class TestTrader:
    """Test the Trader class"""

    def test_initialization(self, mock_api, mock_settings):
        """Test trader initializes correctly"""
        trader = Trader(mock_api, mock_settings)

        assert trader.api == mock_api
        assert trader.settings == mock_settings
        assert trader.analyzer is not None
        assert trader.analyzer.min_buy_value_increase_pct == 10.0

    def test_analyze_market(self, trader, mock_api):
        """Test market analysis"""
        mock_league = Mock()
        mock_market = Mock()

        # Create mock players
        player1 = create_mock_market_player("John", "Doe", 1000000, 1500000)
        player2 = create_mock_market_player("Jane", "Smith", 2000000, 1800000)

        mock_market.players = [player1, player2]
        mock_api.get_market.return_value = mock_market

        analyses = trader.analyze_market(mock_league)

        assert len(analyses) == 2
        assert all(isinstance(a, PlayerAnalysis) for a in analyses)
        mock_api.get_market.assert_called_once_with(mock_league)

    def test_analyze_team(self, trader, mock_api):
        """Test team analysis"""
        mock_league = Mock()
        mock_team_info = Mock()

        # Create mock players
        player1 = Mock()
        player1.first_name = "Player"
        player1.last_name = "One"
        player1.market_value = 1500000
        player1.points = 50
        player1.average_points = 5.0

        player2 = Mock()
        player2.first_name = "Player"
        player2.last_name = "Two"
        player2.market_value = 2000000
        player2.points = 80
        player2.average_points = 8.0

        mock_team_info.players = [player1, player2]
        mock_api.get_team_info.return_value = mock_team_info

        analyses = trader.analyze_team(mock_league)

        assert len(analyses) == 2
        assert all(isinstance(a, PlayerAnalysis) for a in analyses)
        mock_api.get_team_info.assert_called_once_with(mock_league)

    @patch('rehoboam.trader.console')
    def test_execute_trades_dry_run(self, mock_console, trader, mock_api):
        """Test executing trades in dry run mode"""
        mock_league = Mock()
        mock_team_info = Mock()
        mock_team_info.budget = 10000000
        mock_api.get_team_info.return_value = mock_team_info

        # Create a buy analysis
        player = create_mock_market_player("John", "Doe", 1000000, 1500000)
        analysis = Mock()
        analysis.player = player
        analysis.current_price = 1000000

        trader.settings.dry_run = True

        results = trader.execute_trades(mock_league, [analysis])

        assert len(results["bought"]) == 1
        assert len(results["failed"]) == 0
        assert len(results["skipped"]) == 0
        # Should not actually call the API in dry run
        mock_api.buy_player.assert_not_called()

    @patch('rehoboam.trader.console')
    def test_execute_trades_budget_constraint(self, mock_console, trader, mock_api):
        """Test that trades are skipped when budget is insufficient"""
        mock_league = Mock()
        mock_team_info = Mock()
        mock_team_info.budget = 2000000  # Only 2M budget
        mock_api.get_team_info.return_value = mock_team_info

        trader.settings.reserve_budget = 1000000  # Reserve 1M

        # Create expensive player (3M - more than available budget)
        player = create_mock_market_player("Expensive", "Player", 3000000, 4000000)
        analysis = Mock()
        analysis.player = player
        analysis.current_price = 3000000

        results = trader.execute_trades(mock_league, [analysis])

        assert len(results["bought"]) == 0
        assert len(results["skipped"]) == 1

    @patch('rehoboam.trader.console')
    def test_execute_trades_max_player_cost(self, mock_console, trader, mock_api):
        """Test that trades are skipped when player exceeds max cost"""
        mock_league = Mock()
        mock_team_info = Mock()
        mock_team_info.budget = 20000000
        mock_api.get_team_info.return_value = mock_team_info

        trader.settings.max_player_cost = 5000000

        # Create player that exceeds max cost
        player = create_mock_market_player("Expensive", "Player", 6000000, 8000000)
        analysis = Mock()
        analysis.player = player
        analysis.current_price = 6000000

        results = trader.execute_trades(mock_league, [analysis])

        assert len(results["bought"]) == 0
        assert len(results["skipped"]) == 1

    @patch('rehoboam.trader.console')
    def test_execute_trades_live_mode(self, mock_console, trader, mock_api):
        """Test executing trades in live mode"""
        mock_league = Mock()
        mock_team_info = Mock()
        mock_team_info.budget = 10000000
        mock_api.get_team_info.return_value = mock_team_info

        trader.settings.dry_run = False

        player = create_mock_market_player("John", "Doe", 1000000, 1500000)
        analysis = Mock()
        analysis.player = player
        analysis.current_price = 1000000

        mock_api.buy_player.return_value = True

        results = trader.execute_trades(mock_league, [analysis])

        assert len(results["bought"]) == 1
        assert len(results["failed"]) == 0
        mock_api.buy_player.assert_called_once_with(mock_league, player, 1000000)

    @patch('rehoboam.trader.console')
    def test_execute_trades_api_failure(self, mock_console, trader, mock_api):
        """Test handling API failures during trading"""
        mock_league = Mock()
        mock_team_info = Mock()
        mock_team_info.budget = 10000000
        mock_api.get_team_info.return_value = mock_team_info

        trader.settings.dry_run = False

        player = create_mock_market_player("John", "Doe", 1000000, 1500000)
        analysis = Mock()
        analysis.player = player
        analysis.current_price = 1000000

        mock_api.buy_player.side_effect = Exception("API Error")

        results = trader.execute_trades(mock_league, [analysis])

        assert len(results["bought"]) == 0
        assert len(results["failed"]) == 1

    @patch('rehoboam.trader.console')
    def test_auto_trade_with_opportunities(self, mock_console, trader, mock_api):
        """Test automated trading when opportunities exist"""
        mock_league = Mock()
        mock_market = Mock()
        mock_team_info = Mock()
        mock_team_info.budget = 10000000

        # Create undervalued player
        player = create_mock_market_player("Good", "Deal", 1000000, 1500000)
        mock_market.players = [player]

        mock_api.get_market.return_value = mock_market
        mock_api.get_team_info.return_value = mock_team_info

        trader.auto_trade(mock_league, max_trades=5)

        # Should have analyzed the market
        mock_api.get_market.assert_called_once()

    @patch('rehoboam.trader.console')
    def test_auto_trade_no_opportunities(self, mock_console, trader, mock_api):
        """Test automated trading when no opportunities exist"""
        mock_league = Mock()
        mock_market = Mock()

        # Create overvalued player
        player = create_mock_market_player("Bad", "Deal", 1500000, 1000000)
        mock_market.players = [player]

        mock_api.get_market.return_value = mock_market

        trader.auto_trade(mock_league, max_trades=5)

        # Should have analyzed but not attempted to buy
        mock_api.get_market.assert_called_once()
        mock_api.buy_player.assert_not_called()
