"""μ(N), the euro-to-points rate (spec v3 §4.3): properties, not a formula.

The formula is the implementer's call. These tests pin only what the spec
requires of any formula.
"""

from __future__ import annotations

import pytest

from rehoboam.desk.exchange import points_per_euro

P = 255_008.0


def test_money_is_worthless_for_the_last_matchday_before_the_reset():
    assert points_per_euro(1, P) == 0.0


def test_a_euro_is_worth_more_the_more_matchdays_are_left():
    rates = [points_per_euro(n, P) for n in range(1, 18)]

    assert all(later > earlier for earlier, later in zip(rates, rates[1:], strict=False))
    assert all(rate >= 0 for rate in rates)


def test_the_rate_scales_linearly_with_the_sweep():
    base = points_per_euro(6, P)

    assert points_per_euro(6, P, scale=2.0) == pytest.approx(2 * base)
    assert points_per_euro(6, P, scale=0.5) == pytest.approx(0.5 * base)


def test_dearer_points_make_a_euro_worth_fewer_of_them():
    assert points_per_euro(6, 2 * P) == pytest.approx(points_per_euro(6, P) / 2)


@pytest.mark.parametrize(
    ("matchdays_left", "eur_per_point", "scale"),
    [(0, P, 1.0), (-3, P, 1.0), (6, 0.0, 1.0), (6, -P, 1.0), (6, float("nan"), 1.0), (6, P, -1.0)],
)
def test_bad_input_raises_instead_of_guessing(matchdays_left, eur_per_point, scale):
    with pytest.raises(ValueError):
        points_per_euro(matchdays_left, eur_per_point, scale=scale)
