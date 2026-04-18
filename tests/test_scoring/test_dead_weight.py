"""Tests for dead-weight buy prevention.

Verifies that the EP pipeline attaches forced sell plans when buying a player
would permanently bench a same-position peer (e.g. 2nd GK), and that the
fieldability guard in formation.py correctly identifies unfieldable squads.
"""

from rehoboam.formation import _POSITION_MAX_STARTERS, select_best_eleven, validate_formation
from rehoboam.kickbase_client import MarketPlayer
from rehoboam.scoring.decision import DecisionEngine, _would_create_dead_weight
from rehoboam.scoring.models import DataQuality, PlayerScore


def _make_player(player_id, position, market_value=5_000_000):
    return MarketPlayer(
        id=player_id,
        first_name="Test",
        last_name=player_id,
        position=position,
        team_id="t1",
        team_name="Test FC",
        price=market_value,
        market_value=market_value,
        points=100,
        average_points=40.0,
        status=0,
    )


def _make_score(player_id, ep, position="Midfielder", market_value=5_000_000):
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
        fixture_bonus=0.0,
        form_bonus=0.0,
        minutes_bonus=0.0,
        dgw_multiplier=1.0,
        is_dgw=False,
        next_opponent=None,
        notes=[],
        current_price=market_value,
        market_value=market_value,
        position=position,
    )


# ---------------------------------------------------------------------------
# _would_create_dead_weight predicate
# ---------------------------------------------------------------------------


class TestWouldCreateDeadWeight:
    def test_second_gk_is_dead_weight(self):
        """Buying a 2nd GK when 1 already exists → dead weight."""
        squad = [_make_player("gk1", "Goalkeeper")]
        candidate = _make_player("gk2", "Goalkeeper")
        assert _would_create_dead_weight(candidate, squad)

    def test_first_gk_is_not_dead_weight(self):
        """Buying the first GK (fills gap) → not dead weight."""
        squad = [_make_player("def1", "Defender")]
        candidate = _make_player("gk1", "Goalkeeper")
        assert not _would_create_dead_weight(candidate, squad)

    def test_mid_with_few_existing_not_dead_weight(self):
        """Only 1 MID in squad, max=5 → buying 2nd MID is fine."""
        squad = [_make_player("mid1", "Midfielder")]
        candidate = _make_player("mid2", "Midfielder")
        assert not _would_create_dead_weight(candidate, squad)

    def test_fourth_defender_not_dead_weight(self):
        """DEF count=3, max=5 → adding a 4th DEF is fine (could start in 4-x-x)."""
        squad = [_make_player(f"def{i}", "Defender") for i in range(3)]
        candidate = _make_player("def4", "Defender")
        assert not _would_create_dead_weight(candidate, squad)

    def test_fifth_defender_not_dead_weight(self):
        """DEF count=4, max=5 → adding a 5th DEF is fine (could start in 5-x-x)."""
        squad = [_make_player(f"def{i}", "Defender") for i in range(4)]
        candidate = _make_player("def5", "Defender")
        assert not _would_create_dead_weight(candidate, squad)

    def test_sixth_defender_is_dead_weight(self):
        """DEF count=5, max=5 → adding a 6th DEF is dead weight."""
        squad = [_make_player(f"def{i}", "Defender") for i in range(5)]
        candidate = _make_player("def5", "Defender")
        assert _would_create_dead_weight(candidate, squad)

    def test_fourth_forward_is_dead_weight(self):
        """FWD count=3, max=3 → adding a 4th FWD is dead weight."""
        squad = [_make_player(f"fwd{i}", "Forward") for i in range(3)]
        candidate = _make_player("fwd3", "Forward")
        assert _would_create_dead_weight(candidate, squad)


# ---------------------------------------------------------------------------
# _POSITION_MAX_STARTERS constant
# ---------------------------------------------------------------------------


class TestPositionMaxStarters:
    def test_gk_max_is_1(self):
        assert _POSITION_MAX_STARTERS["Goalkeeper"] == 1

    def test_def_max_is_5(self):
        assert _POSITION_MAX_STARTERS["Defender"] == 5

    def test_mid_max_is_5(self):
        assert _POSITION_MAX_STARTERS["Midfielder"] == 5

    def test_fwd_max_is_3(self):
        assert _POSITION_MAX_STARTERS["Forward"] == 3


# ---------------------------------------------------------------------------
# select_best_eleven position cap
# ---------------------------------------------------------------------------


