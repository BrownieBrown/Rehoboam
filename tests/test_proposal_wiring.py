"""Improvement buys become proposals, not purchases.

The whole point of the approval gate: `api.buy_player` must not be reached for
a squad-improving buy without Marco tapping approve.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from rehoboam.auto_trader import AutoTrader
from rehoboam.config import Settings


@pytest.fixture
def trader(tmp_path, monkeypatch):
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "test")
    monkeypatch.chdir(tmp_path)
    return AutoTrader(api=MagicMock(), settings=Settings(), dry_run=False)


@pytest.fixture(autouse=True)
def _stub_trend():
    with patch(
        "rehoboam.services.trend_service.TrendService.get_trend",
        return_value=SimpleNamespace(trend_7d_pct=1.9),
    ):
        yield


def _rec():
    return SimpleNamespace(
        player=SimpleNamespace(
            id="6080",
            first_name="Aleksandar",
            last_name="Pavlović",
            market_value=32_285_629,
            team_name="Bayern",
        ),
        score=SimpleNamespace(expected_points=82.6),
        marginal_ep_gain=57.2,
        recommended_bid=32_608_485,
        replaces_player_name="Klaas",
        replaces_player_ep=25.5,
        reason="upgrade",
    )


def _ctx():
    return SimpleNamespace(current_budget=95_317_114, ep_result={})


class TestProposalReplacesPurchase:
    def test_it_records_a_proposal(self, trader):
        with patch("rehoboam.notify.telegram.send_proposal", return_value=True):
            assert trader._propose_buy(SimpleNamespace(id="L"), _rec(), _ctx()) is True
        assert len(trader.learner.pending_proposals()) == 1

    def test_it_does_not_buy(self, trader):
        with patch("rehoboam.notify.telegram.send_proposal", return_value=True):
            trader._propose_buy(SimpleNamespace(id="L"), _rec(), _ctx())
        trader.api.buy_player.assert_not_called()

    def test_the_stored_message_carries_the_reasoning(self, trader):
        with patch("rehoboam.notify.telegram.send_proposal", return_value=True):
            trader._propose_buy(SimpleNamespace(id="L"), _rec(), _ctx())
        msg = trader.learner.pending_proposals()[0]["message"]
        assert "Klaas" in msg and "57.2" in msg


class TestTelegramFailureIsSurvivable:
    def test_a_failed_send_still_records_the_proposal(self, trader):
        """So it appears in the daily email even if Telegram was down."""
        with patch("rehoboam.notify.telegram.send_proposal", return_value=False):
            assert trader._propose_buy(SimpleNamespace(id="L"), _rec(), _ctx()) is True
        assert len(trader.learner.pending_proposals()) == 1


class TestNoDuplicateProposals:
    def test_a_player_with_a_pending_proposal_is_recognised(self, trader):
        with patch("rehoboam.notify.telegram.send_proposal", return_value=True):
            trader._propose_buy(SimpleNamespace(id="L"), _rec(), _ctx())
        assert trader._has_pending_proposal("6080") is True

    def test_an_unproposed_player_is_not(self, trader):
        assert trader._has_pending_proposal("9999") is False


class TestSellPlanDependentBuys:
    def test_a_buy_with_a_sell_plan_is_flagged(self):
        obj = SimpleNamespace(
            sell_plan=SimpleNamespace(players_to_sell=[SimpleNamespace(player_id="1")])
        )
        assert AutoTrader._needs_sell_plan(obj) is True

    def test_a_plain_buy_is_not(self):
        assert AutoTrader._needs_sell_plan(SimpleNamespace(sell_plan=None)) is False


class TestDryRun:
    def test_a_dry_run_records_nothing_and_sends_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
        monkeypatch.setenv("KICKBASE_PASSWORD", "test")
        monkeypatch.chdir(tmp_path)
        dry = AutoTrader(api=MagicMock(), settings=Settings(), dry_run=True)
        with patch("rehoboam.notify.telegram.send_proposal") as send:
            assert dry._propose_buy(SimpleNamespace(id="L"), _rec(), _ctx()) is True
            send.assert_not_called()
        assert dry.learner.pending_proposals() == []


class TestStaleProposalsStopBlocking:
    def test_a_proposal_older_than_the_window_stops_blocking(self, trader):
        """Expiry is not implemented, so an unbounded guard blocks forever."""
        import sqlite3
        import time as _time

        with patch("rehoboam.notify.telegram.send_proposal", return_value=True):
            trader._propose_buy(SimpleNamespace(id="L"), _rec(), _ctx())
        assert trader._has_pending_proposal("6080") is True

        with sqlite3.connect(trader.learner.db_path) as conn:
            conn.execute(
                "UPDATE trade_proposals SET created_at = ?",
                (_time.time() - 4 * 86400,),
            )
        assert trader._has_pending_proposal("6080") is False


class TestRejectionSuppressesReproposal:
    """Rejecting is an answer, not silence.

    Before this, `rejected` was written and never read: rejecting a player
    removed the only thing suppressing him, so the identical proposal came
    back twelve hours later, forever — the exact daily nagging the approval
    gate exists to end.
    """

    def test_a_rejected_player_is_not_immediately_reproposed(self, trader):
        with patch("rehoboam.notify.telegram.send_proposal", return_value=True):
            trader._propose_buy(SimpleNamespace(id="L"), _rec(), _ctx())
        pid = trader.learner.pending_proposals()[0]["proposal_id"]
        trader.learner.mark_proposal(pid, "rejected")

        assert trader.learner.pending_proposals() == []
        assert trader._has_pending_proposal("6080") is True

    def test_a_rejection_stops_suppressing_once_it_is_old(self, trader):
        """Not forever: the price and the player's form both move."""
        import sqlite3
        import time as _time

        with patch("rehoboam.notify.telegram.send_proposal", return_value=True):
            trader._propose_buy(SimpleNamespace(id="L"), _rec(), _ctx())
        pid = trader.learner.pending_proposals()[0]["proposal_id"]
        trader.learner.mark_proposal(pid, "rejected")

        with sqlite3.connect(trader.learner.db_path) as conn:
            conn.execute("UPDATE trade_proposals SET created_at = ?", (_time.time() - 20 * 86400,))
        assert trader._has_pending_proposal("6080") is False

    def test_an_executed_proposal_does_not_suppress(self, trader):
        """The buy happened; a fresh situation deserves a fresh proposal."""
        with patch("rehoboam.notify.telegram.send_proposal", return_value=True):
            trader._propose_buy(SimpleNamespace(id="L"), _rec(), _ctx())
        pid = trader.learner.pending_proposals()[0]["proposal_id"]
        trader.learner.mark_proposal(pid, "approved")
        trader.learner.set_proposal_status(pid, "executed")

        assert trader._has_pending_proposal("6080") is False


