"""An empty slot is not a 0.0-point starter (session 9c0a6a742dba, 2026-09-22).

`calculate_marginal_ep` measures a candidate against the current best eleven.
With ten players every candidate "displaces the weakest starter (0.0)", so the
marginal gain is his whole EP: three defenders scored on the position prior —
Itakura, Kosugi, Mensah, all 74.9, data grade C — cleared the must-have bar of
62.5 and were bid at up to +35%. Nothing about them was must-have; the slot was
empty.

The rule these tests pin: when the candidate fills an empty slot rather than
displacing a player, his gain is measured against the replacement level for
his position — what the scorer would give an unknown regular starter there
(`cold_start_starter_ep`). A player indistinguishable from that prior gains
nothing; a player who beats it by 46 points is a strong upgrade, not a
must-have. Without a replacement map the old behaviour stands.

`plan_buys` re-measures gains as the squad fills and must apply the same rule,
or its re-measured number silently restores the full-EP gain for the first
pick.
"""

from __future__ import annotations

import pytest

from rehoboam.kickbase_client import MarketPlayer
from rehoboam.scoring.decision import DecisionEngine, plan_buys
from rehoboam.scoring.models import BuyRecommendation, DataQuality, PlayerScore

# The scorer's cold-start starter EP per position on 2026-09-22.
REPLACEMENT = {"Goalkeeper": 81.5, "Defender": 74.9, "Midfielder": 68.4, "Forward": 62.4}


def _score(pid, ep, position):
    dq = DataQuality(
        grade="A",
        games_played=10,
        consistency=0.8,
        has_fixture_data=True,
        has_lineup_data=True,
        warnings=[],
    )
    return PlayerScore(
        player_id=pid,
        expected_points=ep,
        data_quality=dq,
        base_points=ep,
        consistency_bonus=0.0,
        lineup_bonus=0.0,
        fixture_bonus=0.0,
        form_bonus=0.0,
        minutes_bonus=0.0,
        dgw_multiplier=1.0,
        is_dgw=False,
        next_opponent=None,
        notes=[],
        current_price=5_000_000,
        market_value=5_000_000,
        position=position,
    )


def _player(pid, position, price=5_000_000):
    return MarketPlayer(
        id=pid,
        first_name="Test",
        last_name=pid,
        position=position,
        team_id="t1",
        team_name="Test FC",
        price=price,
        market_value=price,
        points=100,
        average_points=12.0,
        status=0,
    )


def _short_squad():
    """Tonight's shape: 1 GK, 4 DEF, 2 MID, 3 FW — ten players, every one a starter."""
    spec = [
        ("gk", "Goalkeeper", 1),
        ("def", "Defender", 4),
        ("mid", "Midfielder", 2),
        ("fw", "Forward", 3),
    ]
    squad, scores = [], []
    for stem, pos, n in spec:
        for i in range(n):
            squad.append(_player(f"{stem}{i}", pos))
            scores.append(_score(f"{stem}{i}", 50.0, pos))
    return squad, scores


def _full_squad():
    squad, scores = _short_squad()
    squad.append(_player("mid_weak", "Midfielder"))
    scores.append(_score("mid_weak", 25.0, "Midfielder"))
    return squad, scores


def _gain(engine, squad, scores, pid, ep, pos):
    return engine.calculate_marginal_ep(
        candidate_score=_score(pid, ep, pos),
        candidate_player=_player(pid, pos),
        squad=squad,
        squad_scores=scores,
    )


class TestFillingAnEmptySlot:
    def test_a_player_at_the_position_prior_gains_nothing(self):
        squad, scores = _short_squad()
        engine = DecisionEngine(replacement_ep=REPLACEMENT)
        result = _gain(engine, squad, scores, "itakura", 74.9, "Defender")
        assert result.marginal_ep_gain == pytest.approx(0.0)
        assert result.fills_empty_slot is True

    def test_a_player_above_the_prior_gains_the_difference(self):
        squad, scores = _short_squad()
        engine = DecisionEngine(replacement_ep=REPLACEMENT)
        result = _gain(engine, squad, scores, "burger", 114.7, "Midfielder")
        assert result.marginal_ep_gain == pytest.approx(114.7 - 68.4)
        assert result.fills_empty_slot is True
        assert result.replaces_player_id is None
        assert result.replaces_player_ep == pytest.approx(68.4)

    def test_the_reference_is_reported_for_the_board(self):
        squad, scores = _short_squad()
        engine = DecisionEngine(replacement_ep=REPLACEMENT)
        result = _gain(engine, squad, scores, "burger", 114.7, "Midfielder")
        assert result.replaces_player_name is None
        assert result.replacement_ep == pytest.approx(68.4)

    def test_without_a_replacement_map_the_old_rule_stands(self):
        squad, scores = _short_squad()
        engine = DecisionEngine()
        result = _gain(engine, squad, scores, "burger", 114.7, "Midfielder")
        assert result.marginal_ep_gain == pytest.approx(114.7)

    def test_an_unknown_position_falls_back_to_the_old_rule(self):
        squad, scores = _short_squad()
        engine = DecisionEngine(replacement_ep={"Defender": 74.9})
        result = _gain(engine, squad, scores, "burger", 114.7, "Midfielder")
        assert result.marginal_ep_gain == pytest.approx(114.7)


class TestDisplacingAPlayerIsUnchanged:
    def test_the_gain_is_still_measured_against_the_displaced_starter(self):
        squad, scores = _full_squad()
        engine = DecisionEngine(replacement_ep=REPLACEMENT)
        result = _gain(engine, squad, scores, "new_mid", 55.0, "Midfielder")
        assert result.marginal_ep_gain == pytest.approx(55.0 - 25.0)
        assert result.fills_empty_slot is False
        assert result.replaces_player_id == "mid_weak"


class TestPlanBuysAppliesTheSameRule:
    def _rec(self, pid, ep, pos, price):
        return BuyRecommendation(
            score=_score(pid, ep, pos),
            player=_player(pid, pos, price=price),
            marginal_ep_gain=0.0,
            effective_ep=ep,
            replaces_player_id=None,
            replaces_player_name=None,
            roster_impact="additional",
            roster_bonus=0.0,
            reason="test",
        )

    def test_the_first_pick_into_an_empty_slot_carries_the_gap_fill_gain(self):
        squad, scores = _short_squad()
        lineup_map = {s.player_id: s.expected_points for s in scores}
        recs = [self._rec("burger", 114.7, "Midfielder", 18_000_000)]
        ordered = plan_buys(
            recs,
            budget=50_000_000,
            squad=squad,
            lineup_map=lineup_map,
            replacement_ep=REPLACEMENT,
        )
        assert ordered[0].marginal_ep_gain == pytest.approx(114.7 - 68.4)

    def test_a_pick_that_displaces_a_starter_is_unchanged(self):
        squad, scores = _full_squad()
        lineup_map = {s.player_id: s.expected_points for s in scores}
        recs = [self._rec("new_mid", 55.0, "Midfielder", 5_000_000)]
        ordered = plan_buys(
            recs,
            budget=50_000_000,
            squad=squad,
            lineup_map=lineup_map,
            replacement_ep=REPLACEMENT,
        )
        assert ordered[0].marginal_ep_gain == pytest.approx(30.0)
