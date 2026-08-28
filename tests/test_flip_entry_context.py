"""Entry context for a flip, reconstructed from recorded market values (REH-104).

`flip_outcomes.trend_at_buy` has existed since the table was created and has
been NULL for all 151 rows: `LearningTracker.record_flip_outcome` runs at SELL
time and `tracked_purchases` never stored the entry context, so there was
nothing to write. That is why REH-34 ("which buy traits predict a profitable
flip?") has never been answerable.

Rather than thread new fields through the buy path — the code that spends real
money, and a change that would only help flips from now on — the context is
reconstructed from `player_mv_history`, which already holds 40,214 snapshots
across 295 players back to 2025-05-09. Every one of the 151 historical flips
has an MV point within 0.45 days of its buy date, so the existing rows can be
backfilled and the entry rule becomes measurable immediately.

The definitions deliberately mirror `services/trend_service.py`: `trend_pct` is
the 14-day change and the direction thresholds are +/-5%. A reconstruction on
any other basis would not correspond to the rule that actually gated the buy
(`profit_trader` requires `trend == "rising" and trend_pct > 5`).
"""

import pytest

from rehoboam.learning.entry_context import EntryContext, entry_context

DAY = 86400.0
BUY = 1_700_000_000.0


def rows(*pairs):
    """(days_before_buy, market_value) -> the (epoch, mv) shape the reader yields."""
    return [(BUY - d * DAY, mv) for d, mv in pairs]


class TestMarketValueAtBuy:
    def test_it_takes_the_snapshot_nearest_the_buy(self):
        ctx = entry_context(rows((0.2, 5_000_000), (9, 4_000_000)), BUY)
        assert ctx.mv_at_buy == 5_000_000

    def test_a_snapshot_beyond_the_tolerance_is_not_used(self):
        """Stale MV is worse than no MV: it would silently mis-state the entry."""
        ctx = entry_context(rows((30, 5_000_000)), BUY)
        assert ctx.mv_at_buy is None

    def test_no_rows_yields_an_empty_context_rather_than_raising(self):
        assert entry_context([], BUY) == EntryContext()


class TestTrendAtBuy:
    def test_a_rise_over_fourteen_days_is_rising(self):
        ctx = entry_context(rows((0, 113_000), (14, 100_000)), BUY)
        assert ctx.trend_pct_at_buy == pytest.approx(13.0)
        assert ctx.trend_at_buy == "rising"

    def test_a_fall_over_fourteen_days_is_falling(self):
        ctx = entry_context(rows((0, 90_000), (14, 100_000)), BUY)
        assert ctx.trend_at_buy == "falling"

    def test_a_small_move_is_stable(self):
        ctx = entry_context(rows((0, 103_000), (14, 100_000)), BUY)
        assert ctx.trend_at_buy == "stable"

    @pytest.mark.parametrize(
        "mv_now,expected",
        [(105_000, "stable"), (105_001, "rising"), (95_000, "stable"), (94_999, "falling")],
    )
    def test_the_thresholds_match_trend_service_exactly(self, mv_now, expected):
        """+/-5% on the 14d change, per trend_service.py:328-333. A different
        boundary would not correspond to the rule that gated the buy."""
        ctx = entry_context(rows((0, mv_now), (14, 100_000)), BUY)
        assert ctx.trend_at_buy == expected

    def test_no_fourteen_day_reference_leaves_the_trend_unknown(self):
        ctx = entry_context(rows((0, 100_000)), BUY)
        assert ctx.trend_pct_at_buy is None
        assert ctx.trend_at_buy is None


class TestDistanceBelowThePeak:
    """The measure the spike says matters. Median MV rose +13.2% in the 14 days
    before a buy and fell -14.2% in the 30 after: the bot buys tops. This is the
    field that makes "do we profit when buying BELOW the recent peak?" answerable.
    """

    def test_buying_at_the_peak_reports_zero(self):
        ctx = entry_context(rows((0, 100_000), (10, 90_000), (20, 80_000)), BUY)
        assert ctx.pct_below_peak_30d_at_buy == pytest.approx(0.0)

    def test_buying_below_the_peak_reports_a_negative_distance(self):
        ctx = entry_context(rows((0, 90_000), (10, 100_000)), BUY)
        assert ctx.pct_below_peak_30d_at_buy == pytest.approx(-10.0)

    def test_the_window_ignores_a_peak_older_than_thirty_days(self):
        ctx = entry_context(rows((0, 90_000), (45, 500_000)), BUY)
        assert ctx.pct_below_peak_30d_at_buy == pytest.approx(0.0)

    def test_snapshots_after_the_buy_are_never_used(self):
        """Look-ahead would make the field predict its own outcome."""
        after = [(BUY + 5 * DAY, 900_000)]
        ctx = entry_context(rows((0, 90_000), (10, 100_000)) + after, BUY)
        assert ctx.pct_below_peak_30d_at_buy == pytest.approx(-10.0)


class TestDegenerateInput:
    def test_a_zero_market_value_does_not_divide_by_zero(self):
        ctx = entry_context(rows((0, 100_000), (14, 0)), BUY)
        assert ctx.trend_pct_at_buy is None

    def test_rows_out_of_order_are_handled(self):
        ctx = entry_context(rows((14, 100_000), (0, 113_000))[::-1], BUY)
        assert ctx.trend_at_buy == "rising"
