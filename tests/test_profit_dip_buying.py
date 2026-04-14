"""Tests for dip-buying logic in ProfitTrader.find_profit_opportunities()
and the matchday hold-time cap in auto_trader._max_flip_hold_days().
"""

from rehoboam.auto_trader import _max_flip_hold_days
from rehoboam.kickbase_client import MarketPlayer
from rehoboam.profit_trader import ProfitTrader


def _make_player(**overrides) -> MarketPlayer:
    defaults = {
        "id": "p1",
        "first_name": "Test",
        "last_name": "Player",
        "position": "Midfielder",
        "team_id": "t1",
        "team_name": "Test FC",
        "price": 5_000_000,
        "market_value": 5_000_000,  # Equal to price → KICKBASE seller
        "points": 80,
        "average_points": 35.0,
        "status": 0,
    }
    defaults.update(overrides)
    return MarketPlayer(**defaults)


def _trend(
    direction: str = "stable",
    pct: float = 0.0,
    current: int = 5_000_000,
    peak: int = 5_500_000,
    is_dip_in_uptrend: bool = False,
    is_recovery: bool = False,
    is_secular_decline: bool = False,
):
    return {
        "has_data": True,
        "trend": direction,
        "trend_pct": pct,
        "current_value": current,
        "peak_value": peak,
        "is_dip_in_uptrend": is_dip_in_uptrend,
        "is_recovery": is_recovery,
        "is_secular_decline": is_secular_decline,
    }


class TestDipInUptrendBuy:
    def test_dip_in_uptrend_creates_opportunity(self):
        """A short-term dip in a longer uptrend → flip candidate."""
        player = _make_player(average_points=35.0)
        trader = ProfitTrader()
        opps = trader.find_profit_opportunities(
            market_players=[player],
            current_budget=20_000_000,
            player_trends={
                player.id: _trend(direction="falling", pct=-3.0, is_dip_in_uptrend=True),
            },
            team_value=80_000_000,
        )
        assert len(opps) == 1
        assert "appreciation" in opps[0].reason.lower() or opps[0].expected_appreciation > 0

    def test_dip_in_uptrend_requires_decent_avg_points(self):
        """Sub-30 avg points → dip-in-uptrend not enough; skip."""
        player = _make_player(average_points=15.0)
        trader = ProfitTrader()
        opps = trader.find_profit_opportunities(
            market_players=[player],
            current_budget=20_000_000,
            player_trends={
                player.id: _trend(direction="falling", pct=-3.0, is_dip_in_uptrend=True),
            },
            team_value=80_000_000,
        )
        # MIN_AVG_POINTS=20 filter would catch this anyway, but verify behavior
        assert opps == []

    def test_recovery_signal_triggers_buy(self):
        """Recovery (short-term up after dip) → flip candidate."""
        player = _make_player(average_points=32.0)
        trader = ProfitTrader()
        opps = trader.find_profit_opportunities(
            market_players=[player],
            current_budget=20_000_000,
            player_trends={
                player.id: _trend(direction="rising", pct=4.0, is_recovery=True),
            },
            team_value=80_000_000,
        )
        assert len(opps) == 1


class TestSecularDeclineBlocked:
    def test_secular_decline_blocks_falling_buy(self):
        """A falling player flagged as secular decline should NOT be a flip candidate
        even if they're far below peak."""
        player = _make_player(average_points=50.0)
        trader = ProfitTrader()
        opps = trader.find_profit_opportunities(
            market_players=[player],
            current_budget=20_000_000,
            player_trends={
                player.id: _trend(
                    direction="falling",
                    pct=-15.0,
                    current=3_000_000,
                    peak=8_000_000,
                    is_secular_decline=True,
                ),
            },
            team_value=80_000_000,
        )
        assert opps == []


class TestLowerDipThreshold:
    def test_below_peak_triggers_mean_reversion(self):
        """Threshold lowered from -50% to -25% — a player far below peak
        with high avg_points qualifies as a mean-reversion play. (Old code
        only triggered below -50%.)"""
        player = _make_player(price=4_000_000, market_value=4_000_000, average_points=50.0)
        trader = ProfitTrader()
        opps = trader.find_profit_opportunities(
            market_players=[player],
            current_budget=20_000_000,
            player_trends={
                player.id: _trend(
                    direction="falling",
                    pct=-8.0,
                    current=4_000_000,
                    peak=8_000_000,  # 50% below peak — old threshold also met
                ),
            },
            team_value=80_000_000,
        )
        assert len(opps) == 1

    def test_secular_decline_blocked_even_far_below_peak(self):
        """Far below peak BUT secular decline → still skipped (regression
        check that the new is_secular_decline gate fires)."""
        player = _make_player(price=4_000_000, market_value=4_000_000, average_points=50.0)
        trader = ProfitTrader()
        opps = trader.find_profit_opportunities(
            market_players=[player],
            current_budget=20_000_000,
            player_trends={
                player.id: _trend(
                    direction="falling",
                    pct=-8.0,
                    current=4_000_000,
                    peak=8_000_000,
                    is_secular_decline=True,
                ),
            },
            team_value=80_000_000,
        )
        assert opps == []


class TestMaxFlipHoldDays:
    def test_unknown_schedule_no_cap(self):
        assert _max_flip_hold_days(None) is None

    def test_3_days_until_match_cap_is_2(self):
        """3 days until match → flip must complete in ≤2 days (1d safety buffer)."""
        assert _max_flip_hold_days(3) == 2

    def test_1_day_until_match_floors_at_1(self):
        """Even with very tight schedule, return at least 1 (don't return 0/negative)."""
        assert _max_flip_hold_days(1) == 1

    def test_far_match_allows_long_holds(self):
        assert _max_flip_hold_days(10) == 9
