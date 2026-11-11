"""The bid (spec v3 §5.5, ADR-0004) and its value refinement (M2-15)."""

from __future__ import annotations

import pytest

from rehoboam.desk import bids

UNIFORM = [k / 100 for k in range(21)]  # rivals paid 0%, 1%, ..., 20%


def test_the_ceiling_is_the_band_median():
    assert bids.ceiling(UNIFORM) == pytest.approx(0.10)


def test_too_few_rival_buys_give_no_bid():
    assert bids.ceiling(UNIFORM[:19]) is None
    assert bids.value_premium(UNIFORM[:19], drift=0.5) is None


def test_no_expected_drift_bids_at_the_value():
    assert bids.value_premium(UNIFORM, drift=0.0) == 0.0


def test_a_modest_riser_bids_where_win_odds_times_margin_peak():
    # EV(p) = F(p)·(drift − p); at drift 10% it peaks just above the 4% rival.
    assert bids.value_premium(UNIFORM, drift=0.10) == pytest.approx(0.041)


def test_a_strong_riser_bids_to_win_up_to_the_cap():
    # At +50% expected, winning matters more than the premium, up to the p90 cap.
    assert bids.value_premium(UNIFORM, drift=0.50) == pytest.approx(0.181)


def test_the_value_bid_never_exceeds_the_cap():
    assert bids.value_premium(UNIFORM, drift=5.0) <= 0.18 + bids.OUTBID_STEP
