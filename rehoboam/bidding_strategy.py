"""Smart bidding strategy to win player auctions while maintaining profitability"""

from dataclasses import dataclass
from typing import Optional

try:
    from .activity_feed_learner import ActivityFeedLearner
    from .bid_learner import BidLearner
except ImportError:
    BidLearner = None
    ActivityFeedLearner = None


@dataclass
class BidRecommendation:
    """Recommended bid for a player"""

    base_price: int  # Player's asking price
    recommended_bid: int  # What we should bid
    overbid_amount: int  # How much over asking price
    overbid_pct: float  # Percentage over asking
    reasoning: str  # Why this bid amount
    max_profitable_bid: int  # Maximum we can bid and still profit


class SmartBidding:
    """Calculate optimal bids to win auctions while maintaining value"""

    def __init__(
        self,
        default_overbid_pct: float = 5.0,  # Default overbid % (reduced to avoid overpaying)
        max_overbid_pct: float = 20.0,  # Never exceed this overbid
        high_value_threshold: float = 70.0,  # Value score for aggressive bidding
        elite_player_threshold: float = 70.0,  # Avg points for elite status
        elite_max_overbid_pct: float = 30.0,  # Can bid more for elite long-term holds
        min_bid_increment: int = 1000,  # Minimum bid increment (€1k)
        bid_learner: Optional["BidLearner"] = None,  # Optional learning from past auctions
        activity_feed_learner: Optional[
            "ActivityFeedLearner"
        ] = None,  # Learn from league transfers
    ):
        self.default_overbid_pct = default_overbid_pct
        self.max_overbid_pct = max_overbid_pct
        self.high_value_threshold = high_value_threshold
        self.elite_player_threshold = elite_player_threshold
        self.elite_max_overbid_pct = elite_max_overbid_pct
        self.min_bid_increment = min_bid_increment
        self.bid_learner = bid_learner
        self.activity_feed_learner = activity_feed_learner or (
            ActivityFeedLearner() if ActivityFeedLearner else None
        )

    def calculate_bid(
        self,
        asking_price: int,
        market_value: int,
        value_score: float,
        confidence: float,
        is_replacement: bool = False,
        replacement_sell_value: int = 0,
        predicted_future_value: int | None = None,
        average_points: float = 0.0,
        is_long_term_hold: bool = False,
        player_id: str | None = None,  # For activity feed learning
        roster_impact: float = 0.0,  # Roster impact score for tier classification
        trend_change_pct: float | None = None,  # Market value trend (negative = falling)
    ) -> BidRecommendation:
        """
        Calculate optimal bid for a player with value-bounded learning

        Args:
            asking_price: Current asking price on market
            market_value: Player's market value
            value_score: Our calculated value score (0-100)
            confidence: Our confidence in this player (0-1)
            is_replacement: Is this replacing another player?
            replacement_sell_value: If replacing, what we get from selling current player
            predicted_future_value: Maximum we think player will be worth (value ceiling)

        Returns:
            BidRecommendation with suggested bid (NEVER exceeds predicted_future_value)
        """
        # Calculate predicted future value if not provided
        if predicted_future_value is None:
            # Estimate based on value score (higher score = more growth expected)
            growth_factor = 1.0 + (value_score / 1000)  # 60 score = 6% growth
            predicted_future_value = int(market_value * growth_factor)

        # Try to use learned overbid if available
        learned_overbid_pct = None
        learning_reason = None

        if self.bid_learner:
            try:
                learned_data = self.bid_learner.get_recommended_overbid(
                    asking_price=asking_price,
                    value_score=value_score,
                    market_value=market_value,
                    predicted_future_value=predicted_future_value,
                )
                learned_overbid_pct = learned_data.get("recommended_overbid_pct", None)
                learning_reason = learned_data.get("reason", None)
            except Exception:
                # Learning failed, fall back to default
                pass

        # Check if this is an elite player we want to keep long-term
        is_elite = is_long_term_hold and average_points >= self.elite_player_threshold

        # Get league competitive intelligence from activity feed
        demand_adjustment = 0.0
        demand_score = 0.0
        league_competitive_level = 0.0

        if self.activity_feed_learner and player_id:
            try:
                # Get player demand score (how hot is this player?)
                demand_score = self.activity_feed_learner.get_player_demand_score(player_id)

                # Get league competitive stats
                league_stats = self.activity_feed_learner.get_competitive_bidding_stats()

                # Calculate league competitive level based on average transfer prices
                if league_stats["total_transfers"] > 0:
                    avg_price = league_stats["avg_transfer_price"]
                    # If league average is high, we need to bid more aggressively
                    if avg_price > 15_000_000:
                        league_competitive_level = 8.0  # Very competitive league
                    elif avg_price > 10_000_000:
                        league_competitive_level = 5.0  # Competitive league
                    elif avg_price > 5_000_000:
                        league_competitive_level = 3.0  # Moderate league
                    else:
                        league_competitive_level = 0.0  # Casual league

                # Adjust for player demand
                if demand_score >= 75:
                    demand_adjustment = 8.0  # Hot player - bid aggressively
                elif demand_score >= 60:
                    demand_adjustment = 5.0  # Above average demand
                elif demand_score >= 50:
                    demand_adjustment = 2.0  # Normal demand
                # else: no adjustment for low demand

            except Exception:
                # Fail silently - don't break bidding if feed learner fails
                pass

        # Classify player tier based on value and roster impact
        player_tier = self._classify_player_tier(value_score, roster_impact)

        # Calculate base overbid percentage (use learned if available)
        if learned_overbid_pct is not None and learned_overbid_pct > 0:
            overbid_pct = learned_overbid_pct
        else:
            overbid_pct = self._calculate_overbid_percentage(
                value_score=value_score,
                confidence=confidence,
                is_replacement=is_replacement,
                is_elite=is_elite,
                player_tier=player_tier,
            )

        # Apply league competitive intelligence
        overbid_pct += league_competitive_level
        overbid_pct += demand_adjustment

        # Trend-based overbid adjustment — don't overbid aggressively on falling players
        if trend_change_pct is not None:
            if trend_change_pct < -10:
                overbid_pct *= 0.3  # Strongly falling: slash overbid to 30%
            elif trend_change_pct < -5:
                overbid_pct *= 0.5  # Falling: halve overbid
            elif trend_change_pct < 0:
                overbid_pct *= 0.75  # Slight decline: reduce by 25%
        else:
            overbid_pct *= 0.6  # Unknown trend: conservative

        # FINAL cap (after ALL adjustments including league + demand + trend)
        max_overbid = self.elite_max_overbid_pct if is_elite else self.max_overbid_pct
        overbid_pct = min(overbid_pct, max_overbid)

        # Calculate raw bid amount
        overbid_amount = int(asking_price * (overbid_pct / 100))

        # Round to nearest increment for realistic bidding
        overbid_amount = self._round_to_increment(overbid_amount)

        # Calculate recommended bid
        recommended_bid = asking_price + overbid_amount

        # LEAGUE RULE ENFORCEMENT: Never bid below market value
        # Add 1% buffer to be competitive while staying compliant (reduced from 2%)
        market_value_floor = int(market_value * 1.01)  # Market value + 1% buffer
        if recommended_bid < market_value_floor:
            recommended_bid = market_value_floor
            overbid_amount = recommended_bid - asking_price

        # CRITICAL: Calculate max profitable bid with VALUE CEILING
        # The absolute maximum is predicted_future_value - we NEVER exceed this
        max_profitable_bid = predicted_future_value

        # For high-confidence bids, allow 10% flexibility above value ceiling
        # This helps win competitive auctions for players we're very confident about
        if confidence >= 0.9:
            max_profitable_bid = int(max_profitable_bid * 1.10)  # 10% flexibility

        # For replacements, consider net cost
        if is_replacement and replacement_sell_value > 0:
            # Net cost = new player cost - sell value
            # We can spend more if we're selling someone good
            replacement_adjusted_max = replacement_sell_value + int(replacement_sell_value * 0.5)
            # But STILL never exceed predicted future value (with confidence flexibility)
            max_profitable_bid = min(max_profitable_bid, replacement_adjusted_max)

        # Apply value ceiling - cap at max_profitable_bid
        if recommended_bid > max_profitable_bid:
            recommended_bid = max_profitable_bid
            overbid_amount = recommended_bid - asking_price

        # Sanity check: Don't bid if value ceiling is below asking price
        if recommended_bid < asking_price:
            recommended_bid = 0  # Signal not to bid
            overbid_amount = 0

        # Recalculate actual overbid percentage
        actual_overbid_pct = (overbid_amount / asking_price) * 100 if asking_price > 0 else 0

        # Generate reasoning
        reasoning = self._generate_reasoning(
            value_score=value_score,
            confidence=confidence,
            overbid_pct=actual_overbid_pct,
            is_replacement=is_replacement,
            learning_reason=learning_reason,
            value_ceiling_applied=recommended_bid >= predicted_future_value,
            is_elite=is_elite,
            league_competitive_level=league_competitive_level,
            demand_adjustment=demand_adjustment,
            player_tier=player_tier,
        )

        return BidRecommendation(
            base_price=asking_price,
            recommended_bid=recommended_bid,
            overbid_amount=overbid_amount,
            overbid_pct=actual_overbid_pct,
            reasoning=reasoning,
            max_profitable_bid=max_profitable_bid,
        )

    def _classify_player_tier(self, value_score: float, roster_impact: float) -> str:
        """
        Classify player into bidding tier based on value and roster impact.

        Tiers:
        - anchor: High value + big roster impact - bid aggressively to secure
        - strong: Good value OR significant roster impact - solid bid
        - tactical: Moderate value, lower roster impact - conservative bid
        - opportunistic: Lower scores - minimum viable bid
        """
        if value_score >= 80 and roster_impact >= 15:
            return "anchor"
        elif value_score >= 70 or roster_impact >= 12:
            return "strong"
        elif value_score >= 50:
            return "tactical"
        else:
            return "opportunistic"

    def _calculate_overbid_percentage(
        self,
        value_score: float,
        confidence: float,
        is_replacement: bool,
        is_elite: bool = False,
        player_tier: str = "tactical",
    ) -> float:
        """Calculate how much to overbid based on player quality and situation"""

        # Base overbid (now 10% instead of 5%)
        overbid = self.default_overbid_pct

        # ELITE PLAYERS: Exceptional long-term holds - bid much more aggressively
        if is_elite:
            overbid += 18.0  # Start much higher for elite players (increased from 15%)

        # Increase for high-value players (we really want them)
        elif value_score >= self.high_value_threshold:
            # High value players: bid more aggressively
            overbid += 8.0  # Increased from 5%

        # Increase based on confidence (more aggressive)
        if confidence >= 0.9:
            overbid += 5.0  # Increased from 3.0
        elif confidence >= 0.7:
            overbid += 3.0  # Increased from 1.5

        # Replacements can bid more (we're selling someone)
        if is_replacement:
            overbid += 3.0  # Increased from 2.0

        # Tier-based bidding adjustments (based on value + roster impact)
        # Anchor players = must win, tactical players = don't overpay
        tier_bonuses = {
            "anchor": 8.0,  # Aggressive - must win
            "strong": 5.0,  # Solid bid
            "tactical": 2.0,  # Conservative
            "opportunistic": 0.0,  # Minimum viable
        }
        overbid += tier_bonuses.get(player_tier, 0.0)

        # Cap at maximum (higher for elite players)
        max_overbid = self.elite_max_overbid_pct if is_elite else self.max_overbid_pct
        overbid = min(overbid, max_overbid)

        return overbid

    def _round_to_increment(self, amount: int) -> int:
        """Round bid to realistic increment"""

        # For small amounts, round to nearest €1k
        if amount < 10000:
            return round(amount / 1000) * 1000

        # For medium amounts, round to nearest €5k
        if amount < 100000:
            return round(amount / 5000) * 5000

        # For large amounts, round to nearest €10k
        return round(amount / 10000) * 10000

    def _generate_reasoning(
        self,
        value_score: float,
        confidence: float,
        overbid_pct: float,
        is_replacement: bool,
        learning_reason: str | None = None,
        value_ceiling_applied: bool = False,
        is_elite: bool = False,
        league_competitive_level: float = 0.0,
        demand_adjustment: float = 0.0,
        player_tier: str = "tactical",
    ) -> str:
        """Generate human-readable reasoning for the bid"""

        reasons = []

        # Elite player status (highest priority)
        if is_elite:
            reasons.append("🌟 ELITE PLAYER - Long-term hold")

        # Player tier (anchor vs tactical)
        tier_labels = {
            "anchor": "🎯 ANCHOR - must secure",
            "strong": "💪 Strong target",
            "tactical": "📊 Tactical buy",
            "opportunistic": "🔍 Opportunistic",
        }
        if player_tier in tier_labels and not is_elite:  # Don't show tier if already elite
            reasons.append(tier_labels[player_tier])

        # League competitive intelligence
        if league_competitive_level >= 8.0:
            reasons.append("🔥 Very competitive league")
        elif league_competitive_level >= 5.0:
            reasons.append("⚡ Competitive league")

        # Player demand signal
        if demand_adjustment >= 8.0:
            reasons.append("🎯 HOT PLAYER - high demand")
        elif demand_adjustment >= 5.0:
            reasons.append("📈 Above avg demand")

        # Learning-based reasoning (priority)
        if learning_reason:
            reasons.append(f"Learned: {learning_reason}")

        # Value-based reasoning
        if value_score >= 90:
            reasons.append("Exceptional value")
        elif value_score >= 70:
            reasons.append("High value")
        elif value_score >= 50:
            reasons.append("Good value")

        # Confidence-based reasoning
        if confidence >= 0.9:
            reasons.append("very confident")
        elif confidence >= 0.7:
            reasons.append("confident")

        # Replacement reasoning
        if is_replacement:
            reasons.append("upgrade replacement")

        # Overbid reasoning
        if overbid_pct >= 10:
            reasons.append(f"aggressive +{overbid_pct:.1f}% overbid")
        elif overbid_pct >= 5:
            reasons.append(f"competitive +{overbid_pct:.1f}% overbid")
        elif overbid_pct > 0:
            reasons.append(f"modest +{overbid_pct:.1f}% overbid")

        # Value ceiling warning
        if value_ceiling_applied:
            reasons.append("⚠️ AT VALUE CEILING")

        return " | ".join(reasons) if reasons else "Standard bid"

    def calculate_batch_bids(
        self, opportunities: list, total_budget: int
    ) -> list[tuple[any, BidRecommendation]]:
        """
        Calculate bids for multiple players considering total budget

        Args:
            opportunities: List of (player_analysis, ...) tuples
            total_budget: Total available budget

        Returns:
            List of (player_analysis, bid_recommendation) tuples
        """
        recommendations = []
        remaining_budget = total_budget

        for analysis in opportunities:
            bid = self.calculate_bid(
                asking_price=analysis.current_price,
                market_value=analysis.market_value,
                value_score=analysis.value_score,
                confidence=analysis.confidence,
                is_replacement=False,  # TODO: integrate replacement logic
            )

            # Check if we can afford this bid
            if bid.recommended_bid <= remaining_budget:
                recommendations.append((analysis, bid))
                remaining_budget -= bid.recommended_bid
            else:
                # Can't afford - skip
                continue

        return recommendations
