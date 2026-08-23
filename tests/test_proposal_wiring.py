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
