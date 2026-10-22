"""ligainsider's verdict as the stand-in for Kickbase's lineup code.

Kickbase's `prob` went behind Kickbase Premium on 2026-10-21 (absent from
`/players/{id}` for every player). ligainsider's predicted eleven becomes its
own fitted feature: measured on matchdays 5–6, named starters started 87%,
alternatives 27%, left out 4%, missing 0%. It is read only where Kickbase sent
no code, and must leave the row untouched when it has nothing to say.
"""

from __future__ import annotations

import pytest

from rehoboam.scoring.decision import unlikely_starter
from rehoboam.scoring.models import DataQuality, PlayerScore
from rehoboam.scoring.v2 import adapter
from rehoboam.scoring.v2.availability import AvailabilityModel
from rehoboam.scoring.v2.lineup_prob import (
    OUTSIDE_ALTERNATIVE,
    OUTSIDE_CONTESTED,
    OUTSIDE_LEFT_OUT,
    OUTSIDE_MISSING,
    OUTSIDE_NAMED,
    OUTSIDE_SURE,
    OUTSIDE_TIER_MIN_OBSERVATIONS,
    LineupProbModel,
    fit_lineup_prob,
    outside_codes,
    outside_in_xi,
    scorer_inputs,
)

MARKOV_STARTER = {1: 0.01, 3: 0.089, 4: 0.064, 5: 0.837}
COVER = 200


def _outside(sure_n: int = 0, contested_n: int = 0) -> LineupProbModel:
    """A fitted model: pooled named mostly started, left out mostly not."""
    counts = {
        OUTSIDE_NAMED: {1: 0, 3: 8, 4: 4, 5: 88},
        OUTSIDE_LEFT_OUT: {1: 2, 3: 25, 4: 69, 5: 4},
    }
    if sure_n:
        counts[OUTSIDE_SURE] = {1: 0, 3: 0, 4: 0, 5: sure_n}
    if contested_n:
        counts[OUTSIDE_CONTESTED] = {1: 0, 3: 0, 4: contested_n, 5: 0}
    return LineupProbModel(counts={}, outside=LineupProbModel(counts=counts))


class TestFit:
    def test_sure_and_in_doubt_also_count_toward_the_pooled_named_row(self):
        model = fit_lineup_prob(
            [],
            outside_observations=[
                (OUTSIDE_SURE, 5),
                (OUTSIDE_CONTESTED, 4),
                (OUTSIDE_NAMED, 5),
                (OUTSIDE_LEFT_OUT, 4),
                (None, 5),  # no verdict: not evidence
                (OUTSIDE_LEFT_OUT, 0),  # not played yet: not evidence
            ],
        )
        assert model.outside.observations(OUTSIDE_NAMED) == 3
        assert model.outside.observations(OUTSIDE_SURE) == 1
        assert model.outside.observations(OUTSIDE_LEFT_OUT) == 1

    def test_no_outside_evidence_fits_no_outside_model(self):
        assert fit_lineup_prob([(1, 5)]).outside is None

    def test_round_trips_through_its_dict_and_reads_an_older_file_without_one(self):
        model = _outside(sure_n=3)
        again = LineupProbModel.from_dict(model.to_dict())
        assert again.outside.counts == model.outside.counts
        assert LineupProbModel.from_dict({"counts": {"1": {"5": 1}}}).outside is None


class TestPredictOutside:
    def test_no_model_or_no_verdict_changes_nothing(self):
        assert LineupProbModel(counts={}).predict_outside(OUTSIDE_LEFT_OUT, MARKOV_STARTER) == (
            MARKOV_STARTER
        )
        assert _outside().predict_outside(None, MARKOV_STARTER) == MARKOV_STARTER

    def test_left_out_pulls_the_start_down(self):
        out = _outside().predict_outside(OUTSIDE_LEFT_OUT, MARKOV_STARTER)
        assert out[5] < 0.2
        assert sum(out.values()) == pytest.approx(1.0)

    def test_a_thin_tier_backs_off_to_the_pooled_named_counts(self):
        model = _outside(contested_n=OUTSIDE_TIER_MIN_OBSERVATIONS - 1)
        assert model.predict_outside(OUTSIDE_CONTESTED, MARKOV_STARTER) == model.outside.predict(
            OUTSIDE_NAMED, MARKOV_STARTER
        )

    def test_a_tier_with_enough_observations_is_served_on_its_own(self):
        model = _outside(contested_n=OUTSIDE_TIER_MIN_OBSERVATIONS)
        out = model.predict_outside(OUTSIDE_CONTESTED, MARKOV_STARTER)
        assert out == model.outside.predict(OUTSIDE_CONTESTED, MARKOV_STARTER)
        assert out[4] > 0.8  # this synthetic tier only ever came on


def _row(team, pid, slot, in_xi):
    return {"team_id": team, "player_id": pid, "slot": slot, "in_xi": in_xi}


