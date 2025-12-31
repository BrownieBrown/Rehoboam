"""Market analysis and player evaluation"""

from dataclasses import dataclass, field

from .kickbase_client import MarketPlayer
from .value_calculator import PlayerValue


@dataclass
class ScoringFactor:
    """Individual scoring factor contribution"""

    name: str
    raw_value: float  # Original value before weight
    weight: float  # Weight multiplier
    score: float  # Weighted contribution to final score
    description: str  # Human-readable explanation


@dataclass
class FactorWeights:
    """Configuration for factor weights in scoring"""

    # Market buying weights
    base_value: float = 1.0  # Base value score (0-100)
    trend_rising: float = 15.0  # Bonus for rising trends
    trend_falling: float = -20.0  # Penalty for falling trends
    matchup_easy: float = 10.0  # Bonus for easy matchups
    matchup_hard: float = -5.0  # Penalty for hard matchups
    sos_bonus: float = 1.0  # Direct bonus from SOS calculation
    discount: float = 15.0  # Bonus for undervalued players

    # Owned player weights
    profit_target: float = 50.0  # Strong sell signal on profit target
    stop_loss: float = 60.0  # Strongest sell signal
    peak_decline: float = 40.0  # Strong sell on peak decline
    poor_performance: float = 30.0  # Moderate sell on low value
    best_eleven_protection: float = -25.0  # Keep players in best 11
    difficult_schedule: float = 20.0  # Sell before tough fixtures


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
    trend: str | None = None  # "rising", "falling", "stable", "unknown"
    trend_change_pct: float | None = None  # % change over trend period
    metadata: dict | None = None  # Additional data (peak analysis, stats, etc.)
    factors: list[ScoringFactor] = field(default_factory=list)  # Scoring breakdown
    risk_metrics: "RiskMetrics | None" = None  # Risk analysis (volatility, VaR, Sharpe)


