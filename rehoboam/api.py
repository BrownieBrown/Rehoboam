"""KICKBASE API wrapper using v4 endpoints"""

from .kickbase_client import KickbaseV4Client, League, MarketPlayer, Player, User


class KickbaseAPI:
    """Wrapper around the Kickbase v4 API client"""

    def __init__(self, email: str, password: str):
        self.email = email
        self.password = password
        self.client = KickbaseV4Client()
        self._user = None
        self._leagues: list[League] = []

    def login(self) -> bool:
        """Login to KICKBASE"""
        try:
            success = self.client.login(self.email, self.password)
            if success:
                self._user = self.client.user
                self._leagues = self.client.leagues
            return success
        except Exception as e:
            raise Exception(f"Login failed: {e}") from e

    def get_leagues(self) -> list[League]:
        """Get all leagues the user is part of"""
        if not self._leagues:
            raise Exception("Not logged in. Call login() first.")
        return self._leagues

    def get_market(self, league: League) -> list[MarketPlayer]:
        """Get all players on the market"""
        try:
            return self.client.get_market(league.id)
        except Exception as e:
            raise Exception(f"Failed to fetch market: {e}") from e

    def get_my_bids(self, league: League) -> list[MarketPlayer]:
        """Get only players where you have active bids"""
        try:
            return self.client.get_my_bids(league.id)
        except Exception as e:
            raise Exception(f"Failed to fetch my bids: {e}") from e

    def get_team_info(self, league: League) -> dict:
        """Get info about your team budget"""
        try:
            return self.client.get_team_info(league.id)
        except Exception as e:
            raise Exception(f"Failed to fetch team info: {e}") from e

    def get_squad(self, league: League) -> list[Player]:
        """Get all players in your squad"""
        try:
            return self.client.get_squad(league.id)
        except Exception as e:
            raise Exception(f"Failed to fetch squad: {e}") from e

    def get_lineup(self, league: League) -> dict:
        """Get your current lineup"""
        try:
            return self.client.get_lineup(league.id)
        except Exception as e:
            raise Exception(f"Failed to fetch lineup: {e}") from e

    def get_starting_eleven(self, league: League) -> dict:
        """Get your current starting eleven"""
        try:
            return self.client.get_starting_eleven(league.id)
        except Exception as e:
            raise Exception(f"Failed to fetch starting eleven: {e}") from e

    def set_lineup(self, league: League, formation: str, player_ids: list[str]) -> dict:
        """Set starting lineup with formation and player IDs"""
        try:
            return self.client.set_lineup(league.id, formation, player_ids)
        except Exception as e:
            raise Exception(f"Failed to set lineup: {e}") from e

    def buy_player(self, league: League, player: MarketPlayer, price: int) -> bool:
        """Make an offer to buy a player from the market"""
        try:
            return self.client.make_offer(league.id, player.id, price)
        except Exception as e:
            raise Exception(
                f"Failed to buy player {player.first_name} {player.last_name}: {e}"
            ) from e

    def cancel_bid(self, league: League, player: MarketPlayer) -> bool:
        """Cancel your bid on a player"""
        try:
            if not player.user_offer_id:
                raise Exception("No offer ID found - player may not have your bid")
            return self.client.cancel_offer(league.id, player.id, player.user_offer_id)
        except Exception as e:
            raise Exception(
                f"Failed to cancel bid on {player.first_name} {player.last_name}: {e}"
            ) from e

    def sell_player(self, league: League, player: Player, price: int) -> bool:
        """List a player for sale on the transfer market (auction)"""
        try:
            return self.client.add_to_market(league.id, player.id, price)
        except Exception as e:
            raise Exception(
                f"Failed to sell player {player.first_name} {player.last_name}: {e}"
            ) from e

    def sell_player_instant(self, league: League, player: Player) -> dict:
        """Sell a player instantly to Kickbase at market value. Frees the squad slot immediately."""
        try:
            return self.client.sell_to_kickbase(league.id, player.id)
        except Exception as e:
            raise Exception(
                f"Failed to instant-sell player {player.first_name} {player.last_name}: {e}"
            ) from e

    def get_player_info(self, league: League, player_id: str) -> MarketPlayer | Player | None:
        """Get player details - returns a Player-like object for use in routes"""
        try:
            details = self.client.get_player_details(league.id, player_id)
            if not details:
                return None
            # Create a MarketPlayer from the details dict
            return MarketPlayer.from_dict(details)
        except Exception:
            return None

    def get_player_market_value_history(self, league: League, player_id: str) -> list[dict]:
        """Get player market value history"""
        try:
            data = self.client.get_player_market_value_history(league.id, player_id)
            # Convert the raw data to a list of date/value dicts
            items = data.get("it", [])
            return [{"date": item.get("dt"), "value": item.get("mv")} for item in items]
        except Exception:
            return []

    def refresh_token(self) -> bool:
        """Refresh auth token without re-login. Falls back to full login on failure."""
        if not self.client.refresh_tkn:
            return False
        try:
            return self.client.refresh_token(self.client.refresh_tkn)
        except Exception:
            return False

    def get_budget(self, league: League) -> dict:
        """Get budget only (lightweight, no squad fetch)"""
        try:
            return self.client.get_budget(league.id)
        except Exception as e:
            raise Exception(f"Failed to fetch budget: {e}") from e

    def get_competition_matchdays(self, competition_id: str = "1") -> dict:
        """Get matchday schedule for DGW detection"""
        try:
            return self.client.get_competition_matchdays(competition_id)
        except Exception as e:
            raise Exception(f"Failed to fetch matchdays: {e}") from e

    def get_competition_players(
        self, competition_id: str = "1", position: str = "", sorting: str = ""
    ) -> dict:
        """Browse all players in a competition"""
        try:
            return self.client.get_competition_players(competition_id, position, sorting)
        except Exception as e:
            raise Exception(f"Failed to fetch competition players: {e}") from e

    def get_manager_squad(self, league: League, manager_id: str) -> dict:
        """View a competitor's squad"""
        try:
            return self.client.get_manager_squad(league.id, manager_id)
        except Exception as e:
            raise Exception(f"Failed to fetch manager squad: {e}") from e

    def get_league_ranking(self, league: League, day_number: int | None = None) -> dict:
        """Get league ranking/standings.

        ``day_number`` returns historical rankings for that matchday (REH-24
        capture writes the current snapshot only; backfill is a separate
        concern handled outside the bot's session loop).
        """
        try:
            return self.client.get_league_ranking(league.id, day_number=day_number)
        except Exception as e:
            raise Exception(f"Failed to fetch league ranking: {e}") from e

    @property
    def user(self) -> User:
        """Get the logged-in user"""
        return self._user
