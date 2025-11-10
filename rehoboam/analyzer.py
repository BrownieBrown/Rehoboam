"""Market analysis and player evaluation"""

from dataclasses import dataclass

from .kickbase_client import MarketPlayer
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
    trend: str | None = None  # "rising", "falling", "stable", "unknown"
    trend_change_pct: float | None = None  # % change over trend period
    metadata: dict | None = None  # Additional data (peak analysis, stats, etc.)


class MarketAnalyzer:
    """Analyzes market conditions and player values"""

    def __init__(
        self,
        min_buy_value_increase_pct: float,
        min_sell_profit_pct: float,
        max_loss_pct: float,
        min_value_score_to_buy: float = 40.0,
    ):
        self.min_buy_value_increase_pct = min_buy_value_increase_pct
        self.min_sell_profit_pct = min_sell_profit_pct
        self.max_loss_pct = max_loss_pct
        self.min_value_score_to_buy = min_value_score_to_buy

    def analyze_market_player(
        self,
        player: MarketPlayer,
        trend_data: dict | None = None,
        matchup_context: dict | None = None,
    ) -> PlayerAnalysis:
        """Analyze a player on the market for buying opportunity"""
        market_value = player.market_value
        current_price = player.price

        # Calculate value change percentage (for reference)
        if market_value > 0:
            value_change_pct = ((market_value - current_price) / current_price) * 100
        else:
            value_change_pct = 0.0

        # Calculate advanced value metrics WITH trend data (includes peak analysis!)
        player_value = PlayerValue.calculate(player, trend_data=trend_data)

        # Apply matchup and SOS bonuses if available
        base_value_score = player_value.value_score
        total_bonus = 0

        if matchup_context and matchup_context.get("has_data"):
            # Single game matchup bonus
            matchup_bonus_data = matchup_context.get("matchup_bonus", {})
            matchup_bonus_points = matchup_bonus_data.get("bonus_points", 0)
            total_bonus += matchup_bonus_points

            # Strength of schedule bonus (next 3+ games)
            sos_data = matchup_context.get("sos")
            if sos_data:
                sos_bonus_points = sos_data.sos_bonus
                total_bonus += sos_bonus_points

            # Apply total bonus
            player_value.value_score = max(0, min(100, base_value_score + total_bonus))

        # Recommendation logic based on value score
        recommendation = "SKIP"
        confidence = 0.0
        reason = ""

        # Dynamic threshold based on configuration
        buy_threshold = self.min_value_score_to_buy
        decent_threshold = max(buy_threshold - 20, 20)  # 20 points below buy threshold

        if player_value.value_score >= buy_threshold:
            recommendation = "BUY"
            confidence = min(player_value.value_score / 100.0, 1.0)
            reason = f"Good value score: {player_value.value_score:.1f}/100 ({player_value.points_per_million:.1f} pts/M€)"
        elif player_value.value_score >= decent_threshold:
            recommendation = "HOLD"
            confidence = 0.6
            reason = f"Decent value: {player_value.value_score:.1f}/100, below buy threshold ({buy_threshold})"
        else:
            recommendation = "SKIP"
            confidence = 0.8
            reason = f"Poor value score: {player_value.value_score:.1f}/100"

        # Override: if there's a significant market value discount AND decent score, boost recommendation
        min_score_for_discount = max(buy_threshold - 10, 30)
        if (
            value_change_pct >= self.min_buy_value_increase_pct
            and player_value.value_score >= min_score_for_discount
        ):
            recommendation = "BUY"
            confidence = 0.95
            reason = f"Undervalued by {value_change_pct:.1f}% with value score: {player_value.value_score:.1f}/100"

        # Adjust recommendation based on trends (only if we have actual trend data)
        trend = None
        trend_change_pct = None
        if trend_data and trend_data.get("has_data") and trend_data.get("reference_value", 0) > 0:
            trend = trend_data.get("trend", "unknown")
            trend_change_pct = trend_data.get("change_pct", 0.0)

            # NOTE: Trend data is often unavailable for market players
            # Only filter trends if we have reliable data

            # CRITICAL: Filter out falling trends (only with strong signal)
            if trend == "falling" and trend_change_pct < -10:
                # Strong downward trend - downgrade
                if recommendation == "BUY":
                    recommendation = "HOLD"
                    confidence = 0.3
                    reason = f"Falling trend ({trend_change_pct:.1f}%) - risky despite value score: {player_value.value_score:.1f}/100"
                else:
                    reason += f" | Falling trend ({trend_change_pct:.1f}%)"

            # Boost for rising trends
            elif trend == "rising" and trend_change_pct > 15:
                # Strong upward trend - boost if already decent value
                if player_value.value_score >= decent_threshold and recommendation != "BUY":
                    recommendation = "BUY"
                    confidence = 0.85
                    reason = f"Rising trend (+{trend_change_pct:.1f}% over period) with value score: {player_value.value_score:.1f}/100"
                elif recommendation == "BUY":
                    # Already buying, increase confidence
                    confidence = min(confidence + 0.1, 1.0)
                    reason += f" | Rising trend (+{trend_change_pct:.1f}%)"

        # Add matchup and SOS context to reason if available
        if matchup_context and matchup_context.get("has_data"):
            matchup_bonus_data = matchup_context.get("matchup_bonus", {})
            matchup_reason = matchup_bonus_data.get("reason", "")
            matchup_bonus_points = matchup_bonus_data.get("bonus_points", 0)

            if matchup_reason:
                if matchup_bonus_points != 0:
                    sign = "+" if matchup_bonus_points > 0 else ""
                    reason += f" | {matchup_reason} ({sign}{matchup_bonus_points} pts)"
                else:
                    reason += f" | {matchup_reason}"

            # Add SOS info
            sos_data = matchup_context.get("sos")
            if sos_data:
                sos_bonus_points = sos_data.sos_bonus
                # Create visual indicator for difficulty
                if sos_data.short_term_rating == "Very Easy":
                    sos_indicator = "⚡⚡⚡"
                elif sos_data.short_term_rating == "Easy":
                    sos_indicator = "⚡⚡"
                elif sos_data.short_term_rating == "Very Difficult":
                    sos_indicator = "🔥🔥🔥"
                elif sos_data.short_term_rating == "Difficult":
                    sos_indicator = "🔥🔥"
                else:
                    sos_indicator = "→"

                sign = "+" if sos_bonus_points > 0 else ""
                reason += f" | {sos_indicator} SOS: {sos_data.short_term_rating} next 3 ({sign}{sos_bonus_points} pts)"

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
            trend=trend,
            trend_change_pct=trend_change_pct,
        )

    def analyze_owned_player(
        self,
        player,
        purchase_price: int | None = None,
        trend_data: dict | None = None,
        matchup_context: dict | None = None,
        peak_analysis: dict | None = None,
    ) -> PlayerAnalysis:
        """
        Analyze a player you own for selling opportunity
        Uses same criteria as buying: value score, trends, SOS, matchups, peak detection
        """
        current_value = player.market_value
        if purchase_price is None:
            purchase_price = current_value

        # Calculate profit/loss percentage
        if purchase_price > 0:
            profit_pct = ((current_value - purchase_price) / purchase_price) * 100
        else:
            profit_pct = 0.0

        # Calculate value metrics
        player_value = PlayerValue.calculate(player)

        # Start with base value score
        base_value_score = player_value.value_score

        # Apply matchup/SOS context (same as buying)
        total_adjustment = 0
        if matchup_context and matchup_context.get("has_data"):
            matchup_bonus_data = matchup_context.get("matchup_bonus", {})
            total_adjustment += matchup_bonus_data.get("bonus_points", 0)

            sos_data = matchup_context.get("sos")
            if sos_data:
                total_adjustment += sos_data.sos_bonus

            player_value.value_score = max(0, min(100, base_value_score + total_adjustment))

        # Recommendation logic - COMPREHENSIVE
        recommendation = "HOLD"
        confidence = 0.5
        reasons = []

        # SELL SIGNAL 1: Peaked and declining
        if peak_analysis and peak_analysis.get("is_declining"):
            decline_pct = peak_analysis.get("decline_from_peak_pct", 0)
            days_since = peak_analysis.get("days_since_peak", 0)
            recommendation = "SELL"
            confidence = 0.9
            reasons.append(f"Peaked and declining {decline_pct:.1f}% over {days_since}d")

        # SELL SIGNAL 2: Profit target reached
        elif profit_pct >= self.min_sell_profit_pct:
            recommendation = "SELL"
            confidence = min(profit_pct / 10.0, 1.0)
            reasons.append(f"Profit target: {profit_pct:.1f}% gain")

        # SELL SIGNAL 3: Stop-loss triggered
        elif profit_pct <= self.max_loss_pct:
            recommendation = "SELL"
            confidence = 0.9
            reasons.append(f"Stop-loss: {profit_pct:.1f}% loss")

        # SELL SIGNAL 4: Underperforming (low value score)
        elif player_value.value_score < 30:
            recommendation = "SELL"
            confidence = 0.7
            reasons.append(f"Underperforming: {player_value.value_score:.1f}/100")

        # SELL SIGNAL 5: Difficult schedule ahead (CRITICAL!)
        elif matchup_context and matchup_context.get("has_data"):
            sos_data = matchup_context.get("sos")
            if sos_data and sos_data.short_term_rating in ["Difficult", "Very Difficult"]:
                # Difficult schedule + currently profitable = SELL NOW
                if profit_pct > 5:
                    recommendation = "SELL"
                    confidence = 0.85
                    reasons.append(f"Sell before difficult fixtures ({sos_data.short_term_rating})")
                    reasons.append(f"Current profit: {profit_pct:.1f}%")
                else:
                    # Difficult schedule but not profitable yet
                    recommendation = "HOLD"
                    confidence = 0.4
                    reasons.append(f"Difficult fixtures ahead but at loss ({profit_pct:.1f}%)")

        # SELL SIGNAL 6: Falling trend + at profit
        if trend_data and trend_data.get("has_data"):
            trend = trend_data.get("trend", "unknown")
            trend_change = trend_data.get("change_pct", 0)

            if trend in ["falling", "strongly falling"] and profit_pct > 5:
                if recommendation != "SELL":
                    recommendation = "SELL"
                    confidence = 0.75
                    reasons.append(f"Falling trend ({trend_change:.1f}%) - lock in profit")
                else:
                    reasons.append(f"Falling trend ({trend_change:.1f}%)")

            # Rising trend - maybe hold for more profit
            elif trend in ["rising", "strongly rising"] and profit_pct > 0:
                if recommendation == "HOLD":
                    confidence = 0.7
                    reasons.append(f"Rising trend (+{trend_change:.1f}%) - may rise more")

        # Default reason if no sell signals
        if not reasons:
            reasons.append(
                f"Current P/L: {profit_pct:.1f}% (value: {player_value.value_score:.1f}/100)"
            )

        # Add matchup/SOS context to reasons
        if matchup_context and matchup_context.get("has_data"):
            matchup_bonus_data = matchup_context.get("matchup_bonus", {})
            matchup_reason = matchup_bonus_data.get("reason", "")
            matchup_bonus_points = matchup_bonus_data.get("bonus_points", 0)

            if matchup_reason:
                sign = "+" if matchup_bonus_points > 0 else ""
                reasons.append(f"{matchup_reason} ({sign}{matchup_bonus_points} pts)")

            sos_data = matchup_context.get("sos")
            if sos_data:
                sos_bonus_points = sos_data.sos_bonus
                if sos_data.short_term_rating == "Very Easy":
                    sos_indicator = "⚡⚡⚡"
                elif sos_data.short_term_rating == "Easy":
                    sos_indicator = "⚡⚡"
                elif sos_data.short_term_rating == "Very Difficult":
                    sos_indicator = "🔥🔥🔥"
                elif sos_data.short_term_rating == "Difficult":
                    sos_indicator = "🔥🔥"
                else:
                    sos_indicator = "→"

                sign = "+" if sos_bonus_points > 0 else ""
                reasons.append(
                    f"{sos_indicator} SOS: {sos_data.short_term_rating} next 3 ({sign}{sos_bonus_points} pts)"
                )

        reason = " | ".join(reasons)

        # Build metadata with peak analysis and other useful data
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
            value_score=player_value.value_score,
            points_per_million=player_value.points_per_million,
            avg_points_per_million=player_value.avg_points_per_million,
            trend=trend_data.get("direction") if trend_data else None,
            trend_change_pct=trend_data.get("change_pct") if trend_data else None,
            metadata=metadata if metadata else None,
        )

    def find_best_opportunities(
        self, analyses: list[PlayerAnalysis], top_n: int = 10
    ) -> list[PlayerAnalysis]:
        """Find the best buying opportunities from a list of analyses"""
        buy_opportunities = [a for a in analyses if a.recommendation == "BUY"]
        # Sort by confidence * value_score for best opportunities
        buy_opportunities.sort(key=lambda a: a.confidence * a.value_score, reverse=True)
        return buy_opportunities[:top_n]
