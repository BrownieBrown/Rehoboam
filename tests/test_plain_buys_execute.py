"""Plain squad-improvement buys execute behind the safety gate (spec §1).

From 2026-08-24 to PR 1 these buys became Telegram proposals: 13 approvals,
2 acquisitions, the rest poached while the message waited. The session now
places the offer itself through `ExecutionService.buy` with a `BuyGate`, and
writes the case to `trade_proposals` AFTER the fact as executed / refused /
failed, so the board and the daily summary report what happened rather than
what was asked.

These drive the real `ExecutionService` and the real `LearningTracker`
against a mock API: what they assert is that `api.buy_player` is called (or
not), not that a stub recorded an argument.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from rehoboam.auto_trader import AutoTrader, EPSessionContext, MatchdayPhase
from rehoboam.bid_learner import BidLearner
from rehoboam.config import Settings
from rehoboam.learning.tracker import LearningTracker
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
    t.tracker = LearningTracker(t.learner)
    t.execution = ExecutionService(api=api, tracker=t.tracker, dry_run=False)
    return t


@pytest.fixture(autouse=True)
def _trend(request):
    """A gentle uptrend unless a test asks (via indirect param) for another."""
    pct = getattr(request, "param", 1.9)
    with patch(
        "rehoboam.services.trend_service.TrendService.get_trend",
        return_value=SimpleNamespace(trend_7d_pct=pct),
    ):
        yield


def _player(pid="6080", price=32_285_629, team_id="2"):
    return SimpleNamespace(
        id=pid,
        first_name="Aleksandar",
        last_name="Pavlović",
        position="Midfielder",
        price=price,
        market_value=price,
        team_id=team_id,
        team_name="Bayern",
        average_points=119.0,
        status=0,
    )


def _rec(player=None, bid=32_608_485, ep_gain=57.2, sell_plan=None):
    player = player or _player()
    return SimpleNamespace(
        player=player,
        score=SimpleNamespace(expected_points=82.6, data_quality=None, position="Midfielder"),
        marginal_ep_gain=ep_gain,
        recommended_bid=bid,
        replaces_player_name="Klaas",
        replaces_player_ep=25.5,
        roster_impact="upgrade",
        reason="upgrade",
        sell_plan=sell_plan,
    )


def _ctx(rec, budget=95_317_114, *, squad_size=13, phase="aggressive", days=5):
    squad = [
        SimpleNamespace(id=f"s{i}", team_id=f"club{i}", position="Defender")
        for i in range(squad_size)
    ]
    return EPSessionContext(
        ep_result={
            "buy_recs": [rec],
            "trade_pairs": [],
            "squad_scores": [],
            "market_players": {rec.player.id: rec.player},
        },
        matchday_phase=MatchdayPhase(
            days_until_match=days,
            phase=phase,
            max_trades=5,
            allow_flips=False,
            reason="test",
        ),
        my_bids=[],
        my_bid_amounts={},
        squad=squad,
        current_budget=budget,
        team_value=200_000_000,
        flip_budget=budget,
    )


class TestAnUpgradeBecomesAnOffer:
    def test_the_offer_reaches_the_api_at_the_recommended_bid(self, trader, api):
        rec = _rec()

        result = trader._execute_buy(LEAGUE, rec, _ctx(rec), free_slots=2)

        assert result.success
        assert api.buy_player.call_count == 1
        assert api.buy_player.call_args[0][1].id == "6080"
        assert api.buy_player.call_args[0][2] == 32_608_485

    def test_the_case_is_recorded_as_executed_after_the_fact(self, trader):
        rec = _rec()

        trader._execute_buy(LEAGUE, rec, _ctx(rec), free_slots=2)

        rows = trader.learner.proposals_since(0)
        assert [r["status"] for r in rows] == ["executed"]
        assert "Klaas" in rows[0]["message"] and "57.2" in rows[0]["message"]
        assert trader.learner.pending_proposals() == []

    def test_the_bid_is_tracked_as_pending_for_the_ledger(self, trader):
        rec = _rec()

        trader._execute_buy(LEAGUE, rec, _ctx(rec), free_slots=2)

        pending = trader.learner.get_pending_bids()
        assert [b["player_id"] for b in pending] == ["6080"]
        assert pending[0]["tier"] == "strong_upgrade"

    def test_the_session_counts_it(self, trader):
        rec = _rec()
        ctx = _ctx(rec)

        trader._execute_buy(LEAGUE, rec, ctx, free_slots=2)

        assert (ctx.offers_placed, ctx.offers_refused) == (1, 0)

    def test_the_board_carries_the_line(self, trader):
        rec = _rec()

        trader._execute_buy(LEAGUE, rec, _ctx(rec), free_slots=2)

        assert [line.outcome for line in trader._session_board] == ["placed"]
        assert trader._session_board[0].name == "Aleksandar Pavlović"


class TestTheGateStillDecides:
    def test_an_over_ceiling_bid_is_refused_and_recorded(self, trader, api):
        """+40% over market value against the strong tier's 25% ceiling."""
        rec = _rec(bid=45_200_000)
        ctx = _ctx(rec)

        result = trader._execute_buy(LEAGUE, rec, ctx, free_slots=2)

        assert not result.success
        assert api.buy_player.call_count == 0
        rows = trader.learner.proposals_since(0)
        assert [r["status"] for r in rows] == ["refused"]
        assert "REFUSED" in rows[0]["message"] and "overbid" in rows[0]["message"]
        assert (ctx.offers_placed, ctx.offers_refused) == (0, 1)
        assert trader._session_board[0].outcome == "refused"
        assert "overbid" in trader._session_board[0].detail

    def test_no_free_slot_is_refused(self, trader, api):
        rec = _rec()

        result = trader._execute_buy(LEAGUE, rec, _ctx(rec, squad_size=15), free_slots=0)

        assert not result.success
        assert api.buy_player.call_count == 0
        assert "no free squad slot" in trader._session_board[0].detail

    def test_a_kickbase_error_is_recorded_as_failed(self, trader, api):
        api.buy_player.side_effect = Exception("Failed to make offer: 500 - UnderpayNotAllowed")
        rec = _rec()
        ctx = _ctx(rec)

        result = trader._execute_buy(LEAGUE, rec, ctx, free_slots=2)

        assert not result.success
        assert [r["status"] for r in trader.learner.proposals_since(0)] == ["failed"]
        assert "UnderpayNotAllowed" in trader.learner.proposals_since(0)[0]["message"]
        assert (ctx.offers_placed, ctx.offers_refused) == (0, 1)
        assert trader._session_board[0].outcome == "failed"


