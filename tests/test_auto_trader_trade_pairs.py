"""Tests for auto_trader trade pair execution at full squad (15/15).

Verifies that when the squad is full, the bot executes sell→buy swaps
instead of early-exiting with "No squad slots available".
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from rehoboam.auto_trader import AutoTrader
from rehoboam.kickbase_client import MarketPlayer
from rehoboam.scoring.models import DataQuality, PlayerScore, TradePair


def _make_player(id="p1", first_name="Test", last_name="Player", **overrides) -> MarketPlayer:
    defaults = {
        "id": id,
        "first_name": first_name,
        "last_name": last_name,
        "position": "Midfielder",
        "team_id": "t1",
        "team_name": "Test FC",
        "price": 5_000_000,
        "market_value": 5_000_000,
        "points": 100,
        "average_points": 25.0,
        "status": 0,
    }
    defaults.update(overrides)
    return MarketPlayer(**defaults)


def _make_score(player_id="p1", expected_points=50.0) -> PlayerScore:
    return PlayerScore(
        player_id=player_id,
        expected_points=expected_points,
        data_quality=DataQuality(
            grade="B",
            games_played=10,
            consistency=0.7,
            has_fixture_data=True,
            has_lineup_data=True,
            warnings=[],
        ),
        base_points=40.0,
        consistency_bonus=5.0,
        lineup_bonus=0.0,
        fixture_bonus=3.0,
        form_bonus=2.0,
        minutes_bonus=0.0,
        dgw_multiplier=1.0,
        is_dgw=False,
        next_opponent="Opponent FC",
        notes=[],
        current_price=5_000_000,
        market_value=5_000_000,
    )


def _make_trade_pair(
    buy_id="buy1",
    sell_id="sell1",
    buy_ep=70.0,
    sell_ep=30.0,
    recommended_bid=6_000_000,
) -> TradePair:
    buy_player = _make_player(id=buy_id, first_name="Good", last_name="Buyer", price=6_000_000)
    sell_player = _make_player(
        id=sell_id, first_name="Weak", last_name="Seller", market_value=500_000
    )
    return TradePair(
        buy_player=buy_player,
        sell_player=sell_player,
        buy_score=_make_score(buy_id, buy_ep),
        sell_score=_make_score(sell_id, sell_ep),
        net_cost=6_000_000 - int(500_000 * 0.95),
        ep_gain=buy_ep - sell_ep,
        recommended_bid=recommended_bid,
    )


def _make_auto_trader(dry_run=True) -> AutoTrader:
    api = MagicMock()
    settings = MagicMock()
    settings.max_debt_pct_of_team_value = 20
    trader = AutoTrader(
        api=api,
        settings=settings,
        max_trades_per_session=5,
        max_daily_spend=50_000_000,
        dry_run=dry_run,
    )
    return trader


def _mock_ep_result(buy_recs=None, trade_pairs=None):
    return {
        "buy_recs": buy_recs or [],
        "trade_pairs": trade_pairs or [],
        "sell_recs": [],
    }


def _patch_profit_session_deps(mock_ep_result):
    """Context manager that patches all dependencies for run_profit_trading_session."""
    return _combined_patches(mock_ep_result, patch_compliance=True)


def _combined_patches(mock_ep_result, patch_compliance=False):
    """Create all necessary patches for auto_trader tests."""
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        patches = [
            patch("rehoboam.auto_trader.AutoTrader.check_resolved_auctions"),
        ]
        if patch_compliance:
            # These are imported inside run_profit_trading_session
            mock_compliance = patch("rehoboam.league_compliance.LeagueComplianceChecker")
            mock_bid_eval = patch("rehoboam.bid_evaluator.BidEvaluator")
            patches.extend([mock_compliance, mock_bid_eval])

        # Mock the Trader class to avoid needing real settings
        # Imported inside functions as `from .trader import Trader`
        mock_trader_cls = patch("rehoboam.trader.Trader")
        patches.append(mock_trader_cls)

        started = [p.start() for p in patches]
        try:
            # Configure Trader mock (always last in the list)
            mock_trader_instance = started[-1].return_value
            mock_trader_instance.get_ep_recommendations.return_value = mock_ep_result
            mock_trader_instance.trend_service.get_trend.return_value = MagicMock(
                to_dict=lambda: {}
            )
            mock_trader_instance.find_profit_opportunities.return_value = []

            # Configure compliance/evaluator mocks for profit session
            if patch_compliance:
                # LeagueComplianceChecker mock (second-to-last before Trader)
                compliance_mock = started[-3].return_value
                compliance_mock.run_bid_compliance_check.return_value = (0, 0)
                # BidEvaluator mock
                bid_eval_mock = started[-2].return_value
                bid_eval_mock.evaluate_active_bids.return_value = []

            yield mock_trader_instance
        finally:
            for p in patches:
                p.stop()

    return _ctx()


class TestProfitSessionTradePairs:
    """run_profit_trading_session should execute sell→buy swaps at 15/15."""

    def test_executes_trade_pair_at_full_squad(self):
        auto = _make_auto_trader(dry_run=True)
        league = MagicMock()
        pair = _make_trade_pair()

        # Squad is 15/15
        squad = [_make_player(id=f"s{i}") for i in range(15)]
        auto.api.get_squad.return_value = squad
        auto.api.get_my_bids.return_value = []
        auto.api.get_market.return_value = []

        with _patch_profit_session_deps(_mock_ep_result(trade_pairs=[pair])):
            results = auto.run_profit_trading_session(league)

        # Should have both a SELL and a BUY result
        actions = [r.action for r in results]
        assert "SELL" in actions, "Trade pair sell leg was not executed"
        assert "BUY" in actions, "Trade pair buy leg was not executed"

        # Verify sell was for the expendable player
        sell_results = [r for r in results if r.action == "SELL"]
        assert "Weak Seller" in sell_results[0].player_name

        # Verify buy was for the upgrade player
        buy_results = [r for r in results if r.action == "BUY"]
        assert "Good Buyer" in buy_results[0].player_name

    def test_no_trade_pairs_returns_empty_at_full_squad(self):
        auto = _make_auto_trader(dry_run=True)
        league = MagicMock()

        squad = [_make_player(id=f"s{i}") for i in range(15)]
        auto.api.get_squad.return_value = squad
        auto.api.get_my_bids.return_value = []
        auto.api.get_market.return_value = []

        with _patch_profit_session_deps(_mock_ep_result()):
            results = auto.run_profit_trading_session(league)

        assert results == []

    def test_skips_trade_pair_if_already_bid(self):
        auto = _make_auto_trader(dry_run=True)
        league = MagicMock()
        pair = _make_trade_pair(buy_id="already_bid")

        squad = [_make_player(id=f"s{i}") for i in range(15)]
        auto.api.get_squad.return_value = squad
        # Already have a bid on the buy player
        existing_bid = _make_player(id="already_bid")
        existing_bid.user_offer_price = 5_000_000
        auto.api.get_my_bids.return_value = [existing_bid]
        auto.api.get_market.return_value = []

        with _patch_profit_session_deps(_mock_ep_result(trade_pairs=[pair])):
            results = auto.run_profit_trading_session(league)

        # Should skip — no trades executed
        assert len(results) == 0

    def test_respects_max_trades_per_session(self):
        auto = _make_auto_trader(dry_run=True)
        auto.max_trades_per_session = 1  # Only allow 1 trade
        league = MagicMock()

        pairs = [_make_trade_pair(buy_id=f"buy{i}", sell_id=f"sell{i}") for i in range(3)]

        squad = [_make_player(id=f"s{i}") for i in range(15)]
        auto.api.get_squad.return_value = squad
        auto.api.get_my_bids.return_value = []
        auto.api.get_market.return_value = []

        with _patch_profit_session_deps(_mock_ep_result(trade_pairs=pairs)):
            results = auto.run_profit_trading_session(league)

        # Only 1 trade pair should execute (1 sell + 1 buy = 2 results)
        assert len(results) == 2
        assert results[0].action == "SELL"
        assert results[1].action == "BUY"


class TestLineupSessionTradePairs:
    """run_lineup_improvement_session should execute sell→buy swap at 15/15."""

    def test_executes_trade_pair_at_full_squad(self):
        auto = _make_auto_trader(dry_run=True)
        league = MagicMock()
        pair = _make_trade_pair()

        squad = [_make_player(id=f"s{i}") for i in range(15)]
        auto.api.get_squad.return_value = squad
        auto.api.get_my_bids.return_value = []

        with _combined_patches(_mock_ep_result(trade_pairs=[pair])):
            results = auto.run_lineup_improvement_session(league)

        actions = [r.action for r in results]
        assert "SELL" in actions, "Trade pair sell leg was not executed"
        assert "BUY" in actions, "Trade pair buy leg was not executed"

    def test_no_trade_pairs_returns_empty_at_full_squad(self):
        auto = _make_auto_trader(dry_run=True)
        league = MagicMock()

        squad = [_make_player(id=f"s{i}") for i in range(15)]
        auto.api.get_squad.return_value = squad
        auto.api.get_my_bids.return_value = []

        with _combined_patches(_mock_ep_result()):
            results = auto.run_lineup_improvement_session(league)

        assert results == []

    def test_plain_buy_still_works_with_open_slots(self):
        """When squad has room (<15), plain buy_recs should work as before."""
        auto = _make_auto_trader(dry_run=True)
        league = MagicMock()

        buy_rec = SimpleNamespace(
            player=_make_player(id="new_buy", first_name="New", last_name="Buy"),
            recommended_bid=4_000_000,
            reason="Good EP player",
        )

        # Squad has room (12/15)
        squad = [_make_player(id=f"s{i}") for i in range(12)]
        auto.api.get_squad.return_value = squad
        auto.api.get_my_bids.return_value = []

        with _combined_patches(_mock_ep_result(buy_recs=[buy_rec])):
            results = auto.run_lineup_improvement_session(league)

        assert len(results) == 1
        assert results[0].action == "BUY"
        assert "New Buy" in results[0].player_name
