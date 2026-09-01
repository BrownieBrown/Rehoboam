"""The club name reaches the proposal message (REH-117).

`score_player_v2` has `data.team_strength.team_name` in scope and dropped it,
so every proposal Marco received said "unknown club" — the market payload
carries `tid` and never `tn`, and nothing downstream had the name.
"""

from __future__ import annotations

from types import SimpleNamespace

from rehoboam.scoring.models import PlayerData
from rehoboam.scoring.v2.adapter import score_player_v2
from tests.test_scoring_v2.test_adapter import _player


def _data(*, team_strength=None, player_details=None) -> PlayerData:
    return PlayerData(
        player=_player("1"),
        performance=None,
        player_details=player_details,
        team_strength=team_strength,
        opponent_strength=None,
        is_dgw=False,
    )


def test_the_club_comes_from_team_strength():
    score = score_player_v2(_data(team_strength=SimpleNamespace(team_name="Bayer Leverkusen")))

    assert score.club == "Bayer Leverkusen"


def test_it_falls_back_to_player_details():
    score = score_player_v2(_data(player_details={"tn": "1. FC Union Berlin"}))

    assert score.club == "1. FC Union Berlin"


def test_an_unknown_club_is_empty_not_a_guess():
    """Better blank than wrong — a wrong club actively misleads."""
    assert score_player_v2(_data()).club == ""
