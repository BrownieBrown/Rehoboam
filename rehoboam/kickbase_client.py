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
    team_name: str
    price: int
    market_value: int
    points: int
    average_points: float
    status: int
    seller_user_id: str | None = None  # None if KICKBASE is selling
    offer_count: int = 0  # Number of offers on player
    user_offer_price: int | None = None  # Your bid amount if you made one
    user_offer_id: str | None = None  # `uoid`: USER id of the offer holder,
    # present only on listings we have bid on. Not an offer id, despite
    # cancel_offer passing it as {offer_id} — see has_user_offer (REH-95).
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
            team_name=data.get("tn", ""),
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

    def has_user_offer(self) -> bool:
        """Do WE currently hold an offer on this player?

        ``uoid`` is a USER id — the id of the manager holding the offer.
        Observed on a real listing 2026-08-25, with our own bid live::

            "uoid": "3616202",                        # our user id
            "ofs": [{"u": "3616202", "uoid": "3616202", "uop": 60447397}]

        REH-95 claimed the opposite: that ``uoid`` was an OFFER id, inferred
        from ``cancel_offer`` passing it as ``{offer_id}`` in the delete path
        and from a stale comment on this dataclass. That inference was wrong.
        The equality check it replaced worked correctly, ``get_my_bids`` was
        never returning an empty list, and the guards built on it —
        ``pending_bid_total``, ``available_slots``, the already-bidding check —
        were all functioning. Cancellation also works passing this value, so
        that path was never broken either.

        Presence is kept over equality because Kickbase only includes the field
        on a listing we have bid on, so the two agree, and presence keeps
        working if the value ever changes shape. It also errs safe: reporting
        an open bid we do not have makes the bot more conservative about budget
        and slots, whereas missing one would let it overspend.
        """
        return self.user_offer_id is not None

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
    team_id: str
    team_name: str
    market_value: int
    points: int
    average_points: float
    buy_price: int = 0  # Purchase price (calculated from market_value - mvgl)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Player":
        # Squad endpoint uses 'n' for name, 'pos' for position, 'p' for points
        # 'mvgl' = market value gain/loss (NOT purchase price!)
        # Formula: purchase_price = market_value - mvgl
        market_value = data.get("mv", 0)
        mvgl = data.get("mvgl", 0)  # This is the GAIN, not purchase price
        buy_price = market_value - mvgl if mvgl != 0 else 0

        return cls(
            id=data.get("i", ""),
            first_name=data.get("fn", ""),  # May not exist in squad response
            last_name=data.get("n", data.get("ln", "")),  # 'n' in squad, 'ln' in market
            position=MarketPlayer._parse_position(data.get("pos", data.get("p", 0))),
            team_id=data.get("tid", ""),
            team_name=data.get("tn", ""),
            market_value=market_value,
            points=data.get("p", data.get("pts", 0)),  # 'p' in squad, 'pts' in market
            average_points=data.get("ap", 0.0),
            buy_price=buy_price,
        )


