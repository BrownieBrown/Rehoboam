"""Reserve the ability to keep buying (REH-85).

The bot committed EUR 71m of an EUR 80m ceiling to one player and then made
four more buys all season, finishing on EUR 500,000. The champions each made
one EUR 60-65m signing AND roughly 25 more purchases. The difference is not
the size of a bid; it is what the bid leaves behind.
"""

import pytest
from pydantic import ValidationError

from rehoboam.services.pacing import (
    PacingContext,
    available_squad_slots,
    capital_reserve,
    median_move_price,
)


class TestMedianMovePrice:
    def test_returns_the_median_of_the_observed_prices(self):
        assert median_move_price([1_000_000, 5_000_000, 9_000_000], floor_eur=0) == 5_000_000

    def test_even_count_takes_the_lower_of_the_two_middles(self):
        # Deliberately not an average: a reserve must be a price someone
        # actually paid, not an interpolation between two that nobody did.
        assert median_move_price([2_000_000, 4_000_000], floor_eur=0) == 2_000_000

    def test_empty_population_falls_back_to_the_floor(self):
        # A thin window must not collapse the reserve to zero, which would
        # silently disable pacing exactly when there is least evidence.
        assert median_move_price([], floor_eur=3_000_000) == 3_000_000

    def test_floor_wins_when_the_measured_median_is_below_it(self):
        assert median_move_price([500_000, 600_000], floor_eur=3_000_000) == 3_000_000


class TestAvailableSquadSlots:
    def test_open_bids_count_as_filled(self):
        # Kickbase counts a pending offer toward the 15-player cap.
        assert available_squad_slots(squad_size=11, open_bid_count=1) == 3

    def test_full_squad_has_no_slots(self):
        assert available_squad_slots(squad_size=15, open_bid_count=0) == 0

    def test_over_committed_squad_reports_negative_room_intentionally(self):
        # The negative return is load-bearing: `capital_reserve` branches on
        # `slots_to_fill > 0`, and a negative value routes to the
        # `in_season_min_moves` floor rather than being clamped to zero here.
        assert available_squad_slots(squad_size=15, open_bid_count=2) == -2


class TestCapitalReserve:
    """The move-count arithmetic.

    These pass an ample budget at `max_reserve_fraction=1.0` so REH-101's
    budget bound never binds — the subject here is how many moves the reserve
    funds, which is a separate question from whether the bot can afford them.
    The bound has its own file, `test_pacing_bounded_reserve.py`.
    """

    def test_reserve_accounts_for_this_buy_filling_one_slot(self):
        # Off-by-one pin: at slots=3, reserve accounts for 2 moves remaining
        # after this buy, not 3 unfilled slots before. Using 3*median (32_400_000)
        # would incorrectly prohibit large signings.
        assert (
            capital_reserve(
                slots_to_fill=3,
                in_season_min_moves=2,
                median_move=10_800_000,
                budget=1_000_000_000,
                max_reserve_fraction=1.0,
            )
            == 21_600_000
        )

    def test_full_squad_falls_back_to_the_in_season_minimum(self):
        # At 15/15 there are no slots to fill, but the bot must still be able
        # to replace a player mid-season.
        assert (
            capital_reserve(
                slots_to_fill=0,
                in_season_min_moves=2,
                median_move=10_800_000,
                budget=1_000_000_000,
                max_reserve_fraction=1.0,
            )
            == 21_600_000
        )

    def test_negative_slots_never_shrink_the_reserve_below_the_minimum(self):
        assert (
            capital_reserve(
                slots_to_fill=-2,
                in_season_min_moves=2,
                median_move=10_800_000,
                budget=1_000_000_000,
                max_reserve_fraction=1.0,
            )
            == 21_600_000
        )

    def test_reserve_unwinds_below_in_season_minimum_while_slots_remain(self):
        # At slots=1, the last slot may be filled freely (reserve=0).
        # Completing the squad outranks holding replacement money.
        # Old formula (1*median) would incorrectly give 10_800_000.
        assert (
            capital_reserve(
                slots_to_fill=1,
                in_season_min_moves=2,
                median_move=10_800_000,
                budget=1_000_000_000,
                max_reserve_fraction=1.0,
            )
            == 0
        )


