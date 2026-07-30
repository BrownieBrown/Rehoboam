"""Tests for rehoboam.scoring.v2.availability."""

from __future__ import annotations

import pytest

from rehoboam.scoring.v2.availability import AvailabilityModel, fit_availability
from rehoboam.scoring.v2.features import FeatureRow


def _row(prev: int | None, target: int) -> FeatureRow:
    return FeatureRow(
        player_id="1",
        season="2024/2025",
        day_number=1,
        prev_status=prev,
        rolling_minutes_3=0.0,
        matches_seen=1,
        target_status=target,
        target_points=0,
    )


def test_predictions_are_a_probability_distribution():
    model = fit_availability([_row(5, 5)] * 50 + [_row(5, 3)] * 50)
    probs = model.predict(5)
    assert set(probs) == {1, 3, 4, 5}
    assert sum(probs.values()) == pytest.approx(1.0)
    assert all(0.0 <= p <= 1.0 for p in probs.values())


def test_learns_persistence_from_data():
    """Starters mostly start again — the dominant real-world signal."""
    rows = [_row(5, 5)] * 820 + [_row(5, 3)] * 100 + [_row(5, 4)] * 70 + [_row(5, 1)] * 10
    model = fit_availability(rows, shrinkage_k=0.0)
    probs = model.predict(5)
    assert probs[5] == pytest.approx(0.82, abs=0.01)


def test_shrinkage_pulls_sparse_states_toward_the_prior():
    """One observation of a rare state must not produce a 100% estimate."""
    rows = [_row(5, 5)] * 1000 + [_row(1, 3)]
    model = fit_availability(rows, shrinkage_k=20.0)
    probs = model.predict(1)
    assert probs[3] < 0.5, "a single observation should not dominate"


def test_zero_shrinkage_reproduces_raw_frequencies():
    rows = [_row(3, 5)] * 3 + [_row(3, 3)] * 1
    model = fit_availability(rows, shrinkage_k=0.0)
    assert model.predict(3)[5] == pytest.approx(0.75)


def test_unknown_previous_status_falls_back_to_the_prior():
    """A player's first-ever match has no previous status."""
    rows = [_row(5, 5)] * 80 + [_row(5, 4)] * 20
    model = fit_availability(rows)
    probs = model.predict(None)
    assert sum(probs.values()) == pytest.approx(1.0)
    assert probs[5] > probs[4]


def test_previous_status_never_seen_in_training_falls_back_to_the_prior():
    rows = [_row(5, 5)] * 100
    model = fit_availability(rows)
    assert model.predict(1) == model.predict(None)


def test_empty_training_data_yields_a_uniform_prior():
    model = fit_availability([])
    probs = model.predict(5)
    assert sum(probs.values()) == pytest.approx(1.0)
    assert len(set(probs.values())) == 1


def test_round_trips_through_dict():
    model = fit_availability([_row(5, 5)] * 10 + [_row(5, 3)] * 5)
    restored = AvailabilityModel.from_dict(model.to_dict())
    assert restored.predict(5) == model.predict(5)
    assert restored.predict(None) == model.predict(None)


def test_rows_without_a_target_status_are_ignored():
    rows = [_row(5, 5)] * 10 + [FeatureRow("1", "2024/2025", 2, 5, 0.0, 1, None, 0)]
    model = fit_availability(rows, shrinkage_k=0.0)
    assert model.predict(5)[5] == pytest.approx(1.0)
