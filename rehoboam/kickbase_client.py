"""
Custom Kickbase API v4 client based on official API documentation
https://share.apidog.com/fe2420a6-d929-409f-9b1d-35122923316d
"""

import requests
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from datetime import datetime


@dataclass
class User:
    """User information"""
    id: str
    name: str
    email: str
    profile: str
    verified_email: bool

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "User":
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
    def from_dict(cls, data: Dict[str, Any]) -> "League":
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
    seller_user_id: Optional[str] = None  # None if KICKBASE is selling

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MarketPlayer":
        return cls(
            id=data.get("i", ""),
            first_name=data.get("fn", ""),
            last_name=data.get("n", ""),  # 'n' appears to be last name since 'fn' is first name
            position=cls._parse_position(data.get("pos", 0)),
            team_id=data.get("tid", ""),
            price=data.get("prc", 0),
            market_value=data.get("mv", 0),
            points=data.get("pts", 0),
            average_points=data.get("ap", 0.0),
            status=data.get("st", 0),
            seller_user_id=data.get("u"),  # User ID of seller, None/empty if KICKBASE
        )

    def is_kickbase_seller(self) -> bool:
        """Check if KICKBASE is the seller (not another user)"""
        return self.seller_user_id is None or self.seller_user_id == ""

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
    def from_dict(cls, data: Dict[str, Any]) -> "Player":
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
        self.token: Optional[str] = None
        self.token_expire: Optional[str] = None
        self.user: Optional[User] = None
        self.leagues: List[League] = []
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    def login(self, email: str, password: str) -> bool:
        """
        Login to Kickbase
        POST /v4/user/login
        """
        url = f"{self.BASE_URL}/v4/user/login"

        payload = {
            "em": email,
            "pass": password,
            "loy": False,
            "rep": {}
        }

        response = self.session.post(url, json=payload)

        if response.status_code == 200:
            data = response.json()

            # Store authentication token
            self.token = data.get("tkn")
            self.token_expire = data.get("tknex")

            # Update session headers with token
            if self.token:
                self.session.headers.update({
                    "Authorization": f"Bearer {self.token}"
                })

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

    def get_market(self, league_id: str) -> List[MarketPlayer]:
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

    def get_team_info(self, league_id: str) -> Dict[str, Any]:
        """
        Get your team budget information
        GET /v4/leagues/{league_id}/me
        """
        url = f"{self.BASE_URL}/v4/leagues/{league_id}/me"

        response = self.session.get(url)

        if response.status_code == 200:
            data = response.json()
            return {
                "budget": data.get("b", data.get("budget", 0)),
                "team_value": data.get("tv", data.get("teamValue", 0)),
            }
        else:
            raise Exception(f"Failed to fetch team info: {response.status_code} - {response.text}")

    def get_squad(self, league_id: str) -> List[Player]:
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

    def get_lineup(self, league_id: str) -> Dict[str, Any]:
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

    def get_starting_eleven(self, league_id: str) -> Dict[str, Any]:
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
            raise Exception(f"Failed to fetch starting eleven: {response.status_code} - {response.text}")

    def make_offer(self, league_id: str, player_id: str, price: int) -> bool:
        """
        Make an offer for a player
        POST /v4/leagues/{league_id}/market/{player_id}/offers
        """
        url = f"{self.BASE_URL}/v4/leagues/{league_id}/market/{player_id}/offers"

        payload = {
            "pr": price
        }

        response = self.session.post(url, json=payload)

        if response.status_code in [200, 201]:
            return True
        else:
            raise Exception(f"Failed to make offer: {response.status_code} - {response.text}")

    def add_to_market(self, league_id: str, player_id: str, price: int) -> bool:
        """
        Add player to market
        POST /v4/leagues/{league_id}/market
        """
        url = f"{self.BASE_URL}/v4/leagues/{league_id}/market"

        payload = {
            "pi": player_id,
            "pr": price
        }

        response = self.session.post(url, json=payload)

        if response.status_code in [200, 201]:
            return True
        else:
            raise Exception(f"Failed to add to market: {response.status_code} - {response.text}")
