"""The Top-5 rule: finishing well costs you a player.

Nth place must give up one of his N best performers from that matchday.
First place has no choice at all; fifth has the most.
"""

from rehoboam.top5 import (
    ForcedSale,
    choose_least_damaging,
    eligible_pool,
    forced_sale,
    matchday_place,
)


def _standings(*pairs):
    """(manager_id, matchday_points), in any order."""
    return [{"i": i, "sp": p} for i, p in pairs]


class TestFindingOurPlace:
    def test_it_ranks_by_matchday_points(self):
        s = _standings(("a", 500), ("me", 900), ("b", 700))
        assert matchday_place(s, "me") == 1
        assert matchday_place(s, "b") == 2
        assert matchday_place(s, "a") == 3

    def test_a_manager_not_in_the_standings_has_no_place(self):
        assert matchday_place(_standings(("a", 500)), "me") is None

    def test_missing_points_count_as_zero_rather_than_crashing(self):
        s = [{"i": "me"}, {"i": "a", "sp": 100}]
        assert matchday_place(s, "me") == 2


class TestTheEligiblePool:
    """Nth place may sell any of his top N."""

    def _points(self):
        return {"p1": 100.0, "p2": 90.0, "p3": 80.0, "p4": 70.0, "p5": 60.0, "p6": 50.0}

    def test_first_place_has_a_pool_of_exactly_his_best_scorer(self):
        assert eligible_pool(1, self._points()) == ["p1"]

    def test_third_place_may_choose_from_his_top_three(self):
        assert eligible_pool(3, self._points()) == ["p1", "p2", "p3"]

    def test_fifth_place_may_choose_from_his_top_five(self):
        assert eligible_pool(5, self._points()) == ["p1", "p2", "p3", "p4", "p5"]

    def test_sixth_place_owes_nothing(self):
        assert eligible_pool(6, self._points()) == []

    def test_a_pool_cannot_exceed_the_squad(self):
        assert eligible_pool(5, {"p1": 10.0, "p2": 5.0}) == ["p1", "p2"]


class TestChoosingWhoToLose:
    """Selected on FUTURE value, not on what he scored that matchday."""

    def test_it_gives_up_the_lowest_expected_points_not_the_lowest_scorer(self):
        pool = ["spike", "steady"]
        # `spike` scored more that day but is worth far less from here on.
        chosen, _ = choose_least_damaging(pool, {"spike": 20.0, "steady": 80.0})
        assert chosen == "spike"

    def test_first_place_gets_no_choice_and_the_reason_says_so(self):
        chosen, reason = choose_least_damaging(["only"], {"only": 90.0})
        assert chosen == "only"
        assert "no choice" in reason

    def test_the_reason_compares_against_the_best_player_kept(self):
        _, reason = choose_least_damaging(["a", "b", "c"], {"a": 10.0, "b": 80.0, "c": 50.0})
        assert "10.0" in reason and "80.0" in reason

    def test_an_unscored_player_is_given_up_before_a_scored_one(self):
        """We should not keep a player we cannot evaluate over one we can."""
        chosen, _ = choose_least_damaging(["known", "unknown"], {"known": 40.0})
        assert chosen == "unknown"

    def test_an_empty_pool_yields_no_decision(self):
        assert choose_least_damaging([], {"a": 1.0}) is None


class TestTheWholeRule:
    def _args(self, my_points, **over):
        base = {
            "standings": _standings(("me", 900), ("a", 800), ("b", 700)),
            "my_id": "me",
            "matchday_points": my_points,
            "forward_ep": {"star": 95.0, "spike": 12.0, "solid": 60.0},
            "names": {"star": "Star", "spike": "Spike", "solid": "Solid"},
        }
        base.update(over)
        return base

    def test_winning_forces_the_sale_of_the_best_scorer(self):
        sale = forced_sale(**self._args({"spike": 120.0, "star": 90.0, "solid": 80.0}))
        assert sale.place == 1
        assert sale.chosen == "spike"
        assert sale.had_a_choice is False

    def test_third_place_picks_the_least_valuable_of_three(self):
        sale = forced_sale(
            **self._args(
                {"star": 120.0, "solid": 110.0, "spike": 100.0, "other": 10.0},
                standings=_standings(("a", 999), ("b", 950), ("me", 900)),
            )
        )
        assert sale.place == 3
        assert sale.pool == ["star", "solid", "spike"]
        assert sale.chosen == "spike"
        assert sale.had_a_choice is True

    def test_finishing_sixth_owes_nothing(self):
        standings = _standings(*[(f"m{i}", 1000 - i) for i in range(5)], ("me", 100))
        assert forced_sale(**self._args({"star": 50.0}, standings=standings)) is None

    def test_being_absent_from_the_standings_owes_nothing(self):
        assert forced_sale(**self._args({"star": 50.0}, my_id="ghost")) is None

    def test_no_player_points_yields_no_sale_rather_than_a_wrong_one(self):
        assert forced_sale(**self._args({})) is None

    def test_the_result_names_the_player_for_a_human_to_read(self):
        sale = forced_sale(**self._args({"spike": 120.0}))
        assert isinstance(sale, ForcedSale)
        assert sale.chosen_name == "Spike"


