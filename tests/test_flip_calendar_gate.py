"""No new flips into a falling market or a long gap before the next kickoff.

Measured 2026-10-07: over the October break the cap-weighted market fell 0.3
to 1% every night for twelve nights (about -7%), the median player about
-14%; across five seasons, buys made in October and November resold at a
median +4.3% / +7.1%, buys in January, April and May at -3.9% / -8.9% /
-16.2%. A flip is a bet on the market's drift as much as on the player, so
the bot reads the drift (the median nightly move of the universe over the
last nights) and the calendar (days to the next kickoff) before opening one.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from rehoboam.config import Settings
from rehoboam.services.flip_window import flip_gate_reason
from rehoboam.trader import Trader

KW = {"max_days_to_kickoff": 10, "drift_min_pct": 0.0}


class TestTheGate:
    def test_a_long_gap_before_kickoff_closes_the_window(self):
        assert flip_gate_reason(days_until_match=17, market_drift_pct=0.2, **KW) == (
            "kickoff in 17d (> 10): the market drifts down through a break"
        )

    def test_a_falling_market_closes_the_window(self):
        assert flip_gate_reason(days_until_match=5, market_drift_pct=-1.1, **KW) == (
            "the market fell a median -1.1% a night over the last nights"
        )

    def test_a_rising_market_inside_the_window_is_open(self):
        assert flip_gate_reason(days_until_match=5, market_drift_pct=0.3, **KW) is None

    def test_unknowns_do_not_close_it(self):
        assert flip_gate_reason(days_until_match=None, market_drift_pct=None, **KW) is None


class TestTheTraderReadsBoth:
    def _trader(self):
        api = MagicMock()
        api.get_market.return_value = []
        api.get_team_info.return_value = {"budget": 1_000_000, "team_value": 50_000_000}
        return Trader(api, Settings(kickbase_email="test@example.com", kickbase_password="x"))

    def test_a_falling_market_means_no_flip_search(self):
        trader = self._trader()
        with (
            patch.object(Trader, "get_days_until_match", return_value=5),
            patch("rehoboam.store.league_store.LeagueStore.market_drift", return_value=-1.1),
            patch("rehoboam.profit_trader.ProfitTrader.find_profit_opportunities") as find,
        ):
            assert trader.find_profit_opportunities(SimpleNamespace(id="L", name="x")) == []
        find.assert_not_called()

    def test_a_long_gap_means_no_flip_search(self):
        trader = self._trader()
        with (
            patch.object(Trader, "get_days_until_match", return_value=17),
            patch("rehoboam.store.league_store.LeagueStore.market_drift", return_value=0.5),
            patch("rehoboam.profit_trader.ProfitTrader.find_profit_opportunities") as find,
        ):
            assert trader.find_profit_opportunities(SimpleNamespace(id="L", name="x")) == []
        find.assert_not_called()

    def test_an_open_window_searches(self):
        trader = self._trader()
        with (
            patch.object(Trader, "get_days_until_match", return_value=5),
            patch("rehoboam.store.league_store.LeagueStore.market_drift", return_value=0.5),
            patch(
                "rehoboam.profit_trader.ProfitTrader.find_profit_opportunities", return_value=[]
            ) as find,
        ):
            trader.find_profit_opportunities(SimpleNamespace(id="L", name="x"))
        find.assert_called_once()

    def test_an_unreadable_drift_does_not_close_the_window(self):
        trader = self._trader()
        with (
            patch.object(Trader, "get_days_until_match", return_value=5),
            patch(
                "rehoboam.store.league_store.LeagueStore.market_drift",
                side_effect=RuntimeError("down"),
            ),
            patch(
                "rehoboam.profit_trader.ProfitTrader.find_profit_opportunities", return_value=[]
            ) as find,
        ):
            trader.find_profit_opportunities(SimpleNamespace(id="L", name="x"))
        find.assert_called_once()
