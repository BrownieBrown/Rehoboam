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
