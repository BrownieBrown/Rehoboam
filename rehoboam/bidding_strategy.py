"""Smart bidding strategy to win player auctions while maintaining profitability"""

from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)


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
        is_dgw: bool = False,
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
            is_dgw: True when the player has a double gameweek (2 matches in one
                matchday). Averaging across two matches reduces outcome variance,
                so we have higher confidence in the EP prediction — the bid
                confidence is floored at 0.9 to reflect that certainty.

        Returns:
            BidRecommendation — recommended_bid=0 if no improvement warranted
        """
        # DGW players have less variance (2 matches = averaged outcome), so
        # we're more confident in the EP estimate regardless of data quality.
        if is_dgw:
            confidence = max(confidence, 0.9)
        # No improvement → no bid
        if marginal_ep_gain == 0:
            logger.debug(
                "ep-bid SKIP player=%s: marginal_ep_gain=0",
                player_id,
            )
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
            logger.info(
                "ep-bid SKIP player=%s tier=%s offers=%d aggressive=%s | %s",
                player_id,
                ep_tier,
                offer_count,
                has_aggressive_competitors,
                contested_skip_reason,
            )
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

        # Try EP-specific learned overbid if available.
        #
        # The previous call here passed kwargs that didn't match the method
        # signature and treated the dict return as a number, so every bid
        # since the method was added went through with the EP-bid learner
        # silently disabled by the surrounding `except Exception`. Result:
        # `auction_outcomes` data accumulated but never influenced bids
        # (REH-30).
        if self.bid_learner:
            try:
                learned = self.bid_learner.get_ep_recommended_overbid(
                    asking_price=asking_price,
                    marginal_ep_gain=marginal_ep_gain,
                    market_value=market_value,
                    budget_ceiling=budget_ceiling,
                )
                learned_pct = learned.get("recommended_overbid_pct", 0.0)
                if learned_pct > 0:
                    stack_pct = overbid_pct
                    overbid_pct = min(learned_pct, max_overbid)
                    logger.info(
                        "ep-bid learned-override player=%s stack=%.1f%% "
                        "learned=%.1f%% applied=%.1f%% | %s",
                        player_id,
                        stack_pct,
                        learned_pct,
                        overbid_pct,
                        learned.get("reason", ""),
                    )
            except Exception:
                logger.exception(
                    "ep-bid learned-override failed for player=%s — using stack default",
                    player_id,
                )

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
        if is_dgw:
            reasoning_parts.append("DGW")
        if offer_count >= 2:
            reasoning_parts.append(f"contested ({offer_count} offers)")
        if sell_plan:
            reasoning_parts.append(f"sell plan: +€{sell_plan.total_recovery:,} recovery")
        reasoning = " | ".join(reasoning_parts)

        logger.info(
            "ep-bid player=%s tier=%s ep_gain=%+.1f conf=%.2f "
            "ask=%d mv=%d bid=%d overbid=%.1f%% "
            "trend=%s offers=%d dgw=%s demand_adj=%+.1f league_comp=%+.1f "
            "ceiling=%d sell_plan=%s | %s",
            player_id,
            ep_tier,
            marginal_ep_gain,
            confidence,
            asking_price,
            market_value,
            recommended_bid,
            actual_overbid_pct,
            trend_change_pct,
            offer_count,
            is_dgw,
            demand_adjustment,
            league_competitive_level,
            budget_ceiling,
            "yes" if sell_plan else "no",
            reasoning,
        )

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
