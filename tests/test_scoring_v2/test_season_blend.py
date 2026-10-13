"""This season's record blends into the fitted quality (2026-10-13).

The per-player quality in `coefficients.json` is fitted on seasons up to
2025/26 and never sees the season in progress: Diks was EP 112 on five starts
averaging 29 because last season he averaged 94.8. `RateModel.predict` now
takes this season's played points and blends them with the fitted quality in
the training's own quality space, `(n·mean/reference + k·fitted) / (n + k)`.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from rehoboam.scoring.store_scorer import (
    score_stored,
    season_points_from_rows,
)
from rehoboam.scoring.v2.adapter import (
    compose_ep,
    season_points_from_performance,
)
from rehoboam.scoring.v2.coefficients import load_coefficients
from rehoboam.scoring.v2.rate import DEFAULT_SEASON_K, RateModel
from tests.test_scoring_v2.test_store_scorer import _live, _row, _stored

NOW = datetime(2026, 10, 13, 20, 0, tzinfo=timezone.utc)


def _model(**overrides) -> RateModel:
    fields = {
        "base_rate": {1: 0.0, 3: 20.0, 4: 1.0, 5: 100.0},
        "quality": {"star": 1.5},
        "position_prior": {"Forward": 0.8},
        "shrinkage_k": 5.0,
        "scoring_reference": 100.0,
        "season_k": 10.0,
    }
    return RateModel(**{**fields, **overrides})


class TestTheBlend:
    def test_this_seasons_record_pulls_a_fitted_quality_toward_it(self):
        # Ten games at 50 points: quality 0.5 against a reference of 100.
        # (10 × 0.5 + 10 × 1.5) / 20 = 1.0 → 100 points if started.
        assert _model().predict("star", 5, "Forward", season_points=[50.0] * 10) == pytest.approx(
            100.0
        )

    def test_one_game_barely_moves_a_fitted_quality(self):
        # (1 × 0.5 + 10 × 1.5) / 11 ≈ 1.409
        got = _model().predict("star", 5, "Forward", season_points=[50.0])
        assert got == pytest.approx(140.9, abs=0.1)

    def test_an_unknown_player_blends_from_the_position_prior(self):
        # (5 × 1.2 + 10 × 0.8) / 15 ≈ 0.933
        got = _model().predict("new", 5, "Forward", season_points=[120.0] * 5)
        assert got == pytest.approx(93.3, abs=0.1)

    def test_no_season_points_leaves_the_fitted_quality_alone(self):
        assert _model().predict("star", 5, "Forward") == pytest.approx(150.0)
        assert _model().predict("star", 5, "Forward", season_points=[]) == pytest.approx(150.0)

    def test_coefficients_without_a_reference_never_blend(self):
        # A coefficients.json written before the reference was recorded.
        model = _model(scoring_reference=0.0)
        assert model.predict("star", 5, "Forward", season_points=[50.0] * 10) == pytest.approx(
            150.0
        )

    def test_a_non_positive_season_k_turns_the_blend_off(self):
        model = _model(season_k=0.0)
        assert model.predict("star", 5, "Forward", season_points=[50.0] * 10) == pytest.approx(
            150.0
        )

    def test_a_status_with_no_base_rate_stays_zero(self):
        assert _model().predict("star", 1, "Forward", season_points=[50.0] * 10) == 0.0

    def test_round_trips_and_defaults_for_old_files(self):
        model = _model()
        assert RateModel.from_dict(model.to_dict()) == model
        old = {
            k: v
            for k, v in model.to_dict().items()
            if k in ("base_rate", "quality", "position_prior", "shrinkage_k")
        }
        loaded = RateModel.from_dict(old)
        assert loaded.scoring_reference == 0.0
        assert loaded.season_k == DEFAULT_SEASON_K


class TestTheEvidence:
    def test_rows_of_this_season_on_the_pitch_only(self):
        rows = [
            _row(1, "2026-08-22T13:30:00Z", 5, 80),
            _row(2, "2026-08-29T13:30:00Z", 4, 0),  # unused sub
            _row(3, "2026-09-12T13:30:00Z", 3, 25),
            _row(4, "2026-09-19T13:30:00Z", 1, 0),  # out
            _row(5, "2026-09-26T13:30:00Z", 0, 0),  # not played yet
            _row(30, "2026-05-16T13:30:00Z", 5, 140, season="2025/2026"),
        ]
        assert season_points_from_rows(rows, "2026/2027") == [80.0, 25.0]
        assert season_points_from_rows(rows, "2024/2025") == []

    def test_the_performance_payload_reads_the_same(self):
        perf = {
            "it": [
                {"ti": "2025/2026", "ph": [{"day": 30, "st": 5, "p": 140}]},
                {
                    "ti": "2026/2027",
                    "ph": [
                        {"day": 1, "st": 5, "p": 80},
                        {"day": 2, "st": 4, "p": 0},
                        {"day": 3, "st": 3, "p": 25},
                        {"day": 4, "st": 1, "p": 0},
                        {"day": 5, "st": 0, "p": 0},
                    ],
                },
            ]
        }
        assert season_points_from_performance(perf, "2026/2027") == [80.0, 25.0]
        assert season_points_from_performance(None, "2026/2027") == []
        assert season_points_from_performance({"it": []}, "2026/2027") == []


class TestBothPathsAgree:
    COLD = [
        (d, f"2026-{8 + d // 4:02d}-{1 + 7 * (d % 4):02d}T13:30:00Z", 5, 10) for d in range(1, 6)
    ]

    def test_a_fitted_players_cold_season_lowers_him_on_both_paths(self):
        availability, rate, _ = load_coefficients()
        pid = max(rate.quality, key=rate.quality.get)  # the best fitted player
        frozen = RateModel(**{**rate.__dict__, "season_k": 0.0})
        live = _live(pid, "Midfielder", self.COLD)
        stored = score_stored(
            _stored(pid, "Midfielder", self.COLD),
            now=NOW,
            max_status_age_days=60.0,
            availability=availability,
            rate=rate,
        )
        before = score_stored(
            _stored(pid, "Midfielder", self.COLD),
            now=NOW,
            max_status_age_days=60.0,
            availability=availability,
            rate=frozen,
        )
        assert round(stored.predicted_ep, 2) == live.expected_points
        assert stored.predicted_ep < before.predicted_ep

    def test_the_replay_path_is_inert(self):
        # `compose_ep` without season points is the replay's call and must
        # score exactly as before.
        availability, rate, _ = load_coefficients()
        pid = next(iter(rate.quality))
        frozen = RateModel(**{**rate.__dict__, "season_k": 0.0})
        assert compose_ep(pid, 5, "Midfielder", availability, rate) == compose_ep(
            pid, 5, "Midfielder", availability, frozen
        )
