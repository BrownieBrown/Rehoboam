"""The season a Berlin date belongs to, in Kickbase's "2026/2027" form."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from rehoboam.services.season import season_label

BERLIN = ZoneInfo("Europe/Berlin")


def test_autumn_is_the_season_that_just_started():
    assert season_label(datetime(2026, 10, 7, tzinfo=BERLIN)) == "2026/2027"


def test_spring_is_the_season_that_started_last_summer():
    assert season_label(datetime(2027, 5, 16, tzinfo=BERLIN)) == "2026/2027"


def test_july_first_starts_the_new_season():
    assert season_label(datetime(2026, 7, 1, tzinfo=BERLIN)) == "2026/2027"
    assert season_label(datetime(2026, 6, 30, tzinfo=BERLIN)) == "2025/2026"
