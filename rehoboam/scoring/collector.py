"""DataCollector — assembles PlayerData from pre-fetched API data."""

from ..kickbase_client import MarketPlayer
from ..matchup_analyzer import DoubleGameweekInfo, MatchupAnalyzer
from .models import PlayerData


class DataCollector:
    """Assembles and validates data for scoring. Does NOT call the API."""

    def __init__(self, matchup_analyzer: MatchupAnalyzer):
        self.matchup_analyzer = matchup_analyzer

    def collect(
        self,
        player: MarketPlayer,
        performance: dict | None,
        player_details: dict | None,
        team_profiles: dict[str, dict],
    ) -> PlayerData:
        """Assemble PlayerData from pre-fetched data, flagging what's missing."""
        missing = []

        if performance is None:
            missing.append("performance")
        if player_details is None:
            missing.append("player_details")

        # DGW detection
        dgw_info = DoubleGameweekInfo(is_dgw=False)
        if player_details:
            dgw_info = self.matchup_analyzer.detect_double_gameweek(player_details)

        # Team strength
        team_strength = None
        team_profile = team_profiles.get(player.team_id)
        if team_profile:
            team_strength = self.matchup_analyzer.get_team_strength(team_profile)
        else:
            missing.append("team_strength")

        # Opponent strength (from next matchup)
        opponent_strength = None
        if player_details:
            next_matchup = self.matchup_analyzer.get_next_matchup(player_details)
            if next_matchup:
                opponent_profile = team_profiles.get(next_matchup.opponent_id)
                if opponent_profile:
                    opponent_strength = self.matchup_analyzer.get_team_strength(opponent_profile)
                else:
                    missing.append("opponent_strength")
            else:
                missing.append("opponent_strength")
        else:
            missing.append("opponent_strength")

        return PlayerData(
            player=player,
            performance=performance,
            player_details=player_details,
            team_strength=team_strength,
            opponent_strength=opponent_strength,
            dgw_info=dgw_info,
            missing=missing,
        )
