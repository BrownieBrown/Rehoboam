"""The emergency fill asks before it spends, but not forever (REH-114).

Marco wants to approve squad trades. REH-112/113 made the emergency fill
autonomous, and on the 2026-08-31 board that meant EUR 55,485,928 across four
players with no Approve button — the right amount of money to want a say in.

So emergency picks become proposals. The catch is that an empty lineup slot is
-100 every matchday, and a proposal nobody taps protects nothing. The backstop
is therefore **time-based, not phase-based**: `auto_approve_at` is stamped on
the proposal and the next session executes it once that passes.

Phase would be the natural trigger and is the wrong one. `/myeleven` returning
no upcoming fixture is exactly what produced this situation (REH-112), so
"auto-approve once the phase goes locked" can wait forever on the same broken
lookup it exists to survive.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from rehoboam.auto_trader import AutoTrader
from rehoboam.bid_learner import BidLearner
from rehoboam.config import Settings
from rehoboam.kickbase_client import MarketPlayer, Player

LEAGUE = SimpleNamespace(id="1933872", name="PUMARUDEL")
HOUR = 3600.0


def _player(pid, position="Defender", team_id="1"):
    return Player(
        id=pid,
        first_name="F",
        last_name=f"P{pid}",
        position=position,
        team_id=team_id,
        team_name="T",
        market_value=1_000_000,
        points=0,
        average_points=50.0,
    )


def _market_player(pid, price, position="Forward", team_id="7"):
    return MarketPlayer(
        id=pid,
        first_name="F",
        last_name=f"M{pid}",
        position=position,
        team_id=team_id,
        team_name="",
        price=price,
        market_value=price,
        points=0,
        average_points=60.0,
        status=0,
    )


def _rec(pid, price, ep_gain, position="Forward", team_id="7"):
    player = _market_player(pid, price, position, team_id)
    return SimpleNamespace(
        player=player,
        recommended_bid=price,
        marginal_ep_gain=ep_gain,
        score=SimpleNamespace(expected_points=ep_gain, data_quality=None, position=position),
        replaces_player_name=None,
        replaces_player_ep=0.0,
        sell_plan=None,
    )


def _ctx(buy_recs, budget):
    return SimpleNamespace(
        ep_result={
            "buy_recs": list(buy_recs),
            "trade_pairs": [],
            "squad_scores": [],
            "market_players": {r.player.id: r.player for r in buy_recs},
        },
        my_bid_amounts={},
        my_bids=[],
        squad=[],
        current_budget=budget,
        team_value=100_000_000,
        flip_budget=budget,
        executed_trade_count=0,
        matchday_phase=SimpleNamespace(days_until_match=None, phase="moderate"),
    )


@pytest.fixture
def trader(tmp_path, store_dsn, monkeypatch):
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "test")
    monkeypatch.chdir(tmp_path)
    t = AutoTrader(api=SimpleNamespace(), settings=Settings(), dry_run=False)
    t.learner = BidLearner(dsn=store_dsn)
    return t


class TestTheFillProposesRatherThanBuying:
    def test_an_emergency_pick_is_recorded_as_a_proposal(self, trader):
        # One short: GK 1, DEF 4, MID 4, FW 1 (10 players) -- fieldability
        # purchases == 1, and the Forward candidate below closes it (4-4-2).
        squad = (
            [_player("gk0", "Goalkeeper")]
            + [_player(f"d{i}", "Defender") for i in range(4)]
            + [_player(f"m{i}", "Midfielder") for i in range(4)]
            + [_player("f0", "Forward")]
        )
        recs = [_rec("f1", 5_000_000, 60.0)]

        with patch.object(AutoTrader, "_propose_buy", return_value=True) as propose:
            trader._run_emergency_squad_fill(
                league=LEAGUE, ctx=_ctx(recs, 50_000_000), fresh_squad=squad, slots_short=1
            )

        assert propose.called, "emergency fill must propose, not spend unattended"

    def test_it_does_not_reach_the_execution_service(self, trader):
        squad = [_player(f"d{i}") for i in range(7)]
        recs = [_rec("f1", 5_000_000, 60.0)]
        trader.execution = SimpleNamespace(
            buy=lambda *a, **k: pytest.fail("emergency fill bought without approval")
        )

        with patch.object(AutoTrader, "_propose_buy", return_value=True):
            trader._run_emergency_squad_fill(
                league=LEAGUE, ctx=_ctx(recs, 50_000_000), fresh_squad=squad, slots_short=1
            )


def _ten_short_one_forward(team_ids=("1",) * 9):
    """GK 1, DEF 4, MID 4, FW 1: one short, a forward closes it (4-4-2)."""
    ids = list(team_ids)
    return (
        [_player("gk0", "Goalkeeper", ids[0])]
        + [_player(f"d{i}", "Defender", ids[1 + i]) for i in range(4)]
        + [_player(f"m{i}", "Midfielder", ids[5 + i]) for i in range(4)]
        + [_player("f0", "Forward")]
    )


class TestTheFillIgnoresProfitRules:
    """On 2026-09-15 the fill picked Baack for the empty slot and then refused
    to propose him because his market value was down 40% in 7 days — a rule
    written for profit buys. The slot stayed empty at -100. The same board
    listed Henrichs first, with three Leipzig players already held, so the
    gate would have refused him and the basket's pick was wasted."""

    def test_a_falling_player_is_still_proposed_in_an_emergency(self, trader):
        recs = [_rec("f1", 5_000_000, 60.0)]
        with patch.object(AutoTrader, "_propose_buy", return_value=True) as propose:
            trader._run_emergency_squad_fill(
                league=LEAGUE,
                ctx=_ctx(recs, 50_000_000),
                fresh_squad=_ten_short_one_forward(),
                slots_short=1,
            )
        assert propose.called
        assert propose.call_args.kwargs.get("waive_trend_floor") is True

    def test_propose_buy_waives_the_floor_only_when_asked(self, trader):
        trader.dry_run = True  # returns before the store and Telegram are touched
        rec = _rec("f1", 5_000_000, 60.0)
        ctx = _ctx([rec], 50_000_000)
        falling = MagicMock()
        falling.return_value.trend_service.get_trend.return_value.trend_7d_pct = -40.3
        with patch("rehoboam.trader.Trader", falling):
            assert trader._propose_buy(LEAGUE, rec, ctx) is False
            assert trader._propose_buy(LEAGUE, rec, ctx, waive_trend_floor=True) is True
        risks = [r for line in trader._session_proposals for r in line.risks]
        assert any("proposed anyway" in r for r in risks), risks

    def test_a_club_at_its_limit_is_skipped_for_the_next_candidate(self, trader):
        squad = _ten_short_one_forward(("7", "7", "7", "1", "1", "1", "1", "1", "1"))
        recs = [
            _rec("blocked", 4_000_000, 90.0, team_id="7"),
            _rec("ok", 5_000_000, 60.0, team_id="8"),
        ]
        with patch.object(AutoTrader, "_propose_buy", return_value=True) as propose:
            trader._run_emergency_squad_fill(
                league=LEAGUE, ctx=_ctx(recs, 50_000_000), fresh_squad=squad, slots_short=1
            )
        assert [c.args[1].player.id for c in propose.call_args_list] == ["ok"]

    def test_open_bids_count_toward_the_club_limit(self, trader):
        squad = _ten_short_one_forward(("7", "7", "1", "1", "1", "1", "1", "1", "1"))
        recs = [
            _rec("blocked", 4_000_000, 90.0, team_id="7"),
            _rec("ok", 5_000_000, 60.0, team_id="8"),
        ]
        ctx = _ctx(recs, 50_000_000)
        ctx.my_bids = [_market_player("pending", 1_000_000, team_id="7")]
        with patch.object(AutoTrader, "_propose_buy", return_value=True) as propose:
            trader._run_emergency_squad_fill(
                league=LEAGUE, ctx=ctx, fresh_squad=squad, slots_short=1
            )
        assert [c.args[1].player.id for c in propose.call_args_list] == ["ok"]


