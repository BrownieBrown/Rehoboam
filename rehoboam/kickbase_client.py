"""
Custom Kickbase API v4 client based on official API documentation
https://share.apidog.com/fe2420a6-d929-409f-9b1d-35122923316d
"""

from dataclasses import dataclass
from typing import Any

import requests


@dataclass
class User:
    """User information"""

    id: str
    name: str
    email: str
    profile: str
    verified_email: bool

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "User":
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            email=data.get("email", ""),
            profile=data.get("profile", ""),
            verified_email=data.get("vemail", False),
        )


@dataclass
class League:
    """League/Server information"""

    id: str
    name: str
    creator_id: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "League":
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            creator_id=data.get("creatorId", ""),
        )


@dataclass
class MarketPlayer:
    """Player on the market"""

    id: str
    first_name: str
    last_name: str
    position: str
    team_id: str
    price: int
    market_value: int
    points: int
    average_points: float
    status: int
    seller_user_id: str | None = None  # None if KICKBASE is selling
    offer_count: int = 0  # Number of offers on player
    user_offer_price: int | None = None  # Your bid amount if you made one
    user_offer_id: str | None = None  # Your offer ID (needed to cancel bid)
    listed_at: str | None = None  # When player was listed (ISO datetime)
    offers: list = None  # List of all offers

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MarketPlayer":
        # Extract user info (seller)
        user_data = data.get("u")
        seller_user_id = user_data.get("i") if isinstance(user_data, dict) else None

        return cls(
            id=data.get("i", ""),
            first_name=data.get("fn", ""),
            last_name=data.get("n", ""),  # 'n' appears to be last name since 'fn' is first name
            position=cls._parse_position(data.get("pos", 0)),
            team_id=data.get("tid", ""),
            price=data.get("prc", 0),
            market_value=data.get("mv", 0),
            points=data.get("p", data.get("pts", 0)),  # 'p' in market, 'pts' elsewhere
            average_points=data.get("ap", 0.0),
            status=data.get("st", 0),
            seller_user_id=seller_user_id,
            offer_count=data.get("ofc", 0),
            user_offer_price=data.get("uop"),
            user_offer_id=data.get("uoid"),
            listed_at=data.get("dt"),
            offers=data.get("ofs", []),
        )

    def is_kickbase_seller(self) -> bool:
        """Check if KICKBASE is the seller (not another user)"""
        return self.seller_user_id is None or self.seller_user_id == ""

    def has_user_offer(self, user_id: str) -> bool:
        """Check if specific user has made an offer on this player"""
        return self.user_offer_id == user_id

    def get_user_offer_amount(self, user_id: str) -> int | None:
        """Get the offer amount from a specific user"""
        if self.has_user_offer(user_id):
            return self.user_offer_price
        # Check in offers list
        for offer in self.offers or []:
            if offer.get("u") == user_id or offer.get("uoid") == user_id:
                return offer.get("uop")
        return None

    @staticmethod
    def _parse_position(pos: int) -> str:
        """Convert position code to name"""
        positions = {
            1: "Goalkeeper",
            2: "Defender",
            3: "Midfielder",
            4: "Forward",
        }
        return positions.get(pos, "Unknown")


@dataclass
class Player:
    """Player in a team"""

    id: str
    first_name: str
    last_name: str
    position: str
    market_value: int
    points: int
    average_points: float

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Player":
        # Squad endpoint uses 'n' for name, 'pos' for position, 'p' for points
        return cls(
            id=data.get("i", ""),
            first_name=data.get("fn", ""),  # May not exist in squad response
            last_name=data.get("n", data.get("ln", "")),  # 'n' in squad, 'ln' in market
            position=MarketPlayer._parse_position(data.get("pos", data.get("p", 0))),
            market_value=data.get("mv", 0),
            points=data.get("p", data.get("pts", 0)),  # 'p' in squad, 'pts' in market
            average_points=data.get("ap", 0.0),
        )


