"""Tests for REH-101: the reserve must be bounded by money that exists.

REH-85 sized the reserve purely as `moves_wanted * median_move`. Nothing
bounded it by what the bot owns, so it could demand more capital than exists —
and when it did, it refused every buy rather than degrading.

Measured there: at 14 empty slots the reserve is EUR 65-87m against an ~EUR 80m
starting budget, and buy count came out 3 with pacing on and 3 with pacing off,
identically. Live at 12/15 on 2026-08-27 the reserve was EUR 21,600,000 against
a EUR 21,650,227 budget, capping every plain buy at EUR 50,227.

The second-order effect is the sharper one, and has its own test below:
because the reserve scales with EMPTY SLOTS, selling a player below the median
move price makes the next buy *harder*, since it adds a slot (+1 median of
reserve) while adding less than that to the budget.
"""

import pytest

from rehoboam.services.pacing import PacingContext, capital_reserve

MEDIAN = 10_800_000

# The live position on 2026-08-27, which is what the ticket is measured against.
LIVE_BUDGET = 21_650_227
LIVE_SLOTS = 3


class TestTheBoundBites:
    def test_the_bound_caps_a_reserve_larger_than_the_budget_allows(self):
        """Two moves at the median is EUR 21.6m against a EUR 21.65m budget."""
        reserve = capital_reserve(
            slots_to_fill=LIVE_SLOTS,
            in_season_min_moves=2,
            median_move=MEDIAN,
            budget=LIVE_BUDGET,
            max_reserve_fraction=0.5,
        )

        assert reserve == int(LIVE_BUDGET * 0.5)

    def test_a_reserve_the_budget_can_cover_is_left_alone(self):
        """The bound is a ceiling, not a target — it must not inflate."""
        reserve = capital_reserve(
            slots_to_fill=2,  # one move after this buy
            in_season_min_moves=2,
            median_move=MEDIAN,
            budget=200_000_000,
            max_reserve_fraction=0.5,
        )

        assert reserve == MEDIAN

    def test_fraction_of_one_reproduces_the_unbounded_reserve(self):
        """The REH-85 arithmetic must remain reachable, for A/B and rollback."""
        reserve = capital_reserve(
            slots_to_fill=LIVE_SLOTS,
            in_season_min_moves=2,
            median_move=MEDIAN,
            budget=LIVE_BUDGET,
            max_reserve_fraction=1.0,
        )

        assert reserve == 2 * MEDIAN

    def test_a_zero_fraction_disables_the_reserve(self):
        reserve = capital_reserve(
            slots_to_fill=LIVE_SLOTS,
            in_season_min_moves=2,
            median_move=MEDIAN,
            budget=LIVE_BUDGET,
            max_reserve_fraction=0.0,
        )

        assert reserve == 0

    def test_an_empty_wallet_reserves_nothing(self):
        """Nothing to protect, and a fraction of zero is zero either way."""
        reserve = capital_reserve(
            slots_to_fill=14,
            in_season_min_moves=2,
            median_move=MEDIAN,
            budget=0,
            max_reserve_fraction=0.5,
        )

        assert reserve == 0

    def test_a_negative_budget_never_yields_a_negative_reserve(self):
        """The bot is allowed to go into debt; a negative reserve is nonsense."""
        reserve = capital_reserve(
            slots_to_fill=3,
            in_season_min_moves=2,
            median_move=MEDIAN,
            budget=-5_000_000,
            max_reserve_fraction=0.5,
        )

        assert reserve == 0


class TestRaisingCashMustNotMakeBuyingHarder:
    """The acceptance criterion REH-101 was filed on.

    Selling a player adds one empty slot. Under REH-85's unbounded reserve that
    costs a full median move (EUR 10.8m) while the sale raises less than that,
    so the bot ends up strictly worse off for having raised cash.
    """

    @staticmethod
    def _headroom(budget: int, slots: int, fraction: float) -> int:
        reserve = capital_reserve(
            slots_to_fill=slots,
            in_season_min_moves=2,
            median_move=MEDIAN,
            budget=budget,
            max_reserve_fraction=fraction,
        )
        return PacingContext(reserve=reserve, open_offers=0).max_bid(
            budget_ceiling=budget, current_budget=budget
        )

    def test_selling_a_cheap_player_increases_headroom(self):
        """Live case: 12/15 with EUR 21.65m, then sell someone for EUR 5m."""
        before = self._headroom(LIVE_BUDGET, LIVE_SLOTS, 0.5)
        after = self._headroom(LIVE_BUDGET + 5_000_000, LIVE_SLOTS + 1, 0.5)

        assert after > before, (
            f"raising EUR 5m cut headroom from {before:,} to {after:,} — "
            "the bot is worse off for having sold"
        )

    def test_the_unbounded_reserve_is_what_broke_this(self):
        """Pins the regression: at fraction 1.0 the pathology is still there.

        Without this the suite could not tell a real fix from a coincidence.
        """
        before = self._headroom(LIVE_BUDGET, LIVE_SLOTS, 1.0)
        after = self._headroom(LIVE_BUDGET + 5_000_000, LIVE_SLOTS + 1, 1.0)

        assert after < before

    @pytest.mark.parametrize("sale_price", [1_000_000, 5_000_000, 10_000_000, 25_000_000])
    def test_headroom_never_falls_after_a_sale_at_any_price(self, sale_price):
        before = self._headroom(LIVE_BUDGET, LIVE_SLOTS, 0.5)
        after = self._headroom(LIVE_BUDGET + sale_price, LIVE_SLOTS + 1, 0.5)

        assert after >= before


class TestTheKnob:
    def test_the_fraction_is_a_settings_field(self, monkeypatch):
        monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
        monkeypatch.setenv("KICKBASE_PASSWORD", "x")
        from rehoboam.config import Settings

        assert 0.0 <= Settings().pacing_max_reserve_fraction <= 1.0

    def test_a_negative_fraction_is_refused(self, monkeypatch):
        from pydantic import ValidationError

        monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
        monkeypatch.setenv("KICKBASE_PASSWORD", "x")
        monkeypatch.setenv("PACING_MAX_RESERVE_FRACTION", "-0.5")
        from rehoboam.config import Settings

        with pytest.raises(ValidationError):
            Settings()
