"""The value day in force at a moment: Berlin time minus 22 hours (migration 024)."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from rehoboam.sim.history import value_day


@pytest.mark.parametrize(
    ("utc", "expected"),
    [
        # Winter (CET, UTC+1): 10:00 Berlin is priced against yesterday's point,
        # 22:30 Berlin against today's.
        (datetime(2026, 11, 11, 9, 0, tzinfo=timezone.utc), date(2026, 11, 10)),
        (datetime(2026, 11, 11, 20, 59, tzinfo=timezone.utc), date(2026, 11, 10)),
        (datetime(2026, 11, 11, 21, 30, tzinfo=timezone.utc), date(2026, 11, 11)),
        # Summer (CEST, UTC+2): the same Berlin clock is two hours from UTC.
        (datetime(2026, 7, 15, 19, 59, tzinfo=timezone.utc), date(2026, 7, 14)),
        (datetime(2026, 7, 15, 20, 30, tzinfo=timezone.utc), date(2026, 7, 15)),
    ],
)
def test_value_day_follows_the_22_00_berlin_update(utc, expected):
    assert value_day(utc) == expected


def test_value_day_refuses_a_naive_datetime():
    with pytest.raises(ValueError):
        value_day(datetime(2026, 11, 11, 9, 0))
