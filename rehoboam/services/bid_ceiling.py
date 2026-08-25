"""How far above market value a bid may go. One definition, two callers.

`SmartBidding` sizes a bid and `safety_gate.check_buy` verifies one. Before
REH-99 they used different constants — 20%/30% in the bidder, 8% in the gate,
never connected — so Telegram offered buys the gate would refuse and the
Approve button could not work. Both now call `max_allowed_bid`, which is why
the number a proposal shows is always a number that can execute.

    max_allowed_bid = market_value + max(floor, market_value x tier_pct)

**The floor exists because a percentage is the wrong unit at the cheap end.**
Fabio Chiarodia, market value EUR 591,389, was lost to a winner paying
EUR 156,085 over while an 8% cap held the bot to EUR 47,311 — a EUR 109k gap
against a EUR 62M budget.

**The tier percentage exists because the round-trip toll is not universal.**
REH-64 measured a 12.2% toll and capped every buy at 8% on the strength of it,
but that toll is flip economics: it only bites if you sell. A must-have held to
score points all season has no round trip and amortises the premium over 30+
matchdays. A marginal churn candidate genuinely does pay it, and stays tight.

Across the 12 lost auctions carrying `winning_overbid_pct`, a flat 8% would
have won none; the lowest winning overbid was 8.4%.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum


class Tier(str, Enum):
    """Marginal-EP bands, mirroring the ones `SmartBidding` already assigns."""

    MARGINAL = "marginal"
    SOLID = "solid_upgrade"
    STRONG = "strong_upgrade"
    MUST_HAVE = "must_have"


#: Applied when the tier is unknown — a stale or corrupted proposal must not
#: buy itself a larger ceiling by losing its tier.
FALLBACK_TIER = Tier.MARGINAL


def tier_for_marginal_gain(
    marginal_ep_gain: float,
    *,
    must_have: float,
    strong: float,
    solid: float,
) -> Tier:
    """Band a marginal EP gain, using the thresholds `SmartBidding` is given.

    Thresholds are passed in rather than read from config so this stays pure
    and so the bidder and the gate cannot drift onto different bands.
    """
    if marginal_ep_gain >= must_have:
        return Tier.MUST_HAVE
    if marginal_ep_gain >= strong:
        return Tier.STRONG
    if marginal_ep_gain >= solid:
        return Tier.SOLID
    return Tier.MARGINAL


def max_allowed_bid(
    *,
    market_value: int,
    tier: Tier | str | None,
    floor_eur: int,
    tier_pcts: Mapping[Tier, float],
) -> int:
    """The highest bid permitted for this player, in euros.

    Returns 0 for a non-positive market value: `check_buy` reports that as its
    own failure, and this must not hand back a spendable ceiling computed from
    a nonsense input.
    """
    if market_value <= 0:
        return 0

    resolved = FALLBACK_TIER
    if tier is not None:
        try:
            resolved = Tier(tier)
        except ValueError:
            resolved = FALLBACK_TIER

    pct = tier_pcts.get(resolved, tier_pcts[FALLBACK_TIER])
    return market_value + max(floor_eur, int(market_value * pct / 100.0))


@dataclass(frozen=True)
class BidCeilingPolicy:
    """The configured ceiling, carried as one value instead of loose numbers.

    Built from `Settings.bid_ceiling_policy()` and handed to both
    `SmartBidding` and `safety_gate.check_buy`, so neither can be constructed
    with half the policy or a stale copy of it.
    """

    floor_eur: int
    tier_pcts: Mapping[Tier, float]

    def max_bid(self, market_value: int, tier: Tier | str | None) -> int:
        """The highest bid permitted for this player, in euros."""
        return max_allowed_bid(
            market_value=market_value,
            tier=tier,
            floor_eur=self.floor_eur,
            tier_pcts=self.tier_pcts,
        )
