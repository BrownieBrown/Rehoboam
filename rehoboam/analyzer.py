"""Market analysis and player evaluation"""

from typing import Optional
from dataclasses import dataclass
from .value_calculator import PlayerValue


@dataclass
class PlayerAnalysis:
    """Analysis result for a player"""

    player: MarketPlayer
    current_price: int
    market_value: int
    value_change_pct: float
    points: int
    average_points: float
    recommendation: str  # "BUY", "SELL", "HOLD", "SKIP"
    confidence: float  # 0.0 to 1.0
    reason: str
    value_score: float = 0.0  # 0-100 combined value metric
    points_per_million: float = 0.0
    avg_points_per_million: float = 0.0


class MarketAnalyzer:
    """Analyzes market conditions and player values"""

    def __init__(
        self,
        min_buy_value_increase_pct: float,
        min_sell_profit_pct: float,
        max_loss_pct: float,
    ):
        self.min_buy_value_increase_pct = min_buy_value_increase_pct
        self.min_sell_profit_pct = min_sell_profit_pct
        self.max_loss_pct = max_loss_pct

    def analyze_market_player(self, player: MarketPlayer) -> PlayerAnalysis:
        """Analyze a player on the market for buying opportunity"""
        market_value = player.market_value
        current_price = player.price

        # Calculate value change percentage (for reference)
        if market_value > 0:
            value_change_pct = ((market_value - current_price) / current_price) * 100
        else:
            value_change_pct = 0.0

        # Calculate advanced value metrics
        player_value = PlayerValue.calculate(player)

        # Recommendation logic based on value score
        recommendation = "SKIP"
        confidence = 0.0
        reason = ""

        # Good value: score >= 60
        # Decent value: score >= 40
        # Poor value: score < 40
        if player_value.value_score >= 60:
            recommendation = "BUY"
            confidence = min(player_value.value_score / 100.0, 1.0)
            reason = f"Excellent value score: {player_value.value_score:.1f}/100 ({player_value.points_per_million:.1f} pts/M€)"
        elif player_value.value_score >= 40:
            recommendation = "HOLD"
            confidence = 0.6
            reason = f"Decent value: {player_value.value_score:.1f}/100, but below buy threshold (60)"
        else:
            recommendation = "SKIP"
            confidence = 0.8
            reason = f"Poor value score: {player_value.value_score:.1f}/100"

        # Override: if there's a significant market value discount AND good score, boost recommendation
        if value_change_pct >= self.min_buy_value_increase_pct and player_value.value_score >= 50:
            recommendation = "BUY"
            confidence = 0.95
            reason = f"Undervalued by {value_change_pct:.1f}% with strong value score: {player_value.value_score:.1f}/100"

        return PlayerAnalysis(
            player=player,
            current_price=current_price,
            market_value=market_value,
            value_change_pct=value_change_pct,
            points=player.points,
            average_points=player.average_points,
            recommendation=recommendation,
            confidence=confidence,
            reason=reason,
            value_score=player_value.value_score,
            points_per_million=player_value.points_per_million,
            avg_points_per_million=player_value.avg_points_per_million,
        )

    def analyze_owned_player(
        self, player, purchase_price: Optional[int] = None
    ) -> PlayerAnalysis:
        """Analyze a player you own for selling opportunity"""
        current_value = player.market_value
        if purchase_price is None:
            purchase_price = current_value

        # Calculate profit/loss percentage
        if purchase_price > 0:
            profit_pct = ((current_value - purchase_price) / purchase_price) * 100
        else:
            profit_pct = 0.0

        # Calculate value metrics for comparison
        player_value = PlayerValue.calculate(player)

        # Recommendation logic
        recommendation = "HOLD"
        confidence = 0.5
        reason = ""

        if profit_pct >= self.min_sell_profit_pct:
            recommendation = "SELL"
            confidence = min(profit_pct / 10.0, 1.0)
            reason = f"Profit target reached: {profit_pct:.1f}% gain (value: {player_value.value_score:.1f}/100)"
        elif profit_pct <= self.max_loss_pct:
            recommendation = "SELL"
            confidence = 0.9
            reason = f"Stop-loss triggered: {profit_pct:.1f}% loss"
        elif player_value.value_score < 30:
            # Player performing poorly, consider selling even without profit
            recommendation = "SELL"
            confidence = 0.7
            reason = f"Underperforming: {player_value.value_score:.1f}/100 value score"
        else:
            recommendation = "HOLD"
            confidence = 0.6
            reason = f"Current profit/loss: {profit_pct:.1f}% (value: {player_value.value_score:.1f}/100)"

        return PlayerAnalysis(
            player=player,
            current_price=purchase_price,
            market_value=current_value,
            value_change_pct=profit_pct,
            points=player.points,
            average_points=player.average_points,
            recommendation=recommendation,
            confidence=confidence,
            reason=reason,
            value_score=player_value.value_score,
            points_per_million=player_value.points_per_million,
            avg_points_per_million=player_value.avg_points_per_million,
        )

    def find_best_opportunities(
        self, analyses: list[PlayerAnalysis], top_n: int = 10
    ) -> list[PlayerAnalysis]:
        """Find the best buying opportunities from a list of analyses"""
        buy_opportunities = [
            a for a in analyses if a.recommendation == "BUY"
        ]
        # Sort by confidence * value_score for best opportunities
        buy_opportunities.sort(
            key=lambda a: a.confidence * a.value_score, reverse=True
        )
        return buy_opportunities[:top_n]
