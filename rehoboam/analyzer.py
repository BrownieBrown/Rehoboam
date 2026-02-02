"""Market analysis and player evaluation"""

from dataclasses import dataclass, field

from .kickbase_client import MarketPlayer
from .risk_analyzer import RiskMetrics
from .value_calculator import PlayerValue


@dataclass
class RosterContext:
    """Context about user's current roster for a position"""

    position: str  # Position name (e.g., "Goalkeeper", "Midfielder")
    current_count: int  # How many players at this position
    minimum_count: int  # Minimum required (GK:1, DEF:3, MID:2, FWD:1)
    existing_players: list  # List of (player, purchase_price, current_value, value_score)
    weakest_player: dict | None  # Player with lowest value score
    is_below_minimum: bool  # True if below minimum required
    upgrade_potential: float  # How much better market player is vs weakest


@dataclass
class RosterImpact:
    """Roster impact information for a buy recommendation"""

    is_upgrade: bool  # True if this is an upgrade over existing player
    impact_type: str  # "upgrade", "depth", "fills_gap", "redundant"
    replaces_player: str | None  # Name of player this would replace
    value_score_gain: float  # Value score improvement over replaced player
    net_cost: int  # Cost after selling replaced player
    reason: str  # Human-readable reason


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
    roster_impact: "RosterImpact | None" = None  # Roster context for buy recommendations


