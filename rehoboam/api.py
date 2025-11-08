"""KICKBASE API wrapper using v4 endpoints"""

from typing import List
from .kickbase_client import KickbaseV4Client, User, League, MarketPlayer, Player


class KickbaseAPI:
    """Wrapper around the Kickbase v4 API client"""

    def __init__(self, email: str, password: str):
        self.email = email
        self.password = password
        self.client = KickbaseV4Client()
        self._user = None
        self._leagues: List[League] = []

    def login(self) -> bool:
        """Login to KICKBASE"""
        try:
            success = self.client.login(self.email, self.password)
            if success:
                self._user = self.client.user
                self._leagues = self.client.leagues
            return success
        except Exception as e:
            raise Exception(f"Login failed: {e}")

    def get_leagues(self) -> List[League]:
        """Get all leagues the user is part of"""
        if not self._leagues:
            raise Exception("Not logged in. Call login() first.")
        return self._leagues

    def get_market(self, league: League) -> List[MarketPlayer]:
        """Get all players on the market"""
        try:
            return self.client.get_market(league.id)
        except Exception as e:
            raise Exception(f"Failed to fetch market: {e}")

    def get_team_info(self, league: League) -> dict:
        """Get info about your team budget"""
        try:
            return self.client.get_team_info(league.id)
        except Exception as e:
            raise Exception(f"Failed to fetch team info: {e}")

    def get_squad(self, league: League) -> List[Player]:
        """Get all players in your squad"""
        try:
            return self.client.get_squad(league.id)
        except Exception as e:
            raise Exception(f"Failed to fetch squad: {e}")

    def get_lineup(self, league: League) -> dict:
        """Get your current lineup"""
        try:
            return self.client.get_lineup(league.id)
        except Exception as e:
            raise Exception(f"Failed to fetch lineup: {e}")

    def get_starting_eleven(self, league: League) -> dict:
        """Get your current starting eleven"""
        try:
            return self.client.get_starting_eleven(league.id)
        except Exception as e:
            raise Exception(f"Failed to fetch starting eleven: {e}")

    def buy_player(
        self, league: League, player: MarketPlayer, price: int
    ) -> bool:
        """Make an offer to buy a player from the market"""
        try:
            return self.client.make_offer(league.id, player.id, price)
        except Exception as e:
            raise Exception(f"Failed to buy player {player.first_name} {player.last_name}: {e}")

    def sell_player(self, league: League, player: Player, price: int) -> bool:
        """List a player for sale"""
        try:
            return self.client.add_to_market(league.id, player.id, price)
        except Exception as e:
            raise Exception(f"Failed to sell player {player.first_name} {player.last_name}: {e}")

    @property
    def user(self) -> User:
        """Get the logged-in user"""
        return self._user
