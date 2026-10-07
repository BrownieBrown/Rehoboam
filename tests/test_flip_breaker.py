"""A drawdown breaker pauses flips after a run of realised losses (2026-10-07).

Five flips closed between 10-04 and 10-06, every one at a loss, -4.8m in
three days, and the bot kept opening more. A desk would have been stopped
after the second. The breaker reads the realised flip P&L over the last
days from the store and closes the window below a loss limit.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from rehoboam.config import Settings
from rehoboam.services.flip_breaker import breaker_reason
from rehoboam.trader import Trader


class TestTheRule:
    def test_losses_past_the_limit_close_the_window(self):
        assert breaker_reason(-10_500_000, loss_limit_eur=10_000_000, days=14) == (
            "flips lost EUR 10,500,000 over the last 14 days (limit EUR 10,000,000)"
        )

    def test_losses_inside_the_limit_do_not(self):
        assert breaker_reason(-4_800_000, loss_limit_eur=10_000_000, days=14) is None

    def test_profit_does_not(self):
        assert breaker_reason(3_000_000, loss_limit_eur=10_000_000, days=14) is None

    def test_unknown_does_not(self):
        assert breaker_reason(None, loss_limit_eur=10_000_000, days=14) is None


class TestTheTraderReadsIt:
    def _trader(self, realised):
        api = MagicMock()
        api.get_market.return_value = []
        api.get_team_info.return_value = {"budget": 1_000_000, "team_value": 50_000_000}
        learner = MagicMock()
        learner.flip_realised_since.return_value = realised
        learner.dsn = None
        settings = Settings(kickbase_email="test@example.com", kickbase_password="x")
        with (
            patch.object(Trader, "_load_curves", return_value=(None, None, None)),
            patch.object(Trader, "_load_forecasts", return_value={}),
        ):
            return Trader(api, settings, bid_learner=learner)

    def test_a_tripped_breaker_means_no_flip_search(self):
        trader = self._trader(-12_000_000)
        with (
            patch.object(Trader, "get_days_until_match", return_value=5),
            patch("rehoboam.store.league_store.LeagueStore.market_drift", return_value=0.5),
            patch("rehoboam.profit_trader.ProfitTrader.find_profit_opportunities") as find,
        ):
            assert trader.find_profit_opportunities(SimpleNamespace(id="L", name="x")) == []
        find.assert_not_called()

    def test_small_losses_leave_it_open(self):
        trader = self._trader(-2_000_000)
        with (
            patch.object(Trader, "get_days_until_match", return_value=5),
            patch("rehoboam.store.league_store.LeagueStore.market_drift", return_value=0.5),
            patch(
                "rehoboam.profit_trader.ProfitTrader.find_profit_opportunities", return_value=[]
            ) as find,
        ):
            trader.find_profit_opportunities(SimpleNamespace(id="L", name="x"))
        find.assert_called_once()