class TestPacingContext:
    def test_max_bid_is_the_ceiling_less_open_offers_and_reserve(self):
        ctx = PacingContext(reserve=32_400_000, open_offers=0)
        assert ctx.max_bid(budget_ceiling=62_307_522, current_budget=62_307_522) == 29_907_522

    def test_open_offers_are_already_spent(self):
        # Kickbase's reported budget does not deduct pending offers, so two
        # bids sized against the same nominal budget can both land.
        ctx = PacingContext(reserve=10_000_000, open_offers=5_000_000)
        assert ctx.max_bid(budget_ceiling=50_000_000, current_budget=50_000_000) == 35_000_000

    def test_max_bid_never_goes_negative(self):
        ctx = PacingContext(reserve=50_000_000, open_offers=0)
        assert ctx.max_bid(budget_ceiling=10_000_000, current_budget=10_000_000) == 0

    def test_reserve_never_exceeds_what_currently_exists(self):
        """A 15/15 squad at EUR 2.0m budget with a EUR 21.6m reserve must not
        refuse a trade that RECOVERS money. Unclamped, the reserve alone
        (21.6m) swamps a budget_ceiling built from a EUR 12.0m sell recovery
        (2.0m + 12.0m = 14.0m), zeroing every trade — including this one,
        which nets +3.14m into the budget. The clamp caps the reserve at
        what is actually spendable right now (current_budget - open_offers),
        so a trade is refused only when it would leave the bot worse off
        than today, never merely because today is already tight.
        """
        ctx = PacingContext(reserve=21_600_000, open_offers=0)
        # budget_ceiling includes the 12.0m sell recovery; current_budget does not.
        cap = ctx.max_bid(budget_ceiling=14_000_000, current_budget=2_000_000)
        assert cap == 12_000_000
        assert cap >= 8_860_000, "the 8.86m buy this reserve was blocking must now fit"

    def test_the_clamp_does_not_loosen_an_already_binding_reserve(self):
        """When current_budget comfortably clears the reserve, the clamp must
        be a no-op — this is the same arithmetic the pre-clamp code always
        did, and asserts the fix does not loosen the cap in the ordinary case
        where the reserve is genuinely affordable."""
        ctx = PacingContext(reserve=32_400_000, open_offers=0)
        clamped = ctx.max_bid(budget_ceiling=62_307_522, current_budget=62_307_522)
        unclamped_formula = 62_307_522 - 0 - 32_400_000
        assert clamped == unclamped_formula == 29_907_522

    def test_the_unwind_sequence_from_the_spec(self):
        """Section 2 of the design doc, as executable arithmetic.

        The point of deriving the reserve from slots-to-fill rather than a
        constant N is that it unwinds. A constant 3 moves would leave the
        reserve at EUR 32.4m while the budget fell, capping the second buy
        near EUR 4.8m and freezing the bot one purchase later.
        """
        median = 10_800_000
        budget = 62_307_522
        caps = []
        for slots in (3, 2, 1):
            reserve = capital_reserve(
                slots_to_fill=slots,
                in_season_min_moves=2,
                median_move=median,
                budget=budget,
                max_reserve_fraction=0.5,
            )
            cap = PacingContext(reserve=reserve, open_offers=0).max_bid(
                budget, current_budget=budget
            )
            caps.append(cap)
            budget -= cap  # spend the whole cap, the worst case for the next step
        assert caps[0] == 40_707_522
        assert all(c > 0 for c in caps), "the reserve must never freeze the next buy"


def test_auto_trader_slot_helper_delegates_to_the_pacing_module():
    """One definition of the squad cap, not two.

    REH-99 was caused by two constants for one concept living in different
    modules and drifting apart. This asserts the copies cannot.
    """
    from rehoboam import auto_trader
    from rehoboam.services import pacing

    # Identity on the FUNCTION, not on SQUAD_CAP: CPython interns small
    # integers, so `15 is 15` is True even for two separate definitions and
    # would pass before this task did anything.
    assert auto_trader.available_squad_slots is pacing.available_squad_slots
    assert auto_trader.SQUAD_CAP == pacing.SQUAD_CAP
    for squad, bids in ((11, 1), (15, 0), (13, 2), (15, 2)):
        assert auto_trader._available_squad_slots(squad, bids) == pacing.available_squad_slots(
            squad, bids
        )


class TestPacingSettings:
    def test_defaults_match_the_design(self, monkeypatch):
        monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
        monkeypatch.setenv("KICKBASE_PASSWORD", "test")
        from rehoboam.config import Settings

        s = Settings()
        assert s.pacing_enabled is True
        assert s.pacing_in_season_min_moves == 2
        assert s.pacing_window_days == 90
        assert s.pacing_median_floor_eur == 3_000_000

    def test_every_knob_is_overridable_from_the_environment(self, monkeypatch):
        """REH-85 requires all pacing knobs to be re-tunable mid-season from .env
        without a deploy. This follows the convention established by REH-99: every
        tunable is a Settings field, populated from the environment."""
        monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
        monkeypatch.setenv("KICKBASE_PASSWORD", "test")
        monkeypatch.setenv("PACING_ENABLED", "false")
        monkeypatch.setenv("PACING_IN_SEASON_MIN_MOVES", "4")
        monkeypatch.setenv("PACING_WINDOW_DAYS", "30")
        monkeypatch.setenv("PACING_MEDIAN_FLOOR_EUR", "1000000")
        from rehoboam.config import Settings

        s = Settings()
        assert s.pacing_enabled is False
        assert s.pacing_in_season_min_moves == 4
        assert s.pacing_window_days == 30
        assert s.pacing_median_floor_eur == 1_000_000

    @pytest.mark.parametrize(
        "env_var",
        ["PACING_IN_SEASON_MIN_MOVES", "PACING_WINDOW_DAYS", "PACING_MEDIAN_FLOOR_EUR"],
    )
    def test_negative_values_are_rejected(self, monkeypatch, env_var):
        """A better guard than the untested `max(0, ...)` clamps inside
        `capital_reserve` — those silently absorb a bad value instead of
        surfacing it. A negative window/floor/move-count is nonsensical
        (there's no such thing as a negative trailing window or a negative
        euro floor), so Settings should refuse to start with one rather than
        let it flow into arithmetic that quietly floors it to zero."""
        monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
        monkeypatch.setenv("KICKBASE_PASSWORD", "test")
        monkeypatch.setenv(env_var, "-1")
        from rehoboam.config import Settings

        with pytest.raises(ValidationError):
            Settings()
