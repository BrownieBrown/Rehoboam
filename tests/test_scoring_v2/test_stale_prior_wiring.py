"""Wiring the stale-history prior into the scoring path (REH-98).

`availability_probs` is the single chokepoint -- "so the composed EP and the
note reporting P(start) can never disagree about what the model believes" --
so the correction goes there and the note reports it for free.

Two ordering rules matter:

1. It applies ONLY when `prev_status is None`. A fresh in-season status is
   real per-player evidence and beats a season-long average; this path exists
   for the window where that evidence has aged out.
2. It runs BEFORE `apply_availability_override`, so a live injury flag still
   wins outright. A player marked out is out, whatever last season says.
"""

import pytest

from rehoboam.scoring.v2.adapter import availability_probs
from rehoboam.scoring.v2.availability import AvailabilityModel

PRIOR = {1: 0.0388, 3: 0.1945, 4: 0.2089, 5: 0.5578}
TRANSITIONS = {
    1: {1: 0.727, 3: 0.041, 4: 0.201, 5: 0.031},
    3: {1: 0.008, 3: 0.543, 4: 0.135, 5: 0.314},
    4: {1: 0.015, 3: 0.171, 4: 0.661, 5: 0.152},
    5: {1: 0.010, 3: 0.089, 4: 0.064, 5: 0.837},
}


def _model() -> AvailabilityModel:
    return AvailabilityModel(transitions=TRANSITIONS, prior=PRIOR, shrinkage_k=20.0)


class TestAppliesOnlyWhenTheStatusIsStale:
    def test_a_stale_status_lets_the_players_own_record_reduce_it(self):
        """Uzun: 21 of 34, no usable prev_status. This is the fix."""
        out = availability_probs(None, _model(), played_history=(21, 34))
        assert out[5] < PRIOR[5]

    def test_a_fresh_status_ignores_the_season_record_entirely(self):
        """In-season, prev_status=5 is better evidence than a season average."""
        out = availability_probs(5, _model(), played_history=(21, 34))
        assert out == pytest.approx(TRANSITIONS[5])

    def test_no_record_leaves_the_marginal_prior_untouched(self):
        out = availability_probs(None, _model(), played_history=None)
        assert out == pytest.approx(PRIOR)

    def test_an_above_average_record_is_not_inflated(self):
        """Brown: 33 of 34. Downward-only, so he stays at the prior."""
        out = availability_probs(None, _model(), played_history=(33, 34))
        assert out == pytest.approx(PRIOR)


class TestTheInjuryOverrideStillWins:
    def test_a_long_term_injury_beats_a_good_availability_record(self):
        out = availability_probs(None, _model(), live_status=256, played_history=(34, 34))
        assert out[5] == pytest.approx(0.0)
        assert out[1] == pytest.approx(1.0)

    def test_the_two_corrections_compose_downward(self):
        """An uncertain flag on top of a poor record reduces further."""
        record_only = availability_probs(None, _model(), played_history=(21, 34))
        both = availability_probs(None, _model(), live_status=2, played_history=(21, 34))
        assert both[5] < record_only[5]


class TestTheReportedProbabilityMatchesTheComposedEp:
    def test_output_remains_a_probability_distribution(self):
        out = availability_probs(None, _model(), played_history=(7, 34))
        assert sum(out.values()) == pytest.approx(1.0)
        assert all(0.0 <= p <= 1.0 for p in out.values())
