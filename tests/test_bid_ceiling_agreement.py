"""The bidder and the gate must never disagree about the ceiling (REH-99).

This is the regression test for the reported bug. `SmartBidding` sized bids
with its own 20%/30% caps while `safety_gate.check_buy` enforced a 8% one, and
`trader.py` never connected them — so a proposal could be rendered into
Telegram at +16% and then refused by the gate when the user tapped Approve.
The Approve button could not work, and the proposal was marked `failed`.

The property asserted here is the one that matters: **anything the bidder is
willing to propose, the gate is willing to execute.** It is checked across the
market-value range where the two old constants disagreed, at every tier.
"""

import pytest

from rehoboam.config import Settings
from rehoboam.services.bid_ceiling import BidCeilingPolicy, Tier, max_allowed_bid
from rehoboam.services.safety_gate import check_buy


def _policy() -> BidCeilingPolicy:
    return Settings(kickbase_email="test@example.com", kickbase_password="x").bid_ceiling_policy()


MARKET_VALUES = [
    591_389,  # Chiarodia — the cheap end, where the floor governs
    2_205_708,
    5_466_049,
    12_543_020,  # Matsima — the listing that reproduced the bug
    34_217_295,  # Raum
    44_220_901,  # Undav, the most expensive live listing
]


class TestThePolicyMatchesTheBareFunction:
    @pytest.mark.parametrize("mv", MARKET_VALUES)
    @pytest.mark.parametrize("tier", list(Tier))
    def test_policy_delegates_to_one_definition(self, mv, tier):
        policy = _policy()
        assert policy.max_bid(mv, tier) == max_allowed_bid(
            market_value=mv,
            tier=tier,
            floor_eur=policy.floor_eur,
            tier_pcts=policy.tier_pcts,
        )


class TestTheGateAcceptsWhatTheCeilingAllows:
    @pytest.mark.parametrize("mv", MARKET_VALUES)
    @pytest.mark.parametrize("tier", list(Tier))
    def test_a_bid_at_the_ceiling_passes(self, mv, tier):
        policy = _policy()
        bid = policy.max_bid(mv, tier)
        result = check_buy(
            player_id="1",
            bid=bid,
            market_value=mv,
            current_budget=200_000_000,
            free_slots=3,
            known_player_ids=["1"],
            tier=tier,
            ceiling_policy=policy,
        )
        assert result.ok, result.reasons

    @pytest.mark.parametrize("mv", MARKET_VALUES)
    @pytest.mark.parametrize("tier", list(Tier))
    def test_one_euro_above_the_ceiling_is_refused(self, mv, tier):
        policy = _policy()
        result = check_buy(
            player_id="1",
            bid=policy.max_bid(mv, tier) + 1,
            market_value=mv,
            current_budget=200_000_000,
            free_slots=3,
            known_player_ids=["1"],
            tier=tier,
            ceiling_policy=policy,
        )
        assert not result.ok
        assert any("exceeds" in r for r in result.reasons)


class TestTheReportedBugCannotRecur:
    def test_the_matsima_bid_is_capped_to_something_the_gate_accepts(self):
        """The live reproduction. Market value EUR 12,543,020, +32.1 marginal
        gain, so a solid upgrade.

        The uncapped bidder proposed EUR 14,553,020 (+16.0%) and the old flat
        8% gate refused it. The fix is not that the gate now waves that number
        through — it is that the bidder is held to the same ceiling the gate
        enforces, so what gets proposed is executable.
        """
        policy = _policy()
        capped = policy.max_bid(12_543_020, Tier.SOLID)
        assert capped < 14_553_020, "the old uncapped bid should be trimmed"

        result = check_buy(
            player_id="9642",
            bid=capped,
            market_value=12_543_020,
            current_budget=62_257_522,
            free_slots=4,
            known_player_ids=["9642"],
            tier=Tier.SOLID,
            ceiling_policy=policy,
        )
        assert result.ok, result.reasons

    def test_the_old_uncapped_bid_is_still_refused(self):
        """The gate must not become toothless in the process."""
        policy = _policy()
        result = check_buy(
            player_id="9642",
            bid=14_553_020,
            market_value=12_543_020,
            current_budget=62_257_522,
            free_slots=4,
            known_player_ids=["9642"],
            tier=Tier.SOLID,
            ceiling_policy=policy,
        )
        assert not result.ok

    def test_a_missing_tier_still_refuses_a_wild_bid(self):
        """A corrupted proposal falls back to the tightest tier, not the widest."""
        policy = _policy()
        result = check_buy(
            player_id="1",
            bid=100_000_000,
            market_value=12_543_020,
            current_budget=200_000_000,
            free_slots=3,
            known_player_ids=["1"],
            tier=None,
            ceiling_policy=policy,
        )
        assert not result.ok
