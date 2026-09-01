"""Smart bidding strategy to win player auctions while maintaining profitability"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .scoring.models import SellPlan

from .services.pacing import PacingContext

try:
    from .activity_feed_learner import ActivityFeedLearner
    from .bid_learner import BidLearner
except ImportError:
    BidLearner = None
    ActivityFeedLearner = None

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Marginal-gain tiers
# ---------------------------------------------------------------------------
#
# On the v2 scale: REAL Kickbase matchday points, not the old 0-100 index.
# Measured 2026-07-31 over the full 473-player universe against a synthetic
# mid-table 15-man squad — p85 69.3 / p70 52.6 / p50 43.1. Pre-season
# estimates; `derive-thresholds` could not measure the live market because no
# purchasable listings exist before 2026-08-28.
#
# These are the DEFAULTS. `SmartBidding` takes them as constructor arguments so
# `Settings.bid_tier_*` (readable from `.env`) can override them mid-season
# without a new build — see `trader.py` for the live wiring. The old inline
# values were 20 / 10 / 5, which on the real-points scale sit *below* the
# median gain and so classified almost every candidate as a must-have.
TIER_MUST_HAVE = 62.5
TIER_STRONG_UPGRADE = 37.5
TIER_SOLID_UPGRADE = 25.0

# How much of the budget a signing may consume, as a function of marginal gain.
#
# REH-69: the previous rule was inline arithmetic,
# ``min(0.8, 0.2 + marginal_ep_gain / 50)``, calibrated for the 0-100 index
# where it ramped 0.30 -> 0.70 across the gains the bot actually saw. On real
# points EVERY gain clearing the shipped floor of 40 saturates at 0.80, so a
# +43 signing and a +195 superstar were sized identically — the bot would
# commit 80% of budget to the first qualifying candidate and be unable to
# afford the one that mattered next week.
#
# The ramp now spans the measured operating range: it starts at the
# solid-upgrade tier (p50 = 43.1, the weakest candidate worth recommending)
# and reaches full commitment at p95. Anchoring the top at p95 rather than the
# observed maximum (176.2) keeps the gradient discriminating across the bulk of
# real candidates instead of being stretched flat by one outlier.
BID_FRACTION_MIN = 0.2
BID_FRACTION_MAX = 0.8
BID_FULL_COMMIT_GAIN = 82.0


def max_bid_fraction(
    marginal_ep_gain: float,
    *,
    ramp_start: float = TIER_SOLID_UPGRADE,
    full_commit_gain: float = BID_FULL_COMMIT_GAIN,
) -> float:
    """Fraction of the budget ceiling this gain justifies committing.

    Linear between ``ramp_start`` and ``full_commit_gain``, clamped to
    ``[BID_FRACTION_MIN, BID_FRACTION_MAX]`` outside that range. Committing
    0.8 is the whole war chest, so it should take a gain near the top of the
    measured distribution, not merely clearing the median.
    """
    span = full_commit_gain - ramp_start
    if span <= 0:  # misconfigured — fail toward caution, not toward spending
        return BID_FRACTION_MIN
    progress = (marginal_ep_gain - ramp_start) / span
    progress = max(0.0, min(1.0, progress))
    return BID_FRACTION_MIN + progress * (BID_FRACTION_MAX - BID_FRACTION_MIN)


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
        tier_must_have: float = TIER_MUST_HAVE,  # Marginal EP gain bands, real points
        tier_strong_upgrade: float = TIER_STRONG_UPGRADE,
        tier_solid_upgrade: float = TIER_SOLID_UPGRADE,
        full_commit_gain: float = BID_FULL_COMMIT_GAIN,  # gain earning max budget share
        ceiling_policy=None,  # REH-99: the ceiling the safety gate enforces
    ):
        self.ceiling_policy = ceiling_policy
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
        self.tier_must_have = tier_must_have
        self.tier_strong_upgrade = tier_strong_upgrade
        self.tier_solid_upgrade = tier_solid_upgrade
        self.full_commit_gain = full_commit_gain

    def _max_overbid_pct(self, ep_tier: str, market_value: int) -> float:
        """The overbid ceiling for this tier, as a percentage of market value.

        REH-116. This used to be `elite_max_overbid_pct` (30.0) for must_have
        and `max_overbid_pct` (20.0) otherwise — private constants sitting
        alongside the `BidCeilingPolicy` that REH-99 introduced precisely so
        the bidder and the gate could not disagree. The private ones bound
        first, and were lower, so the policy never governed anything.

        It cost Nusa on 2026-08-31: the learned model asked for 40.8%, the
        private cap applied 30.0%, we bid 34,033,029, and stefan_m took him for
        34,683,029. The policy ceiling was 35,347,089.

        Derived as a percentage rather than compared in euros because the
        policy is `mv + max(floor_eur, mv x pct)` — at the cheap end the floor
        governs and permits MORE than the percentage, which is the Chiarodia
        case. Converting keeps that headroom instead of flattening it away.

        The constructor constants survive only as the no-policy fallback:
        production always passes one, and a bidder with no ceiling at all is a
        worse failure than a slightly wrong one.
        """
        if self.ceiling_policy is None or market_value <= 0:
            return self.elite_max_overbid_pct if ep_tier == "must_have" else self.max_overbid_pct
        ceiling = self.ceiling_policy.max_bid(int(market_value), ep_tier)
        return max(0.0, (ceiling - market_value) / market_value * 100.0)

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
        pacing: PacingContext | None = None,
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
            pacing: REH-85 capital pacing. When given, caps the bid so the
                reserve survives it — the budget needed to make the moves the
                squad still requires. None disables pacing entirely.

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

        # Tier classification based on marginal EP gain (real points — see the
        # TIER_* constants for the measured distribution these come from)
        if marginal_ep_gain >= self.tier_must_have:
            ep_tier = "must_have"
            tier_bonus = 10.0
        elif marginal_ep_gain >= self.tier_strong_upgrade:
            ep_tier = "strong_upgrade"
            tier_bonus = 6.0
        elif marginal_ep_gain >= self.tier_solid_upgrade:
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

        # EP-proportional max bid: larger EP gain justifies spending more of the
        # budget. See max_bid_fraction for why this is no longer inline (REH-69).
        bid_fraction = max_bid_fraction(
            marginal_ep_gain,
            ramp_start=self.tier_solid_upgrade,
            full_commit_gain=self.full_commit_gain,
        )
        ep_max_bid = int(budget_ceiling * bid_fraction)

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

        # The ceiling comes from the ONE policy the safety gate enforces.
        max_overbid = self._max_overbid_pct(ep_tier, market_value)
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
        # affordable player just because their price exceeds the fraction
        # threshold — and include the premium, or the floor silently strips it.
        #
        # Kimmich, 2026-08-24: gain +88.2 cleared BID_FULL_COMMIT_GAIN so the
        # ramp returned its maximum 0.8, but 0.8 x EUR 62.2M = EUR 49.8M sits
        # BELOW his EUR 59.8M asking price. Flooring at the asking price alone
        # left no overbid room at all, so the only thing lifting the bid was the
        # market-value floor: our single most-wanted player, tier must_have, got
        # a 1% bid that any rival beats with 2%. The more a player is worth to
        # us relative to the budget, the weaker the bid became — the reverse of
        # what the tiers exist to express.
        #
        # The fraction decides how much of the war chest a gain justifies, and
        # `budget_ceiling` below still caps the total. It should not also decide
        # whether we may pay a premium on a player we have already committed to.
        ep_max_bid = max(ep_max_bid, asking_price + overbid_amount)

        # Hard cap at EP-proportional max and hard budget ceiling
        recommended_bid = min(recommended_bid, ep_max_bid, budget_ceiling)

        # Market value floor: always bid at least market_value * 1.01
        # Applied AFTER the ep_max cap but still within budget_ceiling.
        market_value_floor = int(market_value * 1.01)
        if recommended_bid < market_value_floor:
            recommended_bid = min(market_value_floor, budget_ceiling)

        # REH-99: the ceiling the safety gate will enforce. Applied last so
        # nothing downstream can lift the bid back over it — this is what makes
        # a proposal shown in Telegram a proposal that can actually execute.
        if self.ceiling_policy is not None and market_value > 0:
            recommended_bid = min(
                recommended_bid, self.ceiling_policy.max_bid(market_value, ep_tier)
            )

        # REH-85: leave enough behind to keep buying. Applied here, after the
        # REH-99 ceiling and the market-value floor above, because that floor
        # unconditionally raises recommended_bid to min(market_value * 1.01,
        # budget_ceiling) whenever it falls short — a cap applied before it
        # would simply be lifted back off.
        #
        # This caps rather than refuses. Where the cap falls below the asking
        # price the block below turns it into recommended_bid = 0, so pacing
        # needs no refusal path competing with the ceiling and the safety gate.
        if pacing is not None:
            pace_cap = pacing.max_bid(budget_ceiling, current_budget)
            if recommended_bid > pace_cap:
                logger.info(
                    "ep-bid paced player=%s bid=%d -> %d (reserve=%d open_offers=%d)",
                    player_id,
                    recommended_bid,
                    pace_cap,
                    pacing.reserve,
                    pacing.open_offers,
                )
                recommended_bid = pace_cap

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
