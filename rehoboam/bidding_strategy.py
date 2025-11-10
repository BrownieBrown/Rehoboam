"""Smart bidding strategy to win player auctions while maintaining profitability"""

from dataclasses import dataclass
from typing import Optional

try:
    from .bid_learner import BidLearner
except ImportError:
    BidLearner = None


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
        default_overbid_pct: float = 5.0,  # Default overbid %
        max_overbid_pct: float = 15.0,  # Never exceed this overbid
        high_value_threshold: float = 70.0,  # Value score for aggressive bidding
        min_bid_increment: int = 1000,  # Minimum bid increment (€1k)
        bid_learner: Optional["BidLearner"] = None,  # Optional learning from past auctions
    ):
        self.default_overbid_pct = default_overbid_pct
        self.max_overbid_pct = max_overbid_pct
        self.high_value_threshold = high_value_threshold
        self.min_bid_increment = min_bid_increment
        self.bid_learner = bid_learner

    def calculate_bid(
        self,
        asking_price: int,
        market_value: int,
        value_score: float,
        confidence: float,
        is_replacement: bool = False,
        replacement_sell_value: int = 0,
        predicted_future_value: int | None = None,
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

        # Calculate base overbid percentage (use learned if available)
        if learned_overbid_pct is not None and learned_overbid_pct > 0:
            overbid_pct = learned_overbid_pct
        else:
            overbid_pct = self._calculate_overbid_percentage(
                value_score=value_score, confidence=confidence, is_replacement=is_replacement
            )

        # Calculate raw bid amount
        overbid_amount = int(asking_price * (overbid_pct / 100))

        # Round to nearest increment for realistic bidding
        overbid_amount = self._round_to_increment(overbid_amount)

        # Calculate recommended bid
        recommended_bid = asking_price + overbid_amount

        # CRITICAL: Calculate max profitable bid with VALUE CEILING
        # The absolute maximum is predicted_future_value - we NEVER exceed this
        max_profitable_bid = predicted_future_value

        # For replacements, consider net cost
        if is_replacement and replacement_sell_value > 0:
            # Net cost = new player cost - sell value
            # We can spend more if we're selling someone good
            replacement_adjusted_max = replacement_sell_value + int(replacement_sell_value * 0.5)
            # But STILL never exceed predicted future value
            max_profitable_bid = min(max_profitable_bid, replacement_adjusted_max)

        # Apply value ceiling - NEVER exceed predicted future value
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
        )

        return BidRecommendation(
            base_price=asking_price,
            recommended_bid=recommended_bid,
            overbid_amount=overbid_amount,
            overbid_pct=actual_overbid_pct,
            reasoning=reasoning,
            max_profitable_bid=max_profitable_bid,
        )

    def _calculate_overbid_percentage(
        self, value_score: float, confidence: float, is_replacement: bool
    ) -> float:
        """Calculate how much to overbid based on player quality and situation"""

        # Base overbid
        overbid = self.default_overbid_pct

        # Increase for high-value players (we really want them)
        if value_score >= self.high_value_threshold:
            # High value players: bid more aggressively
            overbid += 5.0

        # Increase based on confidence
        if confidence >= 0.9:
            overbid += 3.0
        elif confidence >= 0.7:
            overbid += 1.5

        # Replacements can bid more (we're selling someone)
        if is_replacement:
            overbid += 2.0

        # Cap at maximum
        overbid = min(overbid, self.max_overbid_pct)

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
    ) -> str:
        """Generate human-readable reasoning for the bid"""

        reasons = []

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
