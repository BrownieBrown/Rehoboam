"""Shared fixtures.

`permissive_gate` exists so tests that are about something *else* — the
kickoff-lockout guard, emergency fill ordering — can satisfy
`ExecutionService.buy`'s required gate without restating seven fields each
time. Tests that are about the gate build their own, so a permissive default
can never accidentally be what makes a gate assertion pass.
"""

from __future__ import annotations

import pytest

from rehoboam.services.bid_ceiling import BidCeilingPolicy, Tier
from rehoboam.services.safety_gate import BuyGate

#: Settings defaults, restated so a config change cannot silently retune tests.
CEILING_POLICY = BidCeilingPolicy(
    floor_eur=250_000,
    tier_pcts={
        Tier.MARGINAL: 8.0,
        Tier.SOLID: 15.0,
        Tier.STRONG: 25.0,
        Tier.MUST_HAVE: 35.0,
    },
)


def permissive_buy_gate(player_id: str = "p1", **overrides) -> BuyGate:
    """A gate that permits any realistic test bid for `player_id`.

    The market value and allowance are deliberately enormous so no assertion
    in an unrelated test ever turns on the ceiling or the budget rule.
    """
    fields: dict = {
        "market_value": 1_000_000_000,
        "spendable_budget": 1_000_000_000,
        "known_player_ids": (player_id,),
        "free_slots": 1,
        "tier": None,
        "ceiling_policy": CEILING_POLICY,
    }
    fields.update(overrides)
    return BuyGate(**fields)


@pytest.fixture
def permissive_gate() -> BuyGate:
    return permissive_buy_gate()
