from rehoboam.replay.rules import (
    EMPTY_SLOT_PENALTY,
    MAX_PER_TEAM,
    MAX_SQUAD_SIZE,
    can_buy,
    can_field_eleven,
    empty_slot_penalty,
)
from rehoboam.replay.state import ReplayPlayer, ReplayState


def _p(pid, pos="Forward", team="40"):
    return ReplayPlayer(id=pid, position=pos, team_id=team)


def _squad(spec):
    return {pid: _p(pid, pos, team) for pid, pos, team in spec}


def test_buy_allowed_within_all_limits():
    state = ReplayState(budget=10_000_000, squad={})
    assert can_buy(state, _p("x"), 5_000_000, team_value=50_000_000) == (True, "")


def test_buy_blocked_at_squad_cap():
    squad = _squad([(str(i), "Forward", str(i)) for i in range(MAX_SQUAD_SIZE)])
    state = ReplayState(budget=100_000_000, squad=squad)
    allowed, reason = can_buy(state, _p("new", team="99"), 1_000_000, team_value=50_000_000)
    assert allowed is False
    assert "squad full" in reason


def test_buy_blocked_at_team_limit():
    squad = _squad([(str(i), "Forward", "40") for i in range(MAX_PER_TEAM)])
    state = ReplayState(budget=100_000_000, squad=squad)
    allowed, reason = can_buy(state, _p("new", team="40"), 1_000_000, team_value=50_000_000)
    assert allowed is False
    assert "team limit" in reason


def test_buy_allowed_for_different_team_at_team_limit():
    squad = _squad([(str(i), "Forward", "40") for i in range(MAX_PER_TEAM)])
    state = ReplayState(budget=100_000_000, squad=squad)
    assert can_buy(state, _p("new", team="7"), 1_000_000, team_value=50_000_000)[0] is True


def test_buy_allowed_into_negative_budget_within_credit_line():
    # credit line = 70% of 50,000,000 = 35,000,000
    state = ReplayState(budget=0, squad={})
    assert can_buy(state, _p("x"), 30_000_000, team_value=50_000_000)[0] is True


def test_buy_blocked_beyond_credit_line():
    state = ReplayState(budget=0, squad={})
    allowed, reason = can_buy(state, _p("x"), 40_000_000, team_value=50_000_000)
    assert allowed is False
    assert "credit line" in reason


def test_credit_line_boundary_is_inclusive():
    state = ReplayState(budget=0, squad={})
    assert can_buy(state, _p("x"), 35_000_000, team_value=50_000_000)[0] is True


def test_can_field_eleven_accepts_a_shape_that_can_actually_start():
    """REH-68: this fixture used to be 1 GK / 3 DEF / 6 MID / 1 FW and asserted
    True, but midfielders cap at 5 across every Kickbase formation, so only ten
    of those eleven could ever start. The old guard checked position *minimums*
    only and waved it through. Corrected to a shape that genuinely fields
    eleven: 1 + 4 + 5 + 1.
    """
    ok = _squad(
        [("g", "Goalkeeper", "1")]
        + [(f"d{i}", "Defender", str(i)) for i in range(4)]
        + [(f"m{i}", "Midfielder", str(i + 10)) for i in range(5)]
        + [("f", "Forward", "20")]
    )
    assert can_field_eleven(ReplayState(budget=0, squad=ok)) is True


def test_can_field_eleven_false_without_goalkeeper():
    squad = _squad([(f"m{i}", "Midfielder", str(i)) for i in range(11)])
    assert can_field_eleven(ReplayState(budget=0, squad=squad)) is False


def test_can_field_eleven_false_with_too_few_players():
    squad = _squad([(f"m{i}", "Midfielder", str(i)) for i in range(10)])
    assert can_field_eleven(ReplayState(budget=0, squad=squad)) is False


def test_empty_slot_penalty_scales_with_missing_slots():
    assert empty_slot_penalty(11) == 0
    assert empty_slot_penalty(10) == EMPTY_SLOT_PENALTY
    assert empty_slot_penalty(9) == 2 * EMPTY_SLOT_PENALTY
