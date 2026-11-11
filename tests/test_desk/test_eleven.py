"""The best legal eleven (spec v3 §5.3 C1)."""

from __future__ import annotations

import pytest

from rehoboam.desk.eleven import EMPTY_SLOT_POINTS, best_eleven
from rehoboam.desk.model import Player


def _p(pid, position, ep):
    return Player(pid, position, market_value=1_000_000, ep_next=ep, drift=0.0)


def _squad(gk=1, df=4, md=4, fw=2, ep=50.0):
    return (
        [_p(f"g{i}", "Goalkeeper", ep) for i in range(gk)]
        + [_p(f"d{i}", "Defender", ep) for i in range(df)]
        + [_p(f"m{i}", "Midfielder", ep) for i in range(md)]
        + [_p(f"f{i}", "Forward", ep) for i in range(fw)]
    )


def test_a_full_squad_fields_eleven_without_penalty():
    score, eleven = best_eleven(_squad())

    assert len(eleven) == 11
    assert score == pytest.approx(11 * 50.0)


def test_the_formation_follows_the_best_players():
    squad = _squad(df=3, md=3, fw=3) + [
        _p("f_star", "Forward", 200.0),
        _p("m_extra", "Midfielder", 10.0),
    ]

    score, eleven = best_eleven(squad)

    assert "f_star" in {p.player_id for p in eleven}
    # 3-4-3 with the star: no fourth defender for 4-2-4, so all four midfielders play.
    assert score == pytest.approx(50.0 + 3 * 50.0 + (3 * 50.0 + 10.0) + (200.0 + 2 * 50.0))


def test_an_empty_slot_costs_one_hundred():
    score, eleven = best_eleven(_squad(gk=0))

    assert len(eleven) == 10
    assert score == pytest.approx(10 * 50.0 + EMPTY_SLOT_POINTS)
