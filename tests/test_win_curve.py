"""The win curve: what the league paid, read back as the premium a tier should bid."""

from __future__ import annotations

import pytest

from rehoboam.services.ceiling_derivation import WinnerRow
from rehoboam.services.win_curve import WinCurve

BANDS = (0, 5_000_000, 15_000_000)


def _rows(n_top=40, n_mid=40, n_low=40):
    rows = []
    rows += [
        WinnerRow(20_000_000, 4.0 + i * (11 / max(1, n_top - 1))) for i in range(n_top)
    ]  # 4..15
    rows += [
        WinnerRow(8_000_000, 2.0 + i * (30 / max(1, n_mid - 1))) for i in range(n_mid)
    ]  # 2..32
    rows += [
        WinnerRow(2_000_000, 0.0 + i * (100 / max(1, n_low - 1))) for i in range(n_low)
    ]  # 0..100
    return rows


class TestReadingTheCurve:
    def test_a_must_have_at_15m_reads_the_bands_p75(self):
        curve = WinCurve.from_rows(_rows(), bands=BANDS, min_sample=30)
        point = curve.premium_at(18_000_000, 0.75)
        assert point is not None
        assert point.premium_pct == pytest.approx(4.0 + 0.75 * 11, abs=0.01)
        assert point.band_lower == 15_000_000
        assert point.sample == 40
        assert point.pooled is False

    def test_each_band_has_its_own_curve(self):
        curve = WinCurve.from_rows(_rows(), bands=BANDS, min_sample=30)
        assert curve.premium_at(2_000_000, 0.5).premium_pct == pytest.approx(50.0, abs=0.01)
        assert curve.premium_at(8_000_000, 0.5).premium_pct == pytest.approx(17.0, abs=0.01)
        assert curve.premium_at(20_000_000, 0.5).premium_pct == pytest.approx(9.5, abs=0.01)

    def test_a_quantile_of_zero_is_the_cheapest_winning_bid_never_below_zero(self):
        rows = [WinnerRow(20_000_000, -3.0)] + [WinnerRow(20_000_000, float(i)) for i in range(40)]
        curve = WinCurve.from_rows(rows, bands=BANDS, min_sample=30)
        assert curve.premium_at(20_000_000, 0.0).premium_pct == 0.0

    def test_the_win_share_is_the_share_of_winners_at_or_below_the_premium(self):
        curve = WinCurve.from_rows(_rows(), bands=BANDS, min_sample=30)
        assert curve.win_share(20_000_000, 15.0) == pytest.approx(1.0)
        assert curve.win_share(20_000_000, 3.0) == pytest.approx(0.0)
        assert 0.4 < curve.win_share(20_000_000, 9.5) < 0.6


class TestThinEvidence:
    def test_a_thin_band_borrows_the_pooled_curve(self):
        curve = WinCurve.from_rows(_rows(n_top=5), bands=BANDS, min_sample=30)
        point = curve.premium_at(20_000_000, 0.5)
        assert point.pooled is True
        assert point.sample == 85
        assert point.band_lower == 15_000_000

    def test_too_little_evidence_says_nothing(self):
        curve = WinCurve.from_rows(_rows(5, 5, 5), bands=BANDS, min_sample=30)
        assert curve.premium_at(20_000_000, 0.5) is None
        assert curve.win_share(20_000_000, 10.0) is None

    def test_a_value_below_the_first_band_says_nothing(self):
        curve = WinCurve.from_rows(_rows(), bands=(5_000_000, 15_000_000), min_sample=30)
        assert curve.premium_at(1_000_000, 0.5) is None

    def test_no_bands_is_refused(self):
        with pytest.raises(ValueError):
            WinCurve(bands=(), premiums_by_band={}, min_sample=30)

    def test_total_counts_every_priced_buy_in_a_band(self):
        curve = WinCurve.from_rows(_rows(), bands=BANDS, min_sample=30)
        assert curve.total == 120
        assert curve.sample(20_000_000) == 40
