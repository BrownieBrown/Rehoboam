"""Tests for Trader.get_days_until_match.

This is the input to the matchday phase machine (auto_trader._get_matchday_phase)
and to the budget-at-kickoff guard in ExecutionService.buy. When it returns
None, the phase falls back to "moderate" and the 0-1 day "locked" phase can
never trigger -- the bot keeps trading into kickoff.

On 2026-08-21 the live /myeleven response for the new season carried none of
the aliases this function looked for. Its response keys were:
    clpc, cpte, lp, lpc, mu, nlp, p, pa
The match date had moved into ``nlp``, a list of the squad's players for the
next lineup, each entry carrying its own fixture date under ``md``.
"""

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from rehoboam.config import Settings
from rehoboam.trader import Trader


def _trader(starting_eleven):
    api = MagicMock()
    api.get_starting_eleven.return_value = starting_eleven
    return Trader(api=api, settings=Settings())


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class TestNextMatchDetection:
    def test_reads_match_date_from_nlp_entries(self):
        """The live 2026/27 shape: date lives in nlp[].md, not nm/nextMatch."""
        now = datetime.now(tz=timezone.utc)
        trader = _trader(
            {
                "clpc": 11,
                "cpte": False,
                "lp": [],
                "lpc": 0,
                "p": [],
                "pa": True,
                "nlp": [
                    {"i": "72", "n": "Flekken", "md": _iso(now + timedelta(days=6, hours=2))},
                    {"i": "157", "n": "Stark", "md": _iso(now + timedelta(days=7, hours=1))},
                    {"i": "999", "n": "Other", "md": _iso(now + timedelta(days=8, hours=3))},
                ],
            }
        )
        assert trader.get_days_until_match(MagicMock()) == 6

    def test_picks_the_earliest_upcoming_fixture(self):
        """Budget must be non-negative at the FIRST kickoff, so take the min."""
        now = datetime.now(tz=timezone.utc)
        trader = _trader(
            {
                "nlp": [
                    {"md": _iso(now + timedelta(days=9, hours=1))},
                    {"md": _iso(now + timedelta(days=2, hours=1))},
                    {"md": _iso(now + timedelta(days=5, hours=1))},
                ]
            }
        )
        assert trader.get_days_until_match(MagicMock()) == 2

    def test_imminent_match_reports_zero_not_none(self):
        """A match hours away must reach the 'locked' phase, not 'moderate'."""
        now = datetime.now(tz=timezone.utc)
        trader = _trader({"nlp": [{"md": _iso(now + timedelta(hours=5))}]})
        assert trader.get_days_until_match(MagicMock()) == 0

    def test_ignores_fixtures_already_played(self):
        """Past dates in nlp must not be read as an imminent match."""
        now = datetime.now(tz=timezone.utc)
        trader = _trader(
            {
                "nlp": [
                    {"md": _iso(now - timedelta(days=3))},
                    {"md": _iso(now + timedelta(days=4, hours=2))},
                ]
            }
        )
        assert trader.get_days_until_match(MagicMock()) == 4

    def test_legacy_nm_field_still_honoured(self):
        """Back-compat: if nm returns, keep using it."""
        now = datetime.now(tz=timezone.utc)
        trader = _trader({"nm": _iso(now + timedelta(days=3, hours=1))})
        assert trader.get_days_until_match(MagicMock()) == 3

    def test_unrecognised_shape_warns_instead_of_failing_silently(self, caplog):
        """A schema change must be loud -- this guard is worth ~1,100 points."""
        trader = _trader({"clpc": 11, "cpte": False, "pa": True})
        with caplog.at_level(logging.WARNING):
            assert trader.get_days_until_match(MagicMock()) is None
        assert caplog.records, "schema miss must be logged, not swallowed"