class TestSettling:
    """The adapter: fetch, decide, sell once, remember."""

    def _api(self, standings, points_by_player, user_id="me"):
        from unittest.mock import MagicMock

        api = MagicMock()
        api.user.id = user_id
        api.client.BASE_URL = "https://x"
        api.client.session.get.return_value.json.return_value = {"us": standings}

        def _perf(league_id, player_id):
            pts = points_by_player.get(str(player_id))
            if pts is None:
                return {"it": []}
            return {"it": [{"ph": [{"day": 1, "st": 5, "p": pts}]}]}

        api.client.get_player_performance.side_effect = _perf
        return api

    def _squad(self):
        from types import SimpleNamespace

        return [SimpleNamespace(id=p, last_name=p.title()) for p in ("spike", "star", "solid")]

    def _learner(self, tmp_path):
        from rehoboam.bid_learner import BidLearner

        return BidLearner(db_path=tmp_path / "b.db")

    def test_it_sells_the_least_damaging_eligible_player(self, tmp_path):
        from types import SimpleNamespace

        from rehoboam.top5 import settle

        api = self._api(_standings(("me", 900), ("a", 800)), {"spike": 120, "star": 90})
        learner = self._learner(tmp_path)
        sale = settle(
            api=api,
            league=SimpleNamespace(id="L"),
            learner=learner,
            squad=self._squad(),
            forward_ep={"spike": 12.0, "star": 95.0},
            matchday=1,
        )
        assert sale.place == 1
        assert sale.chosen == "spike"
        api.sell_player_instant.assert_called_once()

    def test_a_second_run_for_the_same_matchday_sells_nothing(self, tmp_path):
        """A re-run must not compound the obligation."""
        from types import SimpleNamespace

        from rehoboam.top5 import settle

        learner = self._learner(tmp_path)
        args = {
            "league": SimpleNamespace(id="L"),
            "learner": learner,
            "squad": self._squad(),
            "forward_ep": {"spike": 12.0, "star": 95.0},
            "matchday": 1,
        }
        api1 = self._api(_standings(("me", 900), ("a", 800)), {"spike": 120, "star": 90})
        settle(api=api1, **args)
        api2 = self._api(_standings(("me", 900), ("a", 800)), {"spike": 120, "star": 90})
        assert settle(api=api2, **args) is None
        api2.sell_player_instant.assert_not_called()

    def test_a_dry_run_decides_but_does_not_sell(self, tmp_path):
        from types import SimpleNamespace

        from rehoboam.top5 import settle

        api = self._api(_standings(("me", 900), ("a", 800)), {"spike": 120})
        sale = settle(
            api=api,
            league=SimpleNamespace(id="L"),
            learner=self._learner(tmp_path),
            squad=self._squad(),
            forward_ep={"spike": 12.0},
            matchday=1,
            dry_run=True,
        )
        assert sale is not None
        api.sell_player_instant.assert_not_called()

    def test_finishing_outside_the_top_five_sells_nothing(self, tmp_path):
        from types import SimpleNamespace

        from rehoboam.top5 import settle

        standings = _standings(*[(f"m{i}", 1000 - i) for i in range(6)], ("me", 1))
        api = self._api(standings, {"spike": 120})
        assert (
            settle(
                api=api,
                league=SimpleNamespace(id="L"),
                learner=self._learner(tmp_path),
                squad=self._squad(),
                forward_ep={"spike": 12.0},
                matchday=1,
            )
            is None
        )
        api.sell_player_instant.assert_not_called()

    def test_a_player_who_did_not_play_is_not_in_the_pool(self, tmp_path):
        """An unused sub has no matchday score to be 'best' at."""
        from types import SimpleNamespace

        from rehoboam.top5 import settle

        api = self._api(_standings(("me", 900), ("a", 800)), {"star": 90})
        sale = settle(
            api=api,
            league=SimpleNamespace(id="L"),
            learner=self._learner(tmp_path),
            squad=self._squad(),
            forward_ep={"spike": 1.0, "star": 95.0},
            matchday=1,
        )
        assert sale.pool == ["star"]
        assert sale.chosen == "star"
