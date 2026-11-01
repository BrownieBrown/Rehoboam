"""The flip entry signal (`services/flip_entry`): the start of a run."""

from __future__ import annotations

import pytest

from rehoboam.services.flip_entry import flip_entry
from rehoboam.services.trend_service import TrendService

M = 1_000_000

RULES = {
    "min_3d_move_pct": 5.0,
    "min_last_move_pct": 0.5,
    "max_decel_ratio": 0.5,
    "expected_gain_pct": 15.0,
    "min_average_points": 20.0,
    "starter_band_from": 5 * M,
    "max_market_value": 15 * M,
}


def _entry(**over):
    args = {
        "market_value": 2 * M,
        "average_points": 40.0,
        "lineup_code": 3,
        "last_move_pct": 3.0,
        "prev_move_pct": 3.0,
        "move_3d_pct": 9.0,
        "forecast_pct": 2.0,
    }
    args.update(over)
    return flip_entry(**args, **RULES)


def test_a_cheap_riser_is_a_flip_priced_on_the_measured_gain():
    e = _entry()
    assert e.ok and e.reason == "ok" and e.expected_pct == 15.0


def test_no_14_day_trend_is_needed():
    # The case the old rule dropped: three nights of +3%, no 14-day history.
    assert _entry(move_3d_pct=9.0).ok


@pytest.mark.parametrize(
    ("over", "reason"),
    [
        ({"market_value": 15 * M}, "price_band"),
        ({"market_value": 0}, "price_band"),
        ({"last_move_pct": None}, "no_history"),
        ({"move_3d_pct": None}, "no_history"),
        ({"last_move_pct": -0.1}, "turned"),
        ({"forecast_pct": -1.0}, "turned"),
        ({"last_move_pct": 0.3}, "weak_last_move"),
        ({"last_move_pct": 1.0, "prev_move_pct": 3.0}, "decelerating"),
        ({"move_3d_pct": 4.9}, "no_3d_momentum"),
    ],
)
def test_refusals_are_named(over, reason):
    e = _entry(**over)
    assert not e.ok and e.reason == reason and e.expected_pct == 0.0


def test_unknown_forecast_does_not_veto():
    assert _entry(forecast_pct=None).ok


class TestCheapBand:
    def test_low_average_non_starter_is_skipped(self):
        assert _entry(average_points=10.0, lineup_code=3).reason == "low_average"

    def test_low_average_starter_is_a_flip(self):
        # A cheap player who has just entered the eleven.
        assert _entry(average_points=0.0, lineup_code=1).ok
        assert _entry(average_points=None, lineup_code=2).ok

    def test_unknown_code_counts_as_not_a_starter(self):
        assert _entry(average_points=10.0, lineup_code=None).reason == "low_average"


class TestMidBand:
    def test_needs_code_one_or_two(self):
        assert _entry(market_value=8 * M, lineup_code=3).reason == "not_starter"
        assert _entry(market_value=8 * M, lineup_code=None).reason == "not_starter"
        assert _entry(market_value=8 * M, lineup_code=2).ok

    def test_average_is_not_the_gate_there(self):
        assert _entry(market_value=8 * M, lineup_code=1, average_points=5.0).ok


class TestThreeNightMove:
    @staticmethod
    def _history(values):
        return {"it": [{"dt": i, "mv": v} for i, v in enumerate(values)]}

    def test_three_nights_back_from_the_current_value(self):
        t = TrendService.analyze(self._history([100, 100, 100, 100, 110]), 110)
        assert t.trend_3d_pct == pytest.approx(10.0)

    def test_a_lagging_history_counts_the_night_to_current(self):
        t = TrendService.analyze(self._history([100, 100, 100, 104]), 110)
        assert t.trend_3d_pct == pytest.approx(10.0)

    def test_short_history_uses_what_there_is(self):
        t = TrendService.analyze(self._history([100, 105]), 105)
        assert t.trend_3d_pct == pytest.approx(5.0)

    def test_carried_in_the_trend_dict(self):
        t = TrendService.analyze(self._history([100, 100, 100, 100, 110]), 110)
        assert t.to_dict()["trend_3d_pct"] == pytest.approx(10.0)


class TestWiring:
    def test_settings_switch_the_rule_on(self):
        from rehoboam.config import Settings
        from rehoboam.trader import flip_entry_rules

        rules = flip_entry_rules(
            Settings(_env_file=None, kickbase_email="t@example.com", kickbase_password="x")
        )
        assert rules is not None and rules.min_3d_move_pct == 5.0
        assert rules.max_market_value == 15 * M

    def test_a_settings_double_keeps_the_legacy_rule(self):
        from types import SimpleNamespace

        from rehoboam.trader import flip_entry_rules

        assert flip_entry_rules(SimpleNamespace()) is None

    def test_the_search_finds_a_fresh_starter_the_legacy_rule_drops(self):
        from types import SimpleNamespace

        from rehoboam.profit_trader import FlipEntryRules, ProfitTrader

        player = SimpleNamespace(
            id="p1",
            first_name="A",
            last_name="Fresh",
            position="Defender",
            status=0,
            price=2 * M,
            market_value=2 * M,
            average_points=8.0,  # under the legacy 20-point floor
            lineup_probability=1,
        )
        trend = {
            "has_data": True,
            "trend": "stable",
            "trend_pct": 2.0,  # no 14-day trend yet
            "trend_1d_pct": 3.0,
            "trend_1d_prev_pct": 3.0,
            "trend_3d_pct": 9.0,
            "current_value": 2 * M,
            "peak_value": 2 * M,
        }
        kwargs = {
            "market_players": [player],
            "current_budget": 10 * M,
            "player_trends": {"p1": trend},
        }
        legacy = ProfitTrader(min_profit_pct=8.0, max_risk_score=60.0)
        momentum = ProfitTrader(
            min_profit_pct=8.0,
            max_risk_score=60.0,
            min_last_move_pct=0.5,
            max_decel_ratio=0.5,
            entry_rules=FlipEntryRules(),
        )
        assert legacy.find_profit_opportunities(**kwargs) == []
        [opp] = momentum.find_profit_opportunities(**kwargs)
        assert opp.expected_appreciation == 15.0
