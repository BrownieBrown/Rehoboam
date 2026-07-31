"""Tests for rehoboam.scoring.v2.rate."""

from __future__ import annotations

import pytest

from rehoboam.scoring.v2.features import FeatureRow
from rehoboam.scoring.v2.rate import RateModel, fit_rate


def _row(player_id: str, status: int, points: int) -> FeatureRow:
    return FeatureRow(
        player_id=player_id,
        season="2024/2025",
        day_number=1,
        prev_status=5,
        rolling_minutes_3=90.0,
        matches_seen=5,
        target_status=status,
        target_points=points,
    )


POSITIONS = {"star": "Forward", "average": "Forward", "keeper": "Goalkeeper"}


def test_base_rate_is_learned_per_status():
    rows = [_row("average", 5, 80)] * 20 + [_row("average", 3, 18)] * 20
    model = fit_rate(rows, POSITIONS)
    assert model.base_rate[5] == pytest.approx(80.0, abs=1.0)
    assert model.base_rate[3] == pytest.approx(18.0, abs=1.0)


def test_a_better_player_scores_above_the_base_rate():
    rows = [_row("average", 5, 80)] * 30 + [_row("star", 5, 160)] * 30
    model = fit_rate(rows, POSITIONS, shrinkage_k=0.0)
    assert model.predict("star", 5, "Forward") > model.predict("average", 5, "Forward")


def test_shrinkage_pulls_a_thin_record_toward_the_position_prior():
    """One 200-point game must not make a player twice the league's best."""
    rows = [_row("average", 5, 80)] * 100 + [_row("star", 5, 200)]
    model = fit_rate(rows, POSITIONS, shrinkage_k=5.0)
    unshrunk = fit_rate(rows, POSITIONS, shrinkage_k=0.0)
    assert model.predict("star", 5, "Forward") < unshrunk.predict("star", 5, "Forward")


def test_a_long_record_stands_on_its_own():
    """With plenty of evidence, shrinkage barely moves the estimate."""
    rows = [_row("average", 5, 80)] * 100 + [_row("star", 5, 160)] * 100
    shrunk = fit_rate(rows, POSITIONS, shrinkage_k=5.0)
    unshrunk = fit_rate(rows, POSITIONS, shrinkage_k=0.0)
    assert shrunk.predict("star", 5, "Forward") == pytest.approx(
        unshrunk.predict("star", 5, "Forward"), rel=0.05
    )


def test_unknown_player_falls_back_to_the_position_prior():
    rows = [_row("average", 5, 80)] * 30
    model = fit_rate(rows, POSITIONS)
    assert model.predict("never-seen", 5, "Forward") == pytest.approx(
        model.base_rate[5] * model.position_prior["Forward"], rel=0.01
    )


def test_unknown_player_and_unknown_position_falls_back_to_the_base_rate():
    rows = [_row("average", 5, 80)] * 30
    model = fit_rate(rows, POSITIONS)
    assert model.predict("never-seen", 5, None) == pytest.approx(model.base_rate[5])


def test_status_with_no_training_data_returns_zero():
    rows = [_row("average", 5, 80)] * 10
    model = fit_rate(rows, POSITIONS)
    assert model.predict("average", 1, "Forward") == 0.0


def test_predictions_are_in_real_points_not_an_index():
    """The v1 scorer's cardinal sin was a 0-100 index masquerading as points."""
    rows = [_row("star", 5, 160)] * 50
    model = fit_rate(rows, POSITIONS, shrinkage_k=0.0)
    assert model.predict("star", 5, "Forward") == pytest.approx(160.0, abs=5.0)


def test_round_trips_through_dict():
    rows = [_row("average", 5, 80)] * 10 + [_row("star", 5, 160)] * 10
    model = fit_rate(rows, POSITIONS)
    restored = RateModel.from_dict(model.to_dict())
    assert restored.predict("star", 5, "Forward") == pytest.approx(
        model.predict("star", 5, "Forward")
    )


def test_empty_training_data_predicts_zero():
    model = fit_rate([], {})
    assert model.predict("anyone", 5, "Forward") == 0.0
