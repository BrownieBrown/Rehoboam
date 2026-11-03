"""A core buy is bid to win (2026-11-03).

The two books (#150) split the squad into the core — the six best by points
per appearance, held for points — and the trading book. The bid did not know
which book a buy would enter: Kabak (114.9 a match, 3rd in our squad) was bid
+1% as a "marginal" EP gain and went for +16.7%; every strong upgrade at 15m+
was capped at the band's break-even (0.0%), a resale rule, where the winners
pay a median +8%. A core buy is held, so resale is not its cost — losing the
auction is. It bids the band's winners' p75, skips the break-even cap, and is
held to the must-have ceiling instead of the 15m+ band cap — in the bidder
and in the gate, from the one policy.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from rehoboam.bidding_strategy import SmartBidding
from rehoboam.services.bid_ceiling import BidCeilingPolicy, Tier, max_allowed_bid
from rehoboam.services.books import Holding, core_buy
from rehoboam.services.ceiling_derivation import WinnerRow
from rehoboam.services.profit_curve import OutcomeRow, ProfitCurve
from rehoboam.services.safety_gate import check_buy
from rehoboam.services.win_curve import WinCurve

TIER_PCTS = {Tier.MARGINAL: 10.0, Tier.SOLID: 15.0, Tier.STRONG: 25.0, Tier.MUST_HAVE: 35.0}
POLICY = BidCeilingPolicy(floor_eur=200_000, tier_pcts=TIER_PCTS, price_bands=((15_000_000, 8.0),))
BANDS = (0, 5_000_000, 15_000_000)


def _squad():
    """Our squad on 2026-11-03, by points per appearance."""
    return [
        Holding("romulo", 14_400_000, 60.0, 122.3),
        Holding("tietz", 22_900_000, 88.5, 119.3),
        Holding("coufal", 22_400_000, 33.5, 106.3),
        Holding("regeer", 14_000_000, 77.9, 106.0),
        Holding("urbig", 7_900_000, 68.7, 102.3),
        Holding("quansah", 19_000_000, 119.1, 96.5),
        Holding("sander", 8_900_000, 91.2, 57.5),
        Holding("marino", 7_900_000, 56.9, 54.6),
    ]


KABAK = Holding("kabak", 20_400_000, 98.8, 114.9)
BURGER = Holding("burger", 16_400_000, 118.2, 92.4)


class TestWhoIsACoreBuy:
    @pytest.mark.parametrize(
        ("candidate", "apps", "expected"),
        [
            (KABAK, 9, True),  # 3rd by average: enters the core
            (KABAK, 4, False),  # too few appearances to trust the average
            (KABAK, None, False),  # unknown appearances: not core
            (BURGER, 9, False),  # 7th: trading book
            (Holding("new", 5_000_000, 90.0, None), 9, False),  # no average: not core
        ],
    )
    def test_core_membership(self, candidate, apps, expected):
        assert (
            core_buy(candidate, _squad(), size=6, appearances=apps, min_appearances=5) is expected
        )

    def test_a_size_of_zero_has_no_core(self):
        assert not core_buy(KABAK, _squad(), size=0, appearances=9, min_appearances=5)


class TestTheCeiling:
    def test_a_core_buy_skips_the_price_band_and_takes_the_must_have_pct(self):
        cap = max_allowed_bid(
            market_value=20_000_000,
            tier=Tier.MARGINAL,
            floor_eur=200_000,
            tier_pcts=TIER_PCTS,
            price_bands=((15_000_000, 8.0),),
            core=True,
        )
        assert cap == 27_000_000

    def test_any_other_buy_keeps_the_band(self):
        assert POLICY.max_bid(20_000_000, Tier.MUST_HAVE) == 21_600_000

    def test_the_policy_passes_core_through(self):
        assert POLICY.max_bid(20_000_000, Tier.MARGINAL, core=True) == 27_000_000


class TestTheGate:
    def _check(self, *, core_buy_: bool):
        return check_buy(
            player_id="kabak",
            bid=23_500_000,  # +17.5%
            market_value=20_000_000,
            current_budget=100_000_000,
            free_slots=2,
            known_player_ids=["kabak"],
            tier=Tier.MARGINAL,
            ceiling_policy=POLICY,
            core_buy=core_buy_,
        )

    def test_the_gate_lets_a_core_bid_past_the_band(self):
        assert self._check(core_buy_=True).ok

    def test_the_gate_still_refuses_the_same_bid_on_a_trading_buy(self):
        verdict = self._check(core_buy_=False)
        assert not verdict.ok
        assert "ceiling" in "; ".join(verdict.reasons)


def _win_curve():
    rows = [WinnerRow(20_000_000, 4.0 + i * (11 / 39)) for i in range(40)]  # p50 9.5, p75 12.25
    return WinCurve.from_rows(rows, bands=BANDS, min_sample=30)


def _profit_curve():
    rows = []
    for _ in range(12):
        rows += [OutcomeRow(20_000_000, 1.0, 0.5), OutcomeRow(20_000_000, 4.0, -5.0)]
        rows += [OutcomeRow(20_000_000, 10.0, -9.0), OutcomeRow(20_000_000, 13.0, -12.0)]
    return ProfitCurve.from_rows(rows, bands=BANDS)


def _bid(*, gain: float, core: bool, quantile: float = 0.75):
    bidder = SmartBidding(
        ceiling_policy=POLICY,
        win_curve=_win_curve(),
        profit_curve=_profit_curve(),
        core_bid_quantile=quantile,
    )
    return bidder.calculate_ep_bid(
        asking_price=20_000_000,
        market_value=20_000_000,
        expected_points=gain,
        marginal_ep_gain=gain,
        confidence=0.9,
        current_budget=500_000_000,
        player_id="kabak",
        trend_change_pct=0.0,
        offer_count=0,
        data_grade="A",
        core_buy=core,
    )


class TestTheBid:
    def test_a_marginal_core_buy_bids_the_winners_p75(self):
        rec = _bid(gain=24.0, core=True)
        assert rec.overbid_pct == pytest.approx(12.25, abs=0.2)
        assert "core buy" in rec.reasoning

    def test_a_core_buy_is_not_capped_at_break_even(self):
        rec = _bid(gain=50.0, core=True)
        assert rec.overbid_pct == pytest.approx(12.25, abs=0.2)
        assert "capped at break-even" not in rec.reasoning

    def test_the_same_strong_upgrade_in_the_trading_book_is_still_capped(self):
        rec = _bid(gain=50.0, core=False)
        assert rec.overbid_pct == pytest.approx(3.0, abs=0.2)

    def test_the_same_marginal_buy_in_the_trading_book_bids_the_floor(self):
        rec = _bid(gain=24.0, core=False)
        assert rec.overbid_pct < 5.0

    def test_the_core_quantile_never_lowers_a_must_have(self):
        rec = _bid(gain=80.0, core=True, quantile=0.5)
        assert rec.overbid_pct == pytest.approx(12.25, abs=0.2)


class TestTheTraderMarksCoreBuys:
    def test_the_gate_builder_reads_core_buy_ids(self):
        from rehoboam.auto_trader import _build_buy_gate

        settings = SimpleNamespace(
            bid_tier_must_have=70.0,
            bid_tier_strong_upgrade=53.0,
            bid_tier_solid_upgrade=43.0,
            bid_ceiling_policy=lambda: POLICY,
            max_single_buy_pct_of_worth=None,
            core_book_enabled=False,
        )
        ctx = SimpleNamespace(
            squad=[],
            my_bids=[],
            ep_result={"market_players": {"kabak": None}, "core_buy_ids": frozenset({"kabak"})},
            current_budget=100_000_000,
        )
        player = SimpleNamespace(id="kabak", market_value=20_000_000, team_id="c")
        gate = _build_buy_gate(
            settings=settings,
            ctx=ctx,
            player=player,
            spendable_budget=100_000_000,
            free_slots=2,
            marginal_ep_gain=24.0,
        )
        assert gate.core_buy is True
        flip_gate = _build_buy_gate(
            settings=settings,
            ctx=ctx,
            player=player,
            spendable_budget=100_000_000,
            free_slots=2,
            marginal_ep_gain=None,  # a flip is never a core buy
        )
        assert flip_gate.core_buy is False


class TestTheTraderDecidesOnce:
    def _trader(self, **settings):
        from unittest.mock import MagicMock

        from rehoboam.trader import Trader

        trader = Trader.__new__(Trader)
        trader.settings = SimpleNamespace(
            core_bid_enabled=True, core_book_size=6, core_bid_min_appearances=5, **settings
        )
        return trader, MagicMock

    @staticmethod
    def _player(pid, avg, mv=10_000_000):
        return SimpleNamespace(id=pid, average_points=avg, market_value=mv)

    def _squad(self):
        return [self._player(h.player_id, h.avg_points, h.market_value) for h in _squad()]

    def test_kabak_is_marked_and_burger_is_not(self):
        from unittest.mock import patch

        trader, _ = self._trader()
        ranks = {"kabak": {"appearances": 9}, "burger": {"appearances": 9}}
        with patch("rehoboam.store.league_store.LeagueStore") as store:
            store.return_value.position_ranks.return_value = ranks
            ids = trader._core_buy_ids(
                self._squad(),
                [],
                [(self._player("kabak", 114.9), None), (self._player("burger", 92.4), None)],
            )
        assert ids == frozenset({"kabak"})

    def test_a_pair_judges_the_squad_without_its_sale(self):
        from unittest.mock import patch

        trader, _ = self._trader()
        # Selling Rômulo (1st) for a 100.0 player: without the sale he would be
        # 7th; with it he is 6th — the core is judged on the squad after the pair.
        ranks = {"x": {"appearances": 9}}
        squad = self._squad() + [self._player("extra", 101.0)]
        with patch("rehoboam.store.league_store.LeagueStore") as store:
            store.return_value.position_ranks.return_value = ranks
            alone = trader._core_buy_ids(squad, [], [(self._player("x", 100.0), None)])
            paired = trader._core_buy_ids(squad, [], [(self._player("x", 100.0), "romulo")])
        assert alone == frozenset()
        assert paired == frozenset({"x"})

    def test_a_failing_store_marks_nobody(self):
        from unittest.mock import patch

        trader, _ = self._trader()
        with patch("rehoboam.store.league_store.LeagueStore", side_effect=RuntimeError("down")):
            ids = trader._core_buy_ids(self._squad(), [], [(self._player("kabak", 114.9), None)])
        assert ids == frozenset()

    def test_off_marks_nobody(self):
        trader, _ = self._trader()
        trader.settings.core_bid_enabled = False
        assert trader._core_buy_ids(self._squad(), [], [(self._player("kabak", 114.9), None)]) == (
            frozenset()
        )
