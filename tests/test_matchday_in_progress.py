"""Trading during a matchday that has already kicked off (REH-110).

`get_days_until_match` takes `min()` of the fixtures still to come, so on a
Saturday morning — with two players already having played on Friday evening —
it reports "match in 0d" and the bot refuses to trade. But the eleven was
locked at Friday's kickoff and every squad change now lands on the NEXT
matchday, so there is nothing left to protect.

Measured live 2026-08-29 09:34Z, straight from /myeleven:

    Pavlović   md=2026-08-28T18:30:00Z   PAST   (-0.63d)
    Führich    md=2026-08-28T18:30:00Z   PAST   (-0.63d)
    Flekken    md=2026-08-29T13:30:00Z   FUTURE (+0.16d)
    Stark      md=2026-08-30T13:30:00Z   FUTURE (+1.16d)

A `md` in the past is proof the round has begun. That costs two tradeable days
per week — the Saturday and Sunday of every matchday.

The budget-at-kickoff guard (REH-11) is deliberately NOT changed: it keys off
`get_days_until_match`, whose "earliest remaining fixture" meaning is the right
one for cash and stays conservative.
"""

from datetime import datetime, timedelta, timezone

import pytest

from rehoboam.trader import matchday_in_progress

NOW = datetime(2026, 8, 29, 9, 34, tzinfo=timezone.utc)


def at(days: float) -> datetime:
    return NOW + timedelta(days=days)


class TestDetectingARoundInProgress:
    def test_a_fixture_already_played_means_the_round_started(self):
        """The live case: Pavlović played Friday, Flekken plays this afternoon."""
        assert matchday_in_progress([at(-0.63), at(0.16), at(1.16)], NOW) is True

    def test_all_fixtures_still_ahead_means_it_has_not(self):
        assert matchday_in_progress([at(0.16), at(1.16)], NOW) is False

    def test_a_finished_round_still_counts_as_free_to_trade(self):
        """Every fixture played and the API has not rolled `md` forward yet.
        Between rounds is the safest moment to trade, not the least safe."""
        assert matchday_in_progress([at(-1.5), at(-0.6)], NOW) is True

    def test_a_stale_fixture_from_a_previous_round_does_not_count(self):
        """Otherwise one un-rolled date would unlock trading forever."""
        assert matchday_in_progress([at(-9.0), at(0.16)], NOW) is False

    def test_no_fixtures_at_all_is_not_a_round_in_progress(self):
        assert matchday_in_progress([], NOW) is False


class TestThePhaseUnlocks:
    def _phase(self, days, monkeypatch, in_progress):
        monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
        monkeypatch.setenv("KICKBASE_PASSWORD", "test")
        from unittest.mock import MagicMock

        from rehoboam.auto_trader import AutoTrader
        from rehoboam.config import Settings

        trader = AutoTrader(api=MagicMock(), settings=Settings(), dry_run=True)
        return trader._get_matchday_phase(days, matchday_in_progress=in_progress)

    def test_a_round_in_progress_trades_even_at_zero_days(self, monkeypatch):
        """The whole point: Saturday and Sunday become tradeable again."""
        p = self._phase(0, monkeypatch, True)
        assert p.allow_flips is True
        assert p.max_trades > 0

    def test_it_still_locks_before_the_first_kickoff(self, monkeypatch):
        """Friday afternoon, nothing played yet — the lineup is still live and
        the -100 risk is real."""
        p = self._phase(0, monkeypatch, False)
        assert p.allow_flips is False
        assert p.max_trades == 0

    @pytest.mark.parametrize("days", [2, 4, 7])
    def test_a_round_in_progress_never_reduces_what_was_allowed(self, days, monkeypatch):
        before = self._phase(days, monkeypatch, False)
        after = self._phase(days, monkeypatch, True)
        assert after.max_trades >= before.max_trades
        assert after.allow_flips or not before.allow_flips
