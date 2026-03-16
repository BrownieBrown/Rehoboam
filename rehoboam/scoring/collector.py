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

        if performance is None:
            missing.append("performance")
        if player_details is None:
            missing.append("player_details")

        team_strength = None
        opponent_strength = None

        if player_details:
            team_id = player_details.get("tid", "")
            if team_id and team_id in team_profiles:
                team_strength = self.matchup_analyzer.get_team_strength(team_profiles[team_id])

            next_matchup = self.matchup_analyzer.get_next_matchup(player_details)
            if next_matchup and next_matchup.opponent_id:
                if next_matchup.opponent_id in team_profiles:
                    opponent_strength = self.matchup_analyzer.get_team_strength(
                        team_profiles[next_matchup.opponent_id]
                    )
                else:
                    missing.append("opponent_strength")
            # If there is no next matchup at all, opponent_strength is simply
            # unavailable rather than "missing" — we do not flag it.

        return PlayerData(
            player=player,
            performance=performance,
            player_details=player_details,
            team_strength=team_strength,
            opponent_strength=opponent_strength,
            is_dgw=False,  # DGW detection is a separate feature
            missing=missing,
        )
