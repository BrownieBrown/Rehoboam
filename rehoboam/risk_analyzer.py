"""Risk analysis for player evaluation - volatility, VaR, and risk-adjusted metrics"""

import math
import statistics
from dataclasses import dataclass


@dataclass
class RiskMetrics:
    """Risk assessment for a player"""

    player_id: str
    player_name: str

    # Volatility metrics
    price_volatility: float  # Standard deviation of daily prices (% of mean)
    performance_volatility: float  # CV of match points
    volatility_score: float  # 0-100, higher = more volatile

    # Value at Risk (VaR)
    var_7d_95pct: float  # 95% confidence max loss in 7 days (%)
    var_30d_95pct: float  # 95% confidence max loss in 30 days (%)

    # Risk-return metrics
    expected_return_30d: float  # From prediction model (%)
    sharpe_ratio: float  # return / volatility (risk-adjusted return)
    risk_category: str  # "Low Risk", "Medium Risk", "High Risk", "Very High Risk"

    # Supporting data
    price_std_dev: float  # Standard deviation in euros
    data_points: int  # Sample size
    confidence: float  # 0-1, reliability of metrics


class RiskAnalyzer:
    """Analyzes risk metrics for players"""

    def __init__(self):
        self.min_data_points = 7  # Minimum days of data for reliable analysis

    def calculate_risk_metrics(
        self,
        player,
        price_history: list[int],  # Daily prices
        performance_volatility: float,  # From PlayerValue
        expected_return_30d: float | None = None,  # From predictions
    ) -> RiskMetrics:
        """
        Calculate comprehensive risk metrics for a player

        Args:
            player: Player object
            price_history: List of daily market values (most recent first)
            performance_volatility: Coefficient of variation from match performance
            expected_return_30d: Expected 30-day return % (from prediction model)

        Returns:
            RiskMetrics object
        """
        player_name = f"{player.first_name} {player.last_name}"
        data_points = len(price_history)

        # If insufficient data, return conservative estimates
        if data_points < 3:
            return self._conservative_risk_metrics(
                player_id=player.id,
                player_name=player_name,
                current_price=player.market_value,
                performance_volatility=performance_volatility,
            )

        # Calculate price volatility
        price_volatility_pct, price_std_dev = self._calculate_price_volatility(price_history)

        # Calculate Value at Risk (VaR)
        var_7d = self._calculate_var(price_history, horizon_days=7, confidence=0.95)
        var_30d = self._calculate_var(price_history, horizon_days=30, confidence=0.95)

        # Normalize volatility to 0-100 score
        volatility_score = self._normalize_volatility_score(price_volatility_pct)

        # Calculate Sharpe ratio
        sharpe_ratio = self._calculate_sharpe_ratio(
            expected_return=expected_return_30d or 0.0, volatility=price_volatility_pct
        )

        # Assess risk category
        risk_category = self._assess_risk_category(volatility_score, var_30d)

        # Calculate confidence based on data quality
        confidence = self._calculate_confidence(data_points, price_volatility_pct)

        return RiskMetrics(
            player_id=player.id,
            player_name=player_name,
            price_volatility=price_volatility_pct,
            performance_volatility=performance_volatility or 0.0,
            volatility_score=volatility_score,
            var_7d_95pct=var_7d,
            var_30d_95pct=var_30d,
            expected_return_30d=expected_return_30d or 0.0,
            sharpe_ratio=sharpe_ratio,
            risk_category=risk_category,
            price_std_dev=price_std_dev,
            data_points=data_points,
            confidence=confidence,
        )

    def _calculate_price_volatility(self, price_history: list[int]) -> tuple[float, float]:
        """
        Calculate price volatility as percentage of mean

        Args:
            price_history: List of daily prices

        Returns:
            (volatility_pct, std_dev) tuple
        """
        if len(price_history) < 2:
            return 0.0, 0.0

        mean_price = statistics.mean(price_history)
        if mean_price == 0:
            return 0.0, 0.0

        std_dev = statistics.stdev(price_history)
        volatility_pct = (std_dev / mean_price) * 100

        return volatility_pct, std_dev

    def _calculate_var(
        self, price_history: list[int], horizon_days: int = 7, confidence: float = 0.95
    ) -> float:
        """
        Calculate Value at Risk using historical simulation

        Args:
            price_history: List of daily prices (most recent first)
            horizon_days: Forecast horizon in days
            confidence: Confidence level (0.95 = 95%)

        Returns:
            VaR as percentage (negative value = expected max loss)
        """
        if len(price_history) < 2:
            return 0.0

        # Calculate daily returns
        returns = []
        for i in range(len(price_history) - 1):
            if price_history[i + 1] > 0:
                daily_return = (price_history[i] - price_history[i + 1]) / price_history[i + 1]
                returns.append(daily_return)

        if not returns:
            return 0.0

        # Sort returns (worst to best)
        returns_sorted = sorted(returns)

        # Find percentile for VaR (5th percentile for 95% confidence)
        percentile_index = int((1 - confidence) * len(returns_sorted))
        percentile_return = (
            returns_sorted[percentile_index]
            if percentile_index < len(returns_sorted)
            else returns_sorted[0]
        )

        # Scale to horizon using square root of time rule
        # VaR(T) ≈ VaR(1) * sqrt(T)
        horizon_var = percentile_return * math.sqrt(horizon_days)

        # Convert to percentage
        var_pct = horizon_var * 100

        return var_pct

    def _normalize_volatility_score(self, volatility_pct: float) -> float:
        """
        Normalize volatility percentage to 0-100 score

        Args:
            volatility_pct: Volatility as percentage of mean

        Returns:
            Score from 0 (very stable) to 100 (extremely volatile)
        """
        # Typical player volatility ranges from 5% (stable) to 50% (very volatile)
        # Map this to 0-100 scale
        if volatility_pct <= 5:
            return 0.0
        elif volatility_pct >= 50:
            return 100.0
        else:
            # Linear mapping: 5% → 0, 50% → 100
            return ((volatility_pct - 5) / 45) * 100

    def _calculate_sharpe_ratio(
        self, expected_return: float, volatility: float, risk_free_rate: float = 0.0
    ) -> float:
        """
        Calculate Sharpe ratio (risk-adjusted return)

        Args:
            expected_return: Expected return percentage
            volatility: Volatility percentage
            risk_free_rate: Risk-free rate (default 0 for simplicity)

        Returns:
            Sharpe ratio (higher is better)
        """
        if volatility == 0:
            # Perfect stability with positive return = infinite Sharpe
            return 10.0 if expected_return > 0 else 0.0

        sharpe = (expected_return - risk_free_rate) / volatility
        return round(sharpe, 2)

    def _assess_risk_category(self, volatility_score: float, var_30d: float) -> str:
        """
        Categorize risk level based on volatility and VaR

        Args:
            volatility_score: Normalized volatility (0-100)
            var_30d: 30-day VaR percentage

        Returns:
            Risk category string
        """
        # Low Risk: stable price and limited downside
        if volatility_score < 15 and var_30d > -10:
            return "Low Risk"

        # High Risk: very volatile or large potential loss
        elif volatility_score > 40 or var_30d < -35:
            return "Very High Risk"

        # High Risk: volatile with significant downside
        elif volatility_score > 25 or var_30d < -20:
            return "High Risk"

        # Medium Risk: everything else
        else:
            return "Medium Risk"

    def _calculate_confidence(self, data_points: int, volatility: float) -> float:
        """
        Calculate confidence in risk metrics based on data quality

        Args:
            data_points: Number of price observations
            volatility: Price volatility percentage

        Returns:
            Confidence score 0-1
        """
        # Base confidence from sample size
        if data_points >= 30:
            size_confidence = 1.0
        elif data_points >= 20:
            size_confidence = 0.9
        elif data_points >= 14:
            size_confidence = 0.8
        elif data_points >= 7:
            size_confidence = 0.6
        else:
            size_confidence = 0.3

        # Reduce confidence for extreme volatility (unstable estimates)
        if volatility > 60:
            volatility_penalty = 0.7
        elif volatility > 40:
            volatility_penalty = 0.85
        else:
            volatility_penalty = 1.0

        return round(size_confidence * volatility_penalty, 2)

    def _conservative_risk_metrics(
        self, player_id: str, player_name: str, current_price: int, performance_volatility: float
    ) -> RiskMetrics:
        """
        Return conservative risk estimates when data is insufficient

        Args:
            player_id: Player ID
            player_name: Player name
            current_price: Current market value
            performance_volatility: Performance CV

        Returns:
            Conservative RiskMetrics
        """
        # Assume high risk due to lack of data
        return RiskMetrics(
            player_id=player_id,
            player_name=player_name,
            price_volatility=25.0,  # Assume moderate-high volatility
            performance_volatility=performance_volatility or 0.5,
            volatility_score=55.0,  # Medium-high risk
            var_7d_95pct=-15.0,  # Assume 15% potential weekly loss
            var_30d_95pct=-25.0,  # Assume 25% potential monthly loss
            expected_return_30d=0.0,
            sharpe_ratio=0.0,
            risk_category="High Risk",
            price_std_dev=current_price * 0.25,
            data_points=0,
            confidence=0.3,  # Low confidence due to lack of data
        )

    def extract_price_history_from_api_data(self, history_data: dict) -> list[int]:
        """
        Extract daily price history from API response

        Args:
            history_data: Response from get_player_market_value_history_v2

        Returns:
            List of daily prices (most recent first)
        """
        prices = []

        if not history_data:
            return prices

        # Extract from "it" array (historical data points)
        it_array = history_data.get("it", [])

        for item in it_array:
            market_value = item.get("mv")
            if market_value:
                prices.append(market_value)

        # Reverse to get most recent first
        prices.reverse()

        return prices

    def get_risk_color(self, risk_category: str) -> str:
        """
        Get Rich color for risk category

        Args:
            risk_category: Risk category string

        Returns:
            Rich color name
        """
        color_map = {
            "Low Risk": "green",
            "Medium Risk": "yellow",
            "High Risk": "red",
            "Very High Risk": "red bold",
        }
        return color_map.get(risk_category, "white")

    def get_sharpe_color(self, sharpe_ratio: float) -> str:
        """
        Get Rich color for Sharpe ratio

        Args:
            sharpe_ratio: Sharpe ratio value

        Returns:
            Rich color name
        """
        if sharpe_ratio >= 1.0:
            return "green"
        elif sharpe_ratio >= 0.5:
            return "yellow"
        else:
            return "red"

    def get_var_color(self, var_pct: float) -> str:
        """
        Get Rich color for VaR

        Args:
            var_pct: VaR percentage (negative = loss)

        Returns:
            Rich color name
        """
        if var_pct > -10:
            return "green"
        elif var_pct > -20:
            return "yellow"
        else:
            return "red"
