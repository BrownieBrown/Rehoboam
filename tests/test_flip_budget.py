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
        assert _compute_flip_budget("moderate", 20_000_000, 5_000_000, 0) == 15_000_000

    def test_moderate_phase_adds_max_debt_like_aggressive(self):
        """Marco, 2026-09-22: "it can go into minus until gameday, always".
        The locked window repays the debt (`_run_debt_recovery`), so 2-4 days
        out is no longer a no-debt zone."""
        assert _compute_flip_budget("moderate", 10_000_000, 0, 99_000_000) == 109_000_000
        assert _compute_flip_budget("moderate", 10_000_000, 2_000_000, 15_000_000) == (
            _compute_flip_budget("aggressive", 10_000_000, 2_000_000, 15_000_000)
        )

    def test_an_unknown_schedule_allows_no_new_debt(self):
        """Neither the schedule nor /myeleven gave a kickoff: the phase is a
        fallback, and the recovery (gated on the day count) could never fire
        before a kickoff the bot cannot see — so no NEW debt, as before."""
        assert (
            _compute_flip_budget(
                "moderate", 10_000_000, 2_000_000, 99_000_000, schedule_known=False
            )
            == 8_000_000
        )

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


class TestKickbaseDebtCap:
    """Kickbase refuses an offer that would take the wallet below -33% of
    total worth (`err 5050 ThirtyThreePercentRuleExceeded`): nine of the
    bot's offers failed that way between 09-22 and 10-04, because its own
    allowance is 60% of team value. The allowance has to stop where Kickbase
    stops, net of what is already committed."""

    def test_the_cap_binds_below_the_sixty_percent_allowance(self):
        from rehoboam.auto_trader import _max_debt

        # 60% of 100m is 60m; 33% of worth (100m - 20m) is 26.4m.
        assert (
            _max_debt(team_value=100_000_000, budget=-20_000_000, max_debt_pct=60.0, worth_pct=33.0)
            == 26_400_000
        )

    def test_a_small_allowance_is_left_alone(self):
        from rehoboam.auto_trader import _max_debt

        assert (
            _max_debt(team_value=100_000_000, budget=0, max_debt_pct=10.0, worth_pct=33.0)
            == 10_000_000
        )

    def test_unknown_team_value_means_no_debt(self):
        from rehoboam.auto_trader import _max_debt

        assert _max_debt(team_value=0, budget=5_000_000, max_debt_pct=60.0, worth_pct=33.0) == 0
