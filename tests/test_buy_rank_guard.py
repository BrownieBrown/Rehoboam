"""The buy side refuses the players the rank rule would sell.

Between 2026-10-12 and 10-24 the bot bought Amoako, Seiwald, Džeko and Diks as
"upgrades" over its weakest starter (Seiwald: EP 44 against Yilmaz's 3.9) and
the rank rule sold each of them one to eleven days later, outside the top 30
at his position on both measures (Amoako 108/76, Seiwald 94/80, Džeko 66/32,
Diks 97/187) — a round trip that only paid the premium. Both sides now read
one predicate, `rank_floor_verdict`, with one floor.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from rehoboam.services.rank_sell import (
    RankSellInput,
    buy_rank_refusals,
    rank_floor_verdict,
    rank_sell_reason,
)

FLOOR = 30
MIN_APPS = 2


def _row(avg, ep, apps=5):
    return {"avg_points_rank_pos": avg, "ep_rank_pos": ep, "appearances": apps}


class TestTheSharedVerdict:
    def test_outside_on_both_measures_is_the_verdict(self):
        assert rank_floor_verdict(
            status=None,
            avg_points_rank_pos=94,
            ep_rank_pos=80,
            appearances=5,
            floor=FLOOR,
            min_appearances=MIN_APPS,
        ).startswith("Outside the top 30")

    @pytest.mark.parametrize(("avg", "ep"), [(14, 181), (44, 7), (30, 30)])
    def test_inside_on_either_measure_is_a_top_player(self, avg, ep):
        assert (
            rank_floor_verdict(
                status=None,
                avg_points_rank_pos=avg,
                ep_rank_pos=ep,
                appearances=5,
                floor=FLOOR,
                min_appearances=MIN_APPS,
            )
            is None
        )

    @pytest.mark.parametrize(
        ("avg", "ep", "apps"), [(None, 80, 5), (94, None, 5), (94, 80, 1), (94, 80, None)]
    )
    def test_unknown_quality_is_not_bad_quality(self, avg, ep, apps):
        assert (
            rank_floor_verdict(
                status=None,
                avg_points_rank_pos=avg,
                ep_rank_pos=ep,
                appearances=apps,
                floor=FLOOR,
                min_appearances=MIN_APPS,
            )
            is None
        )

    def test_out_for_weeks_whatever_the_rank(self):
        assert rank_floor_verdict(
            status=1,
            avg_points_rank_pos=3,
            ep_rank_pos=3,
            appearances=9,
            floor=FLOOR,
            min_appearances=MIN_APPS,
        ).startswith("Out for weeks")

    def test_the_sell_rule_still_adds_its_timing_guards(self):
        def held(trend_7d_pct):
            return RankSellInput(
                status=None,
                avg_points_rank_pos=94,
                ep_rank_pos=80,
                appearances=5,
                trend_7d_pct=trend_7d_pct,
                in_best_eleven=False,
                days_until_match=6,
            )

        def reason(inp):
            return rank_sell_reason(
                inp, floor=FLOOR, min_appearances=MIN_APPS, min_days_for_starter=3
            )

        assert reason(held(2.0)) is None  # rising: the sale waits
        assert reason(held(-2.0)).startswith("Outside the top 30")


class TestTheBuyRefusals:
    def test_the_october_round_trips_are_refused(self):
        players = {
            "amoako": SimpleNamespace(status=None),
            "seiwald": SimpleNamespace(status=None),
            "top": SimpleNamespace(status=None),
            "new": SimpleNamespace(status=None),
        }
        ranks = {
            "amoako": _row(108, 76, apps=2),
            "seiwald": _row(94, 80),
            "top": _row(12, 90),
            "new": _row(None, 200, apps=0),
            # "unranked" has no row at all
        }
        refused = buy_rank_refusals(players, ranks, floor=FLOOR, min_appearances=MIN_APPS)
        assert set(refused) == {"amoako", "seiwald"}

    def test_an_injured_listing_is_refused_without_a_rank_row(self):
        players = {"hurt": SimpleNamespace(status=1)}
        refused = buy_rank_refusals(players, {}, floor=FLOOR, min_appearances=MIN_APPS)
        assert refused["hurt"].startswith("Out for weeks")


def _trader(settings_overrides=None):
    from rehoboam.config import Settings
    from rehoboam.trader import Trader

    settings = Settings(
        KICKBASE_EMAIL="t@e.com", KICKBASE_PASSWORD="x", **(settings_overrides or {})
    )
    with (
        patch.object(Trader, "_load_curves", return_value=(None, None, None)),
        patch.object(Trader, "_load_forecasts", return_value={}),
    ):
        return Trader(api=MagicMock(), settings=settings)


class TestTheTraderGuard:
    PLAYERS = {"seiwald": SimpleNamespace(status=None)}

    def test_reads_the_store_and_refuses(self):
        trader = _trader()
        with patch("rehoboam.store.league_store.LeagueStore") as store:
            store.return_value.position_ranks.return_value = {"seiwald": _row(94, 80)}
            assert set(trader._rank_refusals(self.PLAYERS, exempt=False)) == {"seiwald"}

    def test_an_urgent_squad_is_exempt(self):
        trader = _trader()
        with patch("rehoboam.store.league_store.LeagueStore") as store:
            store.return_value.position_ranks.return_value = {"seiwald": _row(94, 80)}
            assert trader._rank_refusals(self.PLAYERS, exempt=True) == {}
            store.assert_not_called()

    def test_a_failing_store_refuses_nobody(self):
        trader = _trader()
        with patch("rehoboam.store.league_store.LeagueStore") as store:
            store.return_value.position_ranks.side_effect = RuntimeError("pooler down")
            assert trader._rank_refusals(self.PLAYERS, exempt=False) == {}

    @pytest.mark.parametrize("flag", ["BUY_RANK_GUARD_ENABLED", "SELL_RANK_ENABLED"])
    def test_off_when_either_switch_is_off(self, flag, monkeypatch):
        monkeypatch.setenv(flag, "false")
        trader = _trader()
        with patch("rehoboam.store.league_store.LeagueStore") as store:
            store.return_value.position_ranks.return_value = {"seiwald": _row(94, 80)}
            assert trader._rank_refusals(self.PLAYERS, exempt=False) == {}
