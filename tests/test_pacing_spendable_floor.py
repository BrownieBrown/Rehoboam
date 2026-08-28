"""REH-107: a reserve must never block a move of typical size.

REH-85 sized the reserve as `moves * median_move`. REH-101 bounded it by
`budget * max_reserve_fraction`, because a reserve larger than the wallet
refuses every buy instead of degrading. Correct, but the bounded figure stops
representing a whole number of moves: at 0.5 of a EUR 21.72m budget it is
EUR 10.86m held back to fund "2 further moves" at a measured median of
EUR 12.5m — it funds 0.87 of one.

So it buys nothing later while blocking the best upgrade available today. A
reserve that exists to keep future moves possible must not make the present
move impossible; that is the incoherence this floor removes.

The bound is a THIRD term rather than a replacement, and it is deliberately
continuous in budget: REH-101's acceptance criterion is that raising cash never
reduces headroom, so a step function keyed on whole moves — the shape this
ticket first sketched — would have reintroduced exactly the pathology it fixed.
See TestRaisingCashIsStillNeverPunished below.
"""

import pytest

from rehoboam.services.pacing import PacingContext, capital_reserve

# The live position on 2026-08-28, which is what the ticket is measured against.
LIVE_BUDGET = 21_720_227
LIVE_SLOTS = 3
LIVE_MEDIAN = 12_500_000

# Engelhardt: +45.0 EP, the largest marginal gain on the board that morning.
ENGELHARDT_ASK = 10_994_415


def _reserve(budget=LIVE_BUDGET, slots=LIVE_SLOTS, median=LIVE_MEDIAN, fraction=0.5, **kw):
    return capital_reserve(
        slots_to_fill=slots,
        in_season_min_moves=2,
        median_move=median,
        budget=budget,
        max_reserve_fraction=fraction,
        **kw,
    )


class TestTheReserveNeverBlocksATypicalMove:
    def test_a_median_move_stays_affordable(self):
        """The whole point: what is held back must not exceed what is spare."""
        assert LIVE_BUDGET - _reserve() >= LIVE_MEDIAN

    def test_the_live_case_unblocks_the_best_upgrade_on_the_board(self):
        """Engelhardt missed the old cap by EUR 134,301 and got bid=0."""
        headroom = LIVE_BUDGET - _reserve()
        assert headroom >= ENGELHARDT_ASK

    def test_a_budget_below_one_move_reserves_nothing(self):
        """Nothing can be both held back and spent. Holding wins nothing."""
        assert _reserve(budget=8_000_000) == 0

    def test_a_rich_bot_is_unaffected_because_the_floor_never_binds(self):
        """The floor is a relief valve, not a new cap on a healthy budget."""
        assert _reserve(budget=200_000_000, slots=2) == LIVE_MEDIAN


class TestTheFloorIsSwitchable:
    def test_zero_reproduces_the_reh_101_behaviour_exactly(self):
        """Rollback affordance, matching how every other pacing knob works."""
        assert _reserve(min_spendable_moves=0.0) == int(LIVE_BUDGET * 0.5)

    def test_a_larger_floor_holds_back_less(self):
        """Monotone in the knob, so a sweep reads in one direction."""
        assert _reserve(min_spendable_moves=2.0) < _reserve(min_spendable_moves=1.0)


class TestRaisingCashIsStillNeverPunished:
    """REH-101's acceptance criterion, re-run across the floor's boundary.

    A step function on whole moves would pass the live case and break here:
    crossing a move boundary would drop headroom by a full median.
    """

    @staticmethod
    def _headroom(budget, slots):
        return PacingContext(reserve=_reserve(budget=budget, slots=slots), open_offers=0).max_bid(
            budget_ceiling=budget, current_budget=budget
        )

    @pytest.mark.parametrize("sale_price", [1, 500_000, 5_000_000, 12_500_000, 40_000_000])
    def test_headroom_never_falls_after_a_sale(self, sale_price):
        before = self._headroom(LIVE_BUDGET, LIVE_SLOTS)
        after = self._headroom(LIVE_BUDGET + sale_price, LIVE_SLOTS + 1)
        assert after >= before

    @pytest.mark.parametrize("budget", range(5_000_000, 60_000_001, 2_500_000))
    def test_headroom_is_monotonic_in_budget_across_the_boundary(self, budget):
        """Swept right through the floor's cliff-risk region."""
        assert self._headroom(budget + 1_000_000, LIVE_SLOTS) >= self._headroom(budget, LIVE_SLOTS)


class TestTheKnobIsTunableFromTheEnvironment:
    """Every pacing tunable is a Settings field, per REH-85/REH-99 convention:
    re-tunable mid-season from .env without a deploy."""

    def _settings(self, monkeypatch, **env):
        monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
        monkeypatch.setenv("KICKBASE_PASSWORD", "test")
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        from rehoboam.config import Settings

        return Settings()

    def test_the_default_leaves_exactly_one_move_spendable(self, monkeypatch):
        assert self._settings(monkeypatch).pacing_min_spendable_moves == 1.0

    def test_it_is_overridable(self, monkeypatch):
        s = self._settings(monkeypatch, PACING_MIN_SPENDABLE_MOVES="2.5")
        assert s.pacing_min_spendable_moves == 2.5

    def test_zero_is_allowed_because_it_is_the_rollback(self, monkeypatch):
        s = self._settings(monkeypatch, PACING_MIN_SPENDABLE_MOVES="0")
        assert s.pacing_min_spendable_moves == 0.0

    def test_a_negative_floor_is_refused(self, monkeypatch):
        """Same reasoning as the other pacing knobs: surface a bad value
        rather than let `max(0.0, ...)` quietly absorb it."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            self._settings(monkeypatch, PACING_MIN_SPENDABLE_MOVES="-1")


class TestTheLiveSessionAppliesTheFloor:
    """The knob is worthless if the session never reads it.

    REH-85 shipped `pacing_max_reserve_fraction` and REH-101 had to add a
    second call-site read for it; this pins the wiring so the same gap cannot
    open again. Budget is chosen so the floor binds and the fraction does not
    (budget < 2 x median), which is the only region where the two differ.
    """

    @staticmethod
    def _trader(settings):
        from unittest.mock import MagicMock

        from rehoboam.trader import Trader

        learner = MagicMock()
        learner.recent_buy_prices.return_value = [10_800_000] * 9
        return Trader(api=MagicMock(), settings=settings, bid_learner=learner)

    def _settings(self, monkeypatch, **env):
        monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
        monkeypatch.setenv("KICKBASE_PASSWORD", "test")
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        from rehoboam.config import Settings

        return Settings()

    def test_the_session_reserve_leaves_a_median_move_spendable(self, monkeypatch):
        trader = self._trader(self._settings(monkeypatch))
        ctx = trader._build_pacing_context(squad_size=12, my_bids=[], current_budget=20_000_000)
        assert ctx is not None
        assert 20_000_000 - ctx.reserve >= 10_800_000

    def test_setting_the_knob_to_zero_restores_the_old_session_reserve(self, monkeypatch):
        trader = self._trader(self._settings(monkeypatch, PACING_MIN_SPENDABLE_MOVES="0"))
        ctx = trader._build_pacing_context(squad_size=12, my_bids=[], current_budget=20_000_000)
        assert ctx is not None
        assert ctx.reserve == 10_000_000  # the REH-101 fraction, unrelieved
