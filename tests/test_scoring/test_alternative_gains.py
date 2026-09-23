"""The runner-up: what the bot gets if it loses this listing."""

from __future__ import annotations

from rehoboam.scoring.decision import alternative_gains
from rehoboam.scoring.models import BuyRecommendation
from tests.test_scoring.test_gap_fill_gain import _player, _score


def _rec(pid, gain, pos):
    return BuyRecommendation(
        score=_score(pid, 50.0, pos),
        player=_player(pid, pos),
        marginal_ep_gain=gain,
        effective_ep=50.0,
        replaces_player_id=None,
        replaces_player_name=None,
        roster_impact="upgrade",
        roster_bonus=0.0,
        reason="test",
    )


def test_the_alternative_is_the_best_other_candidate_at_the_position():
    recs = [
        _rec("burger", 46.3, "Midfielder"),
        _rec("schick", 41.0, "Forward"),
        _rec("mid2", 30.0, "Midfielder"),
    ]
    alt = alternative_gains(recs)
    assert alt["burger"] == 30.0
    assert alt["mid2"] == 46.3
    assert alt["schick"] == 0.0  # unique at his position


def test_a_lone_candidate_is_unique():
    assert alternative_gains([_rec("only", 20.0, "Defender")]) == {"only": 0.0}


def test_no_candidates_no_entries():
    assert alternative_gains([]) == {}
