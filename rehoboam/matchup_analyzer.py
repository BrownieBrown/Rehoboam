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

    def get_next_matchup(self, player_details: dict[str, Any]) -> MatchupInfo | None:
        """
        Get next upcoming matchup for a player

        Args:
            player_details: Response from get_player_details

        Returns:
            MatchupInfo for next match, or None if no upcoming matches
        """
        matchups = player_details.get("mdsum", [])
        player_team_id = player_details.get("tid", "")

        # Find next match (mdst=0 means not played yet)
        upcoming_matches = [m for m in matchups if m.get("mdst") == 0]

        if not upcoming_matches:
            return None

        # Get the first upcoming match
        next_match = upcoming_matches[0]

        # Determine if home or away
        t1_id = next_match.get("t1", "")
        t2_id = next_match.get("t2", "")
        is_home = t1_id == player_team_id

        opponent_id = t2_id if is_home else t1_id

        return MatchupInfo(
            opponent_id=opponent_id,
            opponent_name=f"Team {opponent_id}",  # Will be enriched later
            is_home=is_home,
            match_date=next_match.get("md", ""),
            opponent_rank=None,
            difficulty_score=50.0,  # Default medium difficulty
        )

    def calculate_matchup_difficulty(
        self, player_team: TeamStrength, opponent_team: TeamStrength
    ) -> float:
        """
        Calculate matchup difficulty score

        Args:
            player_team: Player's team strength
            opponent_team: Opponent team strength

        Returns:
            Difficulty score 0-100 (higher = harder matchup)
        """
        # Difficulty is based on opponent strength relative to player's team
        strength_diff = opponent_team.strength_score - player_team.strength_score

        # Normalize to 0-100 scale
        # If opponent is 50 points stronger, that's very hard (90+)
        # If opponent is equal strength, medium difficulty (50)
        # If opponent is 50 points weaker, very easy (10-)

        base_difficulty = 50 + (strength_diff / 2)
        difficulty = max(0, min(100, base_difficulty))

        return round(difficulty, 1)

    def get_matchup_bonus(
        self,
        player_details: dict[str, Any],
        player_team: TeamStrength,
        opponent_team: TeamStrength | None = None,
    ) -> dict[str, Any]:
        """
        Calculate value bonus/penalty based on matchup and team context

        Args:
            player_details: Player details from API
            player_team: Player's team strength
            opponent_team: Optional opponent team strength

        Returns:
            dict with:
            - bonus_points: Points to add to value score (-20 to +20)
            - reason: Explanation
            - matchup_difficulty: 0-100 score
        """
        # Check player status
        status = self.analyze_player_status(player_details)

        # CRITICAL: Penalize injured/unlikely players heavily
        if not status.is_healthy:
            return {
                "bonus_points": -25,
                "reason": f"Injured/unavailable ({status.reason})",
                "matchup_difficulty": None,
                "player_status": status.reason,
            }

        if not status.is_likely_starter:
            penalty = -10 if status.lineup_probability == self.PROB_BENCH else -15
            return {
                "bonus_points": penalty,
                "reason": f"Unlikely to play ({status.reason})",
                "matchup_difficulty": None,
                "player_status": status.reason,
            }

        # Get next matchup
        next_matchup = self.get_next_matchup(player_details)

        if not next_matchup or not opponent_team:
            # No matchup data, give small bonus for strong team
            if player_team.strength_score >= 70:
                return {
                    "bonus_points": 5,
                    "reason": f"Strong team (rank {player_team.league_position})",
                    "matchup_difficulty": None,
                    "player_status": status.reason,
                }
            else:
                return {
                    "bonus_points": 0,
                    "reason": "Likely starter",
                    "matchup_difficulty": None,
                    "player_status": status.reason,
                }

        # Calculate matchup difficulty
        difficulty = self.calculate_matchup_difficulty(player_team, opponent_team)

        # Determine bonus based on matchup
        if difficulty < 30:
            # Easy matchup
            bonus = 10 if status.lineup_probability == self.PROB_STARTER else 5
            reason = f"Easy matchup vs rank {opponent_team.league_position}"
        elif difficulty < 50:
            # Favorable matchup
            bonus = 5 if status.lineup_probability == self.PROB_STARTER else 2
            reason = f"Favorable matchup vs rank {opponent_team.league_position}"
        elif difficulty < 70:
            # Medium matchup
            bonus = 0
            reason = f"Medium matchup vs rank {opponent_team.league_position}"
        else:
            # Hard matchup
            penalty = -5 if status.lineup_probability == self.PROB_STARTER else -8
            bonus = penalty
            reason = f"Difficult matchup vs rank {opponent_team.league_position}"

        # Extra bonus for starters on strong teams
        if status.lineup_probability == self.PROB_STARTER and player_team.strength_score >= 75:
            bonus += 3
            reason += " (key player on top team)"

        return {
            "bonus_points": bonus,
            "reason": reason,
            "matchup_difficulty": difficulty,
            "player_status": status.reason,
            "next_opponent": opponent_team.team_name,
            "next_opponent_rank": opponent_team.league_position,
        }

    def analyze_strength_of_schedule(
        self,
        player_details: dict[str, Any],
        player_team: TeamStrength,
        fetch_opponent_team: callable,
    ) -> StrengthOfSchedule | None:
        """
        Analyze strength of schedule using hybrid approach

        Args:
            player_details: Player details from API
            player_team: Player's team strength
            fetch_opponent_team: Function to fetch opponent team profile

        Returns:
            StrengthOfSchedule with hybrid analysis
        """
        matchups = player_details.get("mdsum", [])
        player_team_id = player_details.get("tid", "")

        # Get upcoming matches (mdst=0 means not played yet)
        upcoming_matches = [m for m in matchups if m.get("mdst") == 0]

        if not upcoming_matches:
            return None

        # Analyze different time horizons
        short_term = self._analyze_schedule_window(
            upcoming_matches[:3], player_team_id, player_team, fetch_opponent_team
        )
        medium_term = self._analyze_schedule_window(
            upcoming_matches[:5], player_team_id, player_team, fetch_opponent_team
        )
        season_long = self._analyze_schedule_window(
            upcoming_matches, player_team_id, player_team, fetch_opponent_team
        )

        # Weighted combination (70% short, 20% medium, 10% season)
        if short_term and medium_term and season_long:
            weighted_sos = (
                (short_term["avg_opponent_strength"] * 0.7)
                + (medium_term["avg_opponent_strength"] * 0.2)
                + (season_long["avg_opponent_strength"] * 0.1)
            )
        elif short_term:
            weighted_sos = short_term["avg_opponent_strength"]
        else:
            return None

        # Calculate bonus based primarily on short-term (next 3 games)
        sos_bonus = self._calculate_sos_bonus(
            short_term["avg_opponent_rank"] if short_term else 9.5
        )

        # Determine difficulty rating
        difficulty_rating = self._get_difficulty_rating(weighted_sos)

        return StrengthOfSchedule(
            upcoming_opponents=short_term["opponent_names"] if short_term else [],
            avg_opponent_strength=round(weighted_sos, 1),
            avg_opponent_rank=round(short_term["avg_opponent_rank"], 1) if short_term else 9.5,
            num_games_analyzed=len(upcoming_matches),
            difficulty_rating=difficulty_rating,
            sos_score=round(weighted_sos, 1),
            sos_bonus=sos_bonus,
            short_term_rating=(
                self._get_difficulty_rating(short_term["avg_opponent_strength"])
                if short_term
                else None
            ),
            medium_term_rating=(
                self._get_difficulty_rating(medium_term["avg_opponent_strength"])
                if medium_term
                else None
            ),
            season_rating=(
                self._get_difficulty_rating(season_long["avg_opponent_strength"])
                if season_long
                else None
            ),
        )

    def _analyze_schedule_window(
        self,
        matches: list[dict[str, Any]],
        player_team_id: str,
        player_team: TeamStrength,
        fetch_opponent_team: callable,
    ) -> dict[str, Any] | None:
        """
        Analyze a specific window of upcoming matches

        Returns:
            dict with avg_opponent_strength, avg_opponent_rank, opponent_names
        """
        if not matches:
            return None

        opponent_strengths = []
        opponent_ranks = []
        opponent_names = []

        for match in matches:
            # Determine opponent
            t1_id = match.get("t1", "")
            t2_id = match.get("t2", "")
            opponent_id = t2_id if t1_id == player_team_id else t1_id

            # Try to get opponent team strength
            try:
                opponent_team = fetch_opponent_team(opponent_id)
                if opponent_team:
                    opponent_strengths.append(opponent_team.strength_score)
                    opponent_ranks.append(opponent_team.league_position)
                    opponent_names.append(opponent_team.team_name)
            except Exception:
                # If we can't fetch opponent, use league average (50 strength, rank 9.5)
                opponent_strengths.append(50.0)
                opponent_ranks.append(9.5)
                opponent_names.append(f"Team {opponent_id}")

        if not opponent_strengths:
            return None

        return {
            "avg_opponent_strength": sum(opponent_strengths) / len(opponent_strengths),
            "avg_opponent_rank": sum(opponent_ranks) / len(opponent_ranks),
            "opponent_names": opponent_names,
        }

    def _calculate_sos_bonus(self, avg_opponent_rank: float) -> int:
        """
        Calculate value score bonus based on strength of schedule

        Args:
            avg_opponent_rank: Average opponent league position (1-18)

        Returns:
            Bonus points (-10 to +10)
        """
        # Very easy schedule (playing weak teams)
        if avg_opponent_rank >= 14:
            return 10
        # Easy schedule
        elif avg_opponent_rank >= 11:
            return 5
        # Medium schedule
        elif avg_opponent_rank >= 8:
            return 0
        # Difficult schedule
        elif avg_opponent_rank >= 4:
            return -5
        # Very difficult schedule (playing top teams)
        else:
            return -10

    def _get_difficulty_rating(self, avg_opponent_strength: float) -> str:
        """Get human-readable difficulty rating"""
        if avg_opponent_strength < 30:
            return "Very Easy"
        elif avg_opponent_strength < 45:
            return "Easy"
        elif avg_opponent_strength < 60:
            return "Medium"
        elif avg_opponent_strength < 75:
            return "Difficult"
        else:
            return "Very Difficult"
