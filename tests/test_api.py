"""Tests for API wrapper"""

from unittest.mock import Mock

import pytest

from rehoboam.api import KickbaseAPI

# Mark all tests in this file as skipped - these tests are for legacy API
# TODO: Rewrite tests for KickbaseV4Client implementation
pytestmark = pytest.mark.skip(reason="Legacy tests for old API - need rewrite for KickbaseV4Client")


@pytest.fixture
def mock_kickbase():
    """Create a mock Kickbase instance"""
    return Mock()


@pytest.fixture
def api(mock_kickbase, monkeypatch):
    """Create a KickbaseAPI instance with mocked Kickbase"""

    def mock_kickbase_init(self):
        self.kickbase = mock_kickbase
        self.email = "test@example.com"
        self.password = "testpassword"
        self._user = None
        self._leagues = []

    monkeypatch.setattr("rehoboam.api.Kickbase", lambda: mock_kickbase)

    return KickbaseAPI("test@example.com", "testpassword")


class TestKickbaseAPI:
    """Test the KickbaseAPI wrapper"""

    def test_initialization(self):
        """Test API initializes with credentials"""
        api = KickbaseAPI("test@example.com", "password123")

        assert api.email == "test@example.com"
        assert api.password == "password123"
        assert api._user is None
        assert api._leagues == []

    def test_login_success(self, api, mock_kickbase):
        """Test successful login"""
        # Mock the user and leagues returned by login
        mock_user = Mock()
        mock_user.name = "Test User"
        mock_leagues = [Mock(name="League 1"), Mock(name="League 2")]

        mock_kickbase.login.return_value = (mock_user, mock_leagues)

        result = api.login()

        assert result is True
        assert api._user == mock_user
        assert api._leagues == mock_leagues
        mock_kickbase.login.assert_called_once_with("test@example.com", "testpassword")

    def test_login_failure(self, api, mock_kickbase):
        """Test login failure"""
        mock_kickbase.login.side_effect = Exception("Invalid credentials")

        with pytest.raises(Exception) as exc_info:
            api.login()

        assert "Login failed" in str(exc_info.value)

    def test_get_leagues_success(self, api):
        """Test getting leagues after login"""
        mock_leagues = [Mock(name="League 1"), Mock(name="League 2")]
        api._leagues = mock_leagues

        leagues = api.get_leagues()

        assert leagues == mock_leagues

    def test_get_leagues_not_logged_in(self, api):
        """Test getting leagues without logging in"""
        with pytest.raises(Exception) as exc_info:
            api.get_leagues()

        assert "Not logged in" in str(exc_info.value)

    def test_get_market(self, api, mock_kickbase):
        """Test getting market data"""
        mock_league = Mock()
        mock_market = Mock()
        mock_market.players = [Mock(), Mock()]

        mock_kickbase.market.return_value = mock_market

        result = api.get_market(mock_league)

        assert result == mock_market
        mock_kickbase.market.assert_called_once_with(mock_league)

    def test_get_market_failure(self, api, mock_kickbase):
        """Test market fetch failure"""
        mock_league = Mock()
        mock_kickbase.market.side_effect = Exception("API error")

        with pytest.raises(Exception) as exc_info:
            api.get_market(mock_league)

        assert "Failed to fetch market" in str(exc_info.value)

    def test_get_team_info(self, api, mock_kickbase):
        """Test getting team info"""
        mock_league = Mock()
        mock_team_info = Mock()
        mock_team_info.players = [Mock(), Mock(), Mock()]
        mock_team_info.budget = 5000000

        mock_kickbase.league_me.return_value = mock_team_info

        result = api.get_team_info(mock_league)

        assert result == mock_team_info
        assert result.budget == 5000000
        mock_kickbase.league_me.assert_called_once_with(mock_league)

    def test_buy_player(self, api, mock_kickbase):
        """Test buying a player"""
        mock_league = Mock()
        mock_player = Mock()
        mock_player.first_name = "John"
        mock_player.last_name = "Doe"
        price = 1000000

        mock_kickbase.make_offer.return_value = None

        result = api.buy_player(mock_league, mock_player, price)

        assert result is True
        mock_kickbase.make_offer.assert_called_once_with(price, mock_player, mock_league)

    def test_buy_player_failure(self, api, mock_kickbase):
        """Test buy player failure"""
        mock_league = Mock()
        mock_player = Mock()
        mock_player.first_name = "John"
        mock_player.last_name = "Doe"
        price = 1000000

        mock_kickbase.make_offer.side_effect = Exception("Insufficient budget")

        with pytest.raises(Exception) as exc_info:
            api.buy_player(mock_league, mock_player, price)

        assert "Failed to buy player John Doe" in str(exc_info.value)

    def test_sell_player(self, api, mock_kickbase):
        """Test selling a player"""
        mock_league = Mock()
        mock_player = Mock()
        mock_player.first_name = "Jane"
        mock_player.last_name = "Smith"
        price = 2000000

        mock_kickbase.add_to_market.return_value = None

        result = api.sell_player(mock_league, mock_player, price)

        assert result is True
        mock_kickbase.add_to_market.assert_called_once_with(price, mock_player, mock_league)

    def test_get_league_stats(self, api, mock_kickbase):
        """Test getting league statistics"""
        mock_league = Mock()
        mock_stats = Mock()

        mock_kickbase.league_stats.return_value = mock_stats

        result = api.get_league_stats(mock_league)

        assert result == mock_stats
        mock_kickbase.league_stats.assert_called_once_with(mock_league)

    def test_user_property(self, api):
        """Test user property getter"""
        mock_user = Mock()
        mock_user.name = "Test User"
        api._user = mock_user

        assert api.user == mock_user
        assert api.user.name == "Test User"
