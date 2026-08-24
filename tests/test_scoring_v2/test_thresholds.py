"""Tests for rehoboam.scoring.v2.thresholds — rarity-based threshold derivation."""

from __future__ import annotations

import pytest

from rehoboam.scoring.v2.thresholds import ThresholdReport, percentile, proposed_tiers


def test_percentile_picks_the_right_element():
    values = [float(i) for i in range(1, 101)]  # 1..100
    assert percentile(values, 0.50) == pytest.approx(51.0, abs=1.0)
    assert percentile(values, 0.85) == pytest.approx(86.0, abs=1.0)


def test_percentile_handles_empty_and_single():
    assert percentile([], 0.5) == 0.0
    assert percentile([7.0], 0.9) == 7.0


def test_proposed_tiers_are_ordered_and_rare_to_common():
    gains = [float(i) for i in range(1, 201)]
    tiers, _ = proposed_tiers(gains, min_gain=0.0)
    assert tiers["must_have"] > tiers["strong_upgrade"] > tiers["solid_upgrade"]


def test_proposed_tiers_reflect_the_intended_rarities():
    """must_have = top 15%, strong = top 30%, solid = top 50% of QUALIFYING gains."""
    gains = [float(i) for i in range(1, 101)]
    tiers, method = proposed_tiers(gains, min_gain=0.0)
    assert method == "rarity"
    assert tiers["solid_upgrade"] == pytest.approx(percentile(gains, 0.50), abs=1.0)
    assert tiers["strong_upgrade"] == pytest.approx(percentile(gains, 0.70), abs=1.0)
    assert tiers["must_have"] == pytest.approx(percentile(gains, 0.85), abs=1.0)


def test_tiers_are_measured_only_over_candidates_that_could_actually_be_bought():
    """Tiers apply after the buy threshold, so the population must match.

    Measuring across everything positive proposes tiers at or below the buy
    threshold, which makes every purchase a must_have and puts maximum overbid
    on all of them.
    """
    gains = [float(i) for i in range(1, 101)]
    tiers, _ = proposed_tiers(gains, min_gain=80.0)
    assert tiers["solid_upgrade"] >= 80.0


def test_a_thin_market_falls_back_to_multiples_of_the_threshold():
    """Percentiles over a handful of points are noise, not rarity."""
    tiers, method = proposed_tiers([30.0, 40.0, 90.0], min_gain=25.0)
    assert "threshold-multiples" in method
    assert tiers["solid_upgrade"] == 25.0
    assert tiers["strong_upgrade"] == 37.5
    assert tiers["must_have"] == 62.5


def test_the_fallback_still_orders_the_tiers():
    tiers, _ = proposed_tiers([30.0], min_gain=25.0)
    assert tiers["must_have"] > tiers["strong_upgrade"] > tiers["solid_upgrade"]


def test_no_positive_gains_falls_back_rather_than_crashing():
    tiers, method = proposed_tiers([], min_gain=25.0)
    assert "threshold-multiples" in method
    assert tiers["solid_upgrade"] == 25.0


def test_report_carries_the_sample_size():
    report = ThresholdReport(
        n_candidates=3,
        gains=[1.0, 2.0, 3.0],
        percentiles={"p50": 2.0},
        proposed={"must_have": 3.0},
    )
    assert report.n_candidates == 3
