"""Ordering buy recommendations for a budget rather than for a single pick.

Ranking by absolute marginal gain answers "which one player helps most". With
money for several, that is the wrong question — and it cost a real opportunity
on 2026-08-24, when one EUR 59.8M signing crowded out three cheaper ones.
"""

from types import SimpleNamespace

from rehoboam.scoring.decision import plan_buys


def _player(pid, position, price):
    return SimpleNamespace(
        id=pid, last_name=pid, position=position, price=price, market_value=price
    )


def _rec(pid, position, price, ep, gain):
    return SimpleNamespace(
        player=_player(pid, position, price),
        score=SimpleNamespace(expected_points=ep, player_id=pid),
        marginal_ep_gain=gain,
    )


def _squad(n=11, ep=30.0):
    """A legal eleven of interchangeable players."""
    squad, lineup = [], {}
    spec = [("Goalkeeper", 1), ("Defender", 4), ("Midfielder", 4), ("Forward", 2)]
    i = 0
    for position, count in spec:
        for _ in range(count):
            p = _player(f"s{i}", position, 1_000_000)
            squad.append(p)
            lineup[p.id] = ep
            i += 1
    return squad[:n], lineup


class TestWhicheverPlanScoresMore:
    """Neither ordering wins by default; the one worth more points does."""

    def _weak_spots(self, n):
        """A squad with `n` weak starters, so `n` cheap signings each help."""
        squad, lineup = _squad(ep=50.0)
        for p in [x for x in squad if x.position == "Defender"][:n]:
            lineup[p.id] = 10.0
        return squad, lineup

    def test_several_cheap_upgrades_beat_one_expensive_one_when_they_add_up(self):
        """The live failure: one big buy consumed the budget and blocked three."""
        squad, lineup = self._weak_spots(3)
        big = _rec("big", "Midfielder", 59_800_000, 120.0, 70.0)
        cheap = [_rec(f"c{i}", "Defender", 4_000_000, 60.0, 50.0) for i in range(3)]
        planned = plan_buys([big, *cheap], budget=62_000_000, squad=squad, lineup_map=lineup)
        chosen = [r.player.id for r in planned[:3]]
        assert set(chosen) == {"c0", "c1", "c2"}
        assert sum(r.marginal_ep_gain for r in planned[:3]) > 70.0

    def test_one_big_signing_wins_when_the_cheap_options_do_not_add_up(self):
        """Greedy-by-ratio alone would take the cheap player and lose points."""
        squad, lineup = self._weak_spots(1)
        big = _rec("big", "Midfielder", 59_800_000, 120.0, 88.0)
        small = _rec("small", "Defender", 4_200_000, 60.0, 30.0)
        planned = plan_buys([big, small], budget=62_000_000, squad=squad, lineup_map=lineup)
        assert planned[0].player.id == "big"

    def test_both_still_fit_when_the_budget_allows(self):
        squad, lineup = _squad()
        big = _rec("big", "Midfielder", 40_000_000, 120.0, 88.0)
        small = _rec("small", "Defender", 4_000_000, 60.0, 30.0)
        planned = plan_buys([big, small], budget=60_000_000, squad=squad, lineup_map=lineup)
        assert {r.player.id for r in planned[:2]} == {"big", "small"}

    def test_an_unaffordable_candidate_is_kept_but_ranked_last(self):
        """Nothing is silently dropped — it just cannot be planned for."""
        squad, lineup = _squad()
        cheap = _rec("cheap", "Defender", 1_000_000, 60.0, 30.0)
        huge = _rec("huge", "Midfielder", 500_000_000, 200.0, 150.0)
        planned = plan_buys([cheap, huge], budget=5_000_000, squad=squad, lineup_map=lineup)
        assert planned[0].player.id == "cheap"
        assert planned[-1].player.id == "huge"


class TestGainsAreNotAdditive:
    """Each gain is measured against the current eleven, so they shrink."""

    def test_the_second_signing_is_re_measured_against_the_improved_eleven(self):
        """One weak starter, so only the FIRST signing gets to replace him."""
        squad, lineup = _squad(ep=50.0)
        weakest = next(p for p in squad if p.position == "Midfielder")
        lineup[weakest.id] = 10.0

        a = _rec("a", "Midfielder", 1_000_000, 90.0, 80.0)
        b = _rec("b", "Midfielder", 1_000_000, 80.0, 70.0)
        planned = plan_buys([a, b], budget=10_000_000, squad=squad, lineup_map=lineup)

        first = next(r for r in planned if r.player.id == "a")
        second = next(r for r in planned if r.player.id == "b")
        # a replaces the 10.0 starter (+80); b then replaces a 50.0 (+30).
        assert first.marginal_ep_gain == 80.0
        assert second.marginal_ep_gain == 30.0
        assert second.marginal_ep_gain < 70.0

    def test_a_candidate_who_no_longer_improves_the_eleven_is_dropped(self):
        """An upgrade on the old worst starter can be no upgrade on his replacement."""
        squad, lineup = _squad(ep=30.0)
        strong = _rec("strong", "Midfielder", 1_000_000, 100.0, 70.0)
        weak = _rec("weak", "Midfielder", 1_000_000, 30.0, 0.1)
        planned = plan_buys([strong, weak], budget=10_000_000, squad=squad, lineup_map=lineup)
        assert planned[0].player.id == "strong"
        assert planned[0].marginal_ep_gain > 0
        # `weak` matches the worst starter exactly, so it adds nothing.
        assert next(r for r in planned if r.player.id == "weak") is planned[-1]


class TestEdges:
    def test_no_candidates_returns_empty(self):
        squad, lineup = _squad()
        assert plan_buys([], budget=10_000_000, squad=squad, lineup_map=lineup) == []

    def test_no_squad_returns_the_input_ranked_rather_than_crashing(self):
        r = _rec("a", "Midfielder", 1_000_000, 90.0, 60.0)
        assert plan_buys([r], budget=10_000_000, squad=[], lineup_map={}) == [r]

    def test_zero_budget_plans_nothing_but_keeps_the_candidates(self):
        squad, lineup = _squad()
        r = _rec("a", "Midfielder", 1_000_000, 90.0, 60.0)
        assert plan_buys([r], budget=0, squad=squad, lineup_map=lineup) == [r]

    def test_a_priceless_candidate_is_not_planned(self):
        """Cost 0 means unknown, not free."""
        squad, lineup = _squad()
        r = _rec("a", "Midfielder", 0, 90.0, 60.0)
        assert plan_buys([r], budget=10_000_000, squad=squad, lineup_map=lineup) == [r]