class TestSelectBestElevenPositionCap:
    def test_second_gk_never_in_best_11(self):
        """With 12 players including 2 GKs, best-11 should only include 1 GK."""
        squad = [
            _make_player("gk1", "Goalkeeper"),
            _make_player("gk2", "Goalkeeper"),
            *[_make_player(f"def{i}", "Defender") for i in range(4)],
            *[_make_player(f"mid{i}", "Midfielder") for i in range(4)],
            *[_make_player(f"fwd{i}", "Forward") for i in range(2)],
        ]
        # GK1 has highest EP so both GKs would be picked without the cap
        values = {p.id: 50.0 for p in squad}
        values["gk1"] = 90.0
        values["gk2"] = 80.0  # Higher than most outfield

        best_11 = select_best_eleven(squad, values)
        gk_count = sum(1 for p in best_11 if p.position == "Goalkeeper")
        assert gk_count == 1, f"Expected 1 GK in best-11, got {gk_count}"
        assert any(p.id == "gk1" for p in best_11), "Best GK should be selected"
        assert not any(p.id == "gk2" for p in best_11), "2nd GK should NOT be in best-11"

    def test_three_gks_only_one_selected(self):
        """With 3 GKs in a 12-player squad, exactly 1 GK in best-11."""
        squad = [
            _make_player("gk1", "Goalkeeper"),
            _make_player("gk2", "Goalkeeper"),
            _make_player("gk3", "Goalkeeper"),
            *[_make_player(f"def{i}", "Defender") for i in range(3)],
            *[_make_player(f"mid{i}", "Midfielder") for i in range(3)],
            *[_make_player(f"fwd{i}", "Forward") for i in range(3)],
        ]
        values = {p.id: 40.0 for p in squad}
        values["gk1"] = 90.0
        values["gk2"] = 85.0
        values["gk3"] = 80.0

        best_11 = select_best_eleven(squad, values)
        gk_count = sum(1 for p in best_11 if p.position == "Goalkeeper")
        assert gk_count == 1

    def test_outfield_player_preferred_over_surplus_gk(self):
        """A MID with lower EP should still beat a surplus GK for a best-11 spot."""
        squad = [
            _make_player("gk1", "Goalkeeper"),
            _make_player("gk2", "Goalkeeper"),
            *[_make_player(f"def{i}", "Defender") for i in range(3)],
            *[_make_player(f"mid{i}", "Midfielder") for i in range(4)],
            *[_make_player(f"fwd{i}", "Forward") for i in range(2)],
        ]
        values = {p.id: 50.0 for p in squad}
        values["gk1"] = 90.0
        values["gk2"] = 80.0  # Higher than mid3
        values["mid3"] = 30.0  # Lowest, but should still beat surplus GK

        best_11 = select_best_eleven(squad, values)
        assert any(
            p.id == "mid3" for p in best_11
        ), "Outfield player should be preferred over surplus GK"
        assert not any(p.id == "gk2" for p in best_11)

    def test_six_defenders_only_five_in_best_11(self):
        """Max 5 DEF can start. 6th DEF should be excluded."""
        squad = [
            _make_player("gk1", "Goalkeeper"),
            *[_make_player(f"def{i}", "Defender") for i in range(6)],
            *[_make_player(f"mid{i}", "Midfielder") for i in range(3)],
            _make_player("fwd0", "Forward"),
        ]
        values = {p.id: 50.0 for p in squad}
        # All defenders have high EP
        for i in range(6):
            values[f"def{i}"] = 70.0 - i

        best_11 = select_best_eleven(squad, values)
        def_count = sum(1 for p in best_11 if p.position == "Defender")
        assert def_count == 5, f"Expected max 5 DEF, got {def_count}"


# ---------------------------------------------------------------------------
# Fieldability guard (validate_formation for flips)
# ---------------------------------------------------------------------------


class TestFieldabilityGuard:
    def test_valid_squad_with_extra_player(self):
        """A 12-player squad with 1 GK + 11 outfield → can field 11."""
        squad = [
            _make_player("gk1", "Goalkeeper"),
            *[_make_player(f"def{i}", "Defender") for i in range(4)],
            *[_make_player(f"mid{i}", "Midfielder") for i in range(5)],
            *[_make_player(f"fwd{i}", "Forward") for i in range(2)],
        ]
        result = validate_formation(squad)
        assert result["can_field_eleven"]

    def test_too_many_gk_still_fieldable(self):
        """13 players with 2 GKs + 11 outfield → can still field 11."""
        squad = [
            _make_player("gk1", "Goalkeeper"),
            _make_player("gk2", "Goalkeeper"),
            *[_make_player(f"def{i}", "Defender") for i in range(4)],
            *[_make_player(f"mid{i}", "Midfielder") for i in range(5)],
            *[_make_player(f"fwd{i}", "Forward") for i in range(2)],
        ]
        result = validate_formation(squad)
        assert result["can_field_eleven"]

    def test_not_enough_outfield_cant_field(self):
        """10 players with 3 GKs → only 7 outfield, need 10 → can't field 11."""
        squad = [
            _make_player("gk1", "Goalkeeper"),
            _make_player("gk2", "Goalkeeper"),
            _make_player("gk3", "Goalkeeper"),
            *[_make_player(f"def{i}", "Defender") for i in range(3)],
            *[_make_player(f"mid{i}", "Midfielder") for i in range(2)],
            *[_make_player(f"fwd{i}", "Forward") for i in range(2)],
        ]
        result = validate_formation(squad)
        # 10 players total but 3 GKs means the minimums are met (1 GK, 3 DEF, 2 MID, 1 FWD)
        # With 10 players >= 11 is False, so can_field_eleven would be about total count
        # Actually: 10 total, 11 needed → can_field_eleven = False
        assert not result["can_field_eleven"]


