"""A sell plan never funds a buy by dumping a player the rank rule keeps.

2026-10-05 10:00: three over-budget buys from the night before carried a sell
plan, and the plan sold Castello Jr. — 14th of all defenders by points per
appearance, out "uncertain" that week with an EP of 11 — at 12.8m against a
27.1m cost (-14.3m realised), to fund a 7m midfielder. The plan ranks by this
week's EP, so an injured top player is its first pick. The rank rule holds him
on purpose; the plan has to respect that.
"""

from __future__ import annotations

from rehoboam.scoring.decision import DecisionEngine
from rehoboam.services.rank_sell import rank_hold_ids
from tests.test_scoring.test_decision import _make_player, _make_score


def _squad():
    squad, scores = [], []
    squad.append(_make_player("gk", "Goalkeeper"))
    scores.append(_make_score("gk", 70.0, "Goalkeeper", market_value=8_000_000))
    for i in range(4):
        squad.append(_make_player(f"d{i}", "Defender"))
        scores.append(_make_score(f"d{i}", 60.0 - i, "Defender", market_value=8_000_000))
    for i in range(4):
        squad.append(_make_player(f"m{i}", "Midfielder"))
        scores.append(_make_score(f"m{i}", 55.0 - i, "Midfielder", market_value=8_000_000))
    squad.append(_make_player("fw", "Forward"))
    scores.append(_make_score("fw", 50.0, "Forward", market_value=8_000_000))
    # The injured top player: lowest EP, so the plan's first pick.
    squad.append(_make_player("castello", "Defender"))
    scores.append(_make_score("castello", 11.0, "Defender", market_value=5_000_000))
    # A genuine benchwarmer.
    squad.append(_make_player("bench", "Midfielder"))
    scores.append(_make_score("bench", 20.0, "Midfielder", market_value=5_000_000))
    return squad, scores


BEST = {"gk", "d0", "d1", "d2", "d3", "m0", "m1", "m2", "m3", "fw"}


class TestTheSellPlanRespectsTheHold:
    def test_without_protection_the_injured_top_player_is_the_first_pick(self):
        squad, scores = _squad()
        plan = DecisionEngine().build_sell_plan(
            bid_amount=4_000_000,
            current_budget=0,
            squad=squad,
            squad_scores=scores,
            best_11_ids=BEST,
            displaced_player_id=None,
        )
        assert [e.player_id for e in plan.players_to_sell] == ["castello"]

    def test_a_protected_player_is_never_in_the_plan(self):
        squad, scores = _squad()
        plan = DecisionEngine().build_sell_plan(
            bid_amount=4_000_000,
            current_budget=0,
            squad=squad,
            squad_scores=scores,
            best_11_ids=BEST,
            displaced_player_id=None,
            protected_ids=frozenset({"castello"}),
        )
        assert [e.player_id for e in plan.players_to_sell] == ["bench"]

    def test_the_hold_stands_even_when_the_plan_cannot_cover(self):
        squad, scores = _squad()
        plan = DecisionEngine().build_sell_plan(
            bid_amount=200_000_000,
            current_budget=0,
            squad=squad,
            squad_scores=scores,
            best_11_ids=BEST,
            displaced_player_id=None,
            protected_ids=frozenset({"castello"}),
        )
        assert not plan.is_viable
        assert "castello" not in [e.player_id for e in plan.players_to_sell]


class TestRankHoldIds:
    """Who the rank rule keeps as a top player: inside the floor on either
    measure, with enough appearances. Unknown quality is not protected —
    protecting every new player would make every sell plan impossible."""

    RANKS = {
        "castello": {"avg_points_rank_pos": 14, "ep_rank_pos": 181, "appearances": 2},
        "raum": {"avg_points_rank_pos": 21, "ep_rank_pos": 1, "appearances": 4},
        "wolfe": {"avg_points_rank_pos": 100, "ep_rank_pos": 43, "appearances": 4},
        "new": {"avg_points_rank_pos": 3, "ep_rank_pos": 2, "appearances": 1},
        "unknown": {"avg_points_rank_pos": None, "ep_rank_pos": 5, "appearances": 0},
    }

    def test_top_on_either_measure_is_held(self):
        held = rank_hold_ids(self.RANKS, floor=30, min_appearances=2)
        assert held == {"castello", "raum"}

    def test_an_empty_read_protects_nobody(self):
        assert rank_hold_ids({}, floor=30, min_appearances=2) == set()
