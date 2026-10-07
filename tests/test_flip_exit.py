"""A flip exits at the turn: the first night its value does not rise.

Measured 2026-10-07 on 48 momentum runs of six or more nights: selling the
morning after the first non-positive night captured 82% of the peak gain on
average; after a run of eight-plus nights ends, the next three nights cost a
median -13.7%. A target sells too early while the run continues (held 8-30
days after a rising-night entry: median +10.9%, 68% wins), and a stop-loss
from cost sells far too late. So: hold for the run, sell at the turn; the
stop-loss stays as the floor, and the old rules apply only when the last
move is unknown.
"""

from __future__ import annotations

from rehoboam.services.flip_exit import flip_exit_reason

KW = {"target_pct": 10.0, "max_loss_pct": -15.0, "exit_on_turn": True}


def _why(profit_pct, last_move_pct, **over):
    kw = {**KW, **over}
    return flip_exit_reason(profit_pct=profit_pct, last_move_pct=last_move_pct, **kw)


class TestTheTurn:
    def test_the_first_non_positive_night_sells_at_a_profit(self):
        assert _why(5.0, -1.2) == "Flip exit at the turn: last night -1.2%, +5.0% vs cost"

    def test_a_flat_night_is_a_turn_too(self):
        assert _why(5.0, 0.0) is not None

    def test_the_turn_sells_at_a_loss_as_well(self):
        # Kömür: bought after the turn, -12% then -9% the next nights. Taking
        # -3% beats waiting for -15%.
        assert _why(-3.0, -8.0) == "Flip exit at the turn: last night -8.0%, -3.0% vs cost"


class TestHoldForTheRun:
    def test_past_the_target_but_still_rising_is_a_hold(self):
        assert _why(12.0, 2.0) is None

    def test_still_rising_below_the_target_is_a_hold(self):
        assert _why(3.0, 1.0) is None

    def test_the_stop_loss_is_still_the_floor_while_rising(self):
        # A rising night after a -20% hole: the floor fires regardless.
        assert _why(-20.0, 0.5) == "Flip stop-loss (-15%): -20.0%"


class TestWithoutALastMove:
    def test_the_target_rule_applies(self):
        assert _why(12.0, None) == "Flip target (10%) hit: +12.0%"

    def test_below_target_holds(self):
        assert _why(5.0, None) is None

    def test_the_stop_loss_applies(self):
        assert _why(-16.0, None) == "Flip stop-loss (-15%): -16.0%"


class TestTheSwitch:
    def test_turned_off_the_target_rule_is_back(self):
        assert _why(12.0, 2.0, exit_on_turn=False) == "Flip target (10%) hit: +12.0%"
        assert _why(5.0, -1.2, exit_on_turn=False) is None
