"""Smart bidding strategy to win player auctions while maintaining profitability"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .scoring.models import SellPlan

try:
    from .activity_feed_learner import ActivityFeedLearner
    from .bid_learner import BidLearner
except ImportError:
    BidLearner = None
    ActivityFeedLearner = None


# ---------------------------------------------------------------------------
# Competitor-aware bidding helpers
# ---------------------------------------------------------------------------


def _contested_skip_reason(
    ep_tier: str,
    offer_count: int,
    has_aggressive_competitors: bool,
) -> str | None:
    """Return a skip reason if the auction isn't worth joining, else None.

    The league is won by matchday points, not by hoarding players. Paying a
    premium in a contested auction for a marginal upgrade just inflates the
    price for a rival without improving our chances of winning — this function
    encodes that restraint.

    Skip rules:
    - Marginal tier + 2+ offers → skip. Never burn budget on a bidding war
      for a player who barely moves our EP.
    - Solid/strong upgrades + 4+ offers + aggressive whales in league → skip.
      Our normal bid won't beat an aggressive competitor willing to overpay,
      and the EP gain doesn't justify matching their premium.

    *must_have* tier is never skipped — we need the player regardless.
    """
    if ep_tier == "marginal" and offer_count >= 2:
        return f"Marginal EP + {offer_count} offers — skipping contested auction"

    if has_aggressive_competitors and offer_count >= 4:
        if ep_tier in ("solid_upgrade", "strong_upgrade"):
            return (
                f"{ep_tier} EP + {offer_count} offers + aggressive league — "
                "won't outbid a whale for a non-essential upgrade"
            )

    return None


def _contested_overbid_bump(ep_tier: str, offer_count: int) -> float:
    """Extra overbid percentage to apply when we're contested but committing.

    Pulls ahead of rival bids for players we actually want. Scaling by tier:
    must_have defends hardest (bigger bump), marginal gets nothing (skipped
    upstream anyway). A lone extra bidder barely moves the needle.
    """
    if offer_count <= 1:
        return 0.0

    # 2-3 offers: moderate pressure; 4+: heavy pressure
    heavy = offer_count >= 4

    if ep_tier == "must_have":
        return 6.0 if heavy else 3.0
    if ep_tier == "strong_upgrade":
        return 3.0 if heavy else 2.0
    if ep_tier == "solid_upgrade":
        return 2.0 if heavy else 1.0
    # marginal tier reached here only if skip logic didn't fire (shouldn't happen)
    return 0.0


@dataclass
class BidRecommendation:
    """Recommended bid for a player"""

    base_price: int  # Player's asking price
    recommended_bid: int  # What we should bid
    overbid_amount: int  # How much over asking price
    overbid_pct: float  # Percentage over asking
    reasoning: str  # Why this bid amount
    budget_ceiling: int  # Maximum we can bid (replaces max_profitable_bid)
    sell_plan: SellPlan | None = field(default=None)  # Paired sell plan (EP flow)
    marginal_ep_gain: float = 0.0  # EP gain that drove this bid

    @property
    def max_profitable_bid(self) -> int:
        """Backward-compatible alias for budget_ceiling."""
        return self.budget_ceiling


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
        bid_learner: BidLearner | None = None,  # Optional learning from past auctions
        activity_feed_learner: ActivityFeedLearner | None = None,  # Learn from league transfers
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
            budget_ceiling=max_profitable_bid,
        )

    def calculate_ep_bid(
        self,
        asking_price: int,
        market_value: int,
        expected_points: float,
        marginal_ep_gain: float,
        confidence: float,
        current_budget: int,
        sell_plan: SellPlan | None = None,
        player_id: str | None = None,
        trend_change_pct: float | None = None,
        offer_count: int = 0,
        has_aggressive_competitors: bool = False,
    ) -> BidRecommendation:
        """
        Calculate optimal bid driven by expected points (EP) gain rather than market value.

        Args:
            asking_price: Current asking price on market
            market_value: Player's market value
            expected_points: Player's estimated matchday points
            marginal_ep_gain: How many extra EP points this player adds vs current squad
            confidence: Our confidence in this player (0-1)
            current_budget: Available budget right now
            sell_plan: Optional plan to sell players to raise funds
            player_id: For activity feed demand lookup
            trend_change_pct: Market value trend (negative = falling)
            offer_count: Number of other managers currently bidding on the player.
                Controls competitor-aware bidding: contested marginal buys are
                skipped (don't inflate rival auctions), must-haves are defended
                harder.
            has_aggressive_competitors: True when the league has known high-threat
                buyers (from ActivityFeedLearner). Tightens the skip criteria for
                mid-tier contested auctions.

        Returns:
            BidRecommendation — recommended_bid=0 if no improvement warranted
        """
        # No improvement → no bid
        if marginal_ep_gain == 0:
            return BidRecommendation(
                base_price=asking_price,
                recommended_bid=0,
                overbid_amount=0,
                overbid_pct=0.0,
                reasoning="No marginal EP gain — skipping",
                budget_ceiling=current_budget,
                sell_plan=sell_plan,
                marginal_ep_gain=0.0,
            )

        # Tier classification based on marginal EP gain
        if marginal_ep_gain >= 20:
            ep_tier = "must_have"
            tier_bonus = 10.0
        elif marginal_ep_gain >= 10:
            ep_tier = "strong_upgrade"
            tier_bonus = 6.0
        elif marginal_ep_gain >= 5:
            ep_tier = "solid_upgrade"
            tier_bonus = 3.0
        else:
            ep_tier = "marginal"
            tier_bonus = 0.0

        # Competitor-aware skip: don't feed contested auctions for players that
        # wouldn't meaningfully change our matchday output. We'd just inflate
        # the price for a rival without improving our chances of winning the league.
        contested_skip_reason = _contested_skip_reason(
            ep_tier=ep_tier,
            offer_count=offer_count,
            has_aggressive_competitors=has_aggressive_competitors,
        )
        if contested_skip_reason is not None:
            return BidRecommendation(
                base_price=asking_price,
                recommended_bid=0,
                overbid_amount=0,
                overbid_pct=0.0,
                reasoning=contested_skip_reason,
                budget_ceiling=current_budget,
                sell_plan=sell_plan,
                marginal_ep_gain=marginal_ep_gain,
            )

        # Budget ceiling = current budget + any sell plan recovery
        budget_ceiling = current_budget + (sell_plan.total_recovery if sell_plan else 0)

        # EP-proportional max bid: larger EP gain justifies spending more of the budget
        max_bid_fraction = min(0.8, 0.2 + marginal_ep_gain / 50)
        ep_max_bid = int(budget_ceiling * max_bid_fraction)

        # League competitive intelligence
        demand_adjustment = 0.0
        league_competitive_level = 0.0

        if self.activity_feed_learner and player_id:
            try:
                demand_score = self.activity_feed_learner.get_player_demand_score(player_id)
                league_stats = self.activity_feed_learner.get_competitive_bidding_stats()

                if league_stats["total_transfers"] > 0:
                    avg_price = league_stats["avg_transfer_price"]
                    if avg_price > 15_000_000:
                        league_competitive_level = 8.0
                    elif avg_price > 10_000_000:
                        league_competitive_level = 5.0
                    elif avg_price > 5_000_000:
                        league_competitive_level = 3.0

                if demand_score >= 75:
                    demand_adjustment = 8.0
                elif demand_score >= 60:
                    demand_adjustment = 5.0
                elif demand_score >= 50:
                    demand_adjustment = 2.0
            except Exception:
                pass

        # Base overbid: 5% default + tier bonus + confidence bonus
        overbid_pct = self.default_overbid_pct + tier_bonus

        if confidence >= 0.9:
            overbid_pct += 5.0
        elif confidence >= 0.7:
            overbid_pct += 3.0

        # Apply league competitive + demand adjustments
        overbid_pct += league_competitive_level
        overbid_pct += demand_adjustment

        # Trend-based overbid reduction
        if trend_change_pct is not None:
            if trend_change_pct < -10:
                overbid_pct *= 0.3
            elif trend_change_pct < -5:
                overbid_pct *= 0.5
            elif trend_change_pct < 0:
                overbid_pct *= 0.75
        else:
            overbid_pct *= 0.6  # Unknown trend: conservative

        # Offer-count bump: when we're contested and the player is worth
        # winning, bid harder to pull ahead of rival offers. Applied AFTER
        # trend scaling so the "fight for this player" signal isn't dampened
        # by a falling market value — contestedness doesn't care about trend.
        overbid_pct += _contested_overbid_bump(ep_tier=ep_tier, offer_count=offer_count)

        # Cap overbid (must_have tier gets elite cap)
        max_overbid = self.elite_max_overbid_pct if ep_tier == "must_have" else self.max_overbid_pct
        overbid_pct = min(overbid_pct, max_overbid)

        # Try EP-specific learned overbid if available
        if self.bid_learner:
            try:
                learned_pct = self.bid_learner.get_ep_recommended_overbid(
                    marginal_ep_gain=marginal_ep_gain,
                    confidence=confidence,
                )
                if learned_pct and learned_pct > 0:
                    overbid_pct = min(learned_pct, max_overbid)
            except AttributeError:
                pass  # Method not yet implemented on BidLearner — expected
            except Exception:
                pass

        # Calculate raw bid from overbid percentage
        overbid_amount = int(asking_price * (overbid_pct / 100))
        overbid_amount = self._round_to_increment(overbid_amount)
        recommended_bid = asking_price + overbid_amount

        # EP-proportional max: floor at asking_price so we never refuse an
        # affordable player just because their price exceeds the fraction threshold.
        ep_max_bid = max(ep_max_bid, asking_price)

        # Hard cap at EP-proportional max and hard budget ceiling
        recommended_bid = min(recommended_bid, ep_max_bid, budget_ceiling)

        # Market value floor: always bid at least market_value * 1.01
        # Applied AFTER the ep_max cap but still within budget_ceiling.
        market_value_floor = int(market_value * 1.01)
        if recommended_bid < market_value_floor:
            recommended_bid = min(market_value_floor, budget_ceiling)

        # If we still can't afford the asking price (truly out of budget), signal no-bid
        if recommended_bid < asking_price:
            recommended_bid = 0
            overbid_amount = 0

        # Recalculate amounts and percentage from final recommended_bid
        overbid_amount = max(0, recommended_bid - asking_price)
        actual_overbid_pct = (overbid_amount / asking_price) * 100 if asking_price > 0 else 0.0

        reasoning_parts = [
            f"EP tier: {ep_tier} (+{marginal_ep_gain:.1f} pts)",
            f"overbid {actual_overbid_pct:.1f}%",
        ]
        if offer_count >= 2:
            reasoning_parts.append(f"contested ({offer_count} offers)")
        if sell_plan:
            reasoning_parts.append(f"sell plan: +€{sell_plan.total_recovery:,} recovery")
        reasoning = " | ".join(reasoning_parts)

        return BidRecommendation(
            base_price=asking_price,
            recommended_bid=recommended_bid,
            overbid_amount=overbid_amount,
            overbid_pct=actual_overbid_pct,
            reasoning=reasoning,
            budget_ceiling=budget_ceiling,
            sell_plan=sell_plan,
            marginal_ep_gain=marginal_ep_gain,
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
