"""Serving-time availability overrides (REH-52 item 4).

The fitted availability model predicts P(status | previous status) from history
alone. Kickbase's live injury flag has no historical counterpart -- it does not
publish what a player's status was on matchday 12 of 2023/24 -- so it cannot be
fitted. It enters here, at serving time, explicitly unfitted.

Direction matters. `rate.py` warns that overriding P(status) upward exposes a
~24% starter overshoot that the paired availability+rate models currently
cancel: quality is pooled across statuses, so rate(5) overstates a true
starter's mean and only stays calibrated because P(5) is the fitted ~82%
rather than 100%. Forcing probability DOWNWARD drives EP toward zero and
cannot inflate anything, so these overrides are downward-only by construction.

Kickbase live injury status codes, verified against `stxt` on the live market
2026-08-28:
    0 = healthy, 1 = injured (out for weeks), 2 = uncertain,
    4 = short-term injury, 256 = long-term injury

Note these are NOT the played-status codes the probability dicts are keyed by
(1 = not in squad, 3 = came on, 4 = unused sub, 5 = started). The two
namespaces collide on 1 and 4; see the comment in availability.py.

The v1 scorer applied -30/-20/-10 point penalties for these. v2 dropped the
signal entirely, which is how a long-term-injured player came to be scored 47.2
EP and named in the starting eleven on 2026-08-22.
"""

import pytest

from rehoboam.scoring.v2.availability import apply_availability_override


def _even_probs() -> dict[int, float]:
    """A starter-ish distribution, roughly what the fitted model returns."""
    return {1: 0.05, 3: 0.10, 4: 0.05, 5: 0.80}


class TestInjuryForcesDidNotPlay:
    def test_long_term_injury_removes_the_start_probability(self):
        """Hoeler: status 256, scored 47.2 EP, named in the starting eleven."""
        out = apply_availability_override(_even_probs(), live_status=256)
        assert out[5] == pytest.approx(0.0)
        assert out[1] == pytest.approx(1.0)

    def test_short_term_injury_removes_it_too(self):
        out = apply_availability_override(_even_probs(), live_status=4)
        assert out[5] == pytest.approx(0.0)

    def test_out_for_weeks_injury_removes_it_too(self):
        """Gantenbein: status 1, "Ankle injury - out for several weeks".

        Returned as the #1 buy recommendation in the live 2026-08-28 session,
        priced at a EUR 1,163,017 bid with a sell plan attached, because
        status 1 was in neither override set and so scored as fully fit
        (REH-105).
        """
        out = apply_availability_override(_even_probs(), live_status=1)
        assert out[5] == pytest.approx(0.0)
        assert out[1] == pytest.approx(1.0)


class TestUncertainIsReducedNotBlocked:
    def test_uncertain_reduces_the_start_probability(self):
        """Fuehrich: status 2, market value rising 15.7% over 30d.

        Same code as a collapsing player, opposite meaning -- so status 2 is a
        haircut, never a block.
        """
        out = apply_availability_override(
            _even_probs(), live_status=2, uncertain_start_multiplier=0.5
        )
        assert out[5] == pytest.approx(0.40)

    def test_uncertain_does_not_zero_the_player(self):
        out = apply_availability_override(
            _even_probs(), live_status=2, uncertain_start_multiplier=0.5
        )
        assert out[5] > 0.0


class TestHealthyAndUnknownAreUntouched:
    def test_healthy_is_unchanged(self):
        probs = _even_probs()
        assert apply_availability_override(probs, live_status=0) == probs

    def test_unknown_status_is_unchanged(self):
        """Fail open. A details fetch that failed is not evidence of injury."""
        probs = _even_probs()
        assert apply_availability_override(probs, live_status=None) == probs

    def test_unrecognised_status_is_unchanged(self):
        probs = _even_probs()
        assert apply_availability_override(probs, live_status=9999) == probs


class TestInvariants:
    @pytest.mark.parametrize("status", [None, 0, 1, 2, 4, 256, 9999])
    def test_the_override_never_raises_the_start_probability(self, status):
        """Downward-only. Raising P(start) would expose rate.py's ~24% overshoot."""
        before = _even_probs()
        after = apply_availability_override(before, live_status=status)
        assert after[5] <= before[5] + 1e-9

    @pytest.mark.parametrize("status", [None, 0, 1, 2, 4, 256, 9999])
    def test_probabilities_still_sum_to_one(self, status):
        after = apply_availability_override(_even_probs(), live_status=status)
        assert sum(after.values()) == pytest.approx(1.0)


class TestMultiplierIsClamped:
    def test_a_multiplier_above_one_cannot_raise_the_start_probability(self):
        """The knob is .env-tunable, so it must not be able to invert the rule.

        A multiplier > 1 would turn an uncertain flag into a promotion and
        reintroduce exactly the upward override rate.py warns about.
        """
        before = _even_probs()
        after = apply_availability_override(before, live_status=2, uncertain_start_multiplier=1.5)
        assert after[5] <= before[5] + 1e-9

    def test_a_negative_multiplier_does_not_produce_negative_probability(self):
        after = apply_availability_override(
            _even_probs(), live_status=2, uncertain_start_multiplier=-2.0
        )
        assert all(p >= 0.0 for p in after.values())
        assert sum(after.values()) == pytest.approx(1.0)
