"""The single source of truth for how far above market value a bid may go (REH-99).

Two caps used to exist independently: `Settings.max_overbid_pct = 8.0`, enforced
only in `safety_gate.check_buy`, and `SmartBidding.max_overbid_pct = 20.0`
(elite 30.0), which actually sized the bid. `trader.py` never passed the former
to the latter, so Telegram offered buys the gate would refuse — the Approve
button could not work. This module exists so the number shown and the number
allowed are computed once, by the same code.

The shape is `market_value + max(floor, market_value x tier_pct)`, and both
halves are load-bearing:

- **The absolute floor** fixes the cheap end. Fabio Chiarodia was lost at a
  market value of EUR 591,389 to a winner paying EUR 156,085 over — while an 8%
  cap stopped the bot at EUR 47,311. A EUR 109k gap against a EUR 62M budget.
  A percentage is simply the wrong unit down there.
- **The tier percentage** keeps discipline where it belongs. A marginal churn
  candidate pays the 12.2% round-trip toll and should not be chased; a
  must-have is held to score points all season, has no round trip, and
  amortises the premium over 30+ matchdays.

Measured against the 12 lost auctions carrying `winning_overbid_pct`, a flat 8%
would have won **none** of them; the lowest winning overbid was 8.4%.
"""

import pytest

from rehoboam.services.bid_ceiling import Tier, max_allowed_bid, tier_for_marginal_gain

# Defaults from Settings, restated so a config change cannot silently
# invalidate the cases these numbers were chosen against.
FLOOR = 250_000
PCTS = {
    Tier.MARGINAL: 8.0,
    Tier.SOLID: 15.0,
    Tier.STRONG: 25.0,
    Tier.MUST_HAVE: 35.0,
}


def _ceiling(mv: int, tier: Tier) -> int:
    return max_allowed_bid(market_value=mv, tier=tier, floor_eur=FLOOR, tier_pcts=PCTS)


class TestTheAbsoluteFloorCoversCheapPlayers:
    def test_chiarodia_would_now_be_winnable(self):
        """EUR 591,389 market value, lost to a winner paying EUR 156,085 over."""
        assert _ceiling(591_389, Tier.MARGINAL) >= 591_389 + 156_085

    def test_the_floor_beats_the_percentage_on_a_cheap_player(self):
        mv = 591_389
        assert _ceiling(mv, Tier.MARGINAL) == mv + FLOOR

    def test_even_the_lowest_tier_gets_the_floor(self):
        """The floor is about the unit being wrong, not about player quality."""
        assert _ceiling(100_000, Tier.MARGINAL) == 100_000 + FLOOR


class TestThePercentageTakesOverOnExpensivePlayers:
    def test_the_percentage_beats_the_floor_once_it_is_larger(self):
        mv = 20_000_000
        assert _ceiling(mv, Tier.MARGINAL) == mv + int(mv * 0.08)

    def test_a_must_have_may_go_further_than_a_marginal(self):
        mv = 20_000_000
        assert _ceiling(mv, Tier.MUST_HAVE) > _ceiling(mv, Tier.MARGINAL)

    @pytest.mark.parametrize(
        "name,mv,winner_extra,tier",
        [
            ("Suzuki", 7_329_934, 750_874, Tier.SOLID),
            ("Bitshiabu", 5_466_049, 1_756_173, Tier.MUST_HAVE),
            ("Vandevoordt", 9_508_586, 2_612_625, Tier.MUST_HAVE),
            ("Honorat", 11_616_331, 3_939_228, Tier.MUST_HAVE),
            ("Lemperle", 15_017_565, 4_982_433, Tier.MUST_HAVE),
        ],
    )
    def test_real_lost_auctions_become_winnable_at_the_right_tier(
        self, name, mv, winner_extra, tier
    ):
        """Every one of these was lost under the 8% cap."""
        assert _ceiling(mv, tier) >= mv + winner_extra, name

    def test_the_runaway_cheap_auctions_stay_out_of_reach(self):
        """Inacio went at +385% of a EUR 5,028,049 market value.

        That is the league overpaying, not the bot being outbid. The ceiling
        must not chase it at any tier.
        """
        assert _ceiling(5_028_049, Tier.MUST_HAVE) < 5_028_049 + 19_361_516


class TestMonotonicity:
    def test_ceiling_never_decreases_as_tier_improves(self):
        mv = 10_000_000
        ceilings = [
            _ceiling(mv, t) for t in (Tier.MARGINAL, Tier.SOLID, Tier.STRONG, Tier.MUST_HAVE)
        ]
        assert ceilings == sorted(ceilings)

    def test_ceiling_always_exceeds_market_value(self):
        for mv in (1, 500_000, 5_000_000, 50_000_000):
            assert _ceiling(mv, Tier.MARGINAL) > mv


class TestGuards:
    @pytest.mark.parametrize("mv", [0, -1, -5_000_000])
    def test_a_non_positive_market_value_allows_nothing(self, mv):
        """`check_buy` reports invalid market value separately; this must not
        hand back a spendable ceiling built on a nonsense input."""
        assert _ceiling(mv, Tier.MUST_HAVE) == 0

    def test_an_unknown_tier_falls_back_to_the_tightest(self):
        """A stale or corrupted proposal must not buy itself a bigger ceiling."""
        got = max_allowed_bid(market_value=20_000_000, tier=None, floor_eur=FLOOR, tier_pcts=PCTS)
        assert got == _ceiling(20_000_000, Tier.MARGINAL)


class TestTierFromMarginalGain:
    """The tier must come from the same bands SmartBidding already uses."""

    @pytest.mark.parametrize(
        "gain,expected",
        [
            (100.0, Tier.MUST_HAVE),
            (62.5, Tier.MUST_HAVE),
            (50.0, Tier.STRONG),
            (37.5, Tier.STRONG),
            (30.0, Tier.SOLID),
            (25.0, Tier.SOLID),
            (10.0, Tier.MARGINAL),
            (0.0, Tier.MARGINAL),
            (-5.0, Tier.MARGINAL),
        ],
    )
    def test_bands_match_the_configured_thresholds(self, gain, expected):
        assert tier_for_marginal_gain(gain, must_have=62.5, strong=37.5, solid=25.0) == expected