class TestOutsideCodes:
    MEMBERS = [
        {"player_id": p, "team_id": t}
        for p, t in (("s1", "2"), ("s2", "2"), ("alt", "2"), ("miss", "2"), ("bench", "2"))
    ] + [{"player_id": "other", "team_id": "9"}]

    def test_a_linked_alternative_puts_his_starter_in_doubt(self):
        rows = [
            _row("2", "s1", 1, True),
            _row("2", "s2", 2, True),
            _row("2", "alt", COVER + 2, False),
            _row("2", "miss", None, False),
        ]
        codes = outside_codes(rows, self.MEMBERS, cover_slot_base=COVER)
        assert codes == {
            "s1": OUTSIDE_SURE,
            "s2": OUTSIDE_CONTESTED,
            "alt": OUTSIDE_ALTERNATIVE,
            "miss": OUTSIDE_MISSING,
            "bench": OUTSIDE_LEFT_OUT,
        }  # "other" plays for a club without a prediction: no verdict

    def test_a_club_stored_before_the_link_cannot_tell_sure_from_in_doubt(self):
        rows = [_row("2", "s1", 1, True), _row("2", "alt", 101, False)]
        codes = outside_codes(rows, self.MEMBERS, cover_slot_base=COVER)
        assert codes["s1"] == OUTSIDE_NAMED
        assert codes["alt"] == OUTSIDE_ALTERNATIVE

    def test_a_player_on_the_page_twice_keeps_the_strongest_verdict(self):
        rows = [_row("2", "s1", 1, True), _row("2", "s1", None, False), _row("2", None, 2, True)]
        codes = outside_codes(rows, self.MEMBERS, cover_slot_base=COVER)
        assert codes["s1"] == OUTSIDE_SURE
        assert codes["s2"] == OUTSIDE_LEFT_OUT  # the named row had no player id

    def test_a_name_the_matcher_could_not_resolve_withholds_the_verdict_from_namesakes(self):
        members = [
            {"player_id": "b1", "team_id": "8", "last_name": "Becker"},
            {"player_id": "b2", "team_id": "8", "last_name": "Becker"},
            {"player_id": "x", "team_id": "8", "last_name": "Karaman"},
        ]
        rows = [
            {
                "team_id": "8",
                "player_id": None,
                "player_name": "T. Becker",
                "slot": 2,
                "in_xi": True,
            }
        ]
        codes = outside_codes(rows, members, cover_slot_base=COVER)
        assert codes == {"x": OUTSIDE_LEFT_OUT}  # either Becker may be the starter

    def test_the_named_flag_and_the_scorer_inputs(self):
        assert [
            outside_in_xi(c) for c in (None, OUTSIDE_SURE, OUTSIDE_NAMED, OUTSIDE_LEFT_OUT)
        ] == [
            None,
            True,
            True,
            False,
        ]
        assert scorer_inputs(OUTSIDE_SURE, xi_enabled=False, fallback_enabled=True) == (
            None,
            OUTSIDE_SURE,
        )
        assert scorer_inputs(OUTSIDE_SURE, xi_enabled=True, fallback_enabled=False) == (
            True,
            None,
        )


class TestAvailabilityProbs:
    def _availability(self):
        return AvailabilityModel(
            transitions={5: MARKOV_STARTER}, prior=MARKOV_STARTER, shrinkage_k=20
        )

    def test_the_verdict_stands_in_only_where_kickbase_sent_no_code(self):
        model = LineupProbModel(
            counts={1: {1: 0, 3: 0, 4: 0, 5: 106}},
            outside=_outside().outside,
        )
        with_code = adapter.availability_probs(
            5,
            self._availability(),
            lineup_probability=1,
            lineup_model=model,
            outside_code=OUTSIDE_LEFT_OUT,
        )
        assert with_code[5] > 0.95  # Kickbase's code 1 decides
        without = adapter.availability_probs(
            5, self._availability(), lineup_model=model, outside_code=OUTSIDE_LEFT_OUT
        )
        assert without[5] < 0.2

    def test_the_injury_flag_still_wins_after_a_named_verdict(self):
        out = adapter.availability_probs(
            5,
            self._availability(),
            lineup_model=_outside(),
            outside_code=OUTSIDE_NAMED,
            live_status=256,
        )
        assert out[5] == pytest.approx(0.0)


def _score(lineup_probability=None, outside_lineup=None) -> PlayerScore:
    return PlayerScore(
        player_id="p",
        expected_points=50.0,
        data_quality=DataQuality(
            grade="A",
            games_played=0,
            consistency=0.0,
            has_fixture_data=False,
            has_lineup_data=False,
            warnings=[],
        ),
        base_points=0.0,
        consistency_bonus=0.0,
        lineup_bonus=0.0,
        fixture_bonus=0.0,
        form_bonus=0.0,
        minutes_bonus=0.0,
        dgw_multiplier=1.0,
        is_dgw=False,
        next_opponent=None,
        notes=[],
        current_price=1,
        market_value=1,
        average_points=0.0,
        position="Forward",
        lineup_probability=lineup_probability,
        outside_lineup=outside_lineup,
    )


@pytest.mark.parametrize(
    ("code", "outside", "skip"),
    [
        (4, None, True),
        (3, OUTSIDE_LEFT_OUT, False),  # Kickbase's code decides when it is there
        (None, OUTSIDE_LEFT_OUT, True),
        (None, OUTSIDE_MISSING, True),
        (None, OUTSIDE_ALTERNATIVE, False),
        (None, OUTSIDE_NAMED, False),
        (None, None, False),
    ],
)
def test_the_buy_filter_reads_the_verdict_when_kickbase_sent_no_code(code, outside, skip):
    assert unlikely_starter(_score(code, outside)) is skip
