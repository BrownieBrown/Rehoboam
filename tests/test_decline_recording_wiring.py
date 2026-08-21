"""REH-86: the decline recorder must actually FIRE, not merely exist.

This ticket's whole subject is instrumentation that was wired but never
populated — `auction_outcomes.winning_bid` is NULL on all 26 production rows
because nothing ever filled it. Shipping a decline writer without proving the
call site reaches it would repeat exactly that.

A live smoke test cannot prove it right now: the pre-season market has no
listings, so `buy_recs` is empty and the branch never runs. This exercises the
branch directly instead.
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from rehoboam.auto_trader import AutoTrader, EPSessionContext, MatchdayPhase
from rehoboam.bid_learner import BidLearner


class _Api:
    def get_squad(self, league):
        return []

    def get_my_bids(self, league):
        return []

    def get_team_info(self, league):
        return {"budget": 90_000_000, "teamValue": 100_000_000}

    def buy_player(self, *a, **k):
        return {"ok": True}


def _rec(pid: str, bid, gain: float, reason: str):
    """A BuyRecommendation-shaped object; only the fields the loop reads."""
    player = SimpleNamespace(
        id=pid,
        first_name="F",
        last_name=pid,
        position="Midfielder",
        price=5_000_000,
        market_value=4_800_000,
    )
    return SimpleNamespace(
        player=player,
        recommended_bid=bid,
        marginal_ep_gain=gain,
        reason=reason,
        sell_plan=None,
    )


@pytest.fixture
def trader(tmp_path, monkeypatch):
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "x")
    from rehoboam.config import Settings

    t = AutoTrader(api=_Api(), settings=Settings(), dry_run=True)
    t.learner = BidLearner(db_path=tmp_path / "bid_learning.db")
    return t


def _ctx(buy_recs):
    return EPSessionContext(
        ep_result={"buy_recs": buy_recs, "trade_pairs": [], "sell_recs": []},
        matchday_phase=MatchdayPhase(
            days_until_match=5, phase="aggressive", max_trades=5, allow_flips=True, reason="test"
        ),
        my_bids=[],
        my_bid_amounts={},
        squad=[],
        current_budget=90_000_000,
        team_value=100_000_000,
        flip_budget=0,
    )


def _declines(trader):
    with sqlite3.connect(trader.learner.db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM buy_decisions")]


def test_a_candidate_the_bot_does_not_bid_on_is_recorded(trader):
    """The silent drop this ticket exists to make visible."""
    trader.run_unified_trade_phase(None, _ctx([_rec("p1", 0, 12.0, "below_ep_threshold")]))

    rows = _declines(trader)
    assert len(rows) == 1
    assert rows[0]["player_id"] == "p1"
    assert rows[0]["reason"] == "below_ep_threshold"
    assert rows[0]["decision"] == "declined"
    assert rows[0]["marginal_ep_gain"] == pytest.approx(12.0)


def test_a_candidate_the_bot_does_bid_on_is_not_recorded_as_declined(trader):
    trader.run_unified_trade_phase(None, _ctx([_rec("p2", 4_000_000, 80.0, "must_have")]))
    assert [r for r in _declines(trader) if r["player_id"] == "p2"] == []


def test_the_budget_ceiling_is_captured_so_affordability_is_diagnosable(trader):
    """Distinguishing "did not want him" from "could not afford him" is the
    entire point — REH-85 turns on a EUR 21.3m listing bid at 1.0% over asking."""
    trader.run_unified_trade_phase(None, _ctx([_rec("p3", None, 190.0, "unaffordable")]))
    row = _declines(trader)[0]
    assert row["budget_ceiling"] == 90_000_000
    assert row["marginal_ep_gain"] == pytest.approx(190.0)


def test_a_learning_failure_never_blocks_the_trade_phase(trader):
    """Instrumentation must not be able to stop a trade."""
    trader.learner = None
    trader.run_unified_trade_phase(None, _ctx([_rec("p4", 0, 1.0, "x")]))  # must not raise