class TestAProposalDoesNotSwitchOnAutonomousPairs:
    """A proposal is not a commitment.

    The pair branch sells a squad player to fund a buy, and skips itself while
    a free slot exists. Because proposing decremented `available_slots`, merely
    PROPOSING a buy was what switched that autonomous selling on — off the back
    of a decision Marco had not made yet.
    """

    def test_proposing_a_buy_does_not_trigger_the_pair_sell(self, trader):
        from rehoboam.auto_trader import EPSessionContext, MatchdayPhase

        buy_rec = SimpleNamespace(
            player=SimpleNamespace(
                id="p1", first_name="T", last_name="Buyer", market_value=1_000_000
            ),
            recommended_bid=1_000_000,
            marginal_ep_gain=10.0,
            reason="upgrade",
            sell_plan=None,
            score=SimpleNamespace(expected_points=50.0),
        )
        pair = SimpleNamespace(
            buy_player=SimpleNamespace(
                id="p2", first_name="T", last_name="PairBuy", market_value=1_000_000
            ),
            sell_player=SimpleNamespace(
                id="s1", first_name="T", last_name="PairSell", market_value=2_000_000
            ),
            recommended_bid=1_000_000,
            marginal_ep_gain=9.0,
            ep_gain=9.0,
            sell_is_starter=False,
            reason="swap",
            sell_plan=None,
            score=SimpleNamespace(expected_points=49.0),
        )
        ctx = EPSessionContext(
            ep_result={"buy_recs": [buy_rec], "trade_pairs": [pair]},
            matchday_phase=MatchdayPhase(
                days_until_match=5,
                phase="aggressive",
                max_trades=5,
                allow_flips=False,
                reason="test",
            ),
            my_bids=[],
            my_bid_amounts={},
            squad=[SimpleNamespace(id=f"s{i}") for i in range(14)],
            current_budget=5_000_000,
            team_value=20_000_000,
            flip_budget=5_000_000,
            executed_trade_count=0,
        )
        # Exactly one free slot: the proposal takes it, and pre-fix that is
        # what let the pair branch through.
        trader.api.get_squad.return_value = [SimpleNamespace(id=f"s{i}") for i in range(14)]
        trader.api.get_my_bids.return_value = []
        trader.api.get_team_info.return_value = {
            "budget": 5_000_000,
            "team_value": 20_000_000,
        }

        with patch("rehoboam.notify.telegram.send_proposal", return_value=True):
            trader.run_unified_trade_phase(league=SimpleNamespace(id="L"), ctx=ctx)

        assert len(trader.learner.pending_proposals()) == 1
        trader.api.sell_player_instant.assert_not_called()
