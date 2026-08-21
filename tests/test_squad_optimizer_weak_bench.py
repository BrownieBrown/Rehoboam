"""REH-83: the weak-bench sell rule must not fire on an unmeasured player.

`value_score` here is `average_points`, which is ~0 for EVERY player before a
season's first matchday. A bare `value_score < 30` therefore selects the whole
bench in week 1 -- not because those players are weak, but because the season
has not produced a statistic yet.

This is the same error REH-80 fixed in the scorer's cold-start prior: absence
of evidence treated as evidence of absence.
"""

from __future__ import annotations

from rehoboam.kickbase_client import Player
from rehoboam.squad_optimizer import SquadOptimizer

# Positive, but under the rule's EUR 2M "budget is tight" line, so the
# opportunistic branch is the one under test -- NOT the negative-budget rescue
# above it, which sells by market value and never reads this threshold.
TIGHT_BUDGET = 1_000_000
GAMEDAY_SOON = 1


def _p(pid: str, position: str, avg: float) -> Player:
    return Player(
        id=pid,
        first_name="P",
        last_name=pid,
        position=position,
        team_id="1",
        team_name="T",
        market_value=1_000_000,
        points=0,
        average_points=avg,
    )


def _squad_of_twelve(bench_avg: float, starters_avg: float = 80.0) -> list[Player]:
    """Eleven fieldable starters plus one clearly-benched extra midfielder."""
    squad = [_p("gk", "Goalkeeper", starters_avg)]
    squad += [_p(f"def{i}", "Defender", starters_avg) for i in range(4)]
    squad += [_p(f"mid{i}", "Midfielder", starters_avg) for i in range(4)]
    squad += [_p(f"fw{i}", "Forward", starters_avg) for i in range(2)]
    squad.append(_p("bench", "Midfielder", bench_avg))
    return squad


def _sell_ids(squad: list[Player]) -> set[str]:
    optimizer = SquadOptimizer(min_squad_size=11, max_squad_size=15)
    result = optimizer.optimize_squad(
        squad=squad,
        player_values={p.id: float(p.average_points or 0) for p in squad},
        current_budget=TIGHT_BUDGET,
        days_until_gameday=GAMEDAY_SOON,
    )
    return {p.id for p in result.players_to_sell}


def test_an_unmeasured_bench_player_is_not_sold_as_weak():
    """average_points == 0 means "no matches played yet", which is every player
    before matchday 1. Selling the bench then costs all injury cover in the
    week the squad is least replaceable."""
    assert "bench" not in _sell_ids(_squad_of_twelve(bench_avg=0.0))


def test_a_measured_weak_bench_player_is_still_sold():
    """The rule must keep working mid-season, which is the case it was for."""
    assert "bench" in _sell_ids(_squad_of_twelve(bench_avg=15.0))


def test_a_measured_strong_bench_player_is_not_sold():
    assert "bench" not in _sell_ids(_squad_of_twelve(bench_avg=50.0))


def test_a_whole_squad_at_season_start_keeps_its_bench():
    """The actual 2026-08-28 shape: nobody has played, so nobody has a score."""
    assert _sell_ids(_squad_of_twelve(bench_avg=0.0, starters_avg=0.0)) == set()
