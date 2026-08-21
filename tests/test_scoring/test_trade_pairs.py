"""Tests for DecisionEngine.build_trade_pairs.

Trade pairs must be valued the same way plain buys are: by how much the
sell->buy swap moves the *best-11 total*, not by the two players' raw EP
difference. A raw difference credits the full EP of a bench player who was
contributing nothing, which is how a backup-keeper-for-backup-keeper swap
came to be scored +52 EP against a squad that already had a 93 EP starter.
"""

from rehoboam.kickbase_client import MarketPlayer
from rehoboam.scoring.decision import DecisionEngine
from rehoboam.scoring.models import DataQuality, PlayerScore


def _make_score(player_id, ep, position="Midfielder", price=5_000_000):
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
        consistency_bonus=0.0,
        lineup_bonus=0.0,
        fixture_bonus=0.0,
        form_bonus=0.0,
        minutes_bonus=0.0,
        dgw_multiplier=1.0,
        is_dgw=False,
        next_opponent=None,
        notes=[],
        current_price=price,
        market_value=price,
        position=position,
    )


def _make_player(player_id, position, price=5_000_000):
    return MarketPlayer(
        id=player_id,
        first_name="Test",
        last_name=player_id,
        position=position,
        team_id="t1",
        team_name="Test FC",
        price=price,
        market_value=price,
        points=100,
        average_points=12.0,
        status=0,
    )


def _squad():
    """A realistic 15-man squad: 2 GK, 5 DEF, 5 MID, 3 FW.

    Mirrors the live 2026-08-21 squad shape — one strong keeper plus a
    worthless backup, which is the configuration that exposed the bug.
    """
    spec = [
        ("gk_star", "Goalkeeper", 93.0),
        ("gk_bench", "Goalkeeper", 0.0),
        ("def1", "Defender", 73.0),
        ("def2", "Defender", 69.0),
        ("def3", "Defender", 36.0),
        ("def4", "Defender", 31.0),
        ("def5", "Defender", 26.0),
        ("mid1", "Midfielder", 101.0),
        ("mid2", "Midfielder", 83.0),
        ("mid3", "Midfielder", 58.0),
        ("mid4", "Midfielder", 34.0),
        ("mid5", "Midfielder", 5.0),
        ("fw1", "Forward", 46.0),
        ("fw2", "Forward", 23.0),
        ("fw3", "Forward", 4.0),
    ]
    players = {pid: _make_player(pid, pos) for pid, pos, _ in spec}
    scores = [_make_score(pid, ep, pos) for pid, pos, ep in spec]
    return players, scores


def _best_11_total(engine, players, scores):
    from rehoboam.formation import select_best_eleven

    score_map = {s.player_id: s.expected_points for s in scores}
    best = select_best_eleven(list(players.values()), score_map)
    return sum(score_map.get(p.id, 0.0) for p in best)


class TestTradePairMarginalEP:
    def test_backup_keeper_swap_is_not_a_52_point_upgrade(self):
        """A bench GK swap cannot improve the best 11 — only one GK ever starts.

        This is the live 2026-08-21 regression: the plain-buy ranker scored
        this same player at marginal 4.6 and skipped him, while the trade
        path scored him +52.2 and bought him for EUR 6,631,597.
        """
        engine = DecisionEngine(min_ep_to_buy=35.0, min_ep_upgrade=40.0)
        squad_players, squad_scores = _squad()

        new_gk = _make_player("gk_new", "Goalkeeper", price=6_000_000)
        new_gk_score = _make_score("gk_new", 52.0, "Goalkeeper", price=6_000_000)

        pairs = engine.build_trade_pairs(
            market_scores=[new_gk_score],
            squad_scores=squad_scores,
            roster_context={},
            budget=80_000_000,
            market_players={"gk_new": new_gk},
            squad_players=squad_players,
        )

        assert pairs == [], (
            "a second keeper cannot enter the best 11, so this swap is worth "
            f"~0 EP, but build_trade_pairs produced: "
            f"{[(p.sell_player.id, p.buy_player.id, p.ep_gain) for p in pairs]}"
        )

    def test_genuine_upgrade_still_produces_a_pair(self):
        """Guard against over-correcting: real upgrades must survive."""
        engine = DecisionEngine(min_ep_to_buy=35.0, min_ep_upgrade=40.0)
        squad_players, squad_scores = _squad()

        star = _make_player("mid_star", "Midfielder", price=20_000_000)
        star_score = _make_score("mid_star", 120.0, "Midfielder", price=20_000_000)

        pairs = engine.build_trade_pairs(
            market_scores=[star_score],
            squad_scores=squad_scores,
            roster_context={},
            budget=80_000_000,
            market_players={"mid_star": star},
            squad_players=squad_players,
        )

        assert len(pairs) == 1, "a 120 EP midfielder is a real upgrade"
        pair = pairs[0]

        # The gain must equal the actual best-11 delta of the whole swap.
        before = _best_11_total(engine, squad_players, squad_scores)
        after_players = {k: v for k, v in squad_players.items() if k != pair.sell_player.id}
        after_players["mid_star"] = star
        after_scores = [s for s in squad_scores if s.player_id != pair.sell_player.id] + [
            star_score
        ]
        after = _best_11_total(engine, after_players, after_scores)

        assert (
            pair.ep_gain == round(after - before, 4) or abs(pair.ep_gain - (after - before)) < 0.01
        ), f"ep_gain {pair.ep_gain} != best-11 delta {after - before}"

    def test_selling_a_starter_charges_for_the_hole_it_leaves(self):
        """Selling a starter must be measured against the squad that keeps him."""
        engine = DecisionEngine(min_ep_to_buy=35.0, min_ep_upgrade=40.0)
        squad_players, squad_scores = _squad()

        # A forward-only squad slice: every bench player is already used up,
        # so the only sell target for a forward is the starting fw1 (46.0).
        buy = _make_player("fw_new", "Forward", price=9_000_000)
        buy_score = _make_score("fw_new", 95.0, "Forward", price=9_000_000)

        pairs = engine.build_trade_pairs(
            market_scores=[buy_score],
            squad_scores=squad_scores,
            roster_context={},
            budget=80_000_000,
            market_players={"fw_new": buy},
            squad_players=squad_players,
        )

        for pair in pairs:
            before = _best_11_total(engine, squad_players, squad_scores)
            after_players = {k: v for k, v in squad_players.items() if k != pair.sell_player.id}
            after_players[pair.buy_player.id] = pair.buy_player
            after_scores = [s for s in squad_scores if s.player_id != pair.sell_player.id] + [
                buy_score
            ]
            after = _best_11_total(engine, after_players, after_scores)
            assert (
                abs(pair.ep_gain - (after - before)) < 0.01
            ), f"ep_gain {pair.ep_gain} != best-11 delta {after - before}"


