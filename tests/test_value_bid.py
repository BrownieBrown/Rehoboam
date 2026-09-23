"""The expected-value premium, with the next-best alternative priced in."""

from __future__ import annotations

import pytest

from rehoboam.services.ceiling_derivation import WinnerRow
from rehoboam.services.value_bid import optimal_premium
from rehoboam.services.win_curve import WinCurve

BANDS = (0, 5_000_000, 15_000_000)
B = 255_000.0


def _curve():
    # 15m+: winners paid 4..15 evenly; 5-15m: 2..32.
    rows = [WinnerRow(20_000_000, 4.0 + i * (11 / 39)) for i in range(40)]
    rows += [WinnerRow(8_000_000, 2.0 + i * (30 / 39)) for i in range(40)]
    return WinCurve.from_rows(rows, bands=BANDS, min_sample=30)


class TestTheOptimum:
    def test_a_small_unique_gain_bids_near_the_floor(self):
        # 5.3 points x 255k = 1.35m of value against an 18m player.
        point = optimal_premium(
            _curve(), market_value=18_000_000, unique_gain_pts=5.3, eur_per_point=B
        )
        assert point is not None
        # On this curve nobody won below +4%, so the optimum sits just above it.
        assert point.premium_pct <= 7.0
        assert 0 < point.win_share < 0.5

    def test_a_large_unique_gain_bids_high_up_the_curve(self):
        point = optimal_premium(
            _curve(), market_value=18_000_000, unique_gain_pts=60.0, eur_per_point=B
        )
        assert point.premium_pct >= 12.0
        assert point.win_share >= 0.75

    def test_the_value_scales_with_the_gain_not_the_tier(self):
        small = optimal_premium(
            _curve(), market_value=18_000_000, unique_gain_pts=10.0, eur_per_point=B
        )
        large = optimal_premium(
            _curve(), market_value=18_000_000, unique_gain_pts=40.0, eur_per_point=B
        )
        assert large.premium_pct > small.premium_pct
        assert large.value_eur == pytest.approx(B * 40)

    def test_a_cheaper_player_is_worth_a_higher_premium_for_the_same_gain(self):
        dear = optimal_premium(
            _curve(), market_value=25_000_000, unique_gain_pts=20.0, eur_per_point=B
        )
        cheap = optimal_premium(
            _curve(), market_value=8_000_000, unique_gain_pts=20.0, eur_per_point=B
        )
        assert cheap.premium_pct > dear.premium_pct

    def test_the_expected_value_is_the_share_times_the_net(self):
        point = optimal_premium(
            _curve(), market_value=18_000_000, unique_gain_pts=20.0, eur_per_point=B
        )
        net = point.value_eur - point.premium_pct / 100 * 18_000_000
        assert point.expected_value_eur == pytest.approx(point.win_share * net)


class TestNoAnswer:
    def test_no_unique_gain_means_no_premium(self):
        assert (
            optimal_premium(_curve(), market_value=18_000_000, unique_gain_pts=0.0, eur_per_point=B)
            is None
        )

    def test_no_price_per_point_means_no_premium(self):
        assert (
            optimal_premium(
                _curve(), market_value=18_000_000, unique_gain_pts=20.0, eur_per_point=None
            )
            is None
        )

    def test_no_curve_evidence_means_no_premium(self):
        thin = WinCurve.from_rows([WinnerRow(20_000_000, 5.0)] * 3, bands=BANDS, min_sample=30)
        assert (
            optimal_premium(thin, market_value=18_000_000, unique_gain_pts=20.0, eur_per_point=B)
            is None
        )
