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

    @classmethod
    def calculate(cls, player, trend_data: dict | None = None) -> "PlayerValue":
        """
        Calculate value metrics for a player

        Args:
            player: Player object with points, market_value, etc.
            trend_data: Optional dict with trend analysis from market value history
                {
                    "trend": "rising" | "falling" | "stable",
                    "trend_pct": 14.5,  # 14-day trend percentage
                    "peak_value": 10000000,
                    "current_value": 8000000,
                    ...
                }
        """
        price = getattr(player, "price", player.market_value)
        market_value = player.market_value
        points = player.points
        avg_points = player.average_points

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
        # Factors: current points, average points, affordability, and market trends
        value_score = cls._calculate_value_score(
            points=points,
            avg_points=avg_points,
            price_millions=price_millions,
            market_value=market_value,
            trend_direction=trend_direction,
            trend_pct=trend_pct,
            vs_peak_pct=vs_peak_pct,
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
        )

    @staticmethod
    def _calculate_value_score(
        points: int,
        avg_points: float,
        price_millions: float,
        market_value: int,
        trend_direction: str | None = None,
        trend_pct: float | None = None,
        vs_peak_pct: float | None = None,
    ) -> float:
        """
        Calculate a value score (0-100) based on multiple factors

        Higher score = better value

        Factors:
        - Points efficiency (0-40)
        - Average points (0-25)
        - Affordability (0-15)
        - Current form (0-20)
        - Market momentum (0-15) NEW!
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
            if vs_peak_pct is not None:
                # Below peak = upside potential (recovery opportunity)
                if vs_peak_pct < -40:
                    # Very far below peak = high recovery potential
                    if trend_direction != "falling" or trend_pct > -10:
                        momentum += 10  # Major upside potential
                elif vs_peak_pct < -25:
                    # Far below peak = good recovery potential
                    if trend_direction != "falling" or trend_pct > -10:
                        momentum += 7
                elif vs_peak_pct < -15:
                    # Moderately below peak = some upside
                    if trend_direction != "falling":
                        momentum += 5
                elif vs_peak_pct > -5 and trend_direction == "falling":
                    # At/near peak but falling = danger zone!
                    momentum -= 5  # Likely to decline from peak

        total_score = points_efficiency + avg_efficiency + affordability + form + momentum
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