class KickbaseV4Client:
    """Client for Kickbase API v4"""

    BASE_URL = "https://api.kickbase.com"

    def __init__(self):
        self.token: str | None = None
        self.token_expire: str | None = None
        self.refresh_tkn: str | None = None
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

            # Store authentication token and refresh token
            self.token = data.get("tkn")
            self.token_expire = data.get("tknex")
            self.refresh_tkn = data.get("rtkn")

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
        return [p for p in all_market if p.has_user_offer()]

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

    def set_lineup(self, league_id: str, formation: str, player_ids: list[str]) -> dict[str, Any]:
        """
        Set starting lineup
        POST /v4/leagues/{league_id}/lineup
        """
        url = f"{self.BASE_URL}/v4/leagues/{league_id}/lineup"
        payload = {"type": formation, "players": player_ids}

        response = self.session.post(url, json=payload)

        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(f"Failed to set lineup: {response.status_code} - {response.text}")

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
            - Status: st (0=healthy, 1/2/4/256=injured/unavailable; 1 = out for weeks)
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

    # ── New endpoints ──────────────────────────────────────────────

    def refresh_token(self, refresh_token: str) -> bool:
        """
        Refresh authentication token without re-login
        POST /v4/user/refreshtokens
        """
        url = f"{self.BASE_URL}/v4/user/refreshtokens"
        payload = {"rtkn": refresh_token}

        response = self.session.post(url, json=payload)

        if response.status_code == 200:
            data = response.json()
            self.token = data.get("tkn")
            self.token_expire = data.get("tknex")
            self.refresh_tkn = data.get("rtkn", refresh_token)
            if self.token:
                self.session.headers.update({"Authorization": f"Bearer {self.token}"})
            return True
        else:
            return False

    def get_budget(self, league_id: str) -> dict[str, Any]:
        """
        Get budget only (lightweight)
        GET /v4/leagues/{league_id}/me/budget
        """
        url = f"{self.BASE_URL}/v4/leagues/{league_id}/me/budget"

        response = self.session.get(url)

        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(f"Failed to fetch budget: {response.status_code} - {response.text}")

    def get_competition_matchdays(self, competition_id: str = "1") -> dict[str, Any]:
        """
        Get matchday schedule for a competition (e.g. Bundesliga)
        GET /v4/competitions/{competition_id}/matchdays

        Use to detect double gameweeks (teams playing twice in one matchday).
        """
        url = f"{self.BASE_URL}/v4/competitions/{competition_id}/matchdays"

        response = self.session.get(url)

        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(f"Failed to fetch matchdays: {response.status_code} - {response.text}")

    def get_competition_players(
        self, competition_id: str = "1", position: str = "", sorting: str = ""
    ) -> dict[str, Any]:
        """
        Browse all players in a competition sorted by stats
        GET /v4/competitions/{competition_id}/players

        Args:
            competition_id: Competition ID (1 = Bundesliga)
            position: Filter by position (optional)
            sorting: Sort field (optional)
        """
        url = f"{self.BASE_URL}/v4/competitions/{competition_id}/players"
        params = {}
        if position:
            params["position"] = position
        if sorting:
            params["sorting"] = sorting

        response = self.session.get(url, params=params)

        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(
                f"Failed to fetch competition players: {response.status_code} - {response.text}"
            )

    def get_competition_player_details(
        self, player_id: str, competition_id: str = "1"
    ) -> dict[str, Any]:
        """
        Get full player details for any player in the competition, including
        one no longer registered in our league
        GET /v4/competitions/{competition_id}/players/{player_id}

        Competition-scoped, so unlike the league-scoped ``get_player_details``
        this resolves players who have since transferred out of the league —
        the v2 training corpus uses it to backfill position/name/team for
        departed players recovered from the learning DB, since neither
        ``get_competition_player_performance`` nor
        ``get_player_market_value_history_v2`` carries a position field at
        all (checked directly against both live responses, 2026-07-29).

        Returns:
            dict with (among others) ``i`` (id), ``fn``/``ln`` (first/last
            name), ``tid`` (team id), ``pos`` (position code: 1=Goalkeeper,
            2=Defender, 3=Midfielder, 4=Forward), ``mv`` (market value).
            ``ap`` (average points) is present only for players with senior
            appearances — omitted, not zero, otherwise.

        Raises:
            Exception on any non-200. A nonexistent player id returns HTTP
            500 with ``{"err": 2, "errMsg": "NotFound", ...}``, not 404 —
            verified by live probe (2026-07-29).
        """
        url = f"{self.BASE_URL}/v4/competitions/{competition_id}/players/{player_id}"

        response = self.session.get(url)

        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(
                f"Failed to fetch competition player details: "
                f"{response.status_code} - {response.text}"
            )

    def get_competition_player_performance(
        self, player_id: str, competition_id: str = "1"
    ) -> dict[str, Any]:
        """
        Get per-match performance history for any player in the competition
        GET /v4/competitions/{competition_id}/players/{player_id}/performance

        Competition-scoped twin of ``get_player_performance``. Needed by the
        v2 training corpus, which sweeps the whole league rather than only
        players in our own league view.

        Returns:
            dict with ``it``: list of seasons, each ``{"ti": title, "ph": [matches]}``
        """
        url = f"{self.BASE_URL}/v4/competitions/{competition_id}/players/{player_id}/performance"

        response = self.session.get(url)

        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(
                f"Failed to fetch competition player performance: "
                f"{response.status_code} - {response.text}"
            )

    def get_competition_table(self, competition_id: str = "1") -> dict[str, Any]:
        """
        Get the league table / standings for a competition
        GET /v4/competitions/{competition_id}/table

        Replaces the homegrown strength-of-schedule rating with real standings.
        """
        url = f"{self.BASE_URL}/v4/competitions/{competition_id}/table"

        response = self.session.get(url)

        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(
                f"Failed to fetch competition table: {response.status_code} - {response.text}"
            )

    def get_lineup_selection(
        self, league_id: str, position: int, start: int = 0, max_items: int = 50
    ) -> dict[str, Any]:
        """
        Browse selectable players by position, paginated
        GET /v4/leagues/{league_id}/lineup/selection

        The full-universe source for the v2 training corpus: sweeping positions
        1-4 to exhaustion reaches ~453 distinct players, and items carry ``pi``
        (id), ``n`` (last name), ``tid``, ``pos``, ``mv`` and ``ap``.

        Two behaviours found by probe (2026-07-29), neither visible in a
        successful response:

        - ``position`` is REQUIRED. Omit it and the endpoint returns zero items —
          which reads as an empty endpoint, not a missing parameter.
        - The server caps the page at 50 items regardless of ``max_items``.
          Callers MUST advance ``start`` by the number of items actually
          returned, never by the requested size, or half of every page is
          silently skipped.

        Args:
            league_id: League ID
            position: 1=Goalkeeper, 2=Defender, 3=Midfielder, 4=Forward
            start: Pagination offset
            max_items: Requested page size (server caps at 50)
        """
        url = f"{self.BASE_URL}/v4/leagues/{league_id}/lineup/selection"
        params = {"position": position, "start": start, "max": max_items}

        response = self.session.get(url, params=params)

        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(
                f"Failed to fetch lineup selection: {response.status_code} - {response.text}"
            )

    def get_player_transfer_history(self, league_id: str, player_id: str) -> dict[str, Any]:
        """
        Get a single player's transfer history within this league
        GET /v4/leagues/{league_id}/players/{player_id}/transferHistory

        The v2 corpus's only source of real, whole-season market prices —
        ``logs/market_prices.db`` is empty and ``league_transfers`` covers
        five weeks, not nine months. ``dt`` reaches back to the start of the
        season (verified by live probe, 2026-07-29), so this reconstructs a
        lower-bound market: if a player changed hands on some day at some
        price, he was buyable that day at that price.

        Returns:
            dict with ``it``: list of transaction records (not capped to the
            current season — the full REH-55 sweep saw records back to 2021
            for players who rarely change hands), each
            ``{"u": counterparty_manager_id, "unm": counterparty_name,
            "dt": ISO-8601 timestamp, "trp": price, "t": transfer type}``.
            ``t`` is stored raw (never interpreted here) — the full sweep
            (2026-07-31, this league) saw 0, 2, and 3, not just the 0/2 the
            probe found: ``t=2`` is the overwhelming majority (~95%) and
            always carries a real positive price — a genuine manager-to-
            manager transaction; ``t=0`` always carries price 0 with a real
            counterparty and clusters at a handful of timestamps that line
            up with season starts/resets — read as an initial/reset squad
            assignment, not a purchase; ``t=3`` always carries price 0 with
            no counterparty at all and clusters similarly — read as a
            player being unassigned back to the pool. Every in-season
            (2025/26) record swept was ``t=2``; 0/3 only appeared at
            season-boundary timestamps. Not confirmed against Kickbase
            documentation — treat as a strong empirical read, not a
            guarantee.
        """
        url = f"{self.BASE_URL}/v4/leagues/{league_id}/players/{player_id}/transferHistory"

        response = self.session.get(url)

        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(
                f"Failed to fetch player transfer history: {response.status_code} - {response.text}"
            )

    def get_manager_squad(self, league_id: str, manager_id: str) -> dict[str, Any]:
        """
        View a competitor's squad
        GET /v4/leagues/{league_id}/managers/{manager_id}/squad
        """
        url = f"{self.BASE_URL}/v4/leagues/{league_id}/managers/{manager_id}/squad"

        response = self.session.get(url)

        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(
                f"Failed to fetch manager squad: {response.status_code} - {response.text}"
            )

    def get_manager_dashboard(self, league_id: str, manager_id: str) -> dict[str, Any]:
        """
        Per-manager dashboard, includes cumulative transfer P&L (`prft`) and
        matchday wins (`mdw`) — neither is in /ranking. REH-38.

        GET /v4/leagues/{league_id}/managers/{manager_id}/dashboard
        """
        url = f"{self.BASE_URL}/v4/leagues/{league_id}/managers/{manager_id}/dashboard"

        response = self.session.get(url)

        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(
                f"Failed to fetch manager dashboard: {response.status_code} - {response.text}"
            )

    def get_manager_transfer_history(
        self, league_id: str, manager_id: str, start: int = 0
    ) -> dict[str, Any]:
        """
        Per-manager transfer history, paginated 25 per page in reverse
        chronological order. ``start`` is the zero-indexed offset; pass 25,
        50, ... to walk backward through history. REH-38.

        GET /v4/leagues/{league_id}/managers/{manager_id}/transfer[?start=N]
        """
        url = f"{self.BASE_URL}/v4/leagues/{league_id}/managers/{manager_id}/transfer"
        if start:
            url = f"{url}?start={int(start)}"

        response = self.session.get(url)

        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(
                f"Failed to fetch manager transfer history: {response.status_code} - {response.text}"
            )

    def get_league_ranking(self, league_id: str, day_number: int | None = None) -> dict[str, Any]:
        """
        Get league ranking/standings.

        ``day_number`` (optional) returns the historical state at the end of
        that matchday — same response shape as the current call. Without it,
        the response reflects the most recent completed matchday (and ``day``
        in the response tells you which one).

        GET /v4/leagues/{league_id}/ranking[?dayNumber=N]
        """
        url = f"{self.BASE_URL}/v4/leagues/{league_id}/ranking"
        if day_number is not None:
            url = f"{url}?dayNumber={int(day_number)}"

        response = self.session.get(url)

        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(
                f"Failed to fetch league ranking: {response.status_code} - {response.text}"
            )

    def get_user_teamcenter(
        self, league_id: str, user_id: str, day_number: int | None = None
    ) -> dict[str, Any]:
        """
        Get a user's per-matchday team center: the lineup they fielded that
        day plus per-player actual points (``p`` in each ``lp[]`` item) and
        per-match status (``mst``: 2 = finished). With ``day_number`` returns
        historical data for that matchday.

        Sum of ``lp[].p`` equals the manager's matchday total — matches
        ``mdp`` in ``/ranking.us[]`` for the same manager and day.

        GET /v4/leagues/{league_id}/users/{user_id}/teamcenter[?dayNumber=N]
        """
        url = f"{self.BASE_URL}/v4/leagues/{league_id}/users/{user_id}/teamcenter"
        if day_number is not None:
            url = f"{url}?dayNumber={int(day_number)}"

        response = self.session.get(url)

        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(
                f"Failed to fetch user teamcenter: {response.status_code} - {response.text}"
            )
