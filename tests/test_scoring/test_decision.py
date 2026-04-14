"""Tests for DecisionEngine."""

from rehoboam.kickbase_client import MarketPlayer
from rehoboam.scoring.decision import DecisionEngine
from rehoboam.scoring.models import DataQuality, PlayerScore


def _make_score(
    player_id,
    ep,
    position="Midfielder",
    price=5_000_000,
    market_value=5_000_000,
    fixture_bonus=0.0,
):
    dq = DataQuality(
        grade="A",
        games_played=15,
        consistency=0.8,
        has_fixture_data=True,
        has_lineup_data=True,
        warnings=[],
    )
    return PlayerScore(
        player_id=player_id,
        expected_points=ep,
        data_quality=dq,
        base_points=ep * 0.5,
        consistency_bonus=5.0,
        lineup_bonus=10.0,
        fixture_bonus=fixture_bonus,
        form_bonus=0.0,
        minutes_bonus=0.0,
        dgw_multiplier=1.0,
        is_dgw=False,
        next_opponent=None,
        notes=[],
        current_price=price,
        market_value=market_value,
        position=position,
    )


def _make_player(player_id, position):
    return MarketPlayer(
        id=player_id,
        first_name="Test",
        last_name=player_id,
        position=position,
        team_id="t1",
        team_name="Test FC",
        price=5_000_000,
        market_value=5_000_000,
        points=100,
        average_points=12.0,
        status=0,
    )


class TestMarginalEP:
    def test_player_improves_best_11(self):
        """New player with higher EP than weakest starter → positive marginal gain."""
        squad = []
        squad_scores = []
        squad.append(_make_player("gk1", "Goalkeeper"))
        squad_scores.append(_make_score("gk1", 40.0, "Goalkeeper"))
        for i in range(3):
            squad.append(_make_player(f"def{i}", "Defender"))
            squad_scores.append(_make_score(f"def{i}", 35.0, "Defender"))
        for i in range(6):
            ep = 50.0 - i * 5  # 50, 45, 40, 35, 30, 25
            squad.append(_make_player(f"mid{i}", "Midfielder"))
            squad_scores.append(_make_score(f"mid{i}", ep, "Midfielder"))
        squad.append(_make_player("fwd0", "Forward"))
        squad_scores.append(_make_score("fwd0", 45.0, "Forward"))

        engine = DecisionEngine()
        candidate = _make_score("new_mid", 55.0, "Midfielder")
        candidate_player = _make_player("new_mid", "Midfielder")

        result = engine.calculate_marginal_ep(
            candidate_score=candidate,
            candidate_player=candidate_player,
            squad=squad,
            squad_scores=squad_scores,
        )

        assert result.marginal_ep_gain > 0
        assert result.replaces_player_id is not None

    def test_player_doesnt_crack_best_11(self):
        """Player worse than all starters → marginal gain = 0."""
        squad = []
        squad_scores = []
        squad.append(_make_player("gk1", "Goalkeeper"))
        squad_scores.append(_make_score("gk1", 40.0, "Goalkeeper"))
        for i in range(3):
            squad.append(_make_player(f"def{i}", "Defender"))
            squad_scores.append(_make_score(f"def{i}", 50.0, "Defender"))
        for i in range(6):
            squad.append(_make_player(f"mid{i}", "Midfielder"))
            squad_scores.append(_make_score(f"mid{i}", 60.0, "Midfielder"))
        squad.append(_make_player("fwd0", "Forward"))
        squad_scores.append(_make_score("fwd0", 55.0, "Forward"))

        engine = DecisionEngine()
        candidate = _make_score("weak", 20.0, "Midfielder")
        candidate_player = _make_player("weak", "Midfielder")

        result = engine.calculate_marginal_ep(
            candidate_score=candidate,
            candidate_player=candidate_player,
            squad=squad,
            squad_scores=squad_scores,
        )

        assert result.marginal_ep_gain == 0


class TestSellPlan:
    def test_viable_sell_plan(self):
        squad = []
        squad_scores = []
        squad.append(_make_player("gk1", "Goalkeeper"))
        squad_scores.append(_make_score("gk1", 40.0, "Goalkeeper", market_value=3_000_000))
        for i in range(3):
            squad.append(_make_player(f"def{i}", "Defender"))
            squad_scores.append(_make_score(f"def{i}", 35.0, "Defender", market_value=5_000_000))
        for i in range(6):
            squad.append(_make_player(f"mid{i}", "Midfielder"))
            squad_scores.append(
                _make_score(f"mid{i}", 50.0 - i * 5, "Midfielder", market_value=8_000_000)
            )
        squad.append(_make_player("fwd0", "Forward"))
        squad_scores.append(_make_score("fwd0", 45.0, "Forward", market_value=10_000_000))

        engine = DecisionEngine()
        result = engine.build_sell_plan(
            bid_amount=20_000_000,
            current_budget=5_000_000,
            squad=squad,
            squad_scores=squad_scores,
            best_11_ids={
                "gk1",
                "def0",
                "def1",
                "def2",
                "mid0",
                "mid1",
                "mid2",
                "mid3",
                "mid4",
                "mid5",
                "fwd0",
            },
            displaced_player_id="mid5",
        )

        assert result.is_viable
        assert result.net_budget_after >= 0

    def test_within_budget_no_sells(self):
        engine = DecisionEngine()
        result = engine.build_sell_plan(
            bid_amount=5_000_000,
            current_budget=10_000_000,
            squad=[],
            squad_scores=[],
            best_11_ids=set(),
            displaced_player_id=None,
        )
        assert result.is_viable
        assert len(result.players_to_sell) == 0

    def test_sell_plan_not_viable_if_all_protected(self):
        squad = [_make_player("gk1", "Goalkeeper")]
        squad_scores = [_make_score("gk1", 40.0, "Goalkeeper", market_value=3_000_000)]

        engine = DecisionEngine()
        result = engine.build_sell_plan(
            bid_amount=50_000_000,
            current_budget=1_000_000,
            squad=squad,
            squad_scores=squad_scores,
            best_11_ids={"gk1"},
            displaced_player_id=None,
        )

        assert not result.is_viable


