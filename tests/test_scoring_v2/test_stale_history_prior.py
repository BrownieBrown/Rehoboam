"""Per-player availability when the last-played status is stale (REH-98).

`max_status_age_days` correctly discards an end-of-season status -- squads
rotate through dead rubbers, and without the bound the final matchday of one
season drives availability for the whole of the next. But the fallback is the
league-wide *marginal prior*, so during the pre-season every player in the
league is scored P(start)=56% simultaneously. On 2026-08-25, three days before
MD1, nine live market candidates all came back with the identical availability
term; the buy ranking was a pure points-when-playing ranking with availability
held constant.

That flatters exactly the wrong player. Can Uzun started 15 of 34 matchdays in
2025/26 -- unused sub or out of squad for MD10-13, MD17, MD20-26 and MD28 --
and was ranked above Nathaniel Brown, who started 30 of 34. Uzun contributes
18% fewer points per matchday and the model could not see it.

Direction matters, for the reason `test_availability_override.py` records:
quality is pooled across statuses, so rate(5) overstates a true starter's mean
by ~24% and stays calibrated only because P(5) is the fitted ~82% rather than
100%. Forcing probability DOWNWARD cannot inflate anything. So this correction
is downward-only, like the injury override beside it.

The scaling quantity is *played share* (statuses 3+5 against all), never start
share. Pooled quality already absorbs each player's 3-vs-5 mix; the split it
does not encode is played-vs-not. Scaling P(3) and P(5) by a common factor
corrects the second without double-counting the first.
"""

import pytest

from rehoboam.scoring.v2.availability import apply_stale_history_prior

# The fitted marginal prior from coefficients.json -- what every player
# currently receives pre-season.
FITTED_PRIOR: dict[int, float] = {1: 0.0388, 3: 0.1945, 4: 0.2089, 5: 0.5578}
PRIOR_PLAYED_SHARE = FITTED_PRIOR[3] + FITTED_PRIOR[5]  # 0.7523


class TestBelowAverageAvailabilityIsReduced:
    def test_uzun_loses_start_probability(self):
        """21 of 34 matchdays played -- the case that motivated the ticket."""
        out = apply_stale_history_prior(
            FITTED_PRIOR,
            played=21,
            matchdays=34,
            prior_played_share=PRIOR_PLAYED_SHARE,
            shrinkage_k=20.0,
        )
        assert out[5] < FITTED_PRIOR[5]
        assert out[5] == pytest.approx(0.4949, abs=0.001)

    def test_the_freed_mass_becomes_did_not_play(self):
        out = apply_stale_history_prior(
            FITTED_PRIOR,
            played=21,
            matchdays=34,
            prior_played_share=PRIOR_PLAYED_SHARE,
            shrinkage_k=20.0,
        )
        assert out[1] > FITTED_PRIOR[1]

    def test_unused_sub_probability_is_left_alone(self):
        """Status 4 is not evidence about the played-vs-not split we correct."""
        out = apply_stale_history_prior(
            FITTED_PRIOR,
            played=21,
            matchdays=34,
            prior_played_share=PRIOR_PLAYED_SHARE,
            shrinkage_k=20.0,
        )
        assert out[4] == pytest.approx(FITTED_PRIOR[4])


class TestAboveAverageAvailabilityIsNeverInflated:
    def test_brown_is_returned_unchanged(self):
        """33 of 34 matchdays. Under-rated, but inflating him would expose the
        ~24% starter bias that the paired availability+rate models cancel."""
        out = apply_stale_history_prior(
            FITTED_PRIOR,
            played=33,
            matchdays=34,
            prior_played_share=PRIOR_PLAYED_SHARE,
            shrinkage_k=20.0,
        )
        assert out == pytest.approx(FITTED_PRIOR)

    def test_an_ever_present_is_returned_unchanged(self):
        out = apply_stale_history_prior(
            FITTED_PRIOR,
            played=34,
            matchdays=34,
            prior_played_share=PRIOR_PLAYED_SHARE,
            shrinkage_k=20.0,
        )
        assert out[5] == pytest.approx(FITTED_PRIOR[5])


