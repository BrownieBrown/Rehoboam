"""P(status | Kickbase lineup code): the fitted second availability feature.

Measured on matchday 4 of 2026/27 (status read the day before kickoff):
code 1 started 100% of the time, code 5 1%; the Markov row for "started last
match" says 84%. The model here blends the code's observed counts onto that
row and must leave everything untouched when it has nothing to say.
"""

from __future__ import annotations

import pytest

from rehoboam.scoring.v2 import adapter
from rehoboam.scoring.v2.availability import AvailabilityModel
from rehoboam.scoring.v2.coefficients import (
    load_coefficients,
    load_lineup_prob,
    save_coefficients,
    save_lineup_prob,
)
from rehoboam.scoring.v2.lineup_prob import (
    LineupProbModel,
    effective_lineup_code,
    fit_lineup_prob,
)

MARKOV_STARTER = {1: 0.01, 3: 0.089, 4: 0.064, 5: 0.837}


def _model(k: float = 20.0) -> LineupProbModel:
    return LineupProbModel(
        counts={
            1: {1: 0, 3: 0, 4: 0, 5: 106},
            3: {1: 0, 3: 34, 4: 15, 5: 42},
            5: {1: 21, 3: 27, 4: 121, 5: 2},
        },
        shrinkage_k=k,
    )


class TestBlend:
    def test_code_one_lifts_the_start_probability_toward_the_observed_share(self):
        out = _model().predict(1, MARKOV_STARTER)
        assert out[5] == pytest.approx((106 + 20 * 0.837) / 126)
        assert sum(out.values()) == pytest.approx(1.0)

    def test_code_five_pulls_it_down(self):
        out = _model().predict(5, MARKOV_STARTER)
        assert out[5] < 0.1
        assert out[4] > 0.5

    def test_none_code_returns_the_fallback_unchanged_as_a_copy(self):
        out = _model().predict(None, MARKOV_STARTER)
        assert out == MARKOV_STARTER
        assert out is not MARKOV_STARTER

    def test_unobserved_code_returns_the_fallback(self):
        assert _model().predict(2, MARKOV_STARTER) == MARKOV_STARTER

    def test_zero_shrinkage_is_the_raw_frequency(self):
        out = _model(k=0.0).predict(1, MARKOV_STARTER)
        assert out[5] == pytest.approx(1.0)

    def test_observations_counts_the_code(self):
        assert _model().observations(1) == 106
        assert _model().observations(2) == 0


class TestFit:
    def test_counts_only_known_codes_and_played_statuses(self):
        model = fit_lineup_prob(
            [(1, 5), (1, 5), (1, 0), (None, 5), (3, None), (3, 3), (5, 1)],
            shrinkage_k=10.0,
            meta={"matchdays": [4]},
        )
        assert model.counts[1] == {1: 0, 3: 0, 4: 0, 5: 2}
        assert model.counts[3][3] == 1
        assert model.counts[5][1] == 1
        assert 2 not in model.counts
        assert model.shrinkage_k == 10.0
        assert model.meta == {"matchdays": [4]}

    def test_round_trip_through_dict(self):
        model = _model()
        again = LineupProbModel.from_dict(model.to_dict())
        assert again == model


class TestEffectiveCode:
    @pytest.mark.parametrize(
        ("code", "xi", "expected"),
        [
            (None, True, None),
            (3, None, 3),
            (1, False, 1),  # a sure starter is not demoted by an outside source
            (5, True, 5),  # nor a sure non-starter promoted
            (2, True, 2),
            (3, True, 2),
            (4, True, 2),
            (2, False, 4),
            (3, False, 4),
            (4, False, 4),
        ],
    )
    def test_only_codes_two_to_four_move(self, code, xi, expected):
        assert effective_lineup_code(code, xi) == expected


class TestWiring:
    """`availability_probs` applies the code after the Markov row and before the injury flag."""

    def _availability(self):
        return AvailabilityModel(
            transitions={5: MARKOV_STARTER}, prior=MARKOV_STARTER, shrinkage_k=20
        )

    def test_without_a_model_the_code_changes_nothing(self):
        out = adapter.availability_probs(5, self._availability(), lineup_probability=1)
        assert out[5] == pytest.approx(0.837)

    def test_with_a_model_the_code_moves_the_row(self):
        out = adapter.availability_probs(
            5, self._availability(), lineup_probability=1, lineup_model=_model()
        )
        assert out[5] > 0.95

    def test_the_injury_flag_still_wins_after_a_code_one(self):
        out = adapter.availability_probs(
            5, self._availability(), lineup_probability=1, lineup_model=_model(), live_status=256
        )
        assert out[5] == pytest.approx(0.0)
        assert out[1] == pytest.approx(1.0)

    def test_an_outside_eleven_moves_a_code_three(self):
        base = adapter.availability_probs(
            5, self._availability(), lineup_probability=3, lineup_model=_model()
        )
        out = adapter.availability_probs(
            5, self._availability(), lineup_probability=3, lineup_model=_model(), predicted_xi=False
        )
        # code 3 → 4 is unobserved in this model, so the outside verdict falls
        # back to the Markov row — still no inflation, and never a crash.
        assert out[5] == pytest.approx(0.837)
        assert base[5] != pytest.approx(0.837)


class TestPersistence:
    def test_save_and_load_the_lineup_model_beside_the_others(self, tmp_path):
        from rehoboam.scoring.v2.availability import fit_availability
        from rehoboam.scoring.v2.features import FeatureRow
        from rehoboam.scoring.v2.rate import fit_rate

        rows = [
            FeatureRow("a", "2024/2025", 1, 5, 90.0, 5, 5, 80),
        ] * 5
        path = tmp_path / "coefficients.json"
        save_coefficients(fit_availability(rows), fit_rate(rows, {"a": "Forward"}), {}, path)
        assert load_lineup_prob(path) is None

        save_lineup_prob(_model(), path)
        assert load_lineup_prob(path) == _model()
        # the other two models survive the lineup save …
        availability, _rate, _meta = load_coefficients(path)
        assert availability.predict(5)[5] > 0.5
        # … and the lineup model survives a refit of the other two
        save_coefficients(fit_availability(rows), fit_rate(rows, {"a": "Forward"}), {}, path)
        assert load_lineup_prob(path) == _model()

    def test_missing_file_means_no_model(self, tmp_path):
        assert load_lineup_prob(tmp_path / "nope.json") is None
