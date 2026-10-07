"""Inside the update window the 22:00 session still bids — on the reading it
has, which is the pre-update value (verified 2026-10-04: Poreba's 22:01 ask was
the 10-03 figure). Today's forecast is exactly the move that reading is about
to make, so the bidder uses it rather than nothing.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from rehoboam.trader import Trader


class _Learner:
    dsn = "postgresql://unit-test"


class _Settings:
    bid_forecast_enabled = True


def _at(berlin_hhmm: str, day: str = "2026-10-04") -> float:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return (
        datetime.fromisoformat(f"{day}T{berlin_hhmm}")
        .replace(tzinfo=ZoneInfo("Europe/Berlin"))
        .timestamp()
    )


def _loader_with(forecasts_for):
    with (
        patch("rehoboam.trader._CURVE_CACHE", {}),
        patch("rehoboam.store.mv_forecast_store.MvForecastStore") as store,
    ):
        store.return_value.forecasts_for.side_effect = forecasts_for
        yield store


class TestTheWindow:
    def test_inside_the_window_the_loader_uses_todays_forecast(self):
        asked: list[date] = []

        def forecasts_for(day):
            asked.append(day)
            return {"p": 0.03}

        with (
            patch("rehoboam.trader._CURVE_CACHE", {}),
            patch("rehoboam.store.mv_forecast_store.MvForecastStore") as store,
            patch("rehoboam.trader.time.time", return_value=_at("22:01")),
        ):
            store.return_value.forecasts_for.side_effect = forecasts_for
            out = Trader._load_forecasts(_Learner(), _Settings())
        assert asked == [date(2026, 10, 4)]
        assert out == {"p": 3.0}

    def test_after_the_window_the_loader_asks_for_tomorrow(self):
        asked: list[date] = []

        def forecasts_for(day):
            asked.append(day)
            return {}

        with (
            patch("rehoboam.trader._CURVE_CACHE", {}),
            patch("rehoboam.store.mv_forecast_store.MvForecastStore") as store,
            patch("rehoboam.trader.time.time", return_value=_at("22:45")),
        ):
            store.return_value.forecasts_for.side_effect = forecasts_for
            Trader._load_forecasts(_Learner(), _Settings())
        assert asked == [date(2026, 10, 5)]
