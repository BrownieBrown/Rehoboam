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
    tiers = proposed_tiers(gains)
    assert tiers["must_have"] > tiers["strong_upgrade"] > tiers["solid_upgrade"]


def test_proposed_tiers_reflect_the_intended_rarities():
    """must_have = top 15%, strong = top 30%, solid = top 50% of positive gains."""
    gains = [float(i) for i in range(1, 101)]
    tiers = proposed_tiers(gains)
    assert tiers["solid_upgrade"] == pytest.approx(percentile(gains, 0.50), abs=1.0)
    assert tiers["strong_upgrade"] == pytest.approx(percentile(gains, 0.70), abs=1.0)
    assert tiers["must_have"] == pytest.approx(percentile(gains, 0.85), abs=1.0)


def test_no_positive_gains_yields_zeros_not_a_crash():
    tiers = proposed_tiers([])
    assert tiers == {"must_have": 0.0, "strong_upgrade": 0.0, "solid_upgrade": 0.0}


def test_report_carries_the_sample_size():
    report = ThresholdReport(
        n_candidates=3,
        gains=[1.0, 2.0, 3.0],
        percentiles={"p50": 2.0},
        proposed={"must_have": 3.0},
    )
    assert report.n_candidates == 3
