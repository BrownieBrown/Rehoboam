"""Which market-value update a bid placed now is judged against."""

from __future__ import annotations

from datetime import date, datetime

from rehoboam.services.mv_forecast import BERLIN, next_update_day


def _at(h, m=0, day=23):
    return datetime(2026, 9, day, h, m, tzinfo=BERLIN).timestamp()


def test_a_morning_session_is_judged_against_tonights_update():
    assert next_update_day(_at(10)) == date(2026, 9, 23)


def test_a_bid_before_the_cutoff_is_still_judged_against_it():
    assert next_update_day(_at(21, 44)) == date(2026, 9, 23)


def test_the_ambiguous_window_uses_no_forecast():
    assert next_update_day(_at(21, 45)) is None
    assert next_update_day(_at(22, 29)) is None


def test_after_the_update_the_next_one_is_tomorrows():
    assert next_update_day(_at(22, 30)) == date(2026, 9, 24)
    assert next_update_day(_at(23, 59)) == date(2026, 9, 24)


def test_after_midnight_the_new_days_update_is_ahead():
    assert next_update_day(_at(0, 30, day=24)) == date(2026, 9, 24)
