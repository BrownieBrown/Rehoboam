"""The market-value forecast rule (spec 2026-09-17) — pure."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from rehoboam.services.mv_forecast import (
    BERLIN,
    METHOD,
    POST,
    PRE,
    SCORED,
    UNSCORABLE,
    backtest,
    berlin_today,
    forecast,
    reading_window,
    score,
)

DAY = date(2026, 9, 17)


def _epoch(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=BERLIN).timestamp()


def test_method_name_is_stable():
    assert METHOD == "momentum-v1"


def test_forecast_is_momentum_times_the_last_move():
    # 9,900,000 -> 10,000,000 is +1.0101 %; 0.9 of that on 10,000,000
    f = forecast("p", 10_000_000, 100_000, DAY, momentum=0.9, cap=0.2)
    assert f is not None
    assert f.player_id == "p" and f.target_day == DAY
    assert f.base_mv == 10_000_000 and f.last_change == 100_000
    assert f.predicted_pct == pytest.approx(0.9 * 100_000 / 9_900_000)
    assert f.predicted_change == round(10_000_000 * 0.9 * 100_000 / 9_900_000)


def test_forecast_follows_a_fall():
    f = forecast("p", 9_000_000, -1_000_000, DAY, momentum=0.9, cap=0.2)
    assert f.predicted_pct == pytest.approx(0.9 * -0.1)
    assert f.predicted_change == -810_000


def test_forecast_caps_a_huge_last_move_in_both_directions():
    up = forecast("p", 2_000_000, 1_000_000, DAY, momentum=0.9, cap=0.2)  # +100 %
    assert up.predicted_pct == pytest.approx(0.18)
    assert up.predicted_change == 360_000
    down = forecast("p", 500_000, -500_000, DAY, momentum=0.9, cap=0.2)  # -50 %
    assert down.predicted_pct == pytest.approx(-0.18)
    assert down.predicted_change == -90_000


def test_a_flat_last_update_forecasts_no_change():
    f = forecast("p", 5_000_000, 0, DAY, momentum=0.9, cap=0.2)
    assert f.predicted_pct == 0 and f.predicted_change == 0


@pytest.mark.parametrize(
    "market_value,last_change",
    [(0, 0), (-5, 0), (1_000_000, 1_000_000), (1_000_000, 2_000_000)],
)
def test_no_forecast_without_a_positive_value_before_and_after(market_value, last_change):
    assert forecast("p", market_value, last_change, DAY, momentum=0.9, cap=0.2) is None


def test_reading_window_before_the_cutoff_is_pre():
    assert reading_window(_epoch(2026, 9, 17, 7, 0)) == (date(2026, 9, 17), PRE)
    assert reading_window(_epoch(2026, 9, 17, 21, 44)) == (date(2026, 9, 17), PRE)
    assert reading_window(_epoch(2026, 9, 17, 0, 5)) == (date(2026, 9, 17), PRE)


def test_reading_window_is_ambiguous_between_the_cutoff_and_the_post_start():
    assert reading_window(_epoch(2026, 9, 17, 21, 45)) is None
    assert reading_window(_epoch(2026, 9, 17, 22, 29)) is None


def test_reading_window_after_the_post_start_is_post():
    assert reading_window(_epoch(2026, 9, 17, 22, 30)) == (date(2026, 9, 17), POST)
    assert reading_window(_epoch(2026, 9, 17, 23, 59)) == (date(2026, 9, 17), POST)


def test_reading_window_follows_berlin_in_winter_too():
    # In January Berlin is UTC+1: 17:00 UTC is 18:00 there, 21:00 UTC is 22:00
    utc_17 = datetime.fromisoformat("2027-01-15T17:00:00+00:00").timestamp()
    assert reading_window(utc_17) == (date(2027, 1, 15), PRE)
    utc_21 = datetime.fromisoformat("2027-01-15T21:00:00+00:00").timestamp()
    assert reading_window(utc_21) is None


def test_berlin_today_crosses_midnight_before_utc_does():
    utc_2230 = datetime.fromisoformat("2026-09-16T22:30:00+00:00").timestamp()
    assert berlin_today(utc_2230) == date(2026, 9, 17)


def test_score_takes_the_next_change_when_the_base_lines_up():
    s = score(10_000_000, 10_200_000, 200_000)
    assert s.outcome == SCORED
    assert s.actual_change == 200_000
    assert s.actual_pct == pytest.approx(0.02)


def test_score_counts_a_flat_update():
    s = score(10_000_000, 10_000_000, 0)
    assert (s.outcome, s.actual_change, s.actual_pct) == (SCORED, 0, 0.0)


def test_score_refuses_a_reading_whose_base_does_not_match():
    s = score(10_000_000, 10_500_000, 200_000)  # previous value 10,300,000
    assert (s.outcome, s.actual_change, s.actual_pct) == (UNSCORABLE, None, None)


def test_score_refuses_a_non_positive_base():
    assert score(0, 100, 100).outcome == UNSCORABLE


def test_backtest_replays_consecutive_days():
    # +10 %, +10 %, then flat: two forecasts (made on day 1 and day 2)
    series = [[100, 110, 121, 121]]
    r = backtest(series, momentum=1.0, cap=0.5)
    assert r.forecasts == 2
    # day 1: predicted +11 (10 % of 110), actual +11 -> hit, miss 0
    # day 2: predicted +12.1 -> round 12, actual 0 -> flat actual, not directional
    assert r.directional == 1 and r.direction_hits == 1
    assert r.direction_rate == 1.0
    assert r.mae_pp == pytest.approx((0 + 10.0) / 2)
    assert r.baseline_mae_pp == pytest.approx((10.0 + 0) / 2)


def test_backtest_counts_a_wrong_direction():
    r = backtest([[100, 110, 99]], momentum=1.0, cap=0.5)
    assert (r.forecasts, r.directional, r.direction_hits) == (1, 1, 0)
    assert r.direction_rate == 0.0


def test_backtest_of_nothing_is_empty_not_an_error():
    r = backtest([[], [5], [5, 6]], momentum=0.9, cap=0.2)
    assert (r.forecasts, r.directional, r.direction_hits) == (0, 0, 0)
    assert r.direction_rate is None
    assert (r.mae_pp, r.baseline_mae_pp) == (0.0, 0.0)
