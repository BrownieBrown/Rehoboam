"""Tests for flip-budget math used by the auto-trade session.

The helper is called twice per session: once when the session context
is built, and again inside the trade phase after earlier phases
(sells, squad optimization, bid cancellations) have changed the
underlying numbers.
"""

from rehoboam.auto_trader import _compute_flip_budget


class TestComputeFlipBudget:
    def test_locked_phase_returns_zero(self):
        assert _compute_flip_budget("locked", 10_000_000, 0, 5_000_000) == 0

    def test_moderate_phase_subtracts_pending_bids(self):
        assert _compute_flip_budget("moderate", 20_000_000, 5_000_000, 10_000_000) == 15_000_000

    def test_moderate_phase_ignores_max_debt(self):
        assert _compute_flip_budget("moderate", 10_000_000, 0, 99_000_000) == 10_000_000

    def test_aggressive_phase_adds_max_debt(self):
        assert _compute_flip_budget("aggressive", 10_000_000, 2_000_000, 15_000_000) == 23_000_000

    def test_canceling_bid_frees_budget(self):
        # Regression guard for the "stale flip_budget" bug: after a bid
        # cancel, pending_bid_total drops, and the trade phase must see
        # the freed cash.
        before = _compute_flip_budget("moderate", 21_000_000, 18_000_000, 0)
        after_cancel = _compute_flip_budget("moderate", 21_000_000, 8_000_000, 0)
        assert after_cancel - before == 10_000_000

    def test_sell_increases_budget(self):
        # Regression guard for the same bug via a different trigger:
        # a mid-session sell raises current_budget, and the trade phase
        # must see the proceeds.
        before = _compute_flip_budget("moderate", 3_000_000, 0, 0)
        after_sell = _compute_flip_budget("moderate", 21_000_000, 0, 0)
        assert after_sell - before == 18_000_000