class TestTheTrendFloorComesFirst:
    @pytest.mark.parametrize("_trend", [-27.0], indirect=True)
    def test_a_steeply_falling_player_is_never_attempted(self, trader, api):
        rec = _rec()

        assert trader._execute_buy(LEAGUE, rec, _ctx(rec), free_slots=2) is None
        assert api.buy_player.call_count == 0
        assert trader.learner.proposals_since(0) == []
        assert trader._session_board == []


class TestASellPlanRidesOnTheBid:
    def test_the_plan_is_persisted_with_the_pending_bid(self, trader, api):
        """Buy first, sell after: `resolve_auctions` runs the sells if we win."""
        plan = SimpleNamespace(
            players_to_sell=[
                SimpleNamespace(player_id="s1"),
                SimpleNamespace(player_id="s2"),
            ]
        )
        rec = _rec(sell_plan=plan)

        trader._execute_buy(LEAGUE, rec, _ctx(rec), free_slots=2)

        assert api.buy_player.call_count == 1
        assert trader.learner.get_pending_bids()[0]["sell_plan_player_ids"] == [
            "s1",
            "s2",
        ]


class TestDryRunSpendsAndRecordsNothing:
    def test_it_calls_no_api_writes_no_row_sends_nothing_but_shows_the_board(
        self, api, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
        monkeypatch.setenv("KICKBASE_PASSWORD", "test")
        monkeypatch.chdir(tmp_path)
        dry = AutoTrader(api=api, settings=Settings(), dry_run=True)
        dry.learner = BidLearner(db_path=tmp_path / "bid_learning.db")
        rec = _rec()
        ctx = _ctx(rec)

        with patch("rehoboam.notify.telegram.send_message") as send:
            result = dry._execute_buy(LEAGUE, rec, ctx, free_slots=2)
            dry._send_session_board(LEAGUE, ctx)
            send.assert_not_called()

        assert result.success
        assert api.buy_player.call_count == 0
        assert dry.learner.proposals_since(0) == []
        assert len(dry._session_board) == 1


class TestTheBoardIsSentOnce:
    def test_it_is_sent_when_an_offer_was_placed(self, trader):
        rec = _rec()
        ctx = _ctx(rec)
        trader._execute_buy(LEAGUE, rec, ctx, free_slots=2)

        with patch("rehoboam.notify.telegram.send_message", return_value=True) as send:
            trader._send_session_board(LEAGUE, ctx)

        send.assert_called_once()
        text = send.call_args[0][2]
        assert "OFFERS PLACED — 1" in text and "Pavlović" in text
        assert "Approve" not in text

    def test_it_is_sent_when_only_refusals_happened(self, trader):
        rec = _rec(bid=45_200_000)
        ctx = _ctx(rec)
        trader._execute_buy(LEAGUE, rec, ctx, free_slots=2)

        with patch("rehoboam.notify.telegram.send_message", return_value=True) as send:
            trader._send_session_board(LEAGUE, ctx)

        assert "REFUSED — 1" in send.call_args[0][2]

    def test_it_is_not_sent_when_nothing_was_attempted(self, trader):
        with patch("rehoboam.notify.telegram.send_message") as send:
            trader._send_session_board(LEAGUE, _ctx(_rec()))

        send.assert_not_called()

    def test_the_header_uses_the_sessions_opening_budget_not_a_derivation(self, trader):
        """`budget_after + sum(placed)` misses trade pairs and flips, which
        also move `ctx.current_budget` without ever appearing in `placed`."""
        trader._session_budget_before = 95_317_114
        rec = _rec()
        ctx = _ctx(rec)

        trader._execute_buy(LEAGUE, rec, ctx, free_slots=2)
        # `_execute_buy` itself never touches `ctx.current_budget` — that's
        # `run_unified_trade_phase`'s job on success — so stand in for it here,
        # then simulate a trade pair/flip elsewhere in the same session that
        # also spent money but never appears in `placed`.
        ctx.current_budget -= rec.recommended_bid
        ctx.current_budget -= 5_000_000

        with patch("rehoboam.notify.telegram.send_message", return_value=True) as send:
            trader._send_session_board(LEAGUE, ctx)

        text = send.call_args[0][2]
        assert "BUDGET EUR 95,317,114 -> EUR 57,708,629 (if every offer lands)" in text


class TestTheUnifiedPhaseBuysInstead:
    def test_a_plain_buy_places_an_offer_and_takes_the_slot(self, trader, api):
        rec = _rec()
        ctx = _ctx(rec, squad_size=14)
        trader.api.get_squad.return_value = ctx.squad
        trader.api.get_my_bids.return_value = []
        trader.api.get_team_info.return_value = {
            "budget": ctx.current_budget,
            "team_value": ctx.team_value,
        }

        results = trader.run_unified_trade_phase(league=LEAGUE, ctx=ctx)

        assert api.buy_player.call_count == 1
        assert [r.action for r in results if r.success] == ["BUY"]
        assert ctx.executed_trade_count == 1
        assert ctx.offers_placed == 1
        assert ctx.current_budget == 95_317_114 - 32_608_485
        trader.api.sell_player_instant.assert_not_called()

    def test_the_club_limit_counts_offers_placed_this_session(self, trader, api):
        """Two more from a club already holding 2 would make 4 — illegal the
        moment the second offer is placed, not when the auction resolves."""
        squad = [
            SimpleNamespace(id="h1", team_id="7", position="Defender"),
            SimpleNamespace(id="h2", team_id="7", position="Defender"),
        ] + [
            SimpleNamespace(id=f"s{i}", team_id=f"club{i}", position="Defender") for i in range(11)
        ]
        rec_a = _rec(
            player=_player(pid="a1", price=10_000_000, team_id="7"),
            bid=10_500_000,
            ep_gain=57.2,  # strong_upgrade tier
        )
        rec_b = _rec(
            player=_player(pid="a2", price=10_000_000, team_id="7"),
            bid=10_500_000,
            ep_gain=50.0,  # solid_upgrade tier
        )
        ctx = EPSessionContext(
            ep_result={
                "buy_recs": [rec_a, rec_b],
                "trade_pairs": [],
                "squad_scores": [],
                "market_players": {
                    rec_a.player.id: rec_a.player,
                    rec_b.player.id: rec_b.player,
                },
            },
            matchday_phase=MatchdayPhase(
                days_until_match=5,
                phase="aggressive",
                max_trades=5,
                allow_flips=False,
                reason="test",
            ),
            my_bids=[],
            my_bid_amounts={},
            squad=squad,
            current_budget=95_317_114,
            team_value=200_000_000,
            flip_budget=95_317_114,
        )
        trader.api.get_squad.return_value = squad
        trader.api.get_my_bids.return_value = []
        trader.api.get_team_info.return_value = {
            "budget": ctx.current_budget,
            "team_value": ctx.team_value,
        }

        trader.run_unified_trade_phase(league=LEAGUE, ctx=ctx)

        assert api.buy_player.call_count == 1
        bought_ids = {call.args[1].id for call in api.buy_player.call_args_list}
        assert rec_b.player.id not in bought_ids
        assert ctx.offers_refused == 1
        refused_line = next(line for line in trader._session_board if line.outcome != "placed")
        assert "club limit" in refused_line.detail


class TestTheProposalMachineryIsGone:
    def test_nothing_proposes_any_more(self):
        for name in (
            "_propose_buy",
            "_has_pending_proposal",
            "_needs_sell_plan",
            "_send_proposal_overview",
        ):
            assert not hasattr(AutoTrader, name), name

    def test_execute_buy_takes_free_slots_and_no_deadline(self):
        params = inspect.signature(AutoTrader._execute_buy).parameters

        assert "free_slots" in params
        assert "auto_approve_at" not in params