class TestTheBackstopIsTimeBased:
    def test_an_emergency_proposal_carries_an_auto_approve_deadline(self, trader):
        """A slot nobody taps is still -100. The proposal expires INTO a buy."""
        trader.learner.record_proposal(
            proposal_id="p1",
            player_id="f1",
            player_name="Striker",
            bid=5_000_000,
            market_value=5_000_000,
            message="m",
            auto_approve_at=time.time() + 24 * HOUR,
        )

        row = trader.learner.get_proposal("p1")

        assert row["auto_approve_at"] is not None

    def test_a_due_proposal_is_returned_for_execution(self, trader):
        trader.learner.record_proposal(
            proposal_id="due",
            player_id="f1",
            player_name="Striker",
            bid=5_000_000,
            market_value=5_000_000,
            message="m",
            auto_approve_at=time.time() - HOUR,
        )

        due = trader.learner.due_auto_approvals(now=time.time())

        assert [d["proposal_id"] for d in due] == ["due"]

    def test_a_proposal_not_yet_due_is_left_alone(self, trader):
        trader.learner.record_proposal(
            proposal_id="later",
            player_id="f1",
            player_name="Striker",
            bid=5_000_000,
            market_value=5_000_000,
            message="m",
            auto_approve_at=time.time() + 6 * HOUR,
        )

        assert trader.learner.due_auto_approvals(now=time.time()) == []

    def test_an_ordinary_proposal_never_auto_approves(self, trader):
        """Only the -100 case earns a backstop. A plain upgrade waits forever."""
        trader.learner.record_proposal(
            proposal_id="plain",
            player_id="f1",
            player_name="Upgrade",
            bid=5_000_000,
            market_value=5_000_000,
            message="m",
        )

        assert trader.learner.due_auto_approvals(now=time.time() + 365 * 24 * HOUR) == []

    def test_an_already_resolved_proposal_is_not_re_executed(self, trader):
        trader.learner.record_proposal(
            proposal_id="done",
            player_id="f1",
            player_name="Striker",
            bid=5_000_000,
            market_value=5_000_000,
            message="m",
            auto_approve_at=time.time() - HOUR,
        )
        trader.learner.set_proposal_status("done", "rejected")

        assert trader.learner.due_auto_approvals(now=time.time()) == []