class TestRecommendSells:
    def test_bench_players_more_expendable(self):
        """Bench players should have higher expendability than starters."""
        squad = []
        squad_scores = []
        squad.append(_make_player("gk1", "Goalkeeper"))
        squad_scores.append(_make_score("gk1", 40.0, "Goalkeeper"))
        for i in range(3):
            squad.append(_make_player(f"def{i}", "Defender"))
            squad_scores.append(_make_score(f"def{i}", 35.0, "Defender"))
        for i in range(4):
            squad.append(_make_player(f"mid{i}", "Midfielder"))
            squad_scores.append(_make_score(f"mid{i}", 50.0, "Midfielder"))
        for i in range(3):
            squad.append(_make_player(f"fwd{i}", "Forward"))
            squad_scores.append(_make_score(f"fwd{i}", 45.0 if i == 0 else 20.0, "Forward"))

        squad_players = {p.id: p for p in squad}
        engine = DecisionEngine()
        recommendations = engine.recommend_sells(
            squad_scores=squad_scores, roster_context={}, squad_players=squad_players
        )

        # bench players (fwd1, fwd2) should be more expendable than starter fwd0
        bench_recs = [r for r in recommendations if r.score.player_id in ("fwd1", "fwd2")]
        starter_fwd_recs = [r for r in recommendations if r.score.player_id == "fwd0"]
        assert bench_recs
        assert starter_fwd_recs
        assert bench_recs[0].expendability > starter_fwd_recs[0].expendability

    def test_position_minimum_players_protected(self):
        """Only goalkeeper → should be marked as protected."""
        squad = [_make_player("gk1", "Goalkeeper")]
        squad_scores = [_make_score("gk1", 40.0, "Goalkeeper")]

        squad_players = {p.id: p for p in squad}
        engine = DecisionEngine()
        recommendations = engine.recommend_sells(
            squad_scores=squad_scores, roster_context={}, squad_players=squad_players
        )

        gk_rec = next(r for r in recommendations if r.score.player_id == "gk1")
        assert gk_rec.is_protected

    def test_tough_run_raises_expendability(self):
        """Two identical-EP players — the one with a tough fixture run ahead
        should be more expendable (selling now beats waiting for EP to drop)."""
        squad = []
        squad_scores = []
        squad.append(_make_player("gk1", "Goalkeeper"))
        squad_scores.append(_make_score("gk1", 40.0, "Goalkeeper"))
        for i in range(3):
            squad.append(_make_player(f"def{i}", "Defender"))
            squad_scores.append(_make_score(f"def{i}", 35.0, "Defender"))
        # Two midfielders with identical EP but different fixture runs
        squad.append(_make_player("mid_easy", "Midfielder"))
        squad_scores.append(_make_score("mid_easy", 45.0, "Midfielder", fixture_bonus=+5.0))
        squad.append(_make_player("mid_tough", "Midfielder"))
        squad_scores.append(_make_score("mid_tough", 45.0, "Midfielder", fixture_bonus=-10.0))
        squad.append(_make_player("fwd0", "Forward"))
        squad_scores.append(_make_score("fwd0", 50.0, "Forward"))

        squad_players = {p.id: p for p in squad}
        engine = DecisionEngine()
        recommendations = engine.recommend_sells(
            squad_scores=squad_scores, roster_context={}, squad_players=squad_players
        )

        tough = next(r for r in recommendations if r.score.player_id == "mid_tough")
        easy = next(r for r in recommendations if r.score.player_id == "mid_easy")

        assert tough.expendability > easy.expendability, (
            "Tough fixture run should raise expendability vs an identical player "
            f"with an easy run (tough={tough.expendability}, easy={easy.expendability})"
        )
        assert "tough run ahead" in tough.reason


class TestSelectLineup:
    def test_returns_score_dict(self):
        """select_lineup returns {player_id: ep} dict."""
        squad_scores = [
            _make_score("p1", 40.0),
            _make_score("p2", 50.0),
            _make_score("p3", 30.0),
        ]
        engine = DecisionEngine()
        result = engine.select_lineup(squad_scores)

        assert isinstance(result, dict)
        assert result["p1"] == 40.0
        assert result["p2"] == 50.0
        assert result["p3"] == 30.0
