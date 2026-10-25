"""Step 5's forced sell repays debt only when nothing else will.

On Saturday 2026-10-17, six days before the next kickoff, the wallet sat at
-9.5m and the bench could not cover it, so `optimize_and_execute_squad` sold
the bench anyway: Maloney at 08:01 (bought the day before) and Hack at 20:01
(bought that morning), both at a loss. Marco's rule is "it can go into minus
until gameday, always", and the locked window already repays the wallet with
the smallest-sufficient-sacrifice planner (`_run_debt_recovery`). The forced
sell is now the safety net for the one case that planner cannot see: an
unknown schedule.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from rehoboam.auto_trader import AutoTrader
from rehoboam.config import Settings
from rehoboam.kickbase_client import Player
from rehoboam.services.budget import forced_sell_due
from rehoboam.squad_optimizer import SquadOptimization
from rehoboam.trader import Trader

LEAGUE = SimpleNamespace(id="1933872", name="PUMARUDEL")


class TestForcedSellDue:
    @pytest.mark.parametrize(
        ("days", "due"),
        [
            (None, True),  # no schedule: the locked window cannot be seen
            (6, False),  # Saturday 2026-10-17
            (2, False),  # moderate: debt is still allowed
            (1, False),  # locked: `_run_debt_recovery` owns this window
            (0, False),
        ],
    )
    def test_only_an_unknown_schedule_forces_a_sale(self, days, due):
        assert forced_sell_due(days) is due


def _bench_player() -> Player:
    return Player(
        id="hack",
        first_name="Robin",
        last_name="Hack",
        position="Midfielder",
        team_id="1",
        team_name="T",
        market_value=4_525_737,
        points=0,
        average_points=20.0,
        buy_price=5_515_253,
    )


def _optimization(days: int | None) -> SquadOptimization:
    bench = _bench_player()
    return SquadOptimization(
        best_eleven=[],
        bench_players=[bench],
        players_to_sell=[bench],
        current_budget=-9_493_523,
        projected_budget=-4_967_786,
        total_squad_value=0,
        best_eleven_value=0,
        bench_value=bench.market_value,
        is_gameday_ready=False,
        days_until_gameday=days,
        recommendations=[],
    )


def _run(days: int | None, tmp_path, monkeypatch) -> MagicMock:
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "test")
    monkeypatch.chdir(tmp_path)

    api = MagicMock()
    api.get_squad.return_value = [_bench_player()]
    api.sell_player_instant.return_value = {"ok": True}
    trader = AutoTrader(api=api, settings=Settings(), dry_run=False)

    with patch.object(Trader, "optimize_squad_for_gameday", return_value=_optimization(days)):
        trader.optimize_and_execute_squad(LEAGUE)
    return api


def test_six_days_out_the_bench_is_kept(tmp_path, monkeypatch):
    api = _run(6, tmp_path, monkeypatch)
    api.sell_player_instant.assert_not_called()


def test_unknown_schedule_still_sells(tmp_path, monkeypatch):
    api = _run(None, tmp_path, monkeypatch)
    assert api.sell_player_instant.call_count == 1