class TestTheStartVersusSubMixIsPreserved:
    def test_scaling_does_not_change_the_ratio_of_start_to_sub(self):
        """The property that avoids double-counting against pooled quality.

        `rate.py` records that quality normalises against a reference pooled
        over statuses 3 and 5, so the 3-vs-5 mix is already in the quality
        coefficient. Only the played-vs-not split is new information.
        """
        out = apply_stale_history_prior(
            FITTED_PRIOR,
            played=10,
            matchdays=34,
            prior_played_share=PRIOR_PLAYED_SHARE,
            shrinkage_k=20.0,
        )
        assert out[5] / out[3] == pytest.approx(FITTED_PRIOR[5] / FITTED_PRIOR[3])


class TestShrinkage:
    def test_a_short_record_is_pulled_toward_no_correction(self):
        """Six matchdays, none played, must not zero out a player.

        Above `min_matchdays`, so this exercises shrinkage rather than the
        guard: without it the ratio would be 0.0 and the player would score 0.
        """
        out = apply_stale_history_prior(
            FITTED_PRIOR,
            played=0,
            matchdays=6,
            prior_played_share=PRIOR_PLAYED_SHARE,
            shrinkage_k=20.0,
        )
        assert out[5] > 0.4 * FITTED_PRIOR[5]

    def test_a_long_record_moves_further_than_a_short_one(self):
        """Same 50% share, but 34 matchdays is stronger evidence than 10."""
        short = apply_stale_history_prior(
            FITTED_PRIOR,
            played=5,
            matchdays=10,
            prior_played_share=PRIOR_PLAYED_SHARE,
            shrinkage_k=20.0,
        )
        long_ = apply_stale_history_prior(
            FITTED_PRIOR,
            played=17,
            matchdays=34,
            prior_played_share=PRIOR_PLAYED_SHARE,
            shrinkage_k=20.0,
        )
        assert long_[5] < short[5]


class TestGuards:
    def test_no_recorded_matchdays_leaves_the_prior_alone(self):
        """A cold-start player is not evidence of unavailability (REH-41)."""
        out = apply_stale_history_prior(
            FITTED_PRIOR,
            played=0,
            matchdays=0,
            prior_played_share=PRIOR_PLAYED_SHARE,
            shrinkage_k=20.0,
        )
        assert out == pytest.approx(FITTED_PRIOR)

    def test_a_record_below_the_minimum_leaves_the_prior_alone(self):
        out = apply_stale_history_prior(
            FITTED_PRIOR,
            played=0,
            matchdays=2,
            prior_played_share=PRIOR_PLAYED_SHARE,
            shrinkage_k=20.0,
            min_matchdays=5,
        )
        assert out == pytest.approx(FITTED_PRIOR)

    def test_a_zero_prior_played_share_cannot_divide_by_zero(self):
        out = apply_stale_history_prior(
            FITTED_PRIOR,
            played=10,
            matchdays=34,
            prior_played_share=0.0,
            shrinkage_k=20.0,
        )
        assert out == pytest.approx(FITTED_PRIOR)


class TestOutputIsAProbabilityDistribution:
    @pytest.mark.parametrize("played", [0, 7, 21, 33, 34])
    def test_probabilities_sum_to_one(self, played):
        out = apply_stale_history_prior(
            FITTED_PRIOR,
            played=played,
            matchdays=34,
            prior_played_share=PRIOR_PLAYED_SHARE,
            shrinkage_k=20.0,
        )
        assert sum(out.values()) == pytest.approx(1.0)
        assert all(0.0 <= p <= 1.0 for p in out.values())

    def test_the_input_mapping_is_not_mutated(self):
        before = dict(FITTED_PRIOR)
        apply_stale_history_prior(
            FITTED_PRIOR,
            played=10,
            matchdays=34,
            prior_played_share=PRIOR_PLAYED_SHARE,
            shrinkage_k=20.0,
        )
        assert FITTED_PRIOR == before