@dataclass
class SellCandidate:
    """Player evaluated as potential sell candidate for squad management"""

    player: any  # Player object
    expendability_score: float  # 0-100 (higher = more expendable, should sell first)
    is_protected: bool  # True if player cannot/should not be sold
    protection_reason: str | None  # "Best 11", "Only GK", "Min DEF", etc.
    value_score: float  # Base value score
    market_value: int  # Current market value
    profit_loss_pct: float  # P/L percentage from purchase
    budget_recovery: int  # How much budget selling recovers
    sos_rating: str | None = None  # "Very Easy" to "Very Difficult"
    team_position: int | None = None  # 1-18 league position
    trend: str | None = None  # "rising", "falling", "stable"
    recovery_signal: str | None = None  # "HOLD", "CUT", or None (for loss recovery)


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
        self.buy_threshold = max(min_value_score_to_buy, 50.0)  # Minimum 50 for buy recommendations
        self.hold_threshold = max(min_value_score_to_buy - 20, 20)
        self.sell_threshold = 40  # For owned players

    def analyze_market_player(
        self,
        player: MarketPlayer,
        trend_data: dict | None = None,
        matchup_context: dict | None = None,
        roster_context: "RosterContext | None" = None,
        performance_data: dict | None = None,
    ) -> PlayerAnalysis:
        """Analyze a player on the market for buying opportunity using factor-based scoring

        Args:
            player: MarketPlayer to analyze
            trend_data: Market value trend data (14-day history)
            matchup_context: Upcoming fixtures and strength of schedule
            roster_context: Current roster composition for upgrade analysis
            performance_data: Match-by-match performance for sample size analysis
        """
        market_value = player.market_value
        current_price = player.price

        # CRITICAL: Skip injured players completely
        # status: 0 = healthy, 1+ = injured/unavailable
        if player.status != 0:
            # Return SKIP recommendation for injured players
            return PlayerAnalysis(
                player=player,
                current_price=current_price,
                market_value=market_value,
                value_change_pct=0.0,
                points=player.points,
                average_points=player.average_points,
                recommendation="SKIP",
                confidence=0.0,
                reason="Player is injured or unavailable",
                value_score=0.0,
                points_per_million=0.0,
                avg_points_per_million=0.0,
                trend=None,
                trend_change_pct=None,
                factors=[],
            )

        # Calculate value change percentage
        if market_value > 0:
            value_change_pct = ((market_value - current_price) / current_price) * 100
        else:
            value_change_pct = 0.0

        # Calculate base player value (with performance data for sample size analysis)
        player_value = PlayerValue.calculate(
            player, trend_data=trend_data, performance_data=performance_data
        )
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
                # Easy matchup - add bonus
                score_contribution = abs(matchup_bonus_points) * self.weights.matchup_easy / 10
                matchup_factor = ScoringFactor(
                    name="Easy Matchup",
                    raw_value=matchup_bonus_points,
                    weight=self.weights.matchup_easy,
                    score=score_contribution,  # Always positive for easy
                    description=matchup_reason,
                )
                factors.append(matchup_factor)
                final_score += matchup_factor.score
            elif matchup_bonus_points < 0:
                # Hard matchup - subtract penalty (use absolute value, then negate)
                score_contribution = -(
                    abs(matchup_bonus_points) * abs(self.weights.matchup_hard) / 10
                )
                matchup_factor = ScoringFactor(
                    name="Hard Matchup",
                    raw_value=matchup_bonus_points,
                    weight=self.weights.matchup_hard,
                    score=score_contribution,  # Always negative for hard
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

        # Factor 5: Market discount/premium
        # Negative value_change_pct means current_price > market_value = undervalued = good deal
        # Positive value_change_pct means current_price < market_value = overvalued = bad deal
        if value_change_pct <= -self.min_buy_value_increase_pct:
            # Player is undervalued (priced below market value) - BONUS
            discount_factor = ScoringFactor(
                name="Undervalued",
                raw_value=value_change_pct,
                weight=self.weights.discount,
                score=self.weights.discount,
                description=f"Priced below market value by {abs(value_change_pct):.1f}%",
            )
            factors.append(discount_factor)
            final_score += discount_factor.score
        elif value_change_pct >= self.min_buy_value_increase_pct:
            # Player is overvalued (priced above market value) - PENALTY
            overvalued_factor = ScoringFactor(
                name="Overvalued",
                raw_value=value_change_pct,
                weight=self.weights.discount,
                score=-self.weights.discount,  # PENALTY not bonus
                description=f"Overpriced by {value_change_pct:.1f}%",
            )
            factors.append(overvalued_factor)
            final_score += overvalued_factor.score

        # Factor 6: Roster Impact (for display and upgrade bonus)
        roster_impact = None
        if roster_context:
            from .roster_analyzer import RosterAnalyzer

            roster_analyzer = RosterAnalyzer()
            roster_impact = roster_analyzer.get_roster_impact(
                market_player=player,
                market_player_score=final_score,  # Use score calculated so far
                roster_context=roster_context,
            )

            # Add bonus for significant upgrades
            if roster_impact.is_upgrade:
                upgrade_bonus = min(roster_impact.value_score_gain, 15.0)  # Cap at 15
                roster_factor = ScoringFactor(
                    name="Squad Upgrade",
                    raw_value=roster_impact.value_score_gain,
                    weight=1.0,
                    score=upgrade_bonus,
                    description=f"Replaces {roster_impact.replaces_player} (+{roster_impact.value_score_gain:.0f} value)",
                )
                factors.append(roster_factor)
                final_score += roster_factor.score
            elif roster_context.is_below_minimum:
                # Bonus for filling a required gap
                gap_factor = ScoringFactor(
                    name="Fills Gap",
                    raw_value=1.0,
                    weight=10.0,
                    score=10.0,
                    description=f"Fills gap ({roster_context.current_count}/{roster_context.minimum_count} {roster_context.position}s)",
                )
                factors.append(gap_factor)
                final_score += gap_factor.score

        # Clamp final score to 0-150 range (can exceed 100 with bonuses)
        final_score = max(0, min(150, final_score))

        # DECISION LOGIC: Stricter quality-based recommendations
        # Don't recommend buying just to recommend something - only genuinely good opportunities
        if final_score >= self.buy_threshold:
            # Additional quality checks for BUY recommendation
            trend_check = True
            if trend_change_pct is not None:
                # TIGHTENED: Reject falling players more aggressively
                # Any decline > 5% is a red flag
                if trend_change_pct < -5:
                    trend_check = False
                # For slight decline (-5 to 0), require high score
                elif trend_change_pct < 0 and final_score < 70:
                    trend_check = False

            # Base value check - player must have decent fundamentals
            base_value_check = base_value_score >= 45.0  # Raised threshold for higher quality

            # Points check - reject players with 0 points (likely injured/benched)
            points_check = player.points > 0

            # Sample size check - reject players with too few games
            # This prevents buying on 1-2 game hot streaks
            sample_size_check = True
            games_played = None
            if player_value.games_played is not None:
                games_played = player_value.games_played
                if games_played < 3:
                    # Less than 3 games = too risky, skip entirely
                    sample_size_check = False
                elif games_played < 5 and final_score < 75:
                    # 3-4 games = risky, need very high score
                    sample_size_check = False

            # Schedule check - only reject VERY hard schedules
            # If no matchup data, assume neutral (don't penalize lack of data)
            schedule_check = True
            if matchup_context and matchup_context.get("has_data"):
                # Check matchup bonus - only reject extremely hard matchups
                matchup_bonus_data = matchup_context.get("matchup_bonus", {})
                matchup_bonus_points = matchup_bonus_data.get("bonus_points", 0)
                if matchup_bonus_points < -8:  # Only very hard matchups (was -5)
                    schedule_check = False

                # Check SOS - only reject very difficult schedules
                sos_data = matchup_context.get("sos")
                if sos_data:
                    sos_rating = sos_data.short_term_rating
                    # Only reject VERY difficult schedules, not just difficult
                    if sos_rating == "Very Difficult":  # Was ["Difficult", "Very Difficult"]
                        schedule_check = False

            if (
                trend_check
                and base_value_check
                and schedule_check
                and points_check
                and sample_size_check
            ):
                recommendation = "BUY"
                confidence = min(final_score / 100.0, 1.0)
            else:
                # Downgrade to HOLD if quality checks fail
                if not points_check:
                    recommendation = "SKIP"
                    confidence = 0.2
                elif not sample_size_check:
                    recommendation = "HOLD"  # Wait for more data
                    confidence = 0.3
                else:
                    recommendation = "HOLD"
                    confidence = 0.5
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

        # Build metadata with SOS rating for compact display
        metadata = {}
        if sos_rating:
            metadata["sos_rating"] = sos_rating

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
            metadata=metadata if metadata else None,
            factors=factors,
            roster_impact=roster_impact,
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
        """
        Find the best buying opportunities from a list of analyses

        Prioritizes rising/stable players over falling players to avoid catching falling knives
        """
        buy_opportunities = [a for a in analyses if a.recommendation == "BUY"]

        # Separate into rising/stable vs falling players
        rising_or_stable = []
        falling_recovery = []

        for analysis in buy_opportunities:
            trend_change = analysis.trend_change_pct if analysis.trend_change_pct else 0

            # Rising or stable trend (or no trend data)
            if trend_change >= -2:  # Allow slight decline (-2%)
                rising_or_stable.append(analysis)
            # Falling but with recovery signs
            elif trend_change >= -10 and analysis.value_score >= 70:
                # Only include falling players if they have high value score (70+)
                # and aren't falling too hard (>-10%)
                falling_recovery.append(analysis)
            # Ignore deeply falling players (catching falling knives)
            # else: skip

        # Score each group
        def calculate_opportunity_score(a: PlayerAnalysis) -> float:
            base_score = a.confidence * a.value_score

            # Bonus for rising trends
            if a.trend and "rising" in a.trend.lower():
                trend_change = a.trend_change_pct if a.trend_change_pct else 0
                if trend_change > 15:
                    base_score *= 1.3  # Strong uptrend bonus
                elif trend_change > 5:
                    base_score *= 1.15  # Moderate uptrend bonus

            # Penalty for falling trends
            elif a.trend and "falling" in a.trend.lower():
                base_score *= 0.7  # Falling penalty

            return base_score

        # Sort each group
        rising_or_stable.sort(key=calculate_opportunity_score, reverse=True)
        falling_recovery.sort(key=calculate_opportunity_score, reverse=True)

        # Prioritize rising/stable, then add falling recovery if we need more
        result = rising_or_stable[:top_n]

        if len(result) < top_n:
            # Add falling recovery players only if we don't have enough rising/stable
            remaining = top_n - len(result)
            result.extend(falling_recovery[:remaining])

        return result[:top_n]

    def rank_squad_for_selling(
        self,
        squad: list,
        player_stats: dict,
        player_values: dict[str, float],
        best_eleven_ids: set[str],
        position_counts: dict[str, int],
        current_budget: int,
        player_matchup_data: dict[str, dict] | None = None,
        team_positions: dict[str, int] | None = None,
        player_trend_data: dict[str, dict] | None = None,
    ) -> tuple[list[SellCandidate], list[SellCandidate] | None]:
        """
        Rank entire squad by expendability for selling decisions.

        Args:
            squad: List of player objects in the squad
            player_stats: Dict mapping player_id -> stats (for purchase price via trp)
            player_values: Dict mapping player_id -> value_score
            best_eleven_ids: Set of player IDs in the best 11
            position_counts: Dict mapping position -> current count
            current_budget: Current budget (negative means deficit)
            player_matchup_data: Dict mapping player_id -> matchup context (SOS ratings)
            team_positions: Dict mapping team_id -> league position (1-18)
            player_trend_data: Dict mapping player_id -> trend data (for recovery prediction)

        Returns:
            Tuple of (all_candidates_ranked, recovery_plan_if_in_deficit)
            - all_candidates_ranked: All players sorted by expendability (most expendable first)
            - recovery_plan: List of players to sell to reach 0 budget (if in deficit)
        """
        from .config import POSITION_MINIMUMS

        candidates = []

        for player in squad:
            value_score = player_values.get(player.id, 0.0)

            # Get purchase price from player object (mvgl from squad API)
            # Negative mvgl indicates free agency acquisition (no cost)
            # Positive mvgl is the actual purchase price
            purchase_price = player.buy_price

            # Calculate profit/loss
            if purchase_price > 0:
                # Normal purchase - calculate P/L from buy price
                profit_loss_pct = ((player.market_value - purchase_price) / purchase_price) * 100
            elif purchase_price < 0:
                # Free agency pickup (negative mvgl) - pure profit, show as percentage of market value
                # Use absolute value to avoid confusion, cap at 100%
                profit_loss_pct = 100.0  # Free acquisition = 100% profit
            else:
                # buy_price = 0 means unknown, fall back to 0%
                profit_loss_pct = 0.0

            # Start with inverse of value score (low value = more expendable)
            expendability = 100 - value_score

            # Determine protection status
            is_protected = False
            protection_reason = None
            position = player.position

            # Check position minimums
            current_at_position = position_counts.get(position, 0)
            min_required = POSITION_MINIMUMS.get(position, 1)

            # Only player at position - cannot sell
            if current_at_position <= 1:
                is_protected = True
                protection_reason = f"Only {position[:2]}"
                expendability -= 50

            # At position minimum - very protected
            elif current_at_position <= min_required:
                is_protected = True
                protection_reason = f"Min {position[:3]}"
                expendability -= 30

            # If we have MORE than minimum at this position and in debt, boost expendability
            # Extra players at position are more expendable when in debt
            # GK especially since you only field 1
            if current_budget < 0 and current_at_position > min_required:
                if position == "Goalkeeper":
                    expendability += 15  # Higher priority to sell extra GK
                else:
                    expendability += 5  # Slight boost for other excess positions

            # In Best 11 - protected but sellable
            if player.id in best_eleven_ids:
                if not is_protected:
                    protection_reason = "Best 11"
                expendability -= 40

            # Extract SOS rating and trend for this player
            sos_rating = None
            if player_matchup_data:
                matchup = player_matchup_data.get(player.id, {})
                if matchup.get("has_data"):
                    sos_data = matchup.get("sos")
                    if sos_data:
                        sos_rating = sos_data.short_term_rating

            # Extract trend data
            trend = None
            trend_change_pct = 0.0
            if player_trend_data:
                trend_data = player_trend_data.get(player.id, {})
                if trend_data.get("has_data"):
                    trend = trend_data.get("trend", "unknown")
                    trend_change_pct = trend_data.get("change_pct", 0.0)

            # Get team league position
            team_pos = None
            if team_positions:
                team_pos = team_positions.get(player.team_id)

            # SOS-BASED EXPENDABILITY
            if sos_rating:
                if sos_rating == "Very Difficult":
                    expendability += 20  # Hard schedule = sell first
                elif sos_rating == "Difficult":
                    expendability += 10
                elif sos_rating == "Easy":
                    expendability -= 10  # Easy schedule = keep
                elif sos_rating == "Very Easy":
                    expendability -= 15

            # TEAM LEAGUE POSITION-BASED EXPENDABILITY
            if team_pos:
                if team_pos >= 16:  # Bottom 3 (relegation zone)
                    expendability += 15
                elif team_pos >= 14:  # Near relegation
                    expendability += 10
                elif team_pos <= 4:  # Top 4
                    expendability -= 10

            # EXPENDABILITY BASED ON P/L (with recovery prediction for losses)
            recovery_signal = None

            if profit_loss_pct < -20:
                # Big loss - check if recovery is possible
                if trend in ["rising", "stable"]:
                    # Value recovering - consider holding
                    expendability += 10  # Still elevated but less urgent
                    recovery_signal = "HOLD"
                else:
                    expendability += 25  # Big loss + declining = sell
                    recovery_signal = "CUT"
            elif profit_loss_pct < -10:
                if trend == "rising":
                    expendability += 5  # Moderate loss but recovering
                    recovery_signal = "HOLD"
                elif trend == "falling" and trend_change_pct < -5:
                    expendability += 20  # Moderate loss + declining = sell soon
                    recovery_signal = "CUT"
                else:
                    expendability += 15
            elif profit_loss_pct < 0:
                # Small loss - factor in trend and schedule for recovery prediction
                if trend == "rising":
                    expendability += 0  # Neutral - recovery likely
                    recovery_signal = "HOLD"
                elif trend == "falling" and trend_change_pct < -10:
                    expendability += 15  # Sharp decline - cut losses
                    recovery_signal = "CUT"
                elif sos_rating in ["Easy", "Very Easy"]:
                    expendability += 0  # Easy schedule = recovery likely
                    recovery_signal = "HOLD"
                elif sos_rating in ["Difficult", "Very Difficult"]:
                    expendability += 12  # Hard schedule = continued decline
                    recovery_signal = "CUT"
                else:
                    expendability += 10

            # Profits = LESS expendable (protect winners)
            # FIXED: Only protect notable profits (>5%), not 0-5%
            elif profit_loss_pct > 20:
                expendability -= 20  # Big profit - protect
            elif profit_loss_pct > 10:
                expendability -= 15
            elif profit_loss_pct > 5:
                expendability -= 10
            # 0-5% profit = neutral (no adjustment)

            # Poor performance (low value score) - more expendable
            if value_score < 35:
                expendability += 10

            # Clamp expendability to 0-100
            expendability = max(0, min(100, expendability))

            candidates.append(
                SellCandidate(
                    player=player,
                    expendability_score=expendability,
                    is_protected=is_protected,
                    protection_reason=protection_reason,
                    value_score=value_score,
                    market_value=player.market_value,
                    profit_loss_pct=profit_loss_pct,
                    budget_recovery=player.market_value,
                    sos_rating=sos_rating,
                    team_position=team_pos,
                    trend=trend,
                    recovery_signal=recovery_signal,
                )
            )

        # Sort by expendability (most expendable first)
        candidates.sort(key=lambda c: c.expendability_score, reverse=True)

        # Calculate recovery plan if in deficit
        recovery_plan = None
        if current_budget < 0:
            recovery_plan = self._calculate_recovery_plan(candidates, current_budget)

        return candidates, recovery_plan

    def _calculate_recovery_plan(
        self, candidates: list[SellCandidate], current_budget: int
    ) -> list[SellCandidate]:
        """
        Find minimum sells to reach 0 budget, prioritizing most expendable.

        Args:
            candidates: List of SellCandidate sorted by expendability
            current_budget: Current budget (negative)

        Returns:
            List of candidates to sell to reach 0+ budget
        """
        # Filter to sellable candidates (not fully protected)
        sellable = [c for c in candidates if not c.is_protected]

        recovery_plan = []
        remaining_deficit = abs(current_budget)

        for candidate in sellable:
            if remaining_deficit <= 0:
                break
            recovery_plan.append(candidate)
            remaining_deficit -= candidate.market_value

        return recovery_plan
