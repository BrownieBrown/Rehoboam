"""The emergency fill spends, in every phase, behind the safety gate (spec §1).

REH-114 turned the fill into a proposal with a 24h auto-approve. Checked on a
12h timer, the deadline fired 24-36h later — El-Faouzi's on 2026-09-02 08:00,
to "no longer on the market". An empty lineup slot is -100 every matchday and
a proposal nobody taps protects nothing, so the fill buys, and `BuyGate` is
the only thing between the pick and the money.

These drive the real `ExecutionService` against a mock API: what they assert
is that `api.buy_player` is called, not that a stub recorded an argument.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from rehoboam.auto_trader import AutoTrader
from rehoboam.bid_learner import BidLearner
from rehoboam.config import Settings
from rehoboam.services.execution import ExecutionService

LEAGUE = SimpleNamespace(id="1933872", name="PUMARUDEL")


@pytest.fixture
def api():
    api = MagicMock()
    api.buy_player = MagicMock(return_value=None)
    return api


@pytest.fixture
def trader(api, tmp_path, monkeypatch):
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "test")
    monkeypatch.chdir(tmp_path)
    t = AutoTrader(api=api, settings=Settings(), dry_run=False)
    t.learner = BidLearner(db_path=tmp_path / "bid_learning.db")
    t.execution = ExecutionService(api=api, tracker=MagicMock(), dry_run=False)
    return t


def _player(pid, position="Forward", price=1_000_000, team_id="1"):
    return SimpleNamespace(
        id=pid,
        first_name="X",
        last_name=f"P{pid}",
        position=position,
        price=price,
        market_value=price,
        average_points=10.0,
        status=0,
        team_id=team_id,
    )


def _rec(player, bid, ep_gain=10.0):
    return SimpleNamespace(
        player=player, recommended_bid=bid, marginal_ep_gain=ep_gain, sell_plan=None
    )


def _ctx(
    buy_recs,
    current_budget,
    *,
    squad=(),
    phase="moderate",
    days_until_match=None,
    my_bid_amounts=None,
):
    return SimpleNamespace(
        ep_result={
            "buy_recs": list(buy_recs),
            "trade_pairs": [],
            "squad_scores": [],
            "market_players": {r.player.id: r.player for r in buy_recs},
        },
        my_bid_amounts=dict(my_bid_amounts) if my_bid_amounts else {},
        my_bids=[],
        squad=list(squad),
        current_budget=current_budget,
        team_value=100_000_000,
        flip_budget=current_budget,
        executed_trade_count=0,
        matchday_phase=SimpleNamespace(days_until_match=days_until_match, phase=phase),
    )


def _short_squad():
    """Ten defenders from ten clubs: one slot short, no club-limit noise."""
    return [_player(f"d{i}", "Defender", team_id=f"club{i}") for i in range(10)]


class TestTheFillBuys:
    def test_a_pick_reaches_the_api_at_the_basket_price(self, trader, api):
        target = _player("f1", price=4_000_000)
        squad = _short_squad()

        results = trader._run_emergency_squad_fill(
            league=LEAGUE,
            ctx=_ctx([_rec(target, 4_000_000)], 50_000_000, squad=squad),
            fresh_squad=squad,
            slots_short=1,
        )

        assert api.buy_player.call_count == 1
        assert api.buy_player.call_args[0][1].id == "f1"
        assert api.buy_player.call_args[0][2] == 4_000_000
        assert [r.action for r in results if r.success] == ["BUY"]

    def test_it_never_proposes(self, trader, api, monkeypatch):
        monkeypatch.setattr(
            AutoTrader,
            "_propose_buy",
            lambda *a, **k: pytest.fail("the emergency fill must spend, not ask"),
        )
        target = _player("f1", price=4_000_000)
        squad = _short_squad()

        trader._run_emergency_squad_fill(
            league=LEAGUE,
            ctx=_ctx([_rec(target, 4_000_000)], 50_000_000, squad=squad),
            fresh_squad=squad,
            slots_short=1,
        )

        assert api.buy_player.call_count == 1

    def test_it_buys_in_the_locked_phase(self, trader, api):
        """Locked blocks trading, not the fill: -100 per slot outranks it."""
        target = _player("f1", price=4_000_000)
        squad = _short_squad()
        ctx = _ctx(
            [_rec(target, 4_000_000)],
            50_000_000,
            squad=squad,
            phase="locked",
            days_until_match=1,
        )

        trader._run_emergency_squad_fill(league=LEAGUE, ctx=ctx, fresh_squad=squad, slots_short=1)

        assert api.buy_player.call_count == 1

    def test_the_spend_is_counted_against_the_daily_limit(self, trader, api):
        target = _player("f1", price=4_000_000)
        squad = _short_squad()
        before = trader.daily_spend

        trader._run_emergency_squad_fill(
            league=LEAGUE,
            ctx=_ctx([_rec(target, 4_000_000)], 50_000_000, squad=squad),
            fresh_squad=squad,
            slots_short=1,
        )

        assert trader.daily_spend == before + 4_000_000

    def test_the_second_pick_sees_what_the_first_one_spent(self, trader, api):
        """EUR 60m of picks against EUR 50m: one buy, never a negative budget."""
        a = _player("a", price=30_000_000)
        b = _player("b", price=30_000_000)
        squad = _short_squad()[:9]
        ctx = _ctx(
            [_rec(a, 30_000_000, ep_gain=20.0), _rec(b, 30_000_000, ep_gain=15.0)],
            50_000_000,
            squad=squad,
        )

        trader._run_emergency_squad_fill(league=LEAGUE, ctx=ctx, fresh_squad=squad, slots_short=2)

        assert api.buy_player.call_count == 1

    def test_money_committed_to_open_offers_is_not_spent_again(self, trader, api):
        """Wallet reads 50m, but 40m of it is already promised to an open

        offer. Spending the full 50m on the fill risks a negative budget at
        kickoff — zero points for the whole matchday, worse than the -100 an
        unfilled slot costs.
        """
        target = _player("f1", price=30_000_000)
        squad = _short_squad()
        ctx = _ctx(
            [_rec(target, 30_000_000)],
            50_000_000,
            squad=squad,
            my_bid_amounts={"open": 40_000_000},
        )

        trader._run_emergency_squad_fill(league=LEAGUE, ctx=ctx, fresh_squad=squad, slots_short=1)

        assert api.buy_player.call_count == 0

    def test_a_pick_that_fits_beside_the_open_offer_is_still_bought(self, trader, api):
        target = _player("f1", price=8_000_000)
        squad = _short_squad()
        ctx = _ctx(
            [_rec(target, 8_000_000)],
            50_000_000,
            squad=squad,
            my_bid_amounts={"open": 40_000_000},
        )

        trader._run_emergency_squad_fill(league=LEAGUE, ctx=ctx, fresh_squad=squad, slots_short=1)

        assert api.buy_player.call_count == 1


class TestTheAutoApproveMachineryIsGone:
    def test_no_deadline_setting(self, monkeypatch):
        monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
        monkeypatch.setenv("KICKBASE_PASSWORD", "test")

        assert not hasattr(Settings(), "emergency_auto_approve_hours")

    def test_no_due_auto_approvals_reader(self, tmp_path):
        assert not hasattr(BidLearner(db_path=tmp_path / "b.db"), "due_auto_approvals")

    def test_the_session_has_no_auto_approval_step(self):
        assert not hasattr(AutoTrader, "_process_due_auto_approvals")

    def test_propose_buy_takes_no_deadline(self):
        params = inspect.signature(AutoTrader._propose_buy).parameters

        assert "auto_approve_at" not in params
