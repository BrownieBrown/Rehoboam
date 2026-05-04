"""Matchup and team strength analysis for player evaluation"""

from dataclasses import dataclass
from typing import Any


@dataclass
class PlayerStatus:
    """Player health and lineup status"""

    is_healthy: bool
    is_likely_starter: bool
    lineup_probability: int  # 1-5 (1=starter, 5=unlikely)
    status_code: int  # 0=healthy, 2/4/256=injured/unavailable
    reason: str


@dataclass
class MatchupInfo:
    """Upcoming matchup information"""

    opponent_id: str
    opponent_name: str
    is_home: bool
    match_date: str
    opponent_rank: int | None = None
    difficulty_score: float = 0.0  # 0-100 (higher = harder)


@dataclass
class StrengthOfSchedule:
    """Strength of schedule analysis"""

    upcoming_opponents: list[str]  # List of opponent names
    avg_opponent_strength: float  # Average strength (0-100)
    avg_opponent_rank: float  # Average league position
    num_games_analyzed: int
    difficulty_rating: str  # "Very Easy", "Easy", "Medium", "Difficult", "Very Difficult"
    sos_score: float  # 0-100 (higher = harder schedule)
    sos_bonus: int  # -10 to +10 points to add to value score
    short_term_rating: str | None = None  # Next 3 games
    medium_term_rating: str | None = None  # Next 5 games
    season_rating: str | None = None  # Full season


@dataclass
class TeamStrength:
    """Team strength metrics"""

    team_id: str
    team_name: str
    league_position: int
    wins: int
    draws: int
    losses: int
    total_points: int
    strength_score: float  # 0-100 (higher = stronger team)


@dataclass
class DoubleGameweekInfo:
    """Double gameweek detection result"""

    is_dgw: bool
    dgw_matchday: str | None = None
    match_count: int = 1


