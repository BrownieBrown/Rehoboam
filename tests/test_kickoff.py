"""The next kickoff: schedule first, /myeleven as the cross-check (spec §3; probe 2026-09-15)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from rehoboam.config import Settings
from rehoboam.kickoff import (
    FinishedMatchday,
    NextFixture,
    finished_matchdays,
    fixtures_from_myeleven,
    next_fixture_from_matchdays,
    next_kickoff_from_matchdays,
)
from rehoboam.trader import Trader

NOW = datetime(2026, 9, 15, 6, 0, tzinfo=timezone.utc)

SCHEDULE = {
    "day": 4,
    "it": [
        {
            "day": 3,
            "mdln": "3 Match Day",
            "it": [
                {
                    "mi": "1",
                    "day": 3,
                    "dt": "2026-09-12T13:30:00Z",
                    "st": 2,
                    "t1": "2",
                    "t2": "9",
                },
            ],
        },
        {
            "day": 4,
            "mdln": "4 Match Day",
            "it": [
                {
                    "mi": "2",
                    "day": 4,
                    "dt": "2026-09-19T13:30:00Z",
                    "st": 0,
                    "t1": "5",
                    "t2": "6",
                },
                {
                    "mi": "3",
                    "day": 4,
                    "dt": "2026-09-18T18:30:00Z",
                    "st": 0,
                    "t1": "2",
                    "t2": "40",
                },
            ],
        },
    ],
}
MYELEVEN = {
    "lp": [{"md": "2026-09-19T13:30:00Z"}],
    "nlp": [{"md": "2026-09-12T13:30:00Z"}, {}],
}


def test_schedule_gives_the_earliest_not_started_fixture():
    assert next_kickoff_from_matchdays(SCHEDULE, NOW) == datetime(
        2026, 9, 18, 18, 30, tzinfo=timezone.utc
    )


def test_schedule_ignores_finished_and_past_fixtures_and_tolerates_junk():
    assert (
        next_kickoff_from_matchdays(
            {"it": [{"it": [{"dt": "2026-09-12T13:30:00Z", "st": 2}]}]}, NOW
        )
        is None
    )
    assert next_kickoff_from_matchdays({}, NOW) is None
    assert (
        next_kickoff_from_matchdays({"it": [{"it": [{"st": 0}, {"dt": "garbage", "st": 0}]}]}, NOW)
        is None
    )


def test_myeleven_fixtures_read_lp_and_nlp():
    assert fixtures_from_myeleven(MYELEVEN) == [
        datetime(2026, 9, 19, 13, 30, tzinfo=timezone.utc),
        datetime(2026, 9, 12, 13, 30, tzinfo=timezone.utc),
    ]


def _trader(schedule, myeleven):
    api = SimpleNamespace(
        get_competition_matchdays=lambda: (
            (_ for _ in ()).throw(schedule) if isinstance(schedule, Exception) else schedule
        ),
        get_starting_eleven=lambda league: myeleven,
        # Trader.__init__ stores api.client on TrendService without ever
        # calling it in this test's code paths -- a placeholder is enough.
        client=None,
    )
    # Trader.__init__ also reads settings.bid_ceiling_policy() directly (not
    # via getattr), so a bare SimpleNamespace() blows up at construction --
    # build a real Settings instead of loosening __init__ for a test double.
    return Trader(api, Settings(kickbase_email="t@e.com", kickbase_password="x"))


def test_next_kickoff_prefers_the_schedule_and_records_the_cross_check():
    nk = _trader(SCHEDULE, MYELEVEN).next_kickoff(SimpleNamespace(id="L"), now=NOW)
    assert nk.source == "schedule"
    assert nk.at == datetime(2026, 9, 18, 18, 30, tzinfo=timezone.utc)
    assert nk.cross_check == datetime(2026, 9, 19, 13, 30, tzinfo=timezone.utc)


def test_next_kickoff_falls_back_to_myeleven_when_the_schedule_fails():
    nk = _trader(RuntimeError("503"), MYELEVEN).next_kickoff(SimpleNamespace(id="L"), now=NOW)
    assert (nk.source, nk.at) == (
        "myeleven",
        datetime(2026, 9, 19, 13, 30, tzinfo=timezone.utc),
    )


def test_next_kickoff_is_none_when_both_sources_are_empty():
    nk = _trader({}, {}).next_kickoff(SimpleNamespace(id="L"), now=NOW)
    assert (nk.source, nk.at, nk.cross_check) == ("none", None, None)


def test_days_until_match_derives_from_next_kickoff():
    trader = _trader(SCHEDULE, MYELEVEN)
    assert trader.get_days_until_match(SimpleNamespace(id="L"), now=NOW) == 3


class TestNextFixture:
    def test_carries_the_matchday_number(self):
        nf = next_fixture_from_matchdays(SCHEDULE, NOW)
        assert nf == NextFixture(
            day_number=4, at=datetime(2026, 9, 18, 18, 30, tzinfo=timezone.utc)
        )

    def test_nothing_upcoming_is_none(self):
        late = datetime(2026, 9, 30, tzinfo=timezone.utc)
        assert next_fixture_from_matchdays(SCHEDULE, late) is None

    def test_next_kickoff_wrapper_is_unchanged(self):
        assert (
            next_kickoff_from_matchdays(SCHEDULE, NOW)
            == next_fixture_from_matchdays(SCHEDULE, NOW).at
        )


class TestFinishedMatchdays:
    def test_only_groups_where_every_fixture_finished(self):
        assert finished_matchdays(SCHEDULE) == [
            FinishedMatchday(
                day_number=3,
                first_kickoff=datetime(2026, 9, 12, 13, 30, tzinfo=timezone.utc),
                last_kickoff=datetime(2026, 9, 12, 13, 30, tzinfo=timezone.utc),
            )
        ]

    def test_a_partly_played_matchday_is_not_finished(self):
        payload = {
            "it": [
                {
                    "day": 5,
                    "it": [
                        {"dt": "2026-10-09T18:30:00Z", "st": 2},
                        {"dt": "2026-10-10T13:30:00Z", "st": 0},
                    ],
                }
            ]
        }
        assert finished_matchdays(payload) == []

    def test_empty_groups_and_bad_dates_are_skipped(self):
        payload = {
            "it": [
                {"day": 1, "it": []},
                {"day": 2, "it": [{"dt": "garbage", "st": 2}]},
                {
                    "day": 3,
                    "it": [
                        {"dt": "2026-08-30T13:30:00Z", "st": 2},
                        {"dt": "2026-08-29T18:30:00Z", "st": 2},
                    ],
                },
                "not a dict",
            ]
        }
        got = finished_matchdays(payload)
        assert [m.day_number for m in got] == [3]
        assert got[0].first_kickoff.day == 29 and got[0].last_kickoff.day == 30

    def test_not_a_dict_is_empty(self):
        assert finished_matchdays(None) == []


class TestTraderDayNumber:
    def test_next_kickoff_carries_the_schedule_day(self):
        nk = _trader(SCHEDULE, {"lp": [], "nlp": []}).next_kickoff(SimpleNamespace(id="L"), now=NOW)
        assert nk.source == "schedule" and nk.day_number == 4

    def test_myeleven_fallback_has_no_day(self):
        soon = "2026-09-18T18:30:00Z"
        nk = _trader({}, {"lp": [{"md": soon}], "nlp": []}).next_kickoff(
            SimpleNamespace(id="L"), now=NOW
        )
        assert nk.source == "myeleven" and nk.day_number is None
