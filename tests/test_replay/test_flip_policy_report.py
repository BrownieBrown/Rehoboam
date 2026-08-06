"""REH-71: the 2x2 report, and the decision rule fixed in advance.

REH-68 measured a 6,162-point faithfulness swing. Against a ~27,000-point
season that will plausibly swallow every flip delta, so the inconclusive branch
is the LIKELY one and has to be stated before the numbers arrive.
"""

from __future__ import annotations

from rehoboam.replay.attribution import NOISE_FLOOR_POINTS, format_flip_policy
from rehoboam.replay.engine import SeasonResult


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
