"""Tests for DecisionEngine — buy/sell/lineup/trade-pair from PlayerScore."""

from rehoboam.analyzer import RosterContext
from rehoboam.kickbase_client import MarketPlayer
from rehoboam.scoring.decision import DecisionEngine
from rehoboam.scoring.models import DataQuality, PlayerScore


def _make_player(**overrides) -> MarketPlayer:
    defaults = {
        "id": "p1",
        "first_name": "Test",
        "last_name": "Player",
        "position": "Midfielder",
        "team_id": "t1",
        "team_name": "Test FC",
        "price": 5_000_000,
        "market_value": 5_000_000,
        "points": 100,
        "average_points": 20.0,
        "status": 0,
    }
    defaults.update(overrides)
    return MarketPlayer(**defaults)


def _make_score(
    player_id: str = "p1",
    ep: float = 60.0,
    grade: str = "A",
    avg_points: float = 20.0,
    price: int = 5_000_000,
    market_value: int = 5_000_000,
    position: str = "Midfielder",
    status: int = 0,
) -> PlayerScore:
    return PlayerScore(
        player_id=player_id,
        expected_points=ep,
        data_quality=DataQuality(
            grade=grade,
            games_played=15,
            consistency=0.8,
            has_fixture_data=True,
            has_lineup_data=True,
            warnings=[],
        ),
        base_points=30.0,
        consistency_bonus=10.0,
        lineup_bonus=20.0,
        fixture_bonus=0.0,
        form_bonus=0.0,
        minutes_bonus=0.0,
        dgw_multiplier=1.0,
        is_dgw=False,
        next_opponent=None,
        notes=[],
        current_price=price,
        market_value=market_value,
        position=position,
        average_points=avg_points,
        status=status,
    )


def _make_roster_context(position: str, current_count: int, minimum_count: int) -> RosterContext:
    return RosterContext(
        position=position,
        current_count=current_count,
        minimum_count=minimum_count,
        existing_players=[],
        weakest_player=None,
        is_below_minimum=current_count < minimum_count,
        upgrade_potential=0.0,
    )


class TestRecommendBuys:
    def test_sorts_by_ep_descending(self):
        engine = DecisionEngine()
        scores = [
            _make_score("p1", ep=50.0, avg_points=20.0),
            _make_score("p2", ep=80.0, avg_points=25.0),
            _make_score("p3", ep=65.0, avg_points=22.0),
        ]
        players = {s.player_id: _make_player(id=s.player_id, average_points=20.0) for s in scores}
        recs = engine.recommend_buys(scores, [], {}, 10_000_000, players)
        assert recs[0].score.player_id == "p2"
        assert recs[1].score.player_id == "p3"

    def test_filters_grade_f(self):
        engine = DecisionEngine()
        scores = [
            _make_score("p1", ep=80.0, grade="F", avg_points=20.0),
            _make_score("p2", ep=60.0, grade="A", avg_points=20.0),
        ]
        players = {s.player_id: _make_player(id=s.player_id, average_points=20.0) for s in scores}
        recs = engine.recommend_buys(scores, [], {}, 10_000_000, players)
        assert len(recs) == 1
        assert recs[0].score.player_id == "p2"

    def test_filters_low_avg_points(self):
        engine = DecisionEngine()
        scores = [
            _make_score("p1", ep=60.0, avg_points=15.0),  # Below 20 threshold
        ]
        players = {"p1": _make_player(id="p1", average_points=15.0)}
        recs = engine.recommend_buys(scores, [], {}, 10_000_000, players)
        assert len(recs) == 0

    def test_filters_low_ep(self):
        engine = DecisionEngine(min_expected_points_to_buy=30.0)
        scores = [
            _make_score("p1", ep=20.0, avg_points=20.0),  # Below 30 EP threshold
        ]
        players = {"p1": _make_player(id="p1", average_points=20.0)}
        recs = engine.recommend_buys(scores, [], {}, 10_000_000, players)
        assert len(recs) == 0

    def test_fills_gap_bonus(self):
        engine = DecisionEngine()
        scores = [_make_score("p1", ep=60.0, avg_points=20.0, position="Goalkeeper")]
        players = {"p1": _make_player(id="p1", position="Goalkeeper", average_points=20.0)}
        roster_ctx = {"Goalkeeper": _make_roster_context("Goalkeeper", 0, 1)}
        recs = engine.recommend_buys(scores, [], roster_ctx, 10_000_000, players)
        assert recs[0].roster_bonus == 10.0
        assert recs[0].reason == "fills_gap"

    def test_respects_budget(self):
        engine = DecisionEngine()
        scores = [_make_score("p1", ep=80.0, price=10_000_000, avg_points=20.0)]
        players = {"p1": _make_player(id="p1", price=10_000_000, average_points=20.0)}
        recs = engine.recommend_buys(scores, [], {}, 5_000_000, players)
        assert len(recs) == 0


class TestRecommendSells:
    def test_sorts_by_ep_ascending(self):
        engine = DecisionEngine()
        scores = [
            _make_score("p1", ep=70.0),
            _make_score("p2", ep=30.0),
            _make_score("p3", ep=50.0),
        ]
        players = {s.player_id: _make_player(id=s.player_id) for s in scores}
        recs = engine.recommend_sells(scores, {}, players)
        assert recs[0].score.player_id == "p2"

    def test_protects_position_minimum(self):
        engine = DecisionEngine()
        scores = [_make_score("p1", ep=20.0)]
        players = {"p1": _make_player(id="p1", position="Goalkeeper")}
        roster_ctx = {"Goalkeeper": _make_roster_context("Goalkeeper", 1, 1)}
        recs = engine.recommend_sells(scores, roster_ctx, players)
        assert recs[0].is_protected is True
        assert recs[0].protection_reason is not None


class TestBuildTradePairs:
    def test_pairs_lowest_sell_with_highest_buy(self):
        engine = DecisionEngine(min_ep_upgrade_threshold=10.0)
        market_scores = [_make_score("m1", ep=80.0, price=8_000_000, avg_points=25.0)]
        squad_scores = [_make_score("s1", ep=30.0, market_value=3_000_000)]
        market_players = {"m1": _make_player(id="m1", price=8_000_000, average_points=25.0)}
        squad_players = {"s1": _make_player(id="s1", market_value=3_000_000)}
        pairs = engine.build_trade_pairs(
            market_scores,
            squad_scores,
            {},
            10_000_000,
            market_players,
            squad_players,
        )
        assert len(pairs) == 1
        assert pairs[0].ep_gain == 50.0

    def test_no_pair_below_threshold(self):
        engine = DecisionEngine(min_ep_upgrade_threshold=10.0)
        market_scores = [_make_score("m1", ep=35.0, avg_points=20.0)]
        squad_scores = [_make_score("s1", ep=30.0)]
        market_players = {"m1": _make_player(id="m1", average_points=20.0)}
        squad_players = {"s1": _make_player(id="s1")}
        pairs = engine.build_trade_pairs(
            market_scores,
            squad_scores,
            {},
            10_000_000,
            market_players,
            squad_players,
        )
        assert len(pairs) == 0


class TestSelectLineup:
    def test_returns_player_id_to_ep_dict(self):
        engine = DecisionEngine()
        scores = [_make_score(f"p{i}", ep=float(60 + i)) for i in range(15)]
        result = engine.select_lineup(scores)
        assert isinstance(result, dict)
        assert all(isinstance(v, float) for v in result.values())
