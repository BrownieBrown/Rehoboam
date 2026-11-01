"""The core book (`services/books`): debt is repaid by the trading book.

Regression: on 2026-10-28 the locked window sold Raum (29.6m, 126.6 a match
after) to repay a debt the bot had run up buying other players.
"""

from __future__ import annotations

import pytest

from rehoboam.services.books import (
    CoreBookCheck,
    Holding,
    core_ids,
    debt_cover_reason,
    liquid_value,
)
from rehoboam.services.debt_recovery import DebtCandidate, plan_debt_recovery

M = 1_000_000


def _h(pid: str, mv_m: float, ep: float, avg: float | None) -> Holding:
    return Holding(player_id=pid, market_value=int(mv_m * M), expected_points=ep, avg_points=avg)


# A squad shaped like 2026-10-27: eight producers, one passenger.
STARS = [_h(f"star{i}", 15.0, 110.0 - i, 120.0 - i) for i in range(8)]
PASSENGER = _h("can", 0.5, 0.0, None)
BENCH_BUY = _h("bench", 22.3, 33.5, 30.0)


class TestCoreIdsContract:
    """What any choice of core must satisfy."""

    def test_size_zero_is_an_empty_core(self):
        assert core_ids(STARS, size=0) == frozenset()

    def test_size_at_or_above_the_squad_is_the_whole_squad(self):
        ids = {h.player_id for h in STARS}
        assert core_ids(STARS, size=8) == ids
        assert core_ids(STARS, size=15) == ids

    def test_never_more_than_size(self):
        assert len(core_ids(STARS + [PASSENGER, BENCH_BUY], size=5)) <= 5

    def test_clear_cut_producer_in_and_passenger_out(self):
        core = core_ids(STARS + [PASSENGER, BENCH_BUY], size=8)
        assert "star0" in core
        assert "bench" not in core
        assert "can" not in core

    def test_empty_squad(self):
        assert core_ids([], size=8) == frozenset()


class TestDebtCover:
    def test_no_debt_is_never_refused(self):
        assert (
            debt_cover_reason(
                holdings=STARS,
                candidate=BENCH_BUY,
                bid=int(23.6 * M),
                wallet=int(30 * M),
                open_offers=0,
                core_size=15,
            )
            is None
        )

    def test_a_non_core_buy_is_its_own_collateral(self):
        # The buy's own market value (22.3m) plus Can (0.5m) covers 22.8m of debt.
        assert (
            debt_cover_reason(
                holdings=STARS + [PASSENGER],
                candidate=BENCH_BUY,
                bid=int(23.6 * M),
                wallet=int(1.0 * M),
                open_offers=0,
                core_size=8,
            )
            is None
        )

    def test_debt_beyond_the_trading_book_is_refused(self):
        # Wallet -5.7m, a 23.6m bid -> 29.3m owed; only 22.8m is non-core.
        reason = debt_cover_reason(
            holdings=STARS + [PASSENGER],
            candidate=BENCH_BUY,
            bid=int(23.6 * M),
            wallet=int(-5.7 * M),
            open_offers=0,
            core_size=8,
        )
        assert reason is not None and "core book" in reason

    def test_open_offers_count_as_won(self):
        args = {
            "holdings": STARS + [PASSENGER],
            "candidate": BENCH_BUY,
            "bid": int(23.6 * M),
            "wallet": int(1.0 * M),
            "core_size": 8,
        }
        assert debt_cover_reason(open_offers=0, **args) is None
        assert debt_cover_reason(open_offers=int(5 * M), **args) is not None

    def test_core_size_zero_lets_the_whole_squad_cover(self):
        assert (
            debt_cover_reason(
                holdings=STARS,
                candidate=BENCH_BUY,
                bid=int(23.6 * M),
                wallet=int(-50 * M),
                open_offers=0,
                core_size=0,
            )
            is None
        )

    def test_liquid_value_skips_the_core(self):
        assert liquid_value([PASSENGER, BENCH_BUY], frozenset({"bench"})) == PASSENGER.market_value

    def test_check_object_binds_everything_but_the_bid(self):
        check = CoreBookCheck(
            holdings=tuple(STARS + [PASSENGER]),
            candidate=BENCH_BUY,
            wallet=int(-5.7 * M),
            open_offers=0,
            core_size=8,
        )
        assert check.reason(int(1 * M)) is None
        assert check.reason(int(23.6 * M)) is not None


class TestRecoverySellsTheCoreLast:
    def test_the_producer_stays_and_the_trading_book_pays(self):
        raum = DebtCandidate(
            player_id="raum",
            name="Raum",
            position="Defender",
            market_value=int(29.6 * M),
            buy_price=int(40.7 * M),
            expected_points=95.0,
            in_best_eleven=True,
            core=True,
        )
        bench = DebtCandidate(
            player_id="bench",
            name="Bench",
            position="Defender",
            market_value=int(22.3 * M),
            buy_price=int(23.6 * M),
            expected_points=33.5,
            in_best_eleven=False,
        )
        kurucay = DebtCandidate(
            player_id="kurucay",
            name="Kuruçay",
            position="Defender",
            market_value=int(11.7 * M),
            buy_price=int(11.8 * M),
            expected_points=60.0,
            in_best_eleven=True,
        )
        plan = plan_debt_recovery(
            [raum, bench, kurucay],
            shortfall=int(29.3 * M),
            position_counts={"Defender": 6},
        )
        assert plan.covered
        assert {c.player_id for c in plan.sells} == {"bench", "kurucay"}

    def test_the_core_is_still_sold_when_nothing_else_covers(self):
        star = DebtCandidate(
            player_id="star",
            name="Star",
            position="Midfielder",
            market_value=int(30 * M),
            buy_price=int(25 * M),
            expected_points=120.0,
            in_best_eleven=True,
            core=True,
        )
        other = DebtCandidate(
            player_id="other",
            name="Other",
            position="Midfielder",
            market_value=int(5 * M),
            buy_price=int(5 * M),
            expected_points=50.0,
            in_best_eleven=True,
            core=True,
        )
        plan = plan_debt_recovery(
            [star, other], shortfall=int(20 * M), position_counts={"Midfielder": 5}
        )
        assert plan.covered


@pytest.mark.parametrize("size", [0, 3, 8, 11])
def test_core_is_a_subset_of_the_holdings(size):
    squad = STARS + [PASSENGER, BENCH_BUY]
    assert core_ids(squad, size=size) <= {h.player_id for h in squad}
