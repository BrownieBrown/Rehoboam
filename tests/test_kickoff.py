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


# ---------------------------------------------------------------------------
# Round-aware reading (2026-10-12): a started round is nobody's "next kickoff".
# ---------------------------------------------------------------------------

from rehoboam.kickoff import (  # noqa: E402
    Round,
    matchday_in_progress_from_matchdays,
    rounds_from_matchdays,
)

# MD5 kicked off Friday 18:30Z with a game none of our players were in; the
# Saturday and Sunday fixtures are still ahead. MD6 is the following Friday.
WEEKEND = {
    "it": [
        {
            "day": 5,
            "it": [
                {"dt": "2026-10-09T18:30:00Z", "st": 2},
                {"dt": "2026-10-10T13:30:00Z", "st": 0},
                {"dt": "2026-10-11T15:30:00Z", "st": 0},
            ],
        },
        {
            "day": 6,
            "it": [
                {"dt": "2026-10-17T13:30:00Z", "st": 0},
                {"dt": "2026-10-16T18:30:00Z", "st": 0},
            ],
        },
    ]
}
SATURDAY = datetime(2026, 10, 10, 8, 0, tzinfo=timezone.utc)
FRIDAY_MORNING = datetime(2026, 10, 9, 8, 0, tzinfo=timezone.utc)
FRIDAY_NIGHT = datetime(2026, 10, 9, 20, 0, tzinfo=timezone.utc)


class TestNextKickoffIsTheNextRound:
    def test_saturday_of_a_started_round_points_at_next_friday(self):
        """The live 2026-10-10 08:00 session: it read 'Sat 13:30, 0d' and locked."""
        assert next_fixture_from_matchdays(WEEKEND, SATURDAY) == NextFixture(
            day_number=6, at=datetime(2026, 10, 16, 18, 30, tzinfo=timezone.utc)
        )

    def test_friday_night_after_the_first_whistle_is_already_the_next_round(self):
        assert next_fixture_from_matchdays(WEEKEND, FRIDAY_NIGHT).day_number == 6

    def test_friday_morning_still_points_at_this_round(self):
        """Nothing played yet, every fixture `st == 0`: Friday 18:30 is the kickoff."""
        unplayed = {
            "it": [
                {"day": g["day"], "it": [{**f, "st": 0} for f in g["it"]]} for g in WEEKEND["it"]
            ]
        }
        assert next_fixture_from_matchdays(unplayed, FRIDAY_MORNING) == NextFixture(
            day_number=5, at=datetime(2026, 10, 9, 18, 30, tzinfo=timezone.utc)
        )

    def test_a_round_whose_status_says_started_is_skipped_even_before_its_date(self):
        """`st != 0` on any fixture marks the round started whatever the clock says."""
        payload = {
            "it": [
                {"day": 5, "it": [{"dt": "2026-10-09T18:30:00Z", "st": 1}]},
                {"day": 6, "it": [{"dt": "2026-10-16T18:30:00Z", "st": 0}]},
            ]
        }
        early = datetime(2026, 10, 9, 18, 0, tzinfo=timezone.utc)
        assert next_fixture_from_matchdays(payload, early).day_number == 6

    def test_a_postponed_fixture_does_not_resurrect_a_finished_round(self):
        """MD5 has one game moved to November: that is not MD5's kickoff."""
        payload = {
            "it": [
                {
                    "day": 5,
                    "it": [
                        {"dt": "2026-10-09T18:30:00Z", "st": 2},
                        {"dt": "2026-11-03T19:30:00Z", "st": 0},
                    ],
                },
                {"day": 6, "it": [{"dt": "2026-10-16T18:30:00Z", "st": 0}]},
            ]
        }
        assert next_fixture_from_matchdays(payload, SATURDAY).day_number == 6

    def test_days_until_match_on_saturday_is_the_week_ahead(self):
        """Six days: aggressive phase, debt allowed, no lockout, no emergency fill."""
        trader = _trader(WEEKEND, {"lp": [{"md": "2026-10-10T13:30:00Z"}], "nlp": []})
        assert trader.get_days_until_match(SimpleNamespace(id="L"), now=SATURDAY) == 6


class TestRoundsFromMatchdays:
    def test_reduces_each_group_to_first_kickoff_and_flags(self):
        assert rounds_from_matchdays(WEEKEND, SATURDAY) == [
            Round(
                day_number=5,
                first_kickoff=datetime(2026, 10, 9, 18, 30, tzinfo=timezone.utc),
                started=True,
                finished=False,
            ),
            Round(
                day_number=6,
                first_kickoff=datetime(2026, 10, 16, 18, 30, tzinfo=timezone.utc),
                started=False,
                finished=False,
            ),
        ]

    def test_groups_without_a_parseable_date_are_dropped(self):
        assert rounds_from_matchdays({"it": [{"day": 1, "it": [{"st": 0}]}, "junk"]}, NOW) == []
        assert rounds_from_matchdays(None, NOW) == []


class TestMatchdayInProgressFromTheSchedule:
    def test_saturday_of_a_started_round_is_in_progress(self):
        """Even though none of OUR players played Friday -- the schedule knows."""
        assert matchday_in_progress_from_matchdays(WEEKEND, SATURDAY) is True

    def test_friday_morning_is_not(self):
        assert matchday_in_progress_from_matchdays(WEEKEND, FRIDAY_MORNING) is False

    def test_a_finished_round_is_not(self):
        done = {"it": [{"day": 5, "it": [{"dt": "2026-10-09T18:30:00Z", "st": 2}]}]}
        assert matchday_in_progress_from_matchdays(done, SATURDAY) is False

    def test_a_round_older_than_the_span_is_not_held_open_by_a_postponed_game(self):
        stale = {
            "it": [
                {
                    "day": 5,
                    "it": [
                        {"dt": "2026-10-09T18:30:00Z", "st": 2},
                        {"dt": "2026-11-03T19:30:00Z", "st": 0},
                    ],
                }
            ]
        }
        thursday = datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc)
        assert matchday_in_progress_from_matchdays(stale, thursday) is False


class TestTraderReadsInProgressFromTheSchedule:
    def test_schedule_wins_over_myeleven(self):
        """2026-10-10 08:00 live: /myeleven had only future fixtures (ours play
        Saturday), so the old reading said 'not started' and the phase locked."""
        nk = _trader(WEEKEND, {"lp": [{"md": "2026-10-10T13:30:00Z"}], "nlp": []}).next_kickoff(
            SimpleNamespace(id="L"), now=SATURDAY
        )
        assert nk.matchday_in_progress is True
        assert nk.day_number == 6
        assert nk.at == datetime(2026, 10, 16, 18, 30, tzinfo=timezone.utc)

    def test_myeleven_is_the_fallback_when_the_schedule_is_down(self):
        played = {"lp": [{"md": "2026-10-09T18:30:00Z"}], "nlp": []}
        nk = _trader(RuntimeError("503"), played).next_kickoff(
            SimpleNamespace(id="L"), now=SATURDAY
        )
        assert nk.source == "none" and nk.matchday_in_progress is True
