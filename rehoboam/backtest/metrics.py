"""Backtest metrics.

Deliberately dependency-free — this codebase ships to an Azure Function and
does not carry numpy/scipy. Both metrics are small enough to implement and
test directly.

MAE is intentionally absent. Median per-player game-to-game standard
deviation is 54.8 points, so a perfect model of a player's true level still
scores MAE around 44. Optimising against MAE drives the model to fit noise.
Ranking is what the bot actually needs: field the right 11, buy the right
player.
"""

from __future__ import annotations

from typing import Any

from rehoboam.formation import select_best_eleven


def _average_ranks(values: list[float]) -> list[float]:
    """Ranks (1-based), with tied values sharing their average rank."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        average_rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = average_rank
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float:
    """Spearman rank correlation. Returns 0.0 when undefined.

    Undefined cases (fewer than two points, or zero variance in either
    series) return 0.0 rather than raising — a matchday where every player
    scored the same is not an error, it just carries no ranking signal.
    """
    n = len(xs)
    if n < 2 or n != len(ys):
        return 0.0

    rx, ry = _average_ranks(xs), _average_ranks(ys)
    mean_x, mean_y = sum(rx) / n, sum(ry) / n

    covariance = sum((a - mean_x) * (b - mean_y) for a, b in zip(rx, ry, strict=True))
    var_x = sum((a - mean_x) ** 2 for a in rx)
    var_y = sum((b - mean_y) ** 2 for b in ry)

    if var_x == 0 or var_y == 0:
        return 0.0
    return covariance / (var_x * var_y) ** 0.5


def lineup_regret(
    squad: list[Any], chosen_ids: list[str], actual_points: dict[str, float]
) -> float:
    """Points left on the bench: best legal 11 minus the 11 actually chosen.

    The hindsight-optimal 11 is computed by handing *actual* points to the
    existing ``select_best_eleven``, which already enforces formation
    legality — so the benchmark is always a lineup we could really have
    fielded, not an illegal all-forwards fantasy.

    Returns:
        Non-negative points. 0.0 means the choice was optimal.
    """
    best = select_best_eleven(squad, actual_points)
    best_total = sum(actual_points.get(p.id, 0.0) for p in best)
    chosen_total = sum(actual_points.get(pid, 0.0) for pid in chosen_ids)
    return best_total - chosen_total
