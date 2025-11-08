"""Advanced player value calculation based on points and performance"""

from dataclasses import dataclass
from typing import Optional


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

    @classmethod
    def calculate(cls, player) -> "PlayerValue":
        """Calculate value metrics for a player"""
        price = getattr(player, 'price', player.market_value)
        market_value = player.market_value
        points = player.points
        avg_points = player.average_points

        # Avoid division by zero
        price_millions = max(price / 1_000_000, 0.001)

        # Points per million euros
        points_per_million = points / price_millions
        avg_points_per_million = avg_points / price_millions

        # Combined value score (0-100)
        # Factors: current points, average points, and affordability
        value_score = cls._calculate_value_score(
            points=points,
            avg_points=avg_points,
            price_millions=price_millions,
            market_value=market_value
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
        )

    @staticmethod
    def _calculate_value_score(points: int, avg_points: float, price_millions: float, market_value: int) -> float:
        """
        Calculate a value score (0-100) based on multiple factors

        Higher score = better value
        """
        # Normalize components to 0-100 scale

        # 1. Points efficiency (0-40 points)
        # Good players: 10+ points per million
        points_efficiency = min((points / price_millions) / 10 * 40, 40)

        # 2. Average points (0-30 points)
        # Good players: 5+ average points per game
        avg_efficiency = min(avg_points * 6, 30)

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

        # 4. Current form (0-15 points)
        # Players scoring well recently
        if points > avg_points * 3:  # Hot streak
            form = 15
        elif points > avg_points * 2:
            form = 10
        elif points > avg_points:
            form = 5
        else:
            form = 0

        total_score = points_efficiency + avg_efficiency + affordability + form
        return round(total_score, 2)

    def is_better_than(self, other: "PlayerValue", threshold: float = 10.0) -> bool:
        """Check if this player is significantly better than another"""
        return self.value_score > (other.value_score + threshold)

    def __str__(self) -> str:
        return (f"Value Score: {self.value_score}/100 | "
                f"Points: {self.points} ({self.average_points:.1f} avg) | "
                f"Efficiency: {self.points_per_million:.1f} pts/M€")
