"""The absolute target bar (2026-08-22 design).

Marginal gain answers "is he worth today's price and who does he displace".
It cannot answer "is this player worth a squad slot at all", because a large
marginal gain against a weak squad still describes a mediocre player. The bar
is that second, absolute question.
"""

from rehoboam.scoring.decision import DecisionEngine
from tests.test_scoring.test_trade_pairs import _make_player, _make_score, _squad


def _engine(bar):
    return DecisionEngine(min_ep_to_buy=35.0, min_ep_upgrade=40.0, target_ep_bar=bar)


def _recommend(engine, ep, *, is_emergency=False):
    squad_players, squad_scores = _squad()
    cand = _make_player("cand", "Midfielder", price=5_000_000)
    return engine.recommend_buys(
        market_scores=[_make_score("cand", ep, "Midfielder", price=5_000_000)],
        squad_scores=squad_scores,
        roster_context={},
        budget=80_000_000,
        market_players={"cand": cand},
        squad_players=squad_players,
        is_emergency=is_emergency,
    )


class TestTargetBar:
    def test_player_below_the_bar_is_not_recommended(self):
        """Large marginal gain against a weak squad is still a mediocre player."""
        assert _recommend(_engine(100.0), 70.0) == []

    def test_player_above_the_bar_is_recommended(self):
        assert len(_recommend(_engine(100.0), 120.0)) == 1

    def test_zero_bar_preserves_existing_behaviour(self):
        assert len(_recommend(_engine(0.0), 70.0)) == 1

    def test_the_bar_yields_in_an_emergency(self):
        """-100 for an empty slot dwarfs the cost of a mediocre signing."""
        assert len(_recommend(_engine(100.0), 70.0, is_emergency=True)) == 1
