"""The overbid ceiling takes the price as an input, not only the EP tier.

`BidCeilingPolicy` caps a bid by how much the player improves OUR eleven
(marginal, solid, strong, must_have → 8/15/25/35%). What it takes to WIN
depends on how expensive he is. Measured on `auction_outcomes` this season
(2026-09-21, 27 auctions, 5 won): at 15m+ we bid +24–26% over market value
and the winners paid a median of +9%; under 5m we bid +13–15% and winners
paid +59%. The axes are crossed — a must-have star gets +35% in a tier that
clears at +9%.

The price bands cap the tier percentage from above: the effective ceiling is
`min(tier_pct, band_pct)` for the band the market value falls in. A band can
only ever lower a ceiling; it never raises one, so the tiers keep their
discipline at the cheap end and the euro floor (Chiarodia) stays.

Bands are configured, not hard-coded: `derive-ceilings` proposes them from
the auction ledger and refuses to propose from a thin sample; the values go
into `OVERBID_PRICE_BANDS` by hand. Empty means off, which is the default.
"""

from __future__ import annotations

import pytest

from rehoboam.config import Settings
from rehoboam.services.bid_ceiling import (
    BidCeilingPolicy,
    Tier,
    max_allowed_bid,
    parse_price_bands,
)

FLOOR = 250_000
PCTS = {Tier.MARGINAL: 8.0, Tier.SOLID: 15.0, Tier.STRONG: 25.0, Tier.MUST_HAVE: 35.0}
BANDS = ((0, 60.0), (5_000_000, 35.0), (15_000_000, 20.0))


def _cap(mv, tier, bands=BANDS):
    return max_allowed_bid(
        market_value=mv, tier=tier, floor_eur=FLOOR, tier_pcts=PCTS, price_bands=bands
    )


class TestTheBandCapsTheTier:
    def test_burger_is_capped_by_his_price_band(self):
        # Must-have at 18,167,712: the tier says 35%, the 15m+ band says 20%.
        assert _cap(18_167_712, Tier.MUST_HAVE) == 18_167_712 + int(18_167_712 * 0.20)

    def test_a_band_never_raises_a_tier_ceiling(self):
        # Marginal at 4m: tier 8% (320,000, above the euro floor), band 60% —
        # the tier still binds.
        assert _cap(4_000_000, Tier.MARGINAL) == 4_000_000 + int(4_000_000 * 0.08)

    def test_the_band_boundary_is_inclusive(self):
        assert _cap(15_000_000, Tier.MUST_HAVE) == 15_000_000 + int(15_000_000 * 0.20)
        assert _cap(14_999_999, Tier.MUST_HAVE) == 14_999_999 + int(14_999_999 * 0.35)

    def test_the_euro_floor_survives(self):
        # Chiarodia: 591,389 — the floor beats both percentages.
        assert _cap(591_389, Tier.MARGINAL) == 591_389 + FLOOR

    def test_no_bands_means_the_tier_alone(self):
        assert _cap(18_167_712, Tier.MUST_HAVE, bands=()) == 18_167_712 + int(18_167_712 * 0.35)

    def test_a_value_below_the_first_band_is_uncapped_by_bands(self):
        bands = ((5_000_000, 35.0), (15_000_000, 20.0))
        assert _cap(3_000_000, Tier.MUST_HAVE, bands=bands) == 3_000_000 + int(3_000_000 * 0.35)


class TestParsing:
    def test_the_env_form(self):
        assert parse_price_bands("5000000:35,15000000:20") == (
            (5_000_000, 35.0),
            (15_000_000, 20.0),
        )

    def test_whitespace_and_order_do_not_matter(self):
        assert parse_price_bands(" 15000000:20 , 5000000:35 ") == (
            (5_000_000, 35.0),
            (15_000_000, 20.0),
        )

    def test_empty_means_off(self):
        assert parse_price_bands("") == ()
        assert parse_price_bands("   ") == ()

    @pytest.mark.parametrize("raw", ["5000000", "abc:20", "5000000:-1", "5000000:20,5000000:30"])
    def test_malformed_input_is_refused(self, raw):
        with pytest.raises(ValueError):
            parse_price_bands(raw)


class TestSettingsCarryTheBands:
    def _settings(self, bands):
        return Settings(
            kickbase_email="test@example.com", kickbase_password="x", overbid_price_bands=bands
        )

    def test_the_default_is_off(self):
        policy = self._settings("").bid_ceiling_policy()
        assert policy.price_bands == ()
        assert policy.max_bid(20_000_000, Tier.MUST_HAVE) == 27_000_000

    def test_a_configured_band_reaches_the_policy(self):
        policy = self._settings("15000000:20").bid_ceiling_policy()
        assert policy.max_bid(20_000_000, Tier.MUST_HAVE) == 24_000_000

    def test_a_malformed_setting_fails_at_startup(self):
        with pytest.raises(ValueError):
            self._settings("15000000").bid_ceiling_policy()


class TestThePolicyObject:
    def test_bands_default_to_none_for_existing_callers(self):
        policy = BidCeilingPolicy(floor_eur=FLOOR, tier_pcts=PCTS)
        assert policy.max_bid(18_167_712, Tier.MUST_HAVE) == 18_167_712 + int(18_167_712 * 0.35)
