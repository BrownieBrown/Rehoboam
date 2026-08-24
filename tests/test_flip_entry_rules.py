"""Flip buys: rising trends only, and never far above market value.

A flip is bought to be sold, so any premium at entry must be earned back
before the trade breaks even. Across 151 real flips the strategy lost
EUR 55.3M at a 28% win rate.
"""

from types import SimpleNamespace

from rehoboam.config import Settings
from rehoboam.profit_trader import ProfitTrader


def _player(price, mv, avg=50.0):
    return SimpleNamespace(
        id="1",
        first_name="A",
        last_name="B",
        position="Midfielder",
        price=price,
        market_value=mv,
        average_points=avg,
        team_id="40",
        status=0,
        points=0,
        user_offer_id=None,
        user_offer_price=0,
    )


def _trend(direction, pct, **extra):
    base = {
        "has_data": True,
        "trend": direction,
        "trend_pct": pct,
        "current_value": 1_000_000,
        "peak_value": 1_000_000,
        "is_dip_in_uptrend": False,
        "is_secular_decline": False,
        "is_recovery": False,
    }
    base.update(extra)
    return base


def _find(trader, player, trend):
    return trader.find_profit_opportunities(
        market_players=[player],
        current_budget=50_000_000,
        player_trends={player.id: trend},
        max_opportunities=5,
    )


class TestRisingOnly:
    def test_a_rising_player_is_a_candidate(self):
        t = ProfitTrader(min_profit_pct=5.0)
        assert _find(t, _player(1_000_000, 1_000_000), _trend("rising", 12.0))

    def test_a_falling_player_is_not(self):
        t = ProfitTrader(min_profit_pct=5.0)
        assert (
            _find(t, _player(1_000_000, 1_000_000), _trend("falling", -30.0, peak_value=2_000_000))
            == []
        )

    def test_a_stable_player_is_not(self):
        """Stable was a bet on modest drift, not on momentum."""
        t = ProfitTrader(min_profit_pct=5.0)
        assert _find(t, _player(1_000_000, 1_000_000, avg=60.0), _trend("stable", 0.0)) == []

    def test_a_dip_in_an_uptrend_is_not(self):
        """Mean reversion is a prediction the market is wrong."""
        t = ProfitTrader(min_profit_pct=5.0)
        assert (
            _find(t, _player(1_000_000, 1_000_000), _trend("falling", -8.0, is_dip_in_uptrend=True))
            == []
        )

    def test_the_mean_reversion_branches_still_work_when_opted_in(self):
        """They are disabled, not deleted — the replay still needs them."""
        t = ProfitTrader(min_profit_pct=5.0, require_rising_trend=False)
        assert _find(t, _player(1_000_000, 1_000_000, avg=60.0), _trend("stable", 0.0))


class TestTheEntryPriceIsCapped:
    """A guard rather than a routine clamp.

    Flips only consider Kickbase-listed players, and for those the asking
    price IS the market value — `is_kickbase` is literally
    `price == market_value`. So the live path already buys at market value and
    the cap never binds. It exists so that a listing priced above market value
    can never be flipped at a premium, whatever route it arrives by: a flip is
    bought to be sold, so any premium has to be earned back before the trade
    breaks even.
    """

    def test_the_ceiling_is_market_value_plus_the_allowed_premium(self):
        t = ProfitTrader(max_overpay_pct=1.0)
        assert t._max_flip_price(_player(1_000_000, 1_000_000)) == 1_010_000

    def test_zero_percent_means_never_above_market_value(self):
        t = ProfitTrader(max_overpay_pct=0.0)
        assert t._max_flip_price(_player(1_000_000, 1_000_000)) == 1_000_000

    def test_a_kickbase_listing_is_bought_at_market_value(self):
        """The normal case: asking price equals market value, so nothing clamps."""
        t = ProfitTrader(min_profit_pct=5.0)
        opps = _find(t, _player(1_000_000, 1_000_000), _trend("rising", 12.0))
        assert opps[0].buy_price == 1_000_000

    def test_the_cap_binds_if_a_listing_is_ever_priced_above_market_value(self):
        t = ProfitTrader(min_profit_pct=5.0)
        player = _player(1_000_000, 1_000_000)
        assert min(1_200_000, t._max_flip_price(player)) == 1_010_000


class TestDefaults:
    def test_the_shipped_settings_are_one_percent_and_rising_only(self):
        s = Settings()
        assert s.max_flip_overpay_pct == 1.0
        assert s.flip_buys_require_rising_trend is True
