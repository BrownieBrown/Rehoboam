"""Tests for rehoboam.scoring.v2.adapter — composing fitted models into PlayerScore."""

from __future__ import annotations

import pytest

from rehoboam.kickbase_client import MarketPlayer
from rehoboam.scoring.models import PlayerData
from rehoboam.scoring.v2.adapter import (
    COLD_START_DISCOUNT,
    compose_ep,
    last_played_status,
    score_player_v2,
)
from rehoboam.scoring.v2.availability import fit_availability
from rehoboam.scoring.v2.features import FeatureRow
from rehoboam.scoring.v2.rate import fit_rate


def _perf(matches: list[dict]) -> dict:
    return {"it": [{"ti": "2025/2026", "ph": matches}]}


def _player(pid: str = "1") -> MarketPlayer:
    return MarketPlayer(
        id=pid,
        first_name="Test",
        last_name="Player",
        position="Midfielder",
        team_id="2",
        team_name="T",
        market_value=1_000_000,
        price=1_000_000,
        points=0,
        average_points=0.0,
        status=0,
    )


def _data(pid: str = "1", performance: dict | None = None) -> PlayerData:
    return PlayerData(
        player=_player(pid),
        performance=performance,
        player_details=None,
        team_strength=None,
        opponent_strength=None,
        is_dgw=False,
    )


def _row(pid: str, prev: int | None, status: int, points: int) -> FeatureRow:
    return FeatureRow(
        player_id=pid,
        season="2024/2025",
        day_number=1,
        prev_status=prev,
        rolling_minutes_3=90.0,
        matches_seen=5,
        target_status=status,
        target_points=points,
    )


def test_last_played_status_reads_the_most_recent_played_match():
    perf = _perf(
        [
            {"day": 1, "st": 5, "p": 80, "mp": "90'"},
            {"day": 2, "st": 3, "p": 12, "mp": "20'"},
        ]
    )
    assert last_played_status(perf) == 3


def test_last_played_status_ignores_unplayed_fixtures():
    """status 0 means the fixture has not happened — it is not 'his last state'."""
    perf = _perf(
        [
            {"day": 1, "st": 5, "p": 80, "mp": "90'"},
            {"day": 2, "st": 0},
        ]
    )
    assert last_played_status(perf) == 5


def test_last_played_status_prefers_the_later_season_over_a_higher_day_number():
    """'Most recent played match' is across seasons, not within one.

    A day-1 match this season is more recent than a day-34 match last season.
    Pinned in both list orders so neither "first season wins" nor "last season
    wins" can pass by accident.
    """
    this_season = ("2025/2026", [{"day": 1, "st": 3, "p": 12, "mp": "20'"}])
    last_season = ("2024/2025", [{"day": 34, "st": 5, "p": 80, "mp": "90'"}])

    for ordering in ((this_season, last_season), (last_season, this_season)):
        perf = {"it": [{"ti": title, "ph": matches} for title, matches in ordering]}
        assert last_played_status(perf) == 3


def test_last_played_status_returns_none_without_history():
    assert last_played_status(None) is None
    assert last_played_status({"it": []}) is None
    assert last_played_status(_perf([])) is None


def test_compose_ep_is_the_probability_weighted_sum():
    rows = [_row("1", 5, 5, 90)] * 20
    av, rate = fit_availability(rows), fit_rate(rows, {"1": "Midfielder"})
    probs = av.predict(5)
    expected = sum(probs[s] * rate.predict("1", s, "Midfielder") for s in (1, 3, 4, 5))
    assert compose_ep("1", 5, "Midfielder", av, rate) == pytest.approx(expected)


def test_score_is_in_real_points_not_an_index():
    """A player who reliably starts and scores ~90 should score near 90, not 40."""
    perf = _perf([{"day": d, "st": 5, "p": 90, "mp": "90'"} for d in range(1, 11)])
    score = score_player_v2(_data(performance=perf))
    assert score.expected_points > 50.0, "real points, not a 0-100 index"


def test_v1_only_fields_are_zeroed_and_explained():
    """PlayerScore carries v1's decomposition; v2 has no counterpart for it."""
    perf = _perf([{"day": 1, "st": 5, "p": 80, "mp": "90'"}])
    score = score_player_v2(_data(performance=perf))
    assert score.base_points == 0.0
    assert score.consistency_bonus == 0.0
    assert score.lineup_bonus == 0.0
    assert score.fixture_bonus == 0.0
    assert score.form_bonus == 0.0
    assert score.minutes_bonus == 0.0
    assert any("availability" in n.lower() for n in score.notes)


def test_player_with_no_history_still_scores():
    """A new signing must not crash — the model falls back to its prior."""
    score = score_player_v2(_data(pid="unknown-player", performance=None))
    assert score.expected_points >= 0.0
    assert score.player_id == "unknown-player"


def test_score_carries_identity_fields_decisions_depend_on():
    perf = _perf([{"day": 1, "st": 5, "p": 80, "mp": "90'"}])
    score = score_player_v2(_data(performance=perf))
    assert score.player_id == "1"
    assert score.position == "Midfielder"
    assert score.market_value == 1_000_000


def test_dgw_multiplies_the_composed_score():
    perf = _perf([{"day": 1, "st": 5, "p": 80, "mp": "90'"}])
    single = score_player_v2(_data(performance=perf))
    dgw_data = _data(performance=perf)
    dgw_data.is_dgw = True
    doubled = score_player_v2(dgw_data)
    assert doubled.expected_points > single.expected_points
    assert doubled.is_dgw is True


# --- REH-80: the cold-start discount ------------------------------------


def test_an_unfitted_player_takes_the_measured_cold_start_discount():
    """A player with no fitted quality falls back to the position prior, which
    is the median of ALL players. Newcomers are not median players: measured
    over 2024/25 and 2025/26 they score 23% fewer points per appearance than
    returning players. The prior is therefore generous, and the discount is
    what removes that bias."""
    rows = [_row("1", 5, 5, 90)] * 20
    av, rate = fit_availability(rows), fit_rate(rows, {"1": "Midfielder"})
    probs = av.predict(5)
    undiscounted = sum(probs[s] * rate.predict("newcomer", s, "Midfielder") for s in (1, 3, 4, 5))

    assert compose_ep("newcomer", 5, "Midfielder", av, rate) == pytest.approx(
        undiscounted * COLD_START_DISCOUNT
    )


def test_a_fitted_player_is_never_discounted():
    """The discount corrects an unmeasured player's prior. A player with fitted
    quality has been measured, so it must not touch him."""
    rows = [_row("1", 5, 5, 90)] * 20
    av, rate = fit_availability(rows), fit_rate(rows, {"1": "Midfielder"})
    probs = av.predict(5)
    expected = sum(probs[s] * rate.predict("1", s, "Midfielder") for s in (1, 3, 4, 5))

    assert compose_ep("1", 5, "Midfielder", av, rate) == pytest.approx(expected)


def test_the_discount_is_a_haircut_not_a_rescale():
    """Guards the direction and the magnitude: it must reduce the score, and
    must not be so severe that an unknown player becomes unbuyable."""
    assert 0.5 < COLD_START_DISCOUNT < 1.0


def test_score_player_v2_applies_the_discount_and_says_so():
    """The note is load-bearing: a score that was quietly reduced is worse than
    one that was not reduced at all."""
    score = score_player_v2(_data("never-fitted", _perf([])))

    assert any("cold start" in n.lower() for n in score.notes)
    assert any(f"{COLD_START_DISCOUNT:.2f}" in n for n in score.notes)
