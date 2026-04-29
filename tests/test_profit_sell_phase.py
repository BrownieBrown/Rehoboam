"""Tests for the trend-aware loss-sell guard in AutoTrader.

The profit-take side of the sell phase already adjusts its threshold based
on 7-day price trend (`_sell_threshold_for_trend`). The loss side did not,
which led to selling rebounding players at a locked-in loss the moment any
unrelated buy candidate showed up. The guard tested here keeps loss-sells
from firing while the player's price is meaningfully rebounding.
"""

from rehoboam.auto_trader import AutoTrader


class TestCanLossSellWithReplacement:
    def test_no_trend_data_allows_sell(self):
        # No data → fall back to legacy behavior so we don't regress on
        # players the trend service can't score yet.
        assert AutoTrader._can_loss_sell_with_replacement(None) is True

    def test_flat_trend_allows_sell(self):
        assert AutoTrader._can_loss_sell_with_replacement(0.0) is True

    def test_declining_trend_allows_sell(self):
        assert AutoTrader._can_loss_sell_with_replacement(-3.5) is True

    def test_steeply_declining_allows_sell(self):
        assert AutoTrader._can_loss_sell_with_replacement(-12.0) is True

    def test_mild_uptick_below_threshold_allows_sell(self):
        # Sub-1% drift is noise, not a rebound — don't paralyze the bot.
        assert AutoTrader._can_loss_sell_with_replacement(0.4) is True

    def test_meaningful_rebound_blocks_sell(self):
        # Svensson's case: down ~10% from buy, but bouncing back at +2%/wk.
        # Realizing the loss now wastes the recovery already in progress.
        assert AutoTrader._can_loss_sell_with_replacement(2.0) is False

    def test_strong_uptrend_blocks_sell(self):
        assert AutoTrader._can_loss_sell_with_replacement(5.0) is False

    def test_threshold_boundary_blocks_sell(self):
        # 1.0% is the cutoff — at-or-above counts as a rebound.
        assert AutoTrader._can_loss_sell_with_replacement(1.0) is False
