"""Advanced player value calculation based on points and performance"""

from dataclasses import dataclass


@dataclass
class PlayerValue:
    """Calculated value metrics for a player"""

    player_id: str
    points: int
    average_points: float
    market_value: int
    price: int

    # Calculated metrics
    points_per_million: float
    avg_points_per_million: float
    value_score: float  # Combined metric (0-100)

    # Trend data (optional)
    trend_direction: str | None = None  # rising, falling, stable, unknown
    trend_pct: float | None = None  # 14-day trend percentage
    vs_peak_pct: float | None = None  # Current vs peak percentage

    # Sample size reliability (NEW)
    games_played: int | None = None  # Number of games played this season
    sample_size_confidence: float | None = None  # 0-1, confidence based on games played
    consistency_score: float | None = None  # 0-1, how consistent is their performance

    @classmethod
    def calculate(
        cls,
        player,
        trend_data: dict | None = None,
        performance_data: dict | None = None,
        matchup_context: dict | None = None,
    ) -> "PlayerValue":
        """
        Calculate value metrics for a player

        Args:
            player: Player object with points, market_value, etc.
            trend_data: Optional dict with trend analysis from market value history
            performance_data: Optional dict with match-by-match performance data
            matchup_context: Optional dict with team form, league position, matchups
                {
                    "player_team": TeamStrength(wins, losses, league_position, ...),
                    "next_matchup": NextMatchup(is_home, ...),
                    ...
                }
        """
        price = getattr(player, "price", player.market_value)
        market_value = player.market_value
        points = player.points
        avg_points = player.average_points

        # Extract games played and consistency from performance data
        games_played = None
        sample_size_confidence = None
        consistency_score = None

        if performance_data:
            games_played, consistency_score = cls._extract_games_and_consistency(performance_data)
            sample_size_confidence = cls._calculate_sample_confidence(games_played)

        # Extract advanced factors (Phase 3)
        team_form_wins = None
        team_form_losses = None
        league_position = None
        minutes_trend = None
        avg_minutes = None
        is_substitute_pattern = None
        home_away_bonus = None

        # Extract from matchup context if available
        if matchup_context and matchup_context.get("has_data"):
            player_team = matchup_context.get("player_team")
            if player_team:
                # Team form (recent wins/losses)
                team_form_wins = getattr(player_team, "recent_wins", None)
                team_form_losses = getattr(player_team, "recent_losses", None)
                # League position
                league_position = getattr(player_team, "league_position", None)

            # Next matchup (home/away bonus)
            next_matchup = matchup_context.get("next_matchup")
            if next_matchup:
                is_home = getattr(next_matchup, "is_home", None)
                # For now, simplified home/away bonus
                # Could be enhanced with player-specific home/away stats
                if is_home is True:
                    home_away_bonus = 2  # Small home advantage
                elif is_home is False:
                    home_away_bonus = -2  # Small away disadvantage

        # Extract minutes trend from performance data
        if performance_data:
            minutes_trend, avg_minutes, is_substitute_pattern = cls._extract_minutes_analysis(
                performance_data
            )

        # Avoid division by zero
        price_millions = max(price / 1_000_000, 0.001)

        # Points per million euros
        points_per_million = points / price_millions
        avg_points_per_million = avg_points / price_millions

        # Extract trend data
        trend_direction = None
        trend_pct = None
        vs_peak_pct = None

        if trend_data and trend_data.get("has_data"):
            trend_direction = trend_data.get("trend", "unknown")
            trend_pct = trend_data.get("trend_pct", 0)

            # Calculate vs peak
            peak_value = trend_data.get("peak_value", 0)
            current_value = trend_data.get("current_value", market_value)
            if peak_value > 0:
                vs_peak_pct = ((current_value - peak_value) / peak_value) * 100

        # Combined value score (0-100)
        # Factors: current points, average points, affordability, market trends, sample size, and advanced factors
        value_score = cls._calculate_value_score(
            points=points,
            avg_points=avg_points,
            price_millions=price_millions,
            market_value=market_value,
            trend_direction=trend_direction,
            trend_pct=trend_pct,
            vs_peak_pct=vs_peak_pct,
            games_played=games_played,
            sample_size_confidence=sample_size_confidence,
            consistency_score=consistency_score,
            # Advanced factors
            team_form_wins=team_form_wins,
            team_form_losses=team_form_losses,
            league_position=league_position,
            minutes_trend=minutes_trend,
            avg_minutes=avg_minutes,
            is_substitute_pattern=is_substitute_pattern,
            home_away_bonus=home_away_bonus,
        )

        return cls(
            player_id=player.id,
            points=points,
            average_points=avg_points,
            market_value=market_value,
            price=price,
            points_per_million=points_per_million,
            avg_points_per_million=avg_points_per_million,
            value_score=value_score,
            trend_direction=trend_direction,
            trend_pct=trend_pct,
            vs_peak_pct=vs_peak_pct,
            games_played=games_played,
            sample_size_confidence=sample_size_confidence,
            consistency_score=consistency_score,
        )

    @staticmethod
    def _extract_games_and_consistency(performance_data: dict) -> tuple[int, float]:
        """
        Extract games played and consistency score from performance data

        Args:
            performance_data: dict with match-by-match performance data

        Returns:
            (games_played, consistency_score)
                games_played: Number of games played this season
                consistency_score: 0-1, how consistent the performance is (1 = very consistent)
        """
        try:
            # Get current season matches
            seasons = performance_data.get("it", [])
            if not seasons:
                return 0, 0.0

            # Sort by season to get the latest one
            seasons_sorted = sorted(seasons, key=lambda s: s.get("ti", ""), reverse=True)
            current_season = seasons_sorted[0] if seasons_sorted else None

            if not current_season:
                return 0, 0.0

            matches = current_season.get("ph", [])

            # CRITICAL FIX: Count only matches where player actually played
            # matches contains ALL team games, but player might not have played in all
            # A player "played" if they have points != 0 (even negative) OR minutes > 0
            # Filter to only matches where player was on the field
            matches_played = []
            for match in matches:
                points = match.get("p", 0)
                # Some matches might have "t" (minutes) field, check that too
                minutes = match.get("t", 0)
                # If player had points (positive or negative) OR had minutes, they played
                if points != 0 or minutes > 0:
                    matches_played.append(match)

            games_played = len(matches_played)

            if games_played == 0:
                return 0, 0.0

            # Calculate consistency: coefficient of variation (lower = more consistent)
            # Get points from each match WHERE PLAYER ACTUALLY PLAYED
            match_points = [match.get("p", 0) for match in matches_played]

            if games_played == 1:
                # Can't calculate consistency with 1 game
                return games_played, 0.5  # Medium confidence

            # Calculate mean and standard deviation
            mean_points = sum(match_points) / games_played
            if mean_points == 0:
                return games_played, 0.0  # All zeros = no consistency

            variance = sum((p - mean_points) ** 2 for p in match_points) / games_played
            std_dev = variance**0.5

            # Coefficient of variation (CV)
            cv = std_dev / mean_points if mean_points > 0 else 1.0

            # Convert CV to consistency score (0-1, where 1 = very consistent)
            # CV of 0 = perfect consistency (score 1.0)
            # CV of 2+ = very inconsistent (score 0.0)
            consistency_score = max(0.0, 1.0 - (cv / 2.0))

            return games_played, consistency_score

        except Exception:
            return 0, 0.0

    @staticmethod
    def _extract_minutes_analysis(
        performance_data: dict,
    ) -> tuple[str | None, float | None, bool | None]:
        """
        Extract minutes trend, average minutes, and substitution pattern

        Args:
            performance_data: dict with match-by-match performance data

        Returns:
            (minutes_trend, avg_minutes, is_substitute_pattern)
                minutes_trend: "increasing", "stable", "decreasing", or None
                avg_minutes: Average minutes per game
                is_substitute_pattern: True if frequently substituted
        """
        try:
            # Get current season matches
            seasons = performance_data.get("it", [])
            if not seasons:
                return None, None, None

            seasons_sorted = sorted(seasons, key=lambda s: s.get("ti", ""), reverse=True)
            current_season = seasons_sorted[0] if seasons_sorted else None

            if not current_season:
                return None, None, None

            matches = current_season.get("ph", [])

            # Get minutes from each match (field "t" = time/minutes)
            minutes_data = []
            for match in matches:
                minutes = match.get("t", None)
                if minutes is not None:
                    minutes_data.append(minutes)

            if len(minutes_data) < 2:
                return None, None, None

            # Calculate average minutes
            avg_minutes = sum(minutes_data) / len(minutes_data)

            # Determine trend (compare first half vs second half of season)
            if len(minutes_data) >= 4:
                half_point = len(minutes_data) // 2
                first_half_avg = sum(minutes_data[:half_point]) / half_point
                second_half_avg = sum(minutes_data[half_point:]) / (len(minutes_data) - half_point)

                diff_pct = ((second_half_avg - first_half_avg) / max(first_half_avg, 1)) * 100

                if diff_pct > 15:
                    minutes_trend = "increasing"
                elif diff_pct < -15:
                    minutes_trend = "decreasing"
                else:
                    minutes_trend = "stable"
            else:
                minutes_trend = "stable"  # Not enough data for trend

            # Detect substitute pattern
            # Player is a substitute if:
            # 1. Average minutes < 60, OR
            # 2. High variance in minutes (some full games, some partial)
            is_substitute_pattern = False

            if avg_minutes < 60:
                is_substitute_pattern = True
            elif len(minutes_data) >= 3:
                # Check variance
                variance = sum((m - avg_minutes) ** 2 for m in minutes_data) / len(minutes_data)
                std_dev = variance**0.5

                # High variance and low avg = substitute
                if std_dev > 25 and avg_minutes < 75:
                    is_substitute_pattern = True

            return minutes_trend, avg_minutes, is_substitute_pattern

        except Exception:
            return None, None, None

    @staticmethod
    def _calculate_sample_confidence(games_played: int | None) -> float:
        """
        Calculate confidence level based on sample size (games played)

        Args:
            games_played: Number of games played

        Returns:
            Confidence score 0-1 (1 = high confidence, 0 = no confidence)
        """
        if games_played is None or games_played == 0:
            return 0.0

        # Confidence curve: reaches ~1.0 at 10+ games
        # 1 game = 0.1 confidence
        # 5 games = 0.5 confidence
        # 10 games = 0.9 confidence
        # 15+ games = 1.0 confidence
        if games_played >= 15:
            return 1.0
        elif games_played >= 10:
            return 0.9
        elif games_played >= 8:
            return 0.8
        elif games_played >= 6:
            return 0.7
        elif games_played >= 5:
            return 0.6
        elif games_played >= 4:
            return 0.5
        elif games_played >= 3:
            return 0.4
        elif games_played >= 2:
            return 0.3
        else:  # 1 game
            return 0.1

    @staticmethod
    def _calculate_value_score(
        points: int,
        avg_points: float,
        price_millions: float,
        market_value: int,
        trend_direction: str | None = None,
        trend_pct: float | None = None,
        vs_peak_pct: float | None = None,
        games_played: int | None = None,
        sample_size_confidence: float | None = None,
        consistency_score: float | None = None,
        # Advanced factors (Phase 3)
        team_form_wins: int | None = None,
        team_form_losses: int | None = None,
        league_position: int | None = None,
        minutes_trend: str | None = None,  # "increasing", "stable", "decreasing"
        avg_minutes: float | None = None,
        is_substitute_pattern: bool | None = None,
        home_away_bonus: float | None = None,
    ) -> float:
        """
        Calculate a value score (0-100) based on multiple factors

        Higher score = better value

        Base Factors:
        - Points efficiency (0-40)
        - Average points (0-25)
        - Affordability (0-15)
        - Current form (0-20)
        - Market momentum (0-15)
        - Sample size reliability penalty (0 to -30)

        Advanced Factors (Phase 3):
        - Team form (0-15)
        - League position (0-10)
        - Minutes trend (-15 to +10)
        - Substitution pattern (-10 to 0)
        - Home/away split (+/-5)
        """
        # Normalize components to 0-100 scale

        # 1. Points efficiency (0-40 points)
        # Good players: 10+ points per million
        points_efficiency = min((points / price_millions) / 10 * 40, 40)

        # 2. Average points (0-25 points)
        # Players with good historical performance get some credit
        avg_efficiency = min(avg_points * 4, 25)

        # 3. Affordability bonus (0-15 points)
        # Cheaper players get bonus (more budget flexibility)
        if price_millions < 5:
            affordability = 15
        elif price_millions < 10:
            affordability = 10
        elif price_millions < 20:
            affordability = 5
        else:
            affordability = 0

        # 4. Current form (0-20 points) - this is the most important
        # Players scoring well recently
        if points > avg_points * 3:  # Hot streak
            form = 20
        elif points > avg_points * 2:
            form = 15
        elif points > avg_points:
            form = 10
        elif points >= avg_points * 0.5 and points > 0:
            # Reasonable performance
            form = 5
        elif points == 0 and avg_points > 50:
            # Strong player but not playing NOW - significant penalty
            form = -15
        elif points == 0 and avg_points > 20:
            # Decent player but not playing - moderate penalty
            form = -10
        elif points == 0 and avg_points > 0:
            # Weak player not playing - small penalty
            form = -5
        else:
            form = 0

        # 5. Market momentum (0-15 points) - NEW!
        # Rising market value = bonus, falling = penalty
        momentum = 0
        if trend_direction and trend_pct is not None:
            if trend_direction == "rising":
                # Rising trend = buy signal
                # Strong rise (>15%) = +15 points
                # Moderate rise (5-15%) = +10 points
                # Weak rise (>0%) = +5 points
                if trend_pct > 15:
                    momentum = 15
                elif trend_pct > 5:
                    momentum = 10
                else:
                    momentum = 5

            elif trend_direction == "falling":
                # Falling trend = sell signal (penalty for buying)
                # Strong fall (>15%) = -15 points
                # Moderate fall (5-15%) = -10 points
                # Weak fall (>0%) = -5 points
                if trend_pct < -15:
                    momentum = -15
                elif trend_pct < -5:
                    momentum = -10
                else:
                    momentum = -5

            # Peak position analysis - significant factor in momentum!
            # CRITICAL: NO recovery bonus for falling players - they are falling knives!
            if vs_peak_pct is not None:
                # Below peak = upside potential ONLY if trend is rising/stable
                if trend_direction == "rising":
                    # Rising from below peak = genuine recovery
                    if vs_peak_pct < -40:
                        momentum += 10  # Major upside potential
                    elif vs_peak_pct < -25:
                        momentum += 7
                    elif vs_peak_pct < -15:
                        momentum += 5
                elif trend_direction == "falling":
                    # FALLING from any position = danger, no recovery bonus
                    # Add extra penalty for falling from near peak
                    if vs_peak_pct > -10:
                        # Near peak and falling = crash incoming
                        momentum -= 8
                    elif vs_peak_pct > -20:
                        # Falling and not far from peak
                        momentum -= 5
                # stable trend: no peak bonus (wait for confirmation)

        # 6. Sample size reliability penalty (0 to -30 points) - NEW!
        # CRITICAL: Players with few games get massive penalty
        # Emre Can with 1 game and 100 points should NOT be valued the same as
        # a player with 10 games and 100 points average
        sample_penalty = 0

        if games_played is not None:
            if games_played == 0:
                # No games = no data, huge penalty
                sample_penalty = -50  # Essentially disqualify
            elif games_played == 1:
                # 1 game = VERY unreliable (like Emre Can)
                sample_penalty = -30  # Massive penalty
            elif games_played == 2:
                # 2 games = still very unreliable
                sample_penalty = -20
            elif games_played <= 4:
                # 3-4 games = unreliable
                sample_penalty = -15
            elif games_played <= 6:
                # 5-6 games = somewhat reliable
                sample_penalty = -10
            elif games_played <= 8:
                # 7-8 games = fairly reliable
                sample_penalty = -5
            # else: 9+ games = reliable, no penalty

            # Additional penalty for inconsistent performers
            if consistency_score is not None and consistency_score < 0.5:
                # Very inconsistent = additional penalty
                sample_penalty -= 5

        # 7. Team Form Factor (0-15 points) - PHASE 3
        # Players on winning teams score more points
        team_form = 0
        if team_form_wins is not None and team_form_losses is not None:
            # Calculate recent form (last 5 games)
            total_games = team_form_wins + team_form_losses
            if total_games > 0:
                win_rate = team_form_wins / total_games

                if win_rate >= 0.8:  # 4-5 wins
                    team_form = 15
                elif win_rate >= 0.6:  # 3 wins
                    team_form = 10
                elif win_rate >= 0.4:  # 2 wins
                    team_form = 5
                elif win_rate <= 0.2:  # 0-1 wins
                    team_form = -5  # Losing streak penalty
                # else: 40-60% = neutral (0 points)

        # 8. League Position Factor (0-10 points) - PHASE 3
        # Top teams and relegation fighters have extra motivation
        position_factor = 0
        if league_position is not None:
            if league_position <= 4:
                # Top 4: Champions League spots = high motivation
                position_factor = 10
            elif league_position >= 15:
                # Bottom 4: Relegation fight = high motivation
                position_factor = 8
            elif 5 <= league_position <= 7:
                # Europa League contention
                position_factor = 5
            # else: mid-table = neutral (0 points)

        # 9. Minutes Played Trend (-15 to +10 points) - PHASE 3
        # Increasing playing time = manager trust growing
        minutes_factor = 0
        if minutes_trend is not None:
            if minutes_trend == "increasing":
                # Getting more minutes = good sign
                if avg_minutes and avg_minutes >= 80:
                    minutes_factor = 10  # Now a guaranteed starter
                else:
                    minutes_factor = 7  # Playing time increasing
            elif minutes_trend == "stable":
                if avg_minutes and avg_minutes >= 85:
                    # Consistent starter
                    minutes_factor = 5
                elif avg_minutes and avg_minutes < 30:
                    # Consistently benched
                    minutes_factor = -8
                # else: regular rotation = neutral
            elif minutes_trend == "decreasing":
                # Losing playing time = bad sign
                if avg_minutes and avg_minutes < 45:
                    minutes_factor = -15  # Losing starting spot
                else:
                    minutes_factor = -10  # Playing time decreasing

        # 10. Substitution Pattern (-10 to 0 points) - PHASE 3
        # Frequent subs = less point-scoring potential
        substitution_penalty = 0
        if is_substitute_pattern:
            # Player comes off bench or gets subbed early
            substitution_penalty = -10

        # 11. Home/Away Split (+/-5 points) - PHASE 3
        # Some players perform better at home/away
        home_away = home_away_bonus if home_away_bonus is not None else 0

        # Calculate total score
        total_score = (
            points_efficiency
            + avg_efficiency
            + affordability
            + form
            + momentum
            + sample_penalty
            + team_form
            + position_factor
            + minutes_factor
            + substitution_penalty
            + home_away
        )

        # Ensure minimum of 0
        return round(max(total_score, 0.0), 2)

    def is_better_than(self, other: "PlayerValue", threshold: float = 10.0) -> bool:
        """Check if this player is significantly better than another"""
        return self.value_score > (other.value_score + threshold)

    def __str__(self) -> str:
        return (
            f"Value Score: {self.value_score}/100 | "
            f"Points: {self.points} ({self.average_points:.1f} avg) | "
            f"Efficiency: {self.points_per_million:.1f} pts/M€"
        )
