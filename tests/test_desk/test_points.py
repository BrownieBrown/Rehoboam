"""The price blend (spec v3 §4.1): a plain least-squares fit, applied with a floor."""

from __future__ import annotations

import math

import pytest

from rehoboam.desk.points import MIN_ROWS, PriceBlend, fit_blend


def _rows(n=200):
    # points = -300 + 0.5·EP + 25·ln(value), exactly
    out = []
    for i in range(n):
        ep = 20.0 + i % 90
        value = 1_000_000 * (1 + i % 25)
        out.append((ep, value, -300 + 0.5 * ep + 25 * math.log(value)))
    return out


def test_the_fit_recovers_an_exact_relation():
    blend = fit_blend(_rows())

    assert blend is not None
    assert (blend.intercept, blend.ep_weight, blend.log_value_weight) == pytest.approx(
        (-300, 0.5, 25), rel=1e-6
    )


def test_too_few_rows_give_no_blend():
    assert fit_blend(_rows(MIN_ROWS - 1)) is None


def test_rows_below_the_fit_population_are_ignored():
    rows = _rows() + [(5.0, 1_000_000, 10_000.0)] * 50  # EP under FIT_MIN_EP: not fitted

    blend = fit_blend(rows)

    assert blend is not None
    assert blend.ep_weight == pytest.approx(0.5, rel=1e-6)


def test_a_cheap_high_ep_player_is_pulled_down_and_never_below_zero():
    blend = PriceBlend(intercept=-383.0, ep_weight=0.48, log_value_weight=26.3, rows=1)

    cheap, dear = blend.apply(100.0, 3_000_000), blend.apply(100.0, 20_000_000)

    assert cheap < dear
    assert blend.apply(10.0, 500_000) == 0.0
    assert blend.apply(80.0, 0) == 80.0  # no value, no blend
