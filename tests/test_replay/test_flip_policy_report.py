"""REH-71: the 2x2 report, and the decision rule fixed in advance.

REH-68 measured a 6,162-point faithfulness swing. Against a ~27,000-point
season that will plausibly swallow every flip delta, so the inconclusive branch
is the LIKELY one and has to be stated before the numbers arrive.
"""

from __future__ import annotations

from rehoboam.replay.attribution import (
    NOISE_FLOOR_POINTS,
    REAL_FLIP_PNL,
    format_flip_policy,
)
from rehoboam.replay.engine import FlipRecord, SeasonResult


def _arms(a: int, b: int, c: int, d: int) -> dict[str, SeasonResult]:
    return {
        key: SeasonResult(total_points=points)
        for key, points in (("A", a), ("B", b), ("C", c), ("D", d))
    }


def test_the_report_names_all_four_arms():
    report = format_flip_policy(_arms(27_000, 27_100, 27_050, 27_150), actual_total=26_172)

    for label in ("A", "B", "C", "D"):
        assert label in report


def test_small_deltas_are_declared_inconclusive():
    """Every arm within the noise floor of every other."""
    report = format_flip_policy(_arms(27_000, 27_100, 27_050, 27_150), actual_total=26_172)

    assert "INCONCLUSIVE" in report


def test_a_delta_clearing_the_noise_floor_is_not_inconclusive():
    report = format_flip_policy(_arms(27_000, 27_000, 20_000, 20_000), actual_total=26_172)

    assert "INCONCLUSIVE" not in report


def test_the_noise_floor_is_the_one_reh_68_measured():
    assert NOISE_FLOOR_POINTS == 6_162


def _arms_with_cash() -> dict[str, SeasonResult]:
    arms = _arms(27_000, 27_100, 27_050, 27_150)
    arms["D"].flips = [
        FlipRecord("a", 10_000_000, 12_000_000, 0.0, 1.0),  # +2M, a win
        FlipRecord("b", 10_000_000, 7_000_000, 0.0, 1.0),  # -3M, a loss
    ]
    return arms


def test_the_report_prints_each_arms_cash():
    """Design S3: the Trading block appears in `replay-flip-policy` too.

    Without it the INCONCLUSIVE verdict routes the decision to cash evidence
    the report never shows, and recovering the per-arm figures costs one extra
    confirmatory `replay-season` run per arm (REH-71 fix round 2, I1).
    """
    report = format_flip_policy(_arms_with_cash(), actual_total=26_172)

    assert "Realised flip P&L" in report
    assert "EUR -1,000,000" in report  # net of D's two round trips
    assert "50.0%" in report  # 1 win of 2


def test_the_per_arm_cash_is_anchored_to_the_real_season():
    """A replay P&L with nothing to compare it against is uninterpretable."""
    report = format_flip_policy(_arms_with_cash(), actual_total=26_172)

    assert format(REAL_FLIP_PNL, "+,") in report
    assert "151" in report


def test_arms_without_profit_selling_are_marked_structural_zeros():
    """A and C cannot close a round trip at all, so their EUR +0 is not a
    measurement of harmless flipping."""
    report = format_flip_policy(_arms_with_cash(), actual_total=26_172)

    assert "structural zero" in report
    assert "Read it as unmeasurable, not free." in report


def test_adding_cash_left_the_points_verdict_untouched():
    arms = _arms_with_cash()

    assert format_flip_policy(arms, actual_total=26_172).count("INCONCLUSIVE") == 1
    for total in (27_000, 27_100, 27_050, 27_150):
        assert f"{total:,}" in format_flip_policy(arms, actual_total=26_172)
