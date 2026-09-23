"""The profit curve: where paying more stops paying back, per price band."""

from __future__ import annotations

import pytest

from rehoboam.services.profit_curve import OutcomeRow, ProfitCurve

BANDS = (0, 5_000_000, 15_000_000)


def _rows():
    rows = []
    # 15m+: every bucket from +3% on loses money; under +3% breaks even.
    for i in range(12):
        rows.append(OutcomeRow(20_000_000, 1.0, 0.5))
        rows.append(OutcomeRow(20_000_000, 4.0 + i * 0.1, -5.0))
        rows.append(OutcomeRow(20_000_000, 7.0, -8.0))
    # 5-15m: pays back up to +12%, negative from there.
    for _ in range(12):
        for lower, profit in (
            (0.0, 3.0),
            (3.0, 5.0),
            (6.0, 2.0),
            (9.0, 0.5),
            (12.0, -6.0),
            (15.0, -4.0),
        ):
            rows.append(OutcomeRow(8_000_000, lower + 1.0, profit))
    # under 5m: profit climbs with the premium, never negative.
    for _ in range(12):
        for lower, profit in ((0.0, 0.0), (9.0, 22.0), (21.0, 47.0)):
            rows.append(OutcomeRow(2_000_000, lower + 1.0, profit))
    return rows


class TestBreakEven:
    def test_above_15m_the_crossing_is_the_first_bucket_past_the_floor(self):
        be = ProfitCurve.from_rows(_rows(), bands=BANDS).break_even(20_000_000)
        assert be is not None
        assert be.premium_pct == pytest.approx(3.0)
        assert be.band_lower == 15_000_000
        assert be.resale_profit_pct_at == pytest.approx(-5.0)

    def test_between_5m_and_15m_the_crossing_is_twelve(self):
        be = ProfitCurve.from_rows(_rows(), bands=BANDS).break_even(8_000_000)
        assert be.premium_pct == pytest.approx(12.0)

    def test_under_5m_there_is_no_crossing(self):
        assert ProfitCurve.from_rows(_rows(), bands=BANDS).break_even(2_000_000) is None

    def test_a_thin_negative_bucket_does_not_declare_the_crossing(self):
        rows = [OutcomeRow(20_000_000, 1.0, 1.0)] * 12 + [OutcomeRow(20_000_000, 4.0, -9.0)] * 3
        assert (
            ProfitCurve.from_rows(rows, bands=BANDS, min_sample=10).break_even(20_000_000) is None
        )

    def test_unsold_buys_carry_no_evidence(self):
        rows = [OutcomeRow(20_000_000, 4.0, None)] * 40
        assert ProfitCurve.from_rows(rows, bands=BANDS).break_even(20_000_000) is None

    def test_a_value_below_the_first_band_has_no_curve(self):
        curve = ProfitCurve.from_rows(_rows(), bands=(5_000_000, 15_000_000))
        assert curve.break_even(1_000_000) is None


class TestExpectedResale:
    def test_reads_the_bucket_the_premium_falls_in(self):
        curve = ProfitCurve.from_rows(_rows(), bands=BANDS)
        assert curve.expected_resale_pct(20_000_000, 8.0) == pytest.approx(-8.0)
        assert curve.expected_resale_pct(2_000_000, 22.0) == pytest.approx(47.0)

    def test_an_empty_or_thin_bucket_says_nothing(self):
        curve = ProfitCurve.from_rows(_rows(), bands=BANDS)
        assert curve.expected_resale_pct(20_000_000, 30.0) is None
        assert curve.expected_resale_pct(20_000_000, -1.0) is None