class KickbaseV4Client:
    """Client for Kickbase API v4"""

    BASE_URL = "https://api.kickbase.com"

    def __init__(self):
        self.token: str | None = None
        self.token_expire: str | None = None
        self.user: User | None = None
        self.leagues: list[League] = []
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

    def login(self, email: str, password: str) -> bool:
        """
        Login to Kickbase
        POST /v4/user/login
        """
        url = f"{self.BASE_URL}/v4/user/login"

        payload = {"em": email, "pass": password, "loy": False, "rep": {}}

        response = self.session.post(url, json=payload)

        if response.status_code == 200:
            data = response.json()

            # Store authentication token
            self.token = data.get("tkn")
            self.token_expire = data.get("tknex")

            # Update session headers with token
            if self.token:
                self.session.headers.update({"Authorization": f"Bearer {self.token}"})

            # Parse user data
            user_data = data.get("u", {})
            self.user = User.from_dict(user_data)

            # Parse leagues/servers
            servers = data.get("srvl", [])
            self.leagues = [League.from_dict(srv) for srv in servers]

            return True
        elif response.status_code == 401:
            raise Exception("Invalid credentials")
        else:
            raise Exception(f"Login failed with status {response.status_code}: {response.text}")

    def get_market(self, league_id: str) -> list[MarketPlayer]:
        """
        Get market players
        GET /v4/leagues/{league_id}/market
        """
        url = f"{self.BASE_URL}/v4/leagues/{league_id}/market"

        response = self.session.get(url)

        if response.status_code == 200:
            data = response.json()
            players_data = data.get("it", [])  # Players are in 'it' not 'pl'
            return [MarketPlayer.from_dict(p) for p in players_data]
        else:
            raise Exception(f"Failed to fetch market: {response.status_code} - {response.text}")

    def get_my_bids(self, league_id: str) -> list[MarketPlayer]:
        """
        Get only players where you have active bids

        Note: The API doesn't have a dedicated "my bids only" endpoint.
        This fetches all market players and filters for players where you have an offer.

        Args:
            league_id: League ID

        Returns:
            List of MarketPlayer objects where you have active bids
        """
        if not self.user:
            raise Exception("Not logged in. Call login() first.")

        all_market = self.get_market(league_id)
        return [p for p in all_market if p.has_user_offer(self.user.id)]

    def get_team_info(self, league_id: str) -> dict[str, Any]:
        """
        Get your team budget and value

        Note: The /me endpoint only returns budget, not team value.
        We calculate team value by summing squad player market values.
        """
        url = f"{self.BASE_URL}/v4/leagues/{league_id}/me"

        response = self.session.get(url)

        if response.status_code == 200:
            data = response.json()
            budget = data.get("b", data.get("budget", 0))

            # Calculate team value from squad
            squad = self.get_squad(league_id)
            team_value = sum(player.market_value for player in squad)

            return {
                "budget": budget,
                "team_value": team_value,
            }
        else:
            raise Exception(f"Failed to fetch team info: {response.status_code} - {response.text}")

    def get_squad(self, league_id: str) -> list[Player]:
        """
        Get your squad players
        GET /v4/leagues/{league_id}/squad
        """
        url = f"{self.BASE_URL}/v4/leagues/{league_id}/squad"

        response = self.session.get(url)

        if response.status_code == 200:
            data = response.json()
            # Players are in 'it' (items), same as market endpoint
            players_data = data.get("it", [])
            return [Player.from_dict(p) for p in players_data]
        else:
            raise Exception(f"Failed to fetch squad: {response.status_code} - {response.text}")

    def get_lineup(self, league_id: str) -> dict[str, Any]:
        """
        Get your current lineup
        GET /v4/leagues/{league_id}/lineup
        """
        url = f"{self.BASE_URL}/v4/leagues/{league_id}/lineup"

        response = self.session.get(url)

        if response.status_code == 200:
            data = response.json()
            return data
        else:
            raise Exception(f"Failed to fetch lineup: {response.status_code} - {response.text}")

    def get_starting_eleven(self, league_id: str) -> dict[str, Any]:
        """
        Get your current starting eleven (always 11 players)
        GET /v4/leagues/{league_id}/teamcenter/myeleven
        """
        url = f"{self.BASE_URL}/v4/leagues/{league_id}/teamcenter/myeleven"

        response = self.session.get(url)

        if response.status_code == 200:
            data = response.json()
            return data
        else:
            raise Exception(
                f"Failed to fetch starting eleven: {response.status_code} - {response.text}"
            )

    def make_offer(self, league_id: str, player_id: str, price: int) -> dict[str, Any]:
        """
        Make an offer for a player
        POST /v4/leagues/{league_id}/market/{player_id}/offers
        """
        url = f"{self.BASE_URL}/v4/leagues/{league_id}/market/{player_id}/offers"

        payload = {"price": price}  # Use "price" not "pr"

        response = self.session.post(url, json=payload)

        if response.status_code in [200, 201]:
            return response.json()  # Returns offer ID
        else:
            raise Exception(f"Failed to make offer: {response.status_code} - {response.text}")

    def cancel_offer(self, league_id: str, player_id: str, offer_id: str) -> dict[str, Any]:
        """
        Cancel your offer/bid on a player
        DELETE /v4/leagues/{league_id}/market/{player_id}/offers/{offer_id}

        Note: This cancels YOUR specific offer on a player (when you're bidding).
        Different from removing a player from market (when you're selling).

        Args:
            league_id: League ID
            player_id: Player ID to cancel bid on
            offer_id: The specific offer ID to cancel (from user_offer_id)

        Returns:
            Response data from cancellation
        """
        url = f"{self.BASE_URL}/v4/leagues/{league_id}/market/{player_id}/offers/{offer_id}"

        response = self.session.delete(url)

        if response.status_code in [200, 201, 204]:
            return response.json() if response.text else {}
        else:
            raise Exception(f"Failed to cancel offer: {response.status_code} - {response.text}")

    def add_to_market(self, league_id: str, player_id: str, price: int) -> dict[str, Any]:
        """
        Add player to market (list for sale)
        POST /v4/leagues/{league_id}/market

        Note: KICKBASE instantly matches market value, so setting price above
        market value forces other managers to bid high, but you can still
        sell to KICKBASE at market value anytime.

        Args:
            league_id: League ID
            player_id: Player ID to list
            price: Asking price (can be above market value)

        Returns:
            Response data from listing
        """
        url = f"{self.BASE_URL}/v4/leagues/{league_id}/market"

        payload = {"pi": player_id, "prc": price}  # Use 'prc' not 'pr' for market listing

        response = self.session.post(url, json=payload)

        if response.status_code in [200, 201]:
            return response.json()
        else:
            raise Exception(f"Failed to add to market: {response.status_code} - {response.text}")

    def sell_to_kickbase(self, league_id: str, player_id: str) -> dict[str, Any]:
        """
        Sell player directly to KICKBASE at market value
        POST /v4/leagues/{league_id}/market/{player_id}/sell

        Args:
            league_id: League ID
            player_id: Player ID to sell

        Returns:
            Response data from sale
        """
        url = f"{self.BASE_URL}/v4/leagues/{league_id}/market/{player_id}/sell"

        response = self.session.post(url, json={})

        if response.status_code in [200, 201]:
            return response.json()
        else:
            raise Exception(f"Failed to sell to KICKBASE: {response.status_code} - {response.text}")

    def accept_offer(self, league_id: str, player_id: str, offer_id: str) -> dict[str, Any]:
        """
        Accept an offer from another manager
        POST /v4/leagues/{league_id}/market/{player_id}/offers/{offer_id}/accept

        Args:
            league_id: League ID
            player_id: Player ID
            offer_id: Offer ID to accept

        Returns:
            Response data from acceptance
        """
        url = f"{self.BASE_URL}/v4/leagues/{league_id}/market/{player_id}/offers/{offer_id}/accept"

        response = self.session.post(url, json={})

        if response.status_code in [200, 201]:
            return response.json()
        else:
            raise Exception(f"Failed to accept offer: {response.status_code} - {response.text}")

    def get_player_offers(self, league_id: str, player_id: str) -> list[dict[str, Any]]:
        """
        Get all offers for a player on the market
        GET /v4/leagues/{league_id}/market/{player_id}/offers

        Args:
            league_id: League ID
            player_id: Player ID

        Returns:
            List of offers
        """
        url = f"{self.BASE_URL}/v4/leagues/{league_id}/market/{player_id}/offers"

        response = self.session.get(url)

        if response.status_code == 200:
            data = response.json()
            return data.get("it", [])  # Offers likely in 'it' field
        else:
            raise Exception(f"Failed to get offers: {response.status_code} - {response.text}")

    def get_player_market_value_history(
        self, league_id: str, player_id: str, timeframe: int = 30
    ) -> dict[str, Any]:
        """
        Get player's market value history
        GET /v4/leagues/{league_id}/players/{player_id}/marketvalue/{timeframe}

        Args:
            league_id: League ID
            player_id: Player ID
            timeframe: Number of days to look back (default: 30)

        Returns:
            dict with market value history including min/max values
        """
        url = f"{self.BASE_URL}/v4/leagues/{league_id}/players/{player_id}/marketvalue/{timeframe}"

        response = self.session.get(url)

        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(
                f"Failed to fetch market value history: {response.status_code} - {response.text}"
            )

    def get_player_performance(self, league_id: str, player_id: str) -> dict[str, Any]:
        """
        Get player's detailed performance data including all matches and points
        GET /v4/leagues/{league_id}/players/{player_id}/performance

        Args:
            league_id: League ID
            player_id: Player ID

        Returns:
            dict with detailed performance data including match history
        """
        url = f"{self.BASE_URL}/v4/leagues/{league_id}/players/{player_id}/performance"

        response = self.session.get(url)

        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(
                f"Failed to fetch player performance: {response.status_code} - {response.text}"
            )

    def get_player_details(self, league_id: str, player_id: str) -> dict[str, Any]:
        """
        Get player's full details including team, matchups, and status
        GET /v4/leagues/{league_id}/players/{player_id}

        Args:
            league_id: League ID
            player_id: Player ID

        Returns:
            dict with:
            - Team info: tid, tn (team name)
            - Status: st (0=healthy, 2/4/256=injured/unavailable)
            - Lineup probability: prob (1=starter, 2-4=rotation, 5=unlikely)
            - Matchups: mdsum (past, current, future matches)
            - Performance: ph (recent match points)
            - Goals/assists: g, a
        """
        url = f"{self.BASE_URL}/v4/leagues/{league_id}/players/{player_id}"

        response = self.session.get(url)

        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(
                f"Failed to fetch player details: {response.status_code} - {response.text}"
            )

    def get_team_profile(self, league_id: str, team_id: str) -> dict[str, Any]:
        """
        Get team profile including standings and all players
        GET /v4/leagues/{league_id}/teams/{team_id}/teamprofile

        Args:
            league_id: League ID
            team_id: Team ID

        Returns:
            dict with:
            - Standings: pl (place), tw (wins), td (draws), tl (losses)
            - Team value: tv
            - Players: it (all players on team with status)
        """
        url = f"{self.BASE_URL}/v4/leagues/{league_id}/teams/{team_id}/teamprofile"

        response = self.session.get(url)

        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(
                f"Failed to fetch team profile: {response.status_code} - {response.text}"
            )

    def get_activities_feed(self, league_id: str, start: int = 0) -> dict[str, Any]:
        """
        Get activities feed - shows all trades, offers, transfers
        GET /v4/leagues/{league_id}/activitiesFeed?start={start}

        Shows recent activities like:
        - Players bought/sold
        - Auction wins/losses
        - Market listings
        - Offers made/received

        Args:
            league_id: League ID
            start: Pagination offset (default: 0)

        Returns:
            dict with:
            - items: List of activity items
            - meta: Pagination metadata
        """
        url = f"{self.BASE_URL}/v4/leagues/{league_id}/activitiesFeed"

        params = {"start": start}
        response = self.session.get(url, params=params)

        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(
                f"Failed to fetch activities feed: {response.status_code} - {response.text}"
            )

    def get_player_statistics(self, player_id: str, league_id: str) -> dict[str, Any]:
        """
        Get detailed player statistics (competition-based endpoint)
        GET /v4/competitions/1/players/{player_id}?leagueId={league_id}

        Returns rich player data including:
        - Current market value (mv)
        - Total points (tp), Average points (ap)
        - Status (st): 0=Fit, others=injured/etc
        - Performance history (ph): Recent match points
        - Match data (mdsum): Past and upcoming fixtures
        - Position, team, etc.

        Args:
            player_id: Player ID
            league_id: League ID (as query parameter)

        Returns:
            dict with comprehensive player statistics
        """
        url = f"{self.BASE_URL}/v4/competitions/1/players/{player_id}"
        params = {"leagueId": league_id}

        response = self.session.get(url, params=params)

        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(
                f"Failed to fetch player statistics: {response.status_code} - {response.text}"
            )

    def get_player_market_value_history_v2(
        self, player_id: str, timeframe: int = 92
    ) -> dict[str, Any]:
        """
        Get player market value history (competition-based endpoint)
        GET /v4/competitions/1/players/{player_id}/marketValue/{timeframe}

        BETTER than the league-based endpoint - returns complete historical data!

        Returns:
            dict with:
            - it: Array of daily values [{"dt": days_since_epoch, "mv": market_value}]
            - trp: Transfer price (0 if KICKBASE-owned)
            - lmv: Lowest market value in timeframe
            - hmv: Highest market value in timeframe (PEAK!)
            - idp: Boolean flag

        Args:
            player_id: Player ID
            timeframe: Days to look back (92=3mo, 365=1yr)

        Returns:
            Market value history with peak/low values
        """
        url = f"{self.BASE_URL}/v4/competitions/1/players/{player_id}/marketValue/{timeframe}"

        response = self.session.get(url)

        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(
                f"Failed to fetch player market value history: {response.status_code} - {response.text}"
            )
