"""When the emergency fill is allowed to spend (2026-09-22).

On 2026-09-22 08:00 UTC the squad had ten players and the next kickoff was
seventeen days away (the October international break). The session called it
a lineup emergency in the *aggressive* phase, built the candidate pool with
the relaxed emergency filters, and bought Seol at 1,951,380 -- a falling
player unlikely to start. Marco cancelled the offer: "Emergency mode should
only be activated 1 day before the line ups are locked in not 17 days before
were normal and profit trades can be done without much risk."

The emergency fill is the "buy almost anything" path. It earns its relaxed
filters only when nothing else can still close the slot before kickoff: the
last day, when the locked phase has stood every other buy path down. Until
then the ordinary trading phases fill the squad with players that pass the
ordinary bars, at ordinary bids.

The unknown schedule stays an emergency. REH-112 exists because on
2026-08-31 a failed fixture lookup left a squad of seven four slots short
with the fill unreachable; a fail-safe has to fail toward fielding an eleven.
"""

from __future__ import annotations

import pytest

from rehoboam.services.emergency_window import emergency_fill_due


class TestTheWindow:
    @pytest.mark.parametrize("days", [0, 1])
    def test_the_last_day_is_an_emergency(self, days):
        assert emergency_fill_due(days, window_days=1) is True

    @pytest.mark.parametrize("days", [2, 3, 4, 6, 17])
    def test_earlier_than_that_the_trading_phases_fill_the_squad(self, days):
        assert emergency_fill_due(days, window_days=1) is False

    def test_an_unknown_schedule_fails_toward_fielding_an_eleven(self):
        assert emergency_fill_due(None, window_days=1) is True

    def test_the_window_is_a_setting(self):
        assert emergency_fill_due(3, window_days=3) is True
        assert emergency_fill_due(4, window_days=3) is False

    def test_the_default_window_is_one_day(self):
        from rehoboam.config import Settings

        assert (
            Settings(kickbase_email="t@example.com", kickbase_password="x").emergency_fill_days == 1
        )
