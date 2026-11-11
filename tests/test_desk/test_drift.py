"""The drift table (spec v3 §4.2): buckets, shrinkage, quantiles, features."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from rehoboam.desk import drift
from rehoboam.desk.drift import DriftFeatures

D = date(2026, 10, 1)


def _f(last_move=0.0, move_3n=0.0, value=3_000_000, falling=False) -> DriftFeatures:
    return DriftFeatures(
        last_move=last_move, move_3n=move_3n, market_value=value, market_falling=falling
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [(4_999_999, 0), (5_000_000, 1), (14_999_999, 1), (15_000_000, 2)],
)
def test_band_edges_are_lower_inclusive(value, expected):
    assert drift.band(value) == expected


@pytest.mark.parametrize(
    ("last_move", "bucket"),
    [(-0.03, 0), (-0.02, 1), (-0.01, 1), (0.0, 2), (0.005, 3), (0.019, 3), (0.02, 4)],
)
def test_last_move_buckets(last_move, bucket):
    assert drift.cell_keys(_f(last_move=last_move))[0][0] == bucket


def _two_cell_table():
    # 60 flat samples in one cell, 3 samples rising 30% in another; same band
    # and market, so the (band, market) parent equals the global cell.
    common = [(_f(), 0.0)] * 60
    rare = [(_f(last_move=0.05, move_3n=0.10), 0.30)] * 3
    return drift.fit(common + rare, lag=1, horizon=7)


def test_a_thin_cell_shrinks_toward_its_parents():
    table = _two_cell_table()
    k = drift.SHRINK_K
    overall = 0.9 / 63
    parent = (3 * 0.30 + k * overall) / (3 + k)  # (last move, band, market)
    expected = (3 * 0.30 + k * parent) / (3 + k)

    estimate = table.predict(_f(last_move=0.05, move_3n=0.10))

    assert estimate.mean == pytest.approx(expected)
    assert overall < estimate.mean < 0.30
    assert estimate.n == 3


def test_the_low_quantile_comes_from_the_first_level_with_enough_samples():
    table = _two_cell_table()

    estimate = table.predict(_f(last_move=0.05, move_3n=0.10))

    # The rare cell and its parent hold 3 samples each, under MIN_QUANTILE_N,
    # so p10 comes from the (band, market) level: 63 samples, 60 of them 0.0.
    assert estimate.low == pytest.approx(0.0)


def test_an_unseen_cell_predicts_its_parents():
    table = _two_cell_table()

    estimate = table.predict(_f(last_move=-0.05, move_3n=-0.10))

    assert estimate.mean == pytest.approx(0.9 / 63)
    assert estimate.n == 0


def test_fit_refuses_no_samples():
    with pytest.raises(ValueError):
        drift.fit([], lag=1, horizon=7)


def test_features_are_the_last_night_and_the_last_three_nights():
    path = {D - timedelta(days=3): 100, D - timedelta(days=1): 110, D: 121}

    features = drift.features_at(path, D, market_falling=True)

    assert features == DriftFeatures(
        last_move=pytest.approx(0.10),
        move_3n=pytest.approx(0.21),
        market_value=121,
        market_falling=True,
    )


def test_features_need_three_nights_of_history_and_a_market_reading():
    path = {D - timedelta(days=1): 110, D: 121}

    assert drift.features_at(path, D, market_falling=False) is None
    assert drift.features_at({**path, D - timedelta(days=3): 100}, D, market_falling=None) is None


def test_target_starts_after_the_lag():
    path = {D + timedelta(days=1): 100, D + timedelta(days=8): 112}

    assert drift.target_at(path, D, lag=1, horizon=7) == pytest.approx(0.12)
    assert drift.target_at(path, D, lag=0, horizon=7) is None


def test_market_moves_ignore_players_under_a_million():
    paths = {
        "cheap": {D - timedelta(days=1): 500_000, D: 1_000_000},  # +100%, ignored
        "a": {D - timedelta(days=1): 2_000_000, D: 2_020_000},  # +1%
        "b": {D - timedelta(days=1): 2_000_000, D: 1_960_000},  # -2%
        "c": {D - timedelta(days=1): 2_000_000, D: 2_000_000},  # 0%
    }

    assert drift.market_moves(paths)[D] == pytest.approx(0.0)


def test_the_market_is_falling_on_the_mean_of_three_nights():
    moves = {D: 0.004, D - timedelta(days=1): -0.010, D - timedelta(days=2): 0.002}

    assert drift.market_falling(moves, D) is True
    assert drift.market_falling(moves, D - timedelta(days=1)) is None


def test_training_samples_stay_inside_the_window():
    path = {D + timedelta(days=i): 1_000_000 + 10_000 * i for i in range(-3, 12)}
    moves = {D + timedelta(days=i): 0.0 for i in range(-5, 12)}

    samples = list(
        drift.training_samples(
            {"p": path}, moves, lag=1, horizon=7, first_day=D, last_day=D + timedelta(days=2)
        )
    )

    assert len(samples) == 3


# ---------------------------------------------------------------------------
# Refinements (M2-11): an optional level above the full cell
# ---------------------------------------------------------------------------


def test_features_carry_the_14_night_move_and_the_last_match_points():
    path = {D - timedelta(days=14): 80, D - timedelta(days=3): 100, D - timedelta(days=1): 110}
    path[D] = 120

    features = drift.features_at(path, D, market_falling=False, last_points=132)

    assert features is not None
    assert features.move_14n == pytest.approx(0.50)
    assert features.last_points == 132


def test_the_14_night_move_is_unknown_without_14_nights_of_history():
    path = {D - timedelta(days=3): 100, D - timedelta(days=1): 110, D: 121}

    features = drift.features_at(path, D, market_falling=False)

    assert features is not None
    assert features.move_14n is None
    assert features.last_points is None


@pytest.mark.parametrize(
    ("known_on", "expected"),
    [
        (D - timedelta(days=1), 140),  # yesterday's match is known
        (D, None),  # a result on the same value day is not priced yet
        (D - timedelta(days=11), None),  # outside the window: no recent match
    ],
)
def test_the_last_match_is_the_newest_known_before_the_day(known_on, expected):
    matches = ((D - timedelta(days=20), 30), (known_on, 140))  # the first is outside the window

    assert drift.last_points_at(matches, D) == expected


def test_last_points_reads_the_match_before_a_later_one():
    matches = ((D - timedelta(days=4), 90), (D + timedelta(days=2), 160))

    assert drift.last_points_at(matches, D) == 90


@pytest.mark.parametrize(
    ("points", "bucket"),
    [(None, 0), (0, 1), (49, 1), (50, 2), (99, 2), (100, 3), (149, 3), (150, 4)],
)
def test_last_points_buckets(points, bucket):
    keys = drift.cell_keys(
        DriftFeatures(0.0, 0.0, 3_000_000, False, move_14n=0.0, last_points=points),
        (drift.LAST_POINTS,),
    )

    assert keys[0][-1] == bucket


def test_a_refined_key_sits_above_the_unrefined_hierarchy():
    f = DriftFeatures(0.0, 0.0, 3_000_000, False, move_14n=-0.25, last_points=None)

    refined = drift.cell_keys(f, drift.REFINEMENTS)

    assert refined[1:] == drift.cell_keys(f)
    assert refined[0] == (*drift.cell_keys(f)[0], 0, 0)


def test_no_refined_level_when_the_14_night_move_is_unknown():
    f = _f()

    assert drift.cell_keys(f, drift.REFINEMENTS) == drift.cell_keys(f)


def test_a_refined_cell_shrinks_toward_its_unrefined_cell():
    # Same unrefined cell; the rebounders (fell 25% over 14 nights) rose 30%.
    flat = [(DriftFeatures(0.0, 0.0, 3_000_000, False, move_14n=0.0), 0.0)] * 60
    rebound = [(DriftFeatures(0.0, 0.0, 3_000_000, False, move_14n=-0.25), 0.30)] * 6
    table = drift.fit(flat + rebound, lag=0, horizon=7, refine=(drift.MOVE_14N,))
    plain = drift.fit(flat + rebound, lag=0, horizon=7)

    rebounder = DriftFeatures(0.0, 0.0, 3_000_000, False, move_14n=-0.25)
    unrefined = plain.predict(rebounder).mean
    k = drift.SHRINK_K

    assert table.predict(rebounder).mean == pytest.approx((6 * 0.30 + k * unrefined) / (6 + k))
    assert table.predict(rebounder).n == 6
    assert plain.predict(rebounder).n == 66


def test_fit_refuses_an_unknown_refinement():
    with pytest.raises(ValueError, match="unknown refinement"):
        drift.fit([(_f(), 0.0)], lag=0, horizon=7, refine=("lineup_code",))


def test_training_samples_carry_the_last_match_points():
    path = {D + timedelta(days=i): 1_000_000 for i in range(-14, 9)}
    moves = {D + timedelta(days=i): 0.0 for i in range(-16, 9)}
    matches = {"p": ((D - timedelta(days=2), 155),)}

    samples = list(
        drift.training_samples(
            {"p": path}, moves, lag=0, horizon=7, first_day=D, last_day=D, matches=matches
        )
    )

    assert [(f.last_points, f.move_14n) for f, _ in samples] == [(155, 0.0)]
