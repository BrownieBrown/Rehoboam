"""The flip window (REH-109).

Zero flips since 2026-05-15. Not selection and not cash policy — the code path
did not execute. `allow_flips` was True in one phase only (`days_until_match
>= 5`), and measured against the real calendar on 2026-08-29 that window was
five hours wide: MD2's last fixture at 2026-08-30T13:30Z, MD3's first at
2026-09-04T18:30Z, so >= 5 days held only between 13:30 and 18:30 that Sunday.
The timer runs 08:00 and 20:00 UTC and missed it by 90 minutes.

Both gates rested on the same false premise — that a trade near a matchday
disturbs the eleven. It cannot: Kickbase locks the lineup at kickoff, and any
squad change lands on the FOLLOWING matchday. What genuinely survives is the
budget-at-kickoff rule (REH-11), which is about cash, not slots, and is
enforced elsewhere.

Hold length is why the two changes must ship together. Within rising-trend
entries — the only class the bot now buys, and the only profitable one — the
0-2 day bucket wins 13% of the time and lost EUR 9.67m over 23 trades, while
22d+ won 66.7% and made EUR 26.85m. Opening the window while
`_max_flip_hold_days` still forces a 1-3 day exit would enable exactly the
losing bucket.
"""

import pytest

from rehoboam.auto_trader import _max_flip_hold_days


def _phase(days, monkeypatch, **env):
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "test")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    from unittest.mock import MagicMock

    from rehoboam.auto_trader import AutoTrader
    from rehoboam.config import Settings

    trader = AutoTrader(api=MagicMock(), settings=Settings(), dry_run=True)
    return trader._get_matchday_phase(days)


class TestTheWindowIsOpenForMostOfTheWeek:
    @pytest.mark.parametrize("days", [2, 3, 4])
    def test_flips_are_allowed_in_the_moderate_phase(self, days, monkeypatch):
        """The change that makes the path executable at all. A Bundesliga round
        gap of ~5 days means 2-4 days out is most of the tradeable week."""
        assert _phase(days, monkeypatch).allow_flips is True

    @pytest.mark.parametrize("days", [5, 9])
    def test_the_aggressive_phase_still_allows_them(self, days, monkeypatch):
        assert _phase(days, monkeypatch).allow_flips is True

    @pytest.mark.parametrize("days", [0, 1])
    def test_the_locked_phase_still_does_not_trade(self, days, monkeypatch):
        """Deliberately unchanged. Nothing about flips justifies reopening the
        phase that also governs max_trades right before a kickoff."""
        p = _phase(days, monkeypatch)
        assert p.allow_flips is False
        assert p.max_trades == 0

    def test_an_unknown_schedule_still_refuses_flips(self, monkeypatch):
        """Fail closed: no fixture date means no budget-at-kickoff guard."""
        assert _phase(None, monkeypatch).allow_flips is False


class TestTheHoldCapNoLongerTracksTheMatchday:
    def test_a_flip_is_not_forced_out_before_kickoff(self):
        """The premise was that a flip must be sellable before the match. The
        lineup locks at kickoff and squad changes land on the next matchday, so
        there is no such deadline — and a 1-3 day forced hold is the 13%-win
        bucket."""
        assert _max_flip_hold_days(2) is None
        assert _max_flip_hold_days(4) is None

    def test_an_unknown_schedule_is_still_unconstrained(self):
        assert _max_flip_hold_days(None) is None

    def test_the_old_behaviour_is_reachable_for_rollback(self):
        """Every behavioural pacing/flip knob in this codebase stays revertible
        from .env without a deploy."""
        assert _max_flip_hold_days(4, respect_matchday=True) == 3
        assert _max_flip_hold_days(1, respect_matchday=True) == 1
