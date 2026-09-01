"""One ceiling, not two — the bidder defers to the policy (REH-116).

REH-99 introduced `BidCeilingPolicy` so `SmartBidding` and `safety_gate` could
not disagree about how far above market value a bid may go. `SmartBidding`
took the policy, and kept its own caps alongside it:

    max_overbid = self.elite_max_overbid_pct if ep_tier == "must_have" else self.max_overbid_pct
    overbid_pct = min(overbid_pct, max_overbid)

`elite_max_overbid_pct` defaults to 30.0 while the policy's `must_have`
ceiling is 35.0, and the private cap binds first. It cost a player:

    Nusa, 2026-08-31.  MV 26,183,029.  Learned model asked 40.8%,
    the private cap applied 30.0% -> we bid 34,033,029.
    stefan_m paid 34,683,029 (+32.5%) and took him.
    The policy ceiling is 35,347,089 — we would have won by 664,060.

That is the sixth instance of "two components each holding a private copy of
one rule" in this repo (REH-99, 100, 101, 107, 111). The cap is not a second
safety net; it is a lower ceiling nobody knew was binding.

The floor matters too: the policy is `mv + max(floor_eur, mv x pct)`, so at
the cheap end it permits MORE than a flat percentage, which is the Chiarodia
case REH-99 documented. A percentage-only cap silently re-imposes the bug.
"""

from __future__ import annotations

import pytest

from rehoboam.bidding_strategy import SmartBidding
from rehoboam.config import Settings

SETTINGS = Settings(kickbase_email="test@example.com", kickbase_password="x")
POLICY = SETTINGS.bid_ceiling_policy()

# The real Nusa listing.
NUSA_MV = 26_183_029
NUSA_WINNER_PAID = 34_683_029


class _LearnerAskingFor:
    """A bid_learner that recommends `pct` — the real Nusa override was 40.8%.

    Prod logged `stack=13.8% learned=40.8% applied=30.0%`: the learned model
    asked for more than the private cap allowed, so the cap is what actually
    priced the bid. Reproducing that needs the override path, not the stack.
    """

    def __init__(self, pct: float):
        self.pct = pct

    def get_ep_recommended_overbid(self, **_kw):
        return {"recommended_overbid_pct": self.pct, "reason": "test override"}


def _bidder(**kw):
    return SmartBidding(ceiling_policy=POLICY, **kw)


def _bid(bidder, *, market_value, marginal_ep_gain, budget=500_000_000):
    return bidder.calculate_ep_bid(
        asking_price=market_value,
        market_value=market_value,
        expected_points=marginal_ep_gain,
        marginal_ep_gain=marginal_ep_gain,
        confidence=0.9,
        current_budget=budget,
        player_id="9507",
        trend_change_pct=1.4,
        offer_count=2,
    )


class TestTheNusaAuction:
    def test_a_must_have_may_be_bid_above_the_old_thirty_percent_cap(self):
        """The private cap is gone; the policy's 35% governs.

        Driven through the learned override, because that is what prod did:
        `stack=13.8% learned=40.8% applied=30.0%`.
        """
        rec = _bid(
            _bidder(bid_learner=_LearnerAskingFor(40.8)),
            market_value=NUSA_MV,
            marginal_ep_gain=86.1,
        )

        ceiling = POLICY.max_bid(NUSA_MV, "must_have")
        assert rec.recommended_bid <= ceiling
        assert rec.recommended_bid > int(
            NUSA_MV * 1.30
        ), "the 30% private cap must no longer bind a must_have"

    def test_the_policy_ceiling_would_have_won_nusa(self):
        """Not a claim about this bid — about the headroom that existed."""
        assert POLICY.max_bid(NUSA_MV, "must_have") > NUSA_WINNER_PAID


class TestTheCeilingIsNeverExceeded:
    @pytest.mark.parametrize(
        ("gain", "tier"),
        [(5.0, "marginal"), (30.0, "solid_upgrade"), (45.0, "strong_upgrade"), (95.0, "must_have")],
    )
    @pytest.mark.parametrize("mv", [591_389, 5_466_049, 26_183_029, 44_220_901])
    def test_every_tier_stays_inside_its_policy_ceiling(self, gain, tier, mv):
        rec = _bid(_bidder(), market_value=mv, marginal_ep_gain=gain)

        assert rec.recommended_bid <= POLICY.max_bid(mv, tier) or rec.recommended_bid == 0


class TestTheCheapEndKeepsItsFloor:
    def test_a_small_market_value_may_exceed_a_flat_percentage(self):
        """`mv + max(floor, mv x pct)` — the Chiarodia case REH-99 fixed.

        A percentage-only cap would hold a EUR 591,389 player to a few tens of
        thousands over, which is how that auction was lost by EUR 109k.
        """
        mv = 591_389

        ceiling = POLICY.max_bid(mv, "marginal")

        assert ceiling - mv >= POLICY.floor_eur


class TestWithoutAPolicy:
    def test_a_bidder_with_no_policy_still_produces_a_bounded_bid(self):
        """Nothing constructs one without a policy in production, but a bid
        with no ceiling at all would be the worst possible failure mode."""
        rec = _bid(SmartBidding(), market_value=NUSA_MV, marginal_ep_gain=86.1)

        assert 0 <= rec.recommended_bid <= NUSA_MV * 2
