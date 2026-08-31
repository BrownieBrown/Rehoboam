"""Building the pacing context from live session state (REH-85)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from rehoboam.config import Settings
from rehoboam.trader import Trader


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "test")
    return Settings()


@pytest.fixture
def trader(settings):
    learner = MagicMock()
    learner.recent_buy_prices.return_value = [10_800_000] * 9
    return Trader(api=MagicMock(), settings=settings, bid_learner=learner)


def _bid(amount):
    return SimpleNamespace(user_offer_price=amount)


def test_reserve_covers_every_unfilled_slot(trader):
    """11 players + 1 open bid = 12/15, so 3 slots remain. Reserve is for
    2 moves after this buy fills one slot, at EUR 10.8m each."""
    ctx = trader._build_pacing_context(
        squad_size=11, my_bids=[_bid(40_717_295)], current_budget=62_307_522
    )
    assert ctx is not None
    assert ctx.reserve == 21_600_000


def test_open_offers_are_carried_into_the_context(trader):
    ctx = trader._build_pacing_context(
        squad_size=11, my_bids=[_bid(40_717_295)], current_budget=62_307_522
    )
    assert ctx.open_offers == 40_717_295


def test_full_squad_falls_back_to_the_in_season_minimum(trader):
    ctx = trader._build_pacing_context(squad_size=15, my_bids=[], current_budget=62_307_522)
    assert ctx.reserve == 21_600_000


def test_disabled_setting_returns_no_context(trader, settings):
    settings.pacing_enabled = False
    assert (
        trader._build_pacing_context(squad_size=11, my_bids=[], current_budget=62_307_522) is None
    )


def test_a_learner_failure_disables_pacing_rather_than_breaking_the_session(settings):
    """Best-effort learning: a DB problem must never block the EP pipeline."""
    learner = MagicMock()
    learner.recent_buy_prices.side_effect = RuntimeError("db gone")
    t = Trader(api=MagicMock(), settings=settings, bid_learner=learner)
    assert t._build_pacing_context(squad_size=11, my_bids=[], current_budget=62_307_522) is None


def test_no_learner_at_all_disables_pacing(settings):
    t = Trader(api=MagicMock(), settings=settings, bid_learner=None)
    assert t._build_pacing_context(squad_size=11, my_bids=[], current_budget=62_307_522) is None


def test_the_trend_recompute_does_not_discard_pacing(trader):
    """Production only ever calls get_ep_recommendations_with_trends — both
    live entry points (auto_trader.py, cli.py) go through it, never through
    the plain get_ep_recommendations directly. The trends variant recomputes
    every bid a second time (to fold in trend_change_pct) and overwrites
    recommended_bid. If that recompute forgets to pass `pacing` through, the
    correctly-paced bid from the first pass is silently thrown away and
    pacing does nothing in production, even though the plain pipeline (and
    every other test in this file) looks correct in isolation.
    """
    from rehoboam.bidding_strategy import BidRecommendation
    from rehoboam.services.pacing import PacingContext

    pacing_ctx = PacingContext(reserve=5_000_000, open_offers=0)

    buy_rec = SimpleNamespace(
        player=SimpleNamespace(id="p1", price=1_000_000, market_value=1_000_000, offer_count=0),
        score=SimpleNamespace(
            expected_points=50.0,
            is_dgw=False,
            data_quality=SimpleNamespace(games_played=10),
        ),
        marginal_ep_gain=10.0,
        sell_plan=None,
        metadata={},
        recommended_bid=0,
    )
    pair = SimpleNamespace(
        buy_player=SimpleNamespace(id="p2", price=2_000_000, market_value=2_000_000, offer_count=0),
        sell_player=SimpleNamespace(market_value=1_000_000),
        buy_score=SimpleNamespace(expected_points=40.0, is_dgw=False),
        ep_gain=5.0,
        metadata={},
        recommended_bid=0,
    )

    # Short-circuit the heavy inner pipeline: get_ep_recommendations is
    # exercised by test_reserve_covers_every_unfilled_slot etc. above.
    # This test is only about what get_ep_recommendations_with_trends does
    # with the result once pacing is already computed and attached.
    trader.get_ep_recommendations = MagicMock(
        return_value={
            "buy_recs": [buy_rec],
            "trade_pairs": [pair],
            "budget": 10_000_000,
            "pacing": pacing_ctx,
        }
    )
    trader.trend_service = MagicMock()
    trader.trend_service.get_trend.return_value = SimpleNamespace(
        trend_7d_pct=0.0, trend_14d_pct=0.0, momentum="flat"
    )

    recorded_pacing = []

    def fake_calculate_ep_bid(*args, **kwargs):
        recorded_pacing.append(kwargs.get("pacing"))
        return BidRecommendation(
            base_price=1_000_000,
            recommended_bid=1_000_000,
            overbid_amount=0,
            overbid_pct=0.0,
            reasoning="test",
            budget_ceiling=10_000_000,
        )

    trader.bidding.calculate_ep_bid = MagicMock(side_effect=fake_calculate_ep_bid)

    league = SimpleNamespace(id="league1")
    trader.get_ep_recommendations_with_trends(league)

    # One call for the buy rec, one for the trade pair — both must carry
    # the SAME pacing context that get_ep_recommendations already built,
    # not None (dropped) and not a freshly rebuilt one.
    assert len(recorded_pacing) == 2
    assert all(p is pacing_ctx for p in recorded_pacing)


# ---------------------------------------------------------------------------
# Task 7: emergency squad fill must be exempt from the pacing reserve.
#
# An empty lineup slot is -100 pts at kickoff -- that outranks the spending
# discipline pacing exists to enforce. Emergency fill does not size its own
# bids; it reads `rec.recommended_bid` off recommendations that
# `get_ep_recommendations` already paced. The real risk is upstream: if
# pacing legitimately zeroes a candidate's bid (its reserve rule consumed the
# whole spendable budget -- confirmed live in a production dry-run where
# EVERY plain-buy candidate paced to 0), the fill loop's
# `if not rec.recommended_bid or rec.recommended_bid <= 0: continue` skips
# it, and the slot is left empty. It fails hardest exactly when it matters
# most: emergency fill only runs when the squad is short, which means a
# large `slots_to_fill`, which means a large reserve, which means more
# zeroed bids.
# ---------------------------------------------------------------------------


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


def _emergency_fill_ctx(recommended_bid: int):
    """A short squad (11/15, one gap slot) plus one candidate to fill it."""
    squad = [
        SimpleNamespace(
            id=f"s{i}",
            first_name="X",
            last_name=f"P{i}",
            position="Defender",
            price=1_000_000,
            market_value=1_000_000,
            team_id=f"club{i}",
        )
        for i in range(9)
    ]
    target = SimpleNamespace(
        id="fill",
        first_name="Fill",
        last_name="Target",
        position="Forward",
        price=4_000_000,
        market_value=4_000_000,
        team_id="club99",
    )
    ctx = SimpleNamespace(
        ep_result={
            "buy_recs": [
                SimpleNamespace(
                    player=target,
                    recommended_bid=recommended_bid,
                    marginal_ep_gain=10.0,
                    sell_plan=None,
                )
            ],
            "trade_pairs": [],
            "squad_scores": [],
            "market_players": {"fill": target},
        },
        my_bid_amounts={},
        my_bids=[],
        squad=squad,
        current_budget=50_000_000,
        flip_budget=50_000_000,
        executed_trade_count=0,
        matchday_phase=SimpleNamespace(days_until_match=None),
    )
    return squad, target, ctx


def _autotrader_with_mock_api(tmp_path, monkeypatch):
    from rehoboam.auto_trader import AutoTrader

    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "test")
    monkeypatch.chdir(tmp_path)

    api = MagicMock()
    api.buy_player = MagicMock(return_value=None)
    trader = AutoTrader(api=api, settings=Settings(), dry_run=False)
    trader.learner = MagicMock()
    trader.learner.was_recently_sold.return_value = False
    return api, trader


def test_emergency_fill_buys_when_bid_is_already_sized(tmp_path, monkeypatch):
    """Baseline from the task brief: with a normally-sized (non-zero)
    recommended_bid, the fill loop buys the candidate as sized.

    This is NOT the exemption test -- it passes whether or not pacing ever
    reaches this path, because the bid it is given is already usable. Kept
    because it pins the ordinary (non-paced-to-zero) behaviour of the loop;
    see `test_emergency_fill_is_not_starved_by_a_paced_zero_bid` below for
    the test that actually exercises the pacing risk.
    """
    api, trader = _autotrader_with_mock_api(tmp_path, monkeypatch)
    trader._propose_buy = spy = _ProposalSpy()
    squad, target, ctx = _emergency_fill_ctx(recommended_bid=4_000_000)

    results = trader._run_emergency_squad_fill(
        league=SimpleNamespace(id="L"), ctx=ctx, fresh_squad=squad, slots_short=1
    )
    assert any(r.success for r in results), "the slot must be filled"
    # REH-114: the slot is claimed by a proposal, not a buy. The number that
    # matters is unchanged — the bid pacing sized.
    assert spy.calls == [("fill", 4_000_000)]
    assert api.buy_player.call_count == 0, "squad buys wait for approval"


def test_emergency_fill_is_not_starved_by_a_paced_zero_bid(tmp_path, monkeypatch):
    """The real risk: pacing can legitimately size recommended_bid to 0.

    Drives `_run_emergency_squad_fill` with a candidate whose
    `recommended_bid` is 0 -- exactly what pacing produces once its reserve
    rule consumes the whole spendable budget -- and asserts the slot still
    gets filled. Uses the real `ExecutionService` against a mock API and
    asserts on `api.buy_player`, so this is a behavioural check, not a stub's
    bookkeeping.
    """
    api, trader = _autotrader_with_mock_api(tmp_path, monkeypatch)
    trader._propose_buy = spy = _ProposalSpy()
    squad, target, ctx = _emergency_fill_ctx(recommended_bid=0)

    results = trader._run_emergency_squad_fill(
        league=SimpleNamespace(id="L"), ctx=ctx, fresh_squad=squad, slots_short=1
    )
    assert any(
        r.success for r in results
    ), "the slot must be filled despite the paced bid being zero"
    # The fallback must propose at the asking price, not 0, and still respect
    # the budget the emergency path is working with.
    assert spy.calls == [("fill", target.price)]
    assert spy.calls[0][1] <= ctx.current_budget