class MarketAnalyzer:
    """Analyzes market conditions and player values"""

    def __init__(
        self,
        min_buy_value_increase_pct: float,
        min_sell_profit_pct: float,
        max_loss_pct: float,
        min_value_score_to_buy: float = 40.0,
        factor_weights: FactorWeights | None = None,
    ):
        self.min_buy_value_increase_pct = min_buy_value_increase_pct
        self.min_sell_profit_pct = min_sell_profit_pct
        self.max_loss_pct = max_loss_pct
        self.min_value_score_to_buy = min_value_score_to_buy
        self.weights = factor_weights or FactorWeights()

        # Decision thresholds (can be tuned)
        self.buy_threshold = min_value_score_to_buy
        self.hold_threshold = max(min_value_score_to_buy - 20, 20)
        self.sell_threshold = 40  # For owned players

    def analyze_market_player(
        self,
        player: MarketPlayer,
        trend_data: dict | None = None,
        matchup_context: dict | None = None,
    ) -> PlayerAnalysis:
        """Analyze a player on the market for buying opportunity using factor-based scoring"""
        market_value = player.market_value
        current_price = player.price

        # Calculate value change percentage
        if market_value > 0:
            value_change_pct = ((market_value - current_price) / current_price) * 100
        else:
            value_change_pct = 0.0

        # Calculate base player value
        player_value = PlayerValue.calculate(player, trend_data=trend_data)
        base_value_score = player_value.value_score

        # FACTOR-BASED SCORING: Calculate each factor independently
        factors = []
        final_score = 0.0

        # Factor 1: Base value score (most important)
        base_factor = ScoringFactor(
            name="Base Value",
            raw_value=base_value_score,
            weight=self.weights.base_value,
            score=base_value_score * self.weights.base_value,
            description=f"Core value: {base_value_score:.1f}/100 ({player_value.points_per_million:.1f} pts/M€)",
        )
        factors.append(base_factor)
        final_score += base_factor.score

        # Factor 2: Trend analysis
        trend = None
        trend_change_pct = None
        if trend_data and trend_data.get("has_data") and trend_data.get("reference_value", 0) > 0:
            trend = trend_data.get("trend", "unknown")
            trend_change_pct = trend_data.get("change_pct", 0.0)

            if trend == "rising" and trend_change_pct > 15:
                # Strong upward trend
                trend_factor = ScoringFactor(
                    name="Rising Trend",
                    raw_value=trend_change_pct,
                    weight=self.weights.trend_rising,
                    score=self.weights.trend_rising,
                    description=f"Strong upward momentum: +{trend_change_pct:.1f}%",
                )
                factors.append(trend_factor)
                final_score += trend_factor.score

            elif trend == "falling" and trend_change_pct < -10:
                # Strong downward trend - penalty
                trend_factor = ScoringFactor(
                    name="Falling Trend",
                    raw_value=trend_change_pct,
                    weight=self.weights.trend_falling,
                    score=self.weights.trend_falling,
                    description=f"Declining value: {trend_change_pct:.1f}%",
                )
                factors.append(trend_factor)
                final_score += trend_factor.score

        # Factor 3: Matchup bonus
        matchup_bonus_points = 0
        if matchup_context and matchup_context.get("has_data"):
            matchup_bonus_data = matchup_context.get("matchup_bonus", {})
            matchup_bonus_points = matchup_bonus_data.get("bonus_points", 0)
            matchup_reason = matchup_bonus_data.get("reason", "")

            if matchup_bonus_points > 0:
                matchup_factor = ScoringFactor(
                    name="Easy Matchup",
                    raw_value=matchup_bonus_points,
                    weight=self.weights.matchup_easy,
                    score=matchup_bonus_points * self.weights.matchup_easy / 10,  # Scale down
                    description=matchup_reason,
                )
                factors.append(matchup_factor)
                final_score += matchup_factor.score
            elif matchup_bonus_points < 0:
                matchup_factor = ScoringFactor(
                    name="Hard Matchup",
                    raw_value=matchup_bonus_points,
                    weight=self.weights.matchup_hard,
                    score=matchup_bonus_points * self.weights.matchup_hard / 10,  # Scale down
                    description=matchup_reason,
                )
                factors.append(matchup_factor)
                final_score += matchup_factor.score

        # Factor 4: Strength of Schedule (SOS)
        sos_bonus_points = 0
        sos_rating = None
        if matchup_context and matchup_context.get("has_data"):
            sos_data = matchup_context.get("sos")
            if sos_data:
                sos_bonus_points = sos_data.sos_bonus
                sos_rating = sos_data.short_term_rating

                if sos_bonus_points != 0:
                    sos_factor = ScoringFactor(
                        name="Schedule Strength",
                        raw_value=sos_bonus_points,
                        weight=self.weights.sos_bonus,
                        score=sos_bonus_points * self.weights.sos_bonus,
                        description=f"{sos_rating} next 3 games",
                    )
                    factors.append(sos_factor)
                    final_score += sos_factor.score

        # Factor 5: Market discount
        if value_change_pct >= self.min_buy_value_increase_pct:
            discount_factor = ScoringFactor(
                name="Market Discount",
                raw_value=value_change_pct,
                weight=self.weights.discount,
                score=self.weights.discount,
                description=f"Undervalued by {value_change_pct:.1f}%",
            )
            factors.append(discount_factor)
            final_score += discount_factor.score

        # Clamp final score to 0-150 range (can exceed 100 with bonuses)
        final_score = max(0, min(150, final_score))

        # DECISION LOGIC: Simple threshold-based
        if final_score >= self.buy_threshold:
            recommendation = "BUY"
            confidence = min(final_score / 100.0, 1.0)
        elif final_score >= self.hold_threshold:
            recommendation = "HOLD"
            confidence = 0.6
        else:
            recommendation = "SKIP"
            confidence = 0.3

        # Build transparent reason from factors
        reason_parts = [f"Score: {final_score:.1f}"]
        for factor in factors:
            if abs(factor.score) > 2:  # Only show significant factors
                sign = "+" if factor.score >= 0 else ""
                reason_parts.append(f"{factor.name}: {sign}{factor.score:.0f}")

        # Add context details
        if sos_rating:
            sos_indicator = self._get_sos_indicator(sos_rating)
            reason_parts.append(f"{sos_indicator} {sos_rating}")

        reason = " | ".join(reason_parts)

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
            value_score=final_score,  # Use final_score instead of base
            points_per_million=player_value.points_per_million,
            avg_points_per_million=player_value.avg_points_per_million,
            trend=trend,
            trend_change_pct=trend_change_pct,
            factors=factors,
        )

    def _get_sos_indicator(self, rating: str) -> str:
        """Get visual indicator for strength of schedule"""
        if rating == "Very Easy":
            return "⚡⚡⚡"
        elif rating == "Easy":
            return "⚡⚡"
        elif rating == "Very Difficult":
            return "🔥🔥🔥"
        elif rating == "Difficult":
            return "🔥🔥"
        else:
            return "→"

    def analyze_owned_player(
        self,
        player,
        purchase_price: int | None = None,
        trend_data: dict | None = None,
        matchup_context: dict | None = None,
        peak_analysis: dict | None = None,
        is_in_best_eleven: bool = False,
    ) -> PlayerAnalysis:
        """Analyze a player you own for selling opportunity using factor-based scoring"""
        current_value = player.market_value
        if purchase_price is None:
            purchase_price = current_value

        # Calculate profit/loss percentage
        if purchase_price > 0:
            profit_pct = ((current_value - purchase_price) / purchase_price) * 100
        else:
            profit_pct = 0.0

        # Calculate base value
        player_value = PlayerValue.calculate(player)
        base_value_score = player_value.value_score

        # FACTOR-BASED SCORING for SELL decisions
        # Higher score = stronger sell signal
        factors = []
        sell_score = 0.0

        # Factor 1: Profit target reached (strong sell signal)
        if profit_pct >= self.min_sell_profit_pct:
            profit_factor = ScoringFactor(
                name="Profit Target",
                raw_value=profit_pct,
                weight=self.weights.profit_target,
                score=self.weights.profit_target,
                description=f"Hit profit target: {profit_pct:.1f}% gain",
            )
            factors.append(profit_factor)
            sell_score += profit_factor.score

        # Factor 2: Stop-loss triggered (strongest sell signal)
        if profit_pct <= self.max_loss_pct:
            loss_factor = ScoringFactor(
                name="Stop Loss",
                raw_value=profit_pct,
                weight=self.weights.stop_loss,
                score=self.weights.stop_loss,
                description=f"Cut losses: {profit_pct:.1f}% down",
            )
            factors.append(loss_factor)
            sell_score += loss_factor.score

        # Factor 3: Peaked and declining
        if peak_analysis and peak_analysis.get("is_declining"):
            decline_pct = peak_analysis.get("decline_from_peak_pct", 0)
            days_since = peak_analysis.get("days_since_peak", 0)

            peak_factor = ScoringFactor(
                name="Peak Decline",
                raw_value=decline_pct,
                weight=self.weights.peak_decline,
                score=self.weights.peak_decline,
                description=f"Value peaked, down {decline_pct:.1f}% over {days_since}d",
            )
            factors.append(peak_factor)
            sell_score += peak_factor.score

        # Factor 4: Poor performance (low value score)
        if base_value_score < 30:
            poor_perf_factor = ScoringFactor(
                name="Poor Performance",
                raw_value=base_value_score,
                weight=self.weights.poor_performance,
                score=self.weights.poor_performance,
                description=f"Underperforming: {base_value_score:.1f}/100",
            )
            factors.append(poor_perf_factor)
            sell_score += poor_perf_factor.score

        # Factor 5: Difficult schedule ahead
        sos_rating = None
        if matchup_context and matchup_context.get("has_data"):
            sos_data = matchup_context.get("sos")
            if sos_data:
                sos_rating = sos_data.short_term_rating
                if sos_rating in ["Difficult", "Very Difficult"] and profit_pct > 5:
                    # Only sell on difficult schedule if profitable
                    schedule_factor = ScoringFactor(
                        name="Difficult Fixtures",
                        raw_value=sos_data.sos_bonus,
                        weight=self.weights.difficult_schedule,
                        score=self.weights.difficult_schedule,
                        description=f"Sell before {sos_rating} games",
                    )
                    factors.append(schedule_factor)
                    sell_score += schedule_factor.score

        # Factor 6: Falling trend (if at profit)
        trend = None
        trend_change_pct = None
        if trend_data and trend_data.get("has_data"):
            trend = trend_data.get("trend", "unknown")
            trend_change_pct = trend_data.get("change_pct", 0)

            if trend in ["falling", "strongly falling"] and profit_pct > 5:
                trend_factor = ScoringFactor(
                    name="Falling Trend",
                    raw_value=trend_change_pct,
                    weight=15.0,  # Moderate signal
                    score=15.0,
                    description=f"Declining {trend_change_pct:.1f}% - lock in profit",
                )
                factors.append(trend_factor)
                sell_score += trend_factor.score

        # Factor 7: Best 11 protection (negative signal - keep them!)
        if is_in_best_eleven:
            # Only apply protection if sell signals aren't too strong
            if sell_score < 80:  # Not stop-loss or severe peak decline
                protection_factor = ScoringFactor(
                    name="Best 11",
                    raw_value=1.0,
                    weight=self.weights.best_eleven_protection,
                    score=self.weights.best_eleven_protection,
                    description="Needed in lineup",
                )
                factors.append(protection_factor)
                sell_score += protection_factor.score

        # Clamp sell score
        sell_score = max(0, sell_score)

        # DECISION LOGIC: Threshold-based
        if sell_score >= self.sell_threshold:
            recommendation = "SELL"
            confidence = min(sell_score / 100.0, 1.0)
        else:
            recommendation = "HOLD"
            # Higher confidence for HOLD when value is good
            if base_value_score >= 60:
                confidence = 0.8
            elif base_value_score >= 40:
                confidence = 0.6
            else:
                confidence = 0.4

        # Build transparent reason
        reason_parts = [f"Sell Score: {sell_score:.0f}"]

        # Add significant factors
        for factor in factors:
            if abs(factor.score) > 5:
                sign = "+" if factor.score >= 0 else ""
                reason_parts.append(f"{factor.name}: {sign}{factor.score:.0f}")

        # Add P/L if not already mentioned
        if not any(f.name in ["Profit Target", "Stop Loss"] for f in factors):
            reason_parts.append(f"P/L: {profit_pct:+.1f}%")

        # Add value score context
        reason_parts.append(f"Value: {base_value_score:.0f}/100")

        # Add SOS indicator
        if sos_rating:
            sos_indicator = self._get_sos_indicator(sos_rating)
            reason_parts.append(f"{sos_indicator} {sos_rating}")

        reason = " | ".join(reason_parts)

        # Build metadata
        metadata = {}
        if peak_analysis:
            metadata.update(peak_analysis)

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
            value_score=base_value_score,  # Keep base value for reference
            points_per_million=player_value.points_per_million,
            avg_points_per_million=player_value.avg_points_per_million,
            trend=trend,
            trend_change_pct=trend_change_pct,
            metadata=metadata if metadata else None,
            factors=factors,
        )

    def find_best_opportunities(
        self, analyses: list[PlayerAnalysis], top_n: int = 10
    ) -> list[PlayerAnalysis]:
        """Find the best buying opportunities from a list of analyses"""
        buy_opportunities = [a for a in analyses if a.recommendation == "BUY"]
        # Sort by confidence * value_score for best opportunities
        buy_opportunities.sort(key=lambda a: a.confidence * a.value_score, reverse=True)
        return buy_opportunities[:top_n]
