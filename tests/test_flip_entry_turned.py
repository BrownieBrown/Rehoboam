"""A flip does not buy a player whose rise has already turned, and its bid
clears the value the listing will settle against.

2026-10-02: Kömür and Wöber were +60% over 14 days, so the 14-day trend said
"rising" — but the rise had decelerated to +1% a night, turned to -1.5%, then
fell -8% and -9% the night before the bid. The bot bid the next morning and
the stop-loss sold both two days later at -20% and -22%. The last nightly
move and the next-update forecast both said no; neither was consulted.

The second half: Kickbase declines a bid that is below the player's market
value when the listing expires. A flip bid at exactly market value therefore
wins only the players that fell overnight and loses every riser — adverse
selection by construction. The entry must clear the forecast value.
"""

from __future__ import annotations

import pytest

from rehoboam.profit_trader import ProfitTrader
from rehoboam.services.trend_service import TrendService
from tests.test_flip_entry_rules import _find, _player, _trend


def _find_with(trader, player, trend, forecasts=None):
    return trader.find_profit_opportunities(
        market_players=[player],
        current_budget=50_000_000,
        player_trends={player.id: trend},
        max_opportunities=5,
        player_forecasts=forecasts or {},
    )


class TestTheRiseHasTurned:
    def test_a_negative_last_move_vetoes_a_rising_flip(self):
        t = ProfitTrader(min_profit_pct=5.0)
        turned = _trend("rising", 60.0, trend_1d_pct=-7.8)
        assert _find_with(t, _player(1_000_000, 1_000_000), turned) == []

    def test_a_negative_forecast_vetoes_a_rising_flip(self):
        t = ProfitTrader(min_profit_pct=5.0)
        p = _player(1_000_000, 1_000_000)
        assert _find_with(t, p, _trend("rising", 12.0, trend_1d_pct=1.0), {p.id: -3.0}) == []

    def test_a_still_rising_player_remains_a_candidate(self):
        t = ProfitTrader(min_profit_pct=5.0)
        p = _player(1_000_000, 1_000_000)
        assert _find_with(t, p, _trend("rising", 12.0, trend_1d_pct=1.2), {p.id: +1.1})

    def test_the_legacy_call_without_forecasts_still_works(self):
        t = ProfitTrader(min_profit_pct=5.0)
        assert _find(t, _player(1_000_000, 1_000_000), _trend("rising", 12.0))


class TestTheLastNightlyMove:
    def test_analyze_reports_the_last_move_when_history_includes_today(self):
        # 10 days flat, then 1,000,000 -> 1,100,000 -> 1,000,000 (-9.09%); the
        # market value the session sees is that last point.
        values = [1_000_000] * 10 + [1_100_000, 1_000_000]
        history = {"it": [{"dt": i, "mv": v} for i, v in enumerate(values)]}
        ta = TrendService.analyze(history, 1_000_000)
        assert ta.trend_1d_pct == pytest.approx(-9.09, abs=0.01)
        assert ta.to_dict()["trend_1d_pct"] == pytest.approx(-9.09, abs=0.01)

    def test_analyze_reports_the_move_from_the_last_point_when_history_lags(self):
        values = [1_000_000] * 10 + [1_100_000]
        history = {"it": [{"dt": i, "mv": v} for i, v in enumerate(values)]}
        ta = TrendService.analyze(history, 1_000_000)
        assert ta.trend_1d_pct == pytest.approx(-9.09, abs=0.01)


class TestTheEntryClearsTheExpiryValue:
    def test_a_rising_forecast_lifts_the_flip_bid_to_the_forecast_value(self):
        t = ProfitTrader(min_profit_pct=5.0, max_overpay_pct=1.0)
        p = _player(1_000_000, 1_000_000)
        opps = _find_with(t, p, _trend("rising", 12.0, trend_1d_pct=2.0), {p.id: +3.0})
        assert opps and opps[0].buy_price == 1_030_000

    def test_an_entry_that_eats_the_margin_is_not_a_flip(self):
        # Expected appreciation 12%, entry +8% leaves 4% < the 5% minimum.
        t = ProfitTrader(min_profit_pct=5.0, max_overpay_pct=1.0)
        p = _player(1_000_000, 1_000_000)
        assert _find_with(t, p, _trend("rising", 12.0, trend_1d_pct=2.0), {p.id: +8.0}) == []

    def test_no_forecast_keeps_the_overpay_cap(self):
        t = ProfitTrader(min_profit_pct=5.0, max_overpay_pct=1.0)
        p = _player(1_000_000, 1_000_000)
        opps = _find_with(t, p, _trend("rising", 12.0, trend_1d_pct=2.0))
        assert opps and opps[0].buy_price == 1_000_000


class TestTheTraderHandsTheForecastsToTheFlipSearch:
    def test_find_profit_opportunities_passes_the_loaded_forecasts(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch

        from rehoboam.config import Settings
        from rehoboam.trader import Trader

        api = MagicMock()
        api.get_market.return_value = []
        api.get_team_info.return_value = {"budget": 1_000_000, "team_value": 50_000_000}
        settings = Settings(kickbase_email="test@example.com", kickbase_password="x")
        trader = Trader(api, settings)
        trader.mv_forecasts = {"1": -3.0}
        with (
            patch.object(Trader, "get_days_until_match", return_value=10),
            patch(
                "rehoboam.profit_trader.ProfitTrader.find_profit_opportunities", return_value=[]
            ) as find,
        ):
            trader.find_profit_opportunities(SimpleNamespace(id="L", name="x"))
        assert find.call_args.kwargs["player_forecasts"] == {"1": -3.0}