# ---------------------------------------------------------------------------
# Integration: recommend_buys with dead-weight guard
# ---------------------------------------------------------------------------


def _build_standard_squad():
    """11-player squad: 1 GK, 3 DEF, 4 MID, 3 FWD."""
    squad = []
    scores = []

    squad.append(_make_player("gk1", "Goalkeeper", market_value=3_000_000))
    scores.append(_make_score("gk1", 35.0, "Goalkeeper", market_value=3_000_000))

    for i in range(3):
        squad.append(_make_player(f"def{i}", "Defender"))
        scores.append(_make_score(f"def{i}", 40.0, "Defender"))

    for i in range(4):
        ep = 50.0 - i * 5  # 50, 45, 40, 35
        squad.append(_make_player(f"mid{i}", "Midfielder"))
        scores.append(_make_score(f"mid{i}", ep, "Midfielder"))

    for i in range(3):
        ep = 45.0 - i * 5  # 45, 40, 35
        squad.append(_make_player(f"fwd{i}", "Forward"))
        scores.append(_make_score(f"fwd{i}", ep, "Forward"))

    return squad, scores


class TestRecommendBuysDeadWeight:
    def test_gk_upgrade_gets_forced_sell_plan(self):
        """Buying a better GK when squad already has one → sell plan for old GK."""
        squad, scores = _build_standard_squad()
        squad_players = {p.id: p for p in squad}

        # Market: a GK with 55 EP (much better than current 35 EP GK)
        market_gk = _make_player("new_gk", "Goalkeeper", market_value=8_000_000)
        market_scores = [_make_score("new_gk", 55.0, "Goalkeeper", market_value=8_000_000)]
        market_players = {market_gk.id: market_gk}

        engine = DecisionEngine(min_ep_to_buy=30.0, min_ep_upgrade=5.0)
        recs = engine.recommend_buys(
            market_scores=market_scores,
            squad_scores=scores,
            roster_context={},
            budget=50_000_000,
            market_players=market_players,
            squad_players=squad_players,
        )

        # Should recommend the GK upgrade
        assert len(recs) == 1
        rec = recs[0]
        assert rec.player.id == "new_gk"
        assert rec.marginal_ep_gain > 0

        # Must have a forced sell plan targeting the old GK
        assert rec.sell_plan is not None
        sell_ids = [e.player_id for e in rec.sell_plan.players_to_sell]
        assert "gk1" in sell_ids

    def test_mid_upgrade_no_forced_sell_plan(self):
        """Buying a better MID when squad has 4 MIDs → no forced sell plan
        because max startable MIDs is 5."""
        squad, scores = _build_standard_squad()
        squad_players = {p.id: p for p in squad}

        market_mid = _make_player("new_mid", "Midfielder", market_value=8_000_000)
        market_scores = [_make_score("new_mid", 60.0, "Midfielder", market_value=8_000_000)]
        market_players = {market_mid.id: market_mid}

        engine = DecisionEngine(min_ep_to_buy=30.0, min_ep_upgrade=5.0)
        recs = engine.recommend_buys(
            market_scores=market_scores,
            squad_scores=scores,
            roster_context={},
            budget=50_000_000,
            market_players=market_players,
            squad_players=squad_players,
        )

        assert len(recs) == 1
        rec = recs[0]
        assert rec.player.id == "new_mid"
        # No forced sell plan — position not saturated
        assert rec.sell_plan is None

    def test_gk_buy_with_one_existing_gk_gets_sell_plan(self):
        """With exactly 1 GK in squad and buying a 2nd, the dead-weight guard
        fires and attaches a viable sell plan for the old GK (because buying
        the new GK means selling the old one is safe — count stays at 1)."""
        squad = [_make_player("gk1", "Goalkeeper", market_value=2_000_000)]
        scores = [_make_score("gk1", 35.0, "Goalkeeper", market_value=2_000_000)]
        squad_players = {p.id: p for p in squad}

        market_gk = _make_player("new_gk", "Goalkeeper", market_value=8_000_000)
        market_scores = [_make_score("new_gk", 55.0, "Goalkeeper", market_value=8_000_000)]
        market_players = {market_gk.id: market_gk}

        engine = DecisionEngine(min_ep_to_buy=30.0, min_ep_upgrade=5.0)
        recs = engine.recommend_buys(
            market_scores=market_scores,
            squad_scores=scores,
            roster_context={},
            budget=50_000_000,
            market_players=market_players,
            squad_players=squad_players,
            is_emergency=True,
        )

        # The GK upgrade should be recommended with a forced sell plan
        gk_rec = next((r for r in recs if r.player.id == "new_gk"), None)
        assert gk_rec is not None, "GK upgrade should be recommended"
        assert gk_rec.sell_plan is not None, "Must have forced sell plan"
        assert gk_rec.sell_plan.is_viable, "Sell plan must be viable"
        sell_ids = [e.player_id for e in gk_rec.sell_plan.players_to_sell]
        assert "gk1" in sell_ids, "Old GK should be the sell target"