class _ApprovalApi:
    """The five calls the approval path makes."""

    def __init__(self, listing, budget=50_000_000, squad=()):
        self.listing = listing
        self.budget = budget
        self.squad = list(squad)
        self.bought: list[tuple[str, int]] = []

    def get_market(self, league):
        return [self.listing]

    def get_squad(self, league):
        return list(self.squad)

    def get_my_bids(self, league):
        return []

    def get_team_info(self, league):
        return {"budget": self.budget, "team_value": 100_000_000}

    def buy_player(self, league, player, price):
        self.bought.append((player.id, price))
        return True


class TestADueProposalExecutesThroughTheSameGate:
    """One copy of the gate, not two.

    REH-99/100/107/111 were all "two components each holding a private copy of
    one rule". A second execute path for auto-approval would be the next one,
    so the webhook and this share `execute_proposal`.
    """

    def _due(self, learner, bid=5_000_000):
        learner.record_proposal(
            proposal_id="due1",
            player_id="f1",
            player_name="Striker",
            bid=bid,
            market_value=5_000_000,
            message="m",
            tier="marginal",
            auto_approve_at=time.time() - HOUR,
        )

    def test_it_reaches_the_api(self, trader):
        from rehoboam.notify.approval import execute_proposal

        self._due(trader.learner)
        api = _ApprovalApi(_market_player("f1", 5_000_000))

        execute_proposal(
            trader.learner.get_proposal("due1"),
            settings=trader.settings,
            learner=trader.learner,
            api=api,
            league=LEAGUE,
        )

        assert api.bought == [("f1", 5_000_000)]
        assert trader.learner.get_proposal("due1")["status"] == "executed"

    def test_the_gate_still_refuses_an_unaffordable_bid(self, trader):
        """The deadline overrides silence, never the budget rule."""
        from rehoboam.notify.approval import execute_proposal

        self._due(trader.learner, bid=90_000_000)
        api = _ApprovalApi(_market_player("f1", 5_000_000), budget=10_000_000)

        execute_proposal(
            trader.learner.get_proposal("due1"),
            settings=trader.settings,
            learner=trader.learner,
            api=api,
            league=LEAGUE,
        )

        assert api.bought == [], "auto-approval must not buy past the gate"
        assert trader.learner.get_proposal("due1")["status"] == "failed"

    def test_a_vanished_listing_fails_rather_than_buying_something_else(self, trader):
        from rehoboam.notify.approval import execute_proposal

        self._due(trader.learner)
        api = _ApprovalApi(_market_player("other", 5_000_000))

        execute_proposal(
            trader.learner.get_proposal("due1"),
            settings=trader.settings,
            learner=trader.learner,
            api=api,
            league=LEAGUE,
        )

        assert api.bought == []
        assert trader.learner.get_proposal("due1")["status"] == "failed"


class TestTheSessionActuallyChecksTheDeadline:
    """A deadline nothing reads is a slot left empty (REH-86's lesson)."""

    def test_a_due_proposal_is_executed_during_a_session(self, trader, tmp_path):
        trader.learner.record_proposal(
            proposal_id="due1",
            player_id="f1",
            player_name="Striker",
            bid=5_000_000,
            market_value=5_000_000,
            message="m",
            tier="marginal",
            auto_approve_at=time.time() - HOUR,
        )
        api = _ApprovalApi(_market_player("f1", 5_000_000))
        trader.api = api

        trader._process_due_auto_approvals(LEAGUE)

        assert api.bought == [("f1", 5_000_000)]

    def test_run_full_session_calls_it(self, trader, monkeypatch):
        with (
            patch.object(AutoTrader, "_process_due_auto_approvals", return_value=None) as process,
            patch.object(AutoTrader, "_build_session_context", side_effect=RuntimeError("stop")),
            patch.object(AutoTrader, "_set_optimal_lineup", return_value=[]),
        ):
            trader.api = _ApprovalApi(_market_player("f1", 5_000_000))
            trader.run_full_session(LEAGUE)

        assert process.called, "the deadline is never read"
