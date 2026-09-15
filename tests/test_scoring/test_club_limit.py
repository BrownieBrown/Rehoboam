"""Kickbase allows three players per club; a fourth never makes the board.

The safety gate already refuses a fourth at buy time (REH-100). On 2026-09-15
the board still listed Henrichs first with three Leipzig players held, so the
emergency basket spent its pick on a player it could not buy. The filter
belongs where the list is built.
"""

from __future__ import annotations

from rehoboam.kickbase_client import MarketPlayer
from rehoboam.scoring.decision import DecisionEngine
from tests.test_scoring.test_trade_pairs import _make_score, _squad


def _player(player_id, position, team_id, price=5_000_000):
    return MarketPlayer(
        id=player_id,
        first_name="Test",
        last_name=player_id,
        position=position,
        team_id=team_id,
        team_name="Test FC",
        price=price,
        market_value=price,
        points=100,
        average_points=12.0,
        status=0,
    )


def _recommend(candidates, *, squad_team_ids):
    squad_players, squad_scores = _squad()
    for pid, team_id in zip(list(squad_players), squad_team_ids, strict=False):
        squad_players[pid] = _player(pid, squad_players[pid].position, team_id)
    engine = DecisionEngine(min_ep_to_buy=35.0, min_ep_upgrade=40.0, target_ep_bar=0.0)
    return engine.recommend_buys(
        market_scores=[
            _make_score(pid, 120.0, "Midfielder", price=5_000_000) for pid, _ in candidates
        ],
        squad_scores=squad_scores,
        roster_context={},
        budget=80_000_000,
        market_players={pid: _player(pid, "Midfielder", team_id) for pid, team_id in candidates},
        squad_players=squad_players,
    )


def test_a_fourth_player_from_a_club_is_not_recommended():
    recs = _recommend(
        [("blocked", "7"), ("fine", "8")],
        squad_team_ids=["7", "7", "7"] + ["1"] * 20,
    )
    assert [r.player.id for r in recs] == ["fine"]


def test_two_from_a_club_leaves_room_for_a_third():
    recs = _recommend(
        [("third", "7")],
        squad_team_ids=["7", "7"] + ["1"] * 20,
    )
    assert [r.player.id for r in recs] == ["third"]


def test_an_unknown_club_is_not_blocked():
    recs = _recommend([("nobody", "")], squad_team_ids=["7"] * 23)
    assert [r.player.id for r in recs] == ["nobody"]