class TestStarterSellPremium:
    """Selling a starter must clear 2x the normal EP threshold.

    Not because the EP math needs correcting -- trade_ep_gain prices the
    starter's lost contribution properly -- but because the sell executes
    immediately while the buy is only a bid, so a lost auction on a starter
    swap leaves a hole in the real starting 11. See the rationale comment in
    DecisionEngine.build_trade_pairs.

    The squad below has no bench defenders, so once the four bench players
    (gk_bench, fw3, mid5, fw2) are consumed by earlier candidates, a fifth
    defender candidate has only a starting defender left to sell.
    """

    @staticmethod
    def _defender_market(eps):
        scores, players = [], {}
        for i, ep in enumerate(eps):
            pid = f"buy{i}"
            players[pid] = _make_player(pid, "Defender", price=5_000_000)
            scores.append(_make_score(pid, ep, "Defender", price=5_000_000))
        return scores, players

    def test_starter_swap_below_2x_is_rejected(self):
        """+49 EP clears the normal 40 threshold but not the 80 starter bar."""
        engine = DecisionEngine(min_ep_to_buy=35.0, min_ep_upgrade=40.0)
        squad_players, squad_scores = _squad()
        market_scores, market_players = self._defender_market([75.0] * 5)

        pairs = engine.build_trade_pairs(
            market_scores=market_scores,
            squad_scores=squad_scores,
            roster_context={},
            budget=80_000_000,
            market_players=market_players,
            squad_players=squad_players,
        )

        # Four bench targets exist; the fifth candidate would have to sell a
        # starter for +49, which is under the 2x bar.
        assert len(pairs) == 4, [(p.sell_player.id, p.buy_player.id, p.ep_gain) for p in pairs]
        best_11_ids = {
            "gk_star",
            "def1",
            "def2",
            "def3",
            "def4",
            "def5",
            "mid1",
            "mid2",
            "mid3",
            "mid4",
            "fw1",
        }
        assert all(p.sell_player.id not in best_11_ids for p in pairs)

    def test_starter_swap_above_2x_is_allowed(self):
        """A big enough upgrade still justifies breaking up the starting 11."""
        engine = DecisionEngine(min_ep_to_buy=35.0, min_ep_upgrade=40.0)
        squad_players, squad_scores = _squad()
        # First four outrank the fifth, so they claim the bench targets and
        # the fifth (+94) falls through to the starter branch.
        market_scores, market_players = self._defender_market([130.0, 130.0, 130.0, 130.0, 120.0])

        pairs = engine.build_trade_pairs(
            market_scores=market_scores,
            squad_scores=squad_scores,
            roster_context={},
            budget=80_000_000,
            market_players=market_players,
            squad_players=squad_players,
        )

        assert len(pairs) == 5, [(p.sell_player.id, p.buy_player.id, p.ep_gain) for p in pairs]
        starter_swaps = [p for p in pairs if p.sell_player.id == "def5"]
        assert len(starter_swaps) == 1, "the fifth candidate should sell a starter"
        assert starter_swaps[0].ep_gain >= 80.0