class MatchupAnalyzer:
    """Analyzes player matchups, team strength, and lineup status"""

    # Status code interpretation
    STATUS_HEALTHY = 0
    STATUS_UNKNOWN_1 = 2
    STATUS_INJURED_SHORT = 4
    STATUS_INJURED_LONG = 256

    # Lineup probability interpretation
    PROB_STARTER = 1
    PROB_ROTATION = 2
    PROB_BENCH = 3
    PROB_RARELY_PLAYS = 4
    PROB_UNLIKELY = 5

    def __init__(self):
        self.team_cache: dict[str, TeamStrength] = {}
        self._dgw_teams: set[str] | None = None  # Cache of team IDs with DGW

    def load_dgw_from_matchdays(self, matchdays_data: dict[str, Any]) -> set[str]:
        """
        Detect DGW teams from competition matchday schedule.
        Call once per session with data from GET /v4/competitions/{id}/matchdays.

        Returns set of team IDs that have double gameweeks.
        """
        dgw_teams: set[str] = set()
        items = matchdays_data.get("it", matchdays_data.get("mds", []))

        # Group matches by matchday, count per team
        for matchday in items:
            md_id = str(matchday.get("id", matchday.get("mdid", "")))
            matches = matchday.get("m", matchday.get("matches", []))
            if not md_id or not matches:
                continue

            team_counts: dict[str, int] = {}
            for match in matches:
                t1 = str(match.get("t1i", match.get("t1", "")))
                t2 = str(match.get("t2i", match.get("t2", "")))
                if t1:
                    team_counts[t1] = team_counts.get(t1, 0) + 1
                if t2:
                    team_counts[t2] = team_counts.get(t2, 0) + 1

            for team_id, count in team_counts.items():
                if count >= 2:
                    dgw_teams.add(team_id)

        self._dgw_teams = dgw_teams
        return dgw_teams

    def is_dgw_team(self, team_id: str) -> bool:
        """Check if a team has a DGW based on loaded matchday data."""
        if self._dgw_teams is None:
            return False
        return team_id in self._dgw_teams

    def detect_double_gameweek(self, player_details: dict[str, Any]) -> DoubleGameweekInfo:
        """
        Detect if a player's team has a double gameweek (2+ matches in one matchday).

        Reads mdsum from player_details, filters upcoming matches (mdst == 0),
        and groups by mdid. If any matchday has 2+ matches, it's a DGW.

        Args:
            player_details: Response from get_player_details containing mdsum

        Returns:
            DoubleGameweekInfo with DGW status
        """
        matchups = player_details.get("mdsum", [])
        if not matchups:
            return DoubleGameweekInfo(is_dgw=False)

        # Filter upcoming matches only (mdst == 0 means not played yet)
        upcoming = [m for m in matchups if m.get("mdst") == 0]
        if not upcoming:
            return DoubleGameweekInfo(is_dgw=False)

        # Group by matchday ID — if any matchday has 2+ matches, it's a DGW
        matchday_counts: dict[str, int] = {}
        for match in upcoming:
            mdid = str(match.get("mdid", ""))
            if mdid:
                matchday_counts[mdid] = matchday_counts.get(mdid, 0) + 1

        for mdid, count in matchday_counts.items():
            if count >= 2:
                return DoubleGameweekInfo(
                    is_dgw=True,
                    dgw_matchday=mdid,
                    match_count=count,
                )

        return DoubleGameweekInfo(is_dgw=False)

    def analyze_player_status(self, player_details: dict[str, Any]) -> PlayerStatus:
        """
        Analyze if player is healthy and likely to play

        Args:
            player_details: Response from get_player_details

        Returns:
            PlayerStatus with health and lineup information
        """
        status_code = player_details.get("st", 0)
        lineup_prob = player_details.get("prob", 5)

        # Determine health
        is_healthy = status_code == self.STATUS_HEALTHY

        # Determine if likely starter
        is_likely_starter = lineup_prob <= self.PROB_ROTATION

        # Generate reason
        if status_code == self.STATUS_INJURED_LONG:
            reason = "Long-term injury"
        elif status_code == self.STATUS_INJURED_SHORT:
            reason = "Injured"
        elif status_code == self.STATUS_UNKNOWN_1:
            reason = "Status uncertain"
        elif lineup_prob == self.PROB_STARTER:
            reason = "Regular starter"
        elif lineup_prob == self.PROB_ROTATION:
            reason = "Rotation player"
        elif lineup_prob == self.PROB_BENCH:
            reason = "Bench player"
        elif lineup_prob == self.PROB_RARELY_PLAYS:
            reason = "Rarely plays"
        elif lineup_prob == self.PROB_UNLIKELY:
            reason = "Unlikely to play"
        else:
            reason = "Healthy, rotation player"

        return PlayerStatus(
            is_healthy=is_healthy,
            is_likely_starter=is_likely_starter,
            lineup_probability=lineup_prob,
            status_code=status_code,
            reason=reason,
        )

    def get_team_strength(self, team_profile: dict[str, Any]) -> TeamStrength:
        """
        Calculate team strength from standings

        Args:
            team_profile: Response from get_team_profile

        Returns:
            TeamStrength with metrics
        """
        team_id = team_profile.get("tid", "")
        team_name = team_profile.get("tn", "Unknown")
        position = team_profile.get("pl", 18)
        wins = team_profile.get("tw", 0)
        draws = team_profile.get("td", 0)
        losses = team_profile.get("tl", 0)

        # Calculate total points (3 for win, 1 for draw)
        total_points = (wins * 3) + draws

        # Calculate strength score (0-100)
        # Based on league position (lower is better)
        # 1st place = 100, 18th place = 0
        position_score = ((18 - position) / 17) * 100

        # Factor in points per game
        games_played = wins + draws + losses
        if games_played > 0:
            points_per_game = total_points / games_played
            ppg_score = (points_per_game / 3) * 100  # Max 3 ppg = 100 score
        else:
            ppg_score = 50

        # Weighted average (60% position, 40% ppg)
        strength_score = (position_score * 0.6) + (ppg_score * 0.4)

        team_strength = TeamStrength(
            team_id=team_id,
            team_name=team_name,
            league_position=position,
            wins=wins,
            draws=draws,
            losses=losses,
            total_points=total_points,
            strength_score=round(strength_score, 1),
        )

        # Cache it
        self.team_cache[team_id] = team_strength

        return team_strength

    def get_next_matchups(self, player_details: dict[str, Any], n: int = 3) -> list[MatchupInfo]:
        """Return up to *n* upcoming MatchupInfo objects from mdsum."""
        matchups = player_details.get("mdsum", [])
        player_team_id = player_details.get("tid", "")
        upcoming = [m for m in matchups if m.get("mdst") == 0]

        results: list[MatchupInfo] = []
        for match in upcoming[:n]:
            t1_id = match.get("t1", "")
            t2_id = match.get("t2", "")
            is_home = t1_id == player_team_id
            opponent_id = t2_id if is_home else t1_id
            results.append(
                MatchupInfo(
                    opponent_id=opponent_id,
                    opponent_name=f"Team {opponent_id}",
                    is_home=is_home,
                    match_date=match.get("md", ""),
                    opponent_rank=None,
                    difficulty_score=50.0,
                )
            )
        return results
