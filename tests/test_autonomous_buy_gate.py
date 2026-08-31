"""The safety gate on the paths nobody approves (REH-100).

`services/safety_gate.check_buy` called itself "the last thing between a
decision and real money" while having exactly one call site: the Telegram
Approve handler. So the guard covered the path where a human had already
looked at the proposal and tapped a button, and did not cover trade pairs,
profit flips, emergency squad fill, or the compliance re-bid — the paths that
spend with nobody in the loop. That is backwards from the intent.

These tests drive the real `ExecutionService` against a mock API rather than a
stub execution layer, so what they assert is that **no HTTP call is made**, not
that some mock recorded a keyword argument. A test that only checked the gate
was *passed* would still pass if the gate were never consulted.

Two refusal policies are deliberately different, and each has a test:

- **Emergency fill** walks down its ranked list. An empty lineup slot is -100
  points, so refusing one candidate must not mean fielding nobody — but the
  budget rule stays hard, because negative budget at kickoff is zero points for
  the entire matchday, which is far worse than -100.
- **Trade pairs** refuse *before* the sell. The sell is irreversible and the buy
  is only a bid, so a gate that fired after the sell would leave the squad a
  player lighter for nothing.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from rehoboam.auto_trader import AutoTrader
from rehoboam.bid_learner import BidLearner
from rehoboam.config import Settings
from rehoboam.services.execution import ExecutionService


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "test")
    return Settings()


@pytest.fixture
def api():
    a = MagicMock()
    a.buy_player = MagicMock(return_value=None)
    a.sell_player_instant = MagicMock(return_value=None)
    a.cancel_bid = MagicMock(return_value=None)
    return a


@pytest.fixture
def trader(tmp_path, settings, api, monkeypatch):
    """An AutoTrader wired to the REAL ExecutionService, so the gate runs."""
    monkeypatch.chdir(tmp_path)
    t = AutoTrader(api=api, settings=settings, dry_run=False)
    t.learner = BidLearner(db_path=tmp_path / "bid_learning.db")
    t.execution = ExecutionService(api=api, tracker=MagicMock(), dry_run=False)
    return t


def _player(pid, position="Forward", price=1_000_000, market_value=None, team_id="1"):
    return SimpleNamespace(
        id=pid,
        first_name="X",
        last_name=f"P{pid}",
        position=position,
        price=price,
        market_value=price if market_value is None else market_value,
        average_points=10.0,
        status=0,
        team_id=team_id,
    )


def _rec(player, bid, ep_gain=10.0):
    return SimpleNamespace(
        player=player,
        recommended_bid=bid,
        marginal_ep_gain=ep_gain,
        sell_plan=None,
    )


def _ctx(buy_recs, current_budget, *, squad=(), market=None, days_until_match=None):
    market_players = market if market is not None else {r.player.id: r.player for r in buy_recs}
    return SimpleNamespace(
        ep_result={
            "buy_recs": list(buy_recs),
            "trade_pairs": [],
            "squad_scores": [],
            "market_players": market_players,
        },
        my_bid_amounts={},
        my_bids=[],
        squad=list(squad),
        current_budget=current_budget,
        flip_budget=current_budget,
        executed_trade_count=0,
        matchday_phase=SimpleNamespace(days_until_match=days_until_match),
    )


class _ProposalSpy:
    """The emergency fill proposes rather than buys since REH-114."""

    def __init__(self):
        self.calls: list[tuple[str, int]] = []

    def __call__(self, league, rec, ctx, *, bid=None, auto_approve_at=None):
        self.calls.append((rec.player.id, int(bid if bid is not None else rec.recommended_bid)))
        return True

    @property
    def ids(self) -> list[str]:
        return [c[0] for c in self.calls]


class TestEmergencyFillRefusal:
    """An empty slot is -100, so a refusal means 'try the next one'."""

    def test_over_ceiling_candidate_is_skipped_and_the_next_one_is_bought(self, trader, api):
        """The first candidate is priced past its ceiling; the second is not.

        `over` is a EUR 5,000,000 player bid at EUR 7,000,000 (+40%), past the
        8% marginal ceiling. `clean` is bid exactly at market value. The slot
        must still get filled — by `clean`.
        """
        over = _player("over", price=5_000_000)
        clean = _player("clean", price=4_000_000)
        squad = [_player(f"s{i}", "Defender", team_id=f"club{i}") for i in range(10)]

        ctx = _ctx(
            [_rec(over, bid=7_000_000, ep_gain=30.0), _rec(clean, bid=4_000_000, ep_gain=5.0)],
            current_budget=50_000_000,
            squad=squad,
        )

        trader._propose_buy = spy = _ProposalSpy()

        results = trader._run_emergency_squad_fill(
            league=SimpleNamespace(id="L"), ctx=ctx, fresh_squad=squad, slots_short=1
        )

        # REH-114: the fill proposes, so the ceiling is pre-flighted here rather
        # than at execution — asking Marco to approve a bid the gate would then
        # refuse is the broken Approve button REH-99 existed to fix.
        assert spy.ids == ["clean"], "the refused candidate must not be proposed"
        assert api.buy_player.call_count == 0, "squad buys wait for approval"
        assert any(r.success for r in results), "the slot must still be filled"

    def test_budget_rule_stays_hard_even_to_fill_a_slot(self, trader, api):
        """-100 for an empty slot beats zero points for the whole matchday.

        The only candidate costs more than the phase allows. Filling the slot
        anyway would take the budget negative, and a negative budget at kickoff
        scores nothing at all — so this must refuse and leave the slot empty.
        """
        pricey = _player("pricey", price=9_000_000)
        squad = [_player(f"s{i}", "Defender", team_id=f"club{i}") for i in range(10)]

        ctx = _ctx([_rec(pricey, bid=9_000_000)], current_budget=1_000_000, squad=squad)

        results = trader._run_emergency_squad_fill(
            league=SimpleNamespace(id="L"), ctx=ctx, fresh_squad=squad, slots_short=1
        )

        assert api.buy_player.call_count == 0
        assert not any(r.success for r in results)


class TestTradePairPreflight:
    """The sell is irreversible; the buy is only a bid. Refuse before selling."""

    def test_a_refused_buy_does_not_sell_the_paired_player(self, trader, api):
        """Gate refusal on the buy leg must leave the sell leg untouched.

        Without a pre-flight the sequence is sell-then-refuse, which is the
        "sold X but failed to buy Y" warning `_run_trade_phase` already prints
        — a squad one player lighter in exchange for nothing.
        """
        sell = _player("sell", price=3_000_000)
        buy = _player("buy", price=5_000_000)
        pair = SimpleNamespace(
            sell_player=sell,
            buy_player=buy,
            recommended_bid=8_000_000,  # +60% — past every ceiling
            ep_gain=30.0,
            sell_is_starter=False,
            metadata={},
        )

        refused = trader._trade_pair_preflight(pair, _ctx([], current_budget=50_000_000))

        assert refused is not None, "an over-ceiling pair must be refused"
        assert api.sell_player_instant.call_count == 0
        assert api.buy_player.call_count == 0


class TestComplianceRebid:
    """A re-bid is mandatory, but cancelling is always legal."""

    def test_a_refused_rebid_cancels_the_bid_instead(self, settings, api):
        """The gate refuses (unknown player), so the bid is cancelled, not raised.

        The compliance path exists because a bid below market value is illegal.
        Its two legal outcomes are "raise the bid" and "cancel the bid" — and
        the code already implements cancel for the not-profitable case. When
        the gate refuses the raise, cancel is the remaining legal move.
        """
        from rehoboam.league_compliance import BidComplianceIssue, LeagueComplianceChecker

        player = _player("ghost", price=1_000_000)
        player.user_offer_id = "offer-1"
        api.get_market = MagicMock(return_value=[player])
        api.get_team_info = MagicMock(return_value={"budget": 10_000_000})

        issue = BidComplianceIssue(
            player_id="ghost",
            player_name="Pghost",
            current_bid=900_000,
            market_value=1_000_000,
            asking_price=1_000_000,
            violation_amount=100_000,
            violation_pct=10.0,
            # Absurdly past any ceiling, so the gate must refuse the raise.
            new_required_bid=50_000_000,
            is_still_profitable=True,
            predicted_value=1_200_000,
            reason="test",
        )

        checker = LeagueComplianceChecker(api, settings)
        adjusted, canceled = checker.resolve_bid_compliance_issues(
            league=SimpleNamespace(id="L"), issues=[issue], dry_run=False
        )

        assert api.buy_player.call_count == 0
        assert api.cancel_bid.call_count == 1
        assert (adjusted, canceled) == (0, 1)


def test_gate_refusal_is_logged_at_error(trader, api, caplog):
    """A refusal is money not spent — it must be visible in the log."""
    from tests.conftest import permissive_buy_gate

    gate = permissive_buy_gate("p1", market_value=1_000_000, spendable_budget=100_000_000)
    with caplog.at_level("ERROR"):
        result = trader.execution.buy(
            league=SimpleNamespace(id="L"),
            player=_player("p1"),
            price=90_000_000,
            reason="test",
            current_budget=100_000_000,
            days_until_match=6,
            gate=gate,
        )
    assert result.success is False
    assert any("GATE REFUSED" in r.message for r in caplog.records)
    assert api.buy_player.call_count == 0
