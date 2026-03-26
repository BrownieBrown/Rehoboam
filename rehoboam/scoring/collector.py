"""DataCollector — assembles PlayerData from pre-fetched API data."""

from rehoboam.kickbase_client import MarketPlayer
from rehoboam.matchup_analyzer import MatchupAnalyzer

from .models import PlayerData


class DataCollector:
    """Assembles and validates player data for scoring.

    Does NOT call the API directly. Receives pre-fetched data from the caller.
    """

    def __init__(self, matchup_analyzer: MatchupAnalyzer):
        self.matchup_analyzer = matchup_analyzer

    def collect(
        self,
        player: MarketPlayer,
        performance: dict | None,
        player_details: dict | None,
        team_profiles: dict[str, dict],
    ) -> PlayerData:
        """Assemble PlayerData from pre-fetched API data.

        Args:
            player: MarketPlayer from the API.
            performance: Performance/stats dict from the API, or None if unavailable.
            player_details: Player detail dict from the API, or None if unavailable.
            team_profiles: Mapping of team_id -> team profile dict (may be empty).

        Returns:
            PlayerData with resolved strengths and a list of any missing fields.
        """
        missing: list[str] = []
        is_dgw = False

        if performance is None:
            missing.append("performance")
        if player_details is None:
            missing.append("player_details")

        team_strength = None
        opponent_strength = None

        upcoming_opponent_strengths = []

        if player_details:
            team_id = player_details.get("tid", "")
            if team_id and team_id in team_profiles:
                team_strength = self.matchup_analyzer.get_team_strength(team_profiles[team_id])

            # Resolve next 3 opponents for multi-fixture lookahead
            next_matchups = self.matchup_analyzer.get_next_matchups(player_details, n=3)
            for matchup in next_matchups:
                if matchup.opponent_id and matchup.opponent_id in team_profiles:
                    opp_str = self.matchup_analyzer.get_team_strength(
                        team_profiles[matchup.opponent_id]
                    )
                    upcoming_opponent_strengths.append(opp_str)

            # Primary opponent (backward compat) = first from the list
            if next_matchups and next_matchups[0].opponent_id:
                if next_matchups[0].opponent_id in team_profiles:
                    opponent_strength = self.matchup_analyzer.get_team_strength(
                        team_profiles[next_matchups[0].opponent_id]
                    )
                else:
                    missing.append("opponent_strength")

            # DGW detection — players with 2+ matches in one matchday score ~1.8x
            dgw_info = self.matchup_analyzer.detect_double_gameweek(player_details)
            is_dgw = dgw_info.is_dgw

        return PlayerData(
            player=player,
            performance=performance,
            player_details=player_details,
            team_strength=team_strength,
            opponent_strength=opponent_strength,
            is_dgw=is_dgw,
            missing=missing,
            upcoming_opponent_strengths=upcoming_opponent_strengths,
        )
