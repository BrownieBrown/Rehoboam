"""The trend discount must survive the learned overbid override (Itakura, 2026-09-22).

`calculate_ep_bid` builds its overbid in two halves. The stack — base + tier +
confidence + league + demand — is scaled down when the player's market value
is falling (x0.3 below -10%/7d, x0.5 below -5%, x0.75 below 0%). Then, when
`bid_learner.get_ep_recommended_overbid` returns a positive number, that number
REPLACES the stack — including the trend scaling that had just been applied.

Prod, session 9c0a6a742dba, 20:01 UTC:

    ep-bid learned-override player=3129 stack=7.5% learned=32.3% applied=32.3%
    ep-bid player=3129 tier=must_have ... bid=8752708 overbid=32.4% trend=-19.4

Ko Itakura's market value had fallen from 8,743,752 to 6,612,708 in a week.
The stack correctly cut its 25% to 7.5%. The override put 32.3% back and the
bot offered 8,752,708 for a grade-C defender scored on the position prior.
Marco cancelled it by hand.

The rule these tests pin: the trend factor applies to whichever base wins,
learned or stacked. Rising players keep the override untouched.
"""

from __future__ import annotations

import pytest

from rehoboam.bidding_strategy import SmartBidding
from rehoboam.config import Settings

POLICY = Settings(kickbase_email="test@example.com", kickbase_password="x").bid_ceiling_policy()

# The Itakura listing as the session saw it.
ITAKURA_MV = 6_612_708
ITAKURA_GAIN = 74.9
ITAKURA_TREND = -19.4
ITAKURA_LEARNED = 32.3


class _LearnerAskingFor:
    def __init__(self, pct: float):
        self.pct = pct

    def get_ep_recommended_overbid(self, **_kw):
        return {"recommended_overbid_pct": self.pct, "reason": "test override"}


def _bid(learned_pct: float, *, trend: float | None, market_value=ITAKURA_MV):
    bidder = SmartBidding(ceiling_policy=POLICY, bid_learner=_LearnerAskingFor(learned_pct))
    return bidder.calculate_ep_bid(
        asking_price=market_value,
        market_value=market_value,
        expected_points=ITAKURA_GAIN,
        marginal_ep_gain=ITAKURA_GAIN,
        confidence=0.7,
        current_budget=500_000_000,
        player_id="3129",
        trend_change_pct=trend,
        offer_count=0,
    )


class TestTheItakuraBid:
    def test_a_steep_fall_cuts_the_learned_overbid_to_a_third(self):
        rec = _bid(ITAKURA_LEARNED, trend=ITAKURA_TREND)
        assert rec.recommended_bid > 0
        # 32.3% x 0.3 = 9.7%; rounding to the bid increment keeps it under 10.
        assert rec.overbid_pct == pytest.approx(ITAKURA_LEARNED * 0.3, abs=0.2)

    def test_the_old_bid_is_no_longer_produced(self):
        rec = _bid(ITAKURA_LEARNED, trend=ITAKURA_TREND)
        assert rec.recommended_bid < 8_752_708

    def test_a_moderate_fall_halves_it(self):
        rec = _bid(ITAKURA_LEARNED, trend=-7.06)  # Querfeld's trend that night
        assert rec.overbid_pct == pytest.approx(ITAKURA_LEARNED * 0.5, abs=0.2)

    def test_a_mild_fall_takes_a_quarter_off(self):
        rec = _bid(ITAKURA_LEARNED, trend=-2.0)
        assert rec.overbid_pct == pytest.approx(ITAKURA_LEARNED * 0.75, abs=0.2)

    def test_an_unknown_trend_is_treated_conservatively_like_the_stack(self):
        rec = _bid(ITAKURA_LEARNED, trend=None)
        assert rec.overbid_pct == pytest.approx(ITAKURA_LEARNED * 0.6, abs=0.2)


class TestRisingPlayersAreUntouched:
    def test_a_rising_player_keeps_the_full_learned_overbid(self):
        rec = _bid(20.0, trend=2.14)  # Burger's trend that night
        assert rec.overbid_pct == pytest.approx(20.0, abs=0.2)

    def test_the_ceiling_still_caps_a_rising_player(self):
        rec = _bid(49.4, trend=2.14)  # Burger: learned 49.4%, must_have ceiling 35%
        assert rec.overbid_pct == pytest.approx(35.0, abs=0.2)

    def test_the_ceiling_is_applied_after_the_trend_not_before(self):
        # Trend first, then cap: 49.4 x 0.3 = 14.8, well under the 35% ceiling.
        # Cap first, then trend would give 35 x 0.3 = 10.5 — also under, but a
        # different number. The order is pinned so the two cannot drift apart.
        rec = _bid(49.4, trend=ITAKURA_TREND)
        assert rec.overbid_pct == pytest.approx(49.4 * 0.3, abs=0.2)
