"""Tests for the trend-aware loss-sell guard in AutoTrader.

The profit-take side of the sell phase already adjusts its threshold based
on 7-day price trend (`_sell_threshold_for_trend`). The loss side did not,
which led to selling rebounding players at a locked-in loss the moment any
unrelated buy candidate showed up. The guard tested here keeps loss-sells
from firing while the player's price is meaningfully rebounding.
"""

from types import SimpleNamespace

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


def _buy_rec(position: str, marginal_ep_gain: float) -> SimpleNamespace:
    """Minimal stand-in for `BuyRecommendation` — only the fields the
    helper reads. Real instances require a full PlayerScore + MarketPlayer
    which would dwarf the test signal.
    """
    return SimpleNamespace(
        player=SimpleNamespace(position=position),
        marginal_ep_gain=marginal_ep_gain,
    )


def _trade_pair(position: str, ep_gain: float) -> SimpleNamespace:
    """Minimal stand-in for `TradePair`. Note the field names differ from
    BuyRecommendation: `buy_player.position` and `ep_gain` (not
    `marginal_ep_gain`)."""
    return SimpleNamespace(
        buy_player=SimpleNamespace(position=position),
        ep_gain=ep_gain,
    )


class TestHasPositionReplacement:
    """Replaces the old coarse `len(buy_recs) > 0 or len(trade_pairs) > 0`
    flag. A defender's loss-sell should fire only when a *defender*
    upgrade is actually queued, not when a forward sits in the pipeline.
    """

    MIN_GAIN = 5.0

    def test_no_candidates_at_all(self):
        assert (
            AutoTrader._has_position_replacement(
                "Defender", buy_recs=[], trade_pairs=[], min_ep_gain=self.MIN_GAIN
            )
            is False
        )

    def test_only_wrong_position_candidates(self):
        # The exact Svensson scenario: defender at a loss, only forward
        # buys queued. Old code triggered sell; new code holds.
        assert (
            AutoTrader._has_position_replacement(
                "Defender",
                buy_recs=[_buy_rec("Forward", 12.0)],
                trade_pairs=[_trade_pair("Midfielder", 8.0)],
                min_ep_gain=self.MIN_GAIN,
            )
            is False
        )

    def test_right_position_below_threshold(self):
        # Same-position upgrade exists but the EP gain is too small to
        # justify locking in a market-value loss.
        assert (
            AutoTrader._has_position_replacement(
                "Defender",
                buy_recs=[_buy_rec("Defender", 2.0)],
                trade_pairs=[],
                min_ep_gain=self.MIN_GAIN,
            )
            is False
        )

    def test_right_position_above_threshold(self):
        assert (
            AutoTrader._has_position_replacement(
                "Defender",
                buy_recs=[_buy_rec("Defender", 8.0)],
                trade_pairs=[],
                min_ep_gain=self.MIN_GAIN,
            )
            is True
        )

    def test_threshold_is_inclusive(self):
        # A candidate exactly at the threshold counts as a valid
        # replacement — matches DecisionEngine's >= semantics.
        assert (
            AutoTrader._has_position_replacement(
                "Forward",
                buy_recs=[_buy_rec("Forward", 5.0)],
                trade_pairs=[],
                min_ep_gain=self.MIN_GAIN,
            )
            is True
        )

    def test_trade_pair_replacement(self):
        # Trade pairs use a different field name (`ep_gain`) and a
        # different access path (`buy_player.position`); both must be
        # honored.
        assert (
            AutoTrader._has_position_replacement(
                "Midfielder",
                buy_recs=[],
                trade_pairs=[_trade_pair("Midfielder", 10.0)],
                min_ep_gain=self.MIN_GAIN,
            )
            is True
        )

    def test_mixed_list_one_valid_match(self):
        # Several candidates of various positions; only one matches the
        # player's position with sufficient gain — that's enough.
        assert (
            AutoTrader._has_position_replacement(
                "Defender",
                buy_recs=[
                    _buy_rec("Forward", 20.0),
                    _buy_rec("Defender", 1.0),  # right pos but too small
                    _buy_rec("Defender", 7.0),  # the match
                    _buy_rec("Midfielder", 15.0),
                ],
                trade_pairs=[_trade_pair("Forward", 12.0)],
                min_ep_gain=self.MIN_GAIN,
            )
            is True
        )

    def test_goalkeeper_specific(self):
        # GK is its own position — a defender candidate must not satisfy
        # a GK loss-sell, even at a huge EP gain.
        assert (
            AutoTrader._has_position_replacement(
                "Goalkeeper",
                buy_recs=[_buy_rec("Defender", 50.0)],
                trade_pairs=[],
                min_ep_gain=self.MIN_GAIN,
            )
            is False
        )
