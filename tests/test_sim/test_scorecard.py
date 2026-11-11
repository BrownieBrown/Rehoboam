"""The leader scorecard (ADR-0012): money, missing players, points, against the top three."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from rehoboam.sim import backtest
from rehoboam.sim.backtest import Card, Scorecard

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


def test_trading_profit_is_each_buys_exit_or_end_value_minus_its_price():
    buys = [(T0, "a", 100), (T0, "b", 200), (T0 + timedelta(days=5), "a", 130)]
    sells = [(T0 + timedelta(days=3), "a", 120)]  # a: bought 100, sold 120; bought again 130

    profit = backtest.trading_profit(buys, sells, end_value={"a": 125, "b": 190}.get)

    assert profit == (120 - 100) + (190 - 200) + (125 - 130)


LEADERS = (
    Card("L1", 56e6, 38e6, 1100.0),
    Card("L2", 50e6, 26e6, 1150.0),
    Card("L3", 40e6, 16e6, 1200.0),
)


def _card(total=45e6, trading=30e6, points=800.0, short=0):
    return Scorecard(v3=Card("v3", total, trading, points, short, 75), leaders=LEADERS)


def test_money_is_at_leader_level_from_the_third_leaders_total_and_the_median_trading():
    assert _card().money_level == "same level"
    assert _card(total=39e6).money_level == "below"
    assert _card(trading=25e6).money_level == "below"
    assert _card(total=57e6, trading=39e6).money_level == "beats"


def test_a_change_is_kept_only_if_no_axis_gets_worse_and_one_gets_better():
    base = _card(short=10)

    assert _card(short=0).keeps(base)[0]  # fewer missing players, nothing worse
    assert not _card(short=0, total=40e6).keeps(base)[0]  # bleeds 5m of money
    assert not _card(short=0, points=780.0).keeps(base)[0]  # loses 20 points
    assert not _card(short=11, points=900.0).keeps(base)[0]  # one more empty slot
    assert not _card(short=10).keeps(base)[0]  # nothing improved
