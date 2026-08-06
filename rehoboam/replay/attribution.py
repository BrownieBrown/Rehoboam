"""Turn a replayed season into an honest, self-caveating report.

The output is an attribution table, not a verdict. Buy-side gains carry an
explicit optimism warning: the replay models no bid competition.
"""

from __future__ import annotations

from dataclasses import dataclass

from rehoboam.replay.engine import SeasonResult

FIDELITY_NOTES = [
    ("Points scoring", "exact", "real per-match points from the corpus"),
    ("Penalty avoidance", "exact", "deterministic"),
    ("Lineup selection", "high", "real squad, real formation rules"),
    ("Sell decisions", "medium", "instant sell at MV; profit flips per --with-flips"),
    ("Buy prices", "high", "real transaction prices"),
    ("Buy availability", "medium", "only players who actually traded are visible"),
    ("Bid competition", "see footer", "absent unless --with-competition"),
]


@dataclass(frozen=True)
class LeagueStanding:
    manager_id: str
    name: str
    total_points: int


def place_in_league(simulated_total: int, standings: list[LeagueStanding]) -> int:
    """1-indexed finishing position. Ties do not overtake the real manager."""
    ahead = sum(1 for s in standings if s.total_points >= simulated_total)
    return ahead + 1


# The real 2025/26 season, for comparison. Without a reference the replay's own
# P&L is uninterpretable.
REAL_FLIP_PNL = -55_256_064
REAL_FLIP_TRIPS = 151
REAL_FLIP_WIN_RATE = 27.8


def trading_summary(result: SeasonResult) -> tuple[int, int, int]:
    """``(realised_pnl, round_trips, wins)`` over completed round trips."""
    pnl = sum(f.proceeds - f.buy_price for f in result.flips)
    wins = sum(1 for f in result.flips if f.proceeds > f.buy_price)
    return pnl, len(result.flips), wins


def attribution_rows(
    result: SeasonResult,
    *,
    actual_total: int,
    actual_per_matchday: dict[int, int],
) -> list[tuple[str, int, str]]:
    """Decompose simulated minus actual into labelled sources."""
    zero_recovered = sum(
        o.points_scored
        for o in result.outcomes
        if actual_per_matchday.get(o.day_number, 0) == 0 and not o.zeroed
    )
    penalties = sum(o.penalty for o in result.outcomes)
    delta = result.total_points - actual_total
    other = delta - zero_recovered

    return [
        ("Zero-point matchdays avoided", zero_recovered, "exact"),
        ("Empty-slot penalties incurred", penalties, "exact"),
        ("Better squad and lineup", other, "medium - buy side is optimistic"),
        ("TOTAL vs actual", delta, "mixed"),
    ]


def format_report(
    result: SeasonResult,
    *,
    actual_total: int,
    actual_per_matchday: dict[int, int],
    standings: list[LeagueStanding],
    min_ep_gain: float,
    with_competition: bool = False,
    with_flips: bool = False,
    with_flip_buys: bool = False,
) -> str:
    """Human-readable replay report with fidelity caveats attached.

    ``min_ep_gain`` is required rather than defaulted: the headline number is a
    function of it, and REH-51 published a result without recording which floor
    produced it. A report that cannot be interpreted later is not a report.
    """
    place = place_in_league(result.total_points, standings)
    total_managers = len(standings) + 1
    lines = [
        "=" * 68,
        "FULL-BOT SEASON REPLAY - 2025/2026",
        "=" * 68,
        "",
        f"Simulated total:  {result.total_points:>8,} points",
        f"Actual total:     {actual_total:>8,} points",
        f"Difference:       {result.total_points - actual_total:>+8,} points",
        "",
        f"FINISHING POSITION: {place} of {total_managers}",
        "",
        "Attribution",
        "-" * 68,
    ]
    for label, points, fidelity in attribution_rows(
        result, actual_total=actual_total, actual_per_matchday=actual_per_matchday
    ):
        lines.append(f"  {label:<34}{points:>+9,}   {fidelity}")

    lines += ["", "Configuration", "-" * 68]
    lines.append(
        f"  {'Marginal EP gain floor':<34}{min_ep_gain:>9,.1f}   real points, "
        "buys below this are skipped"
    )

    if with_flips or with_flip_buys:
        pnl, trips, wins = trading_summary(result)
        rate = (100.0 * wins / trips) if trips else 0.0
        lines += [
            "",
            "Trading (cash - does not enter the points attribution above)",
            "-" * 68,
            f"  {'Realised flip P&L':<34}{'EUR ' + format(pnl, '+,'):>21}",
            f"  {'Round trips completed':<34}{trips:>21,}",
            f"  {'Win rate':<34}{rate:>20.1f}%   ({wins} of {trips})",
            f"  {'Real 2025/26, for comparison':<34}" f"{'EUR ' + format(REAL_FLIP_PNL, '+,'):>21}",
            f"  {'':<34}{REAL_FLIP_TRIPS:>21,} trips, {REAL_FLIP_WIN_RATE}%",
        ]

    lines += ["", "Fidelity", "-" * 68]
    for component, level, basis in FIDELITY_NOTES:
        lines.append(f"  {component:<20}{level:<22}{basis}")

    zeroed = [o.day_number for o in result.outcomes if o.zeroed]
    lines += [
        "",
        f"Matchdays zeroed by negative budget: {zeroed or 'none'}",
        f"Total buys: {sum(o.buys for o in result.outcomes)}   "
        f"Total sells: {sum(o.sells for o in result.outcomes)}",
        f"Final budget: EUR {result.final_budget:,}",
        "",
    ]
    if with_competition:
        lines += [
            "Bid competition IS modelled: a listing is won only by bidding above",
            "what the real buyer paid, and the winning bid is what we pay.",
        ]
    else:
        lines += [
            "This models no bid competition: any listed player the bot wanted, it got.",
            "Treat the buy-side contribution as an upper bound.",
        ]
    if with_flips:
        lines += [
            "Profit flipping IS modelled: take profit / cut loss against the",
            "cost basis. Real flipping lost EUR 55.3M over 151 flips, so this is",
            "expected to LOWER the result, not raise it.",
        ]
    else:
        lines += [
            "Profit flipping is NOT modelled: the bot sells only to make room or",
            "to restore solvency, while the live bot also trades for gain.",
        ]
    if with_flip_buys:
        lines += [
            "Flip BUYING is modelled: candidates come from the real ProfitTrader,",
            "bid at an economic ceiling rather than by marginal EP gain.",
        ]
    else:
        lines += [
            "Flip BUYING is NOT modelled: every buy here is justified by expected",
            "points, while the live bot also buys purely for appreciation.",
        ]
    if not (with_competition and with_flips and with_flip_buys):
        lines += ["INCOMPLETE - diagnostic only, not a season result."]
    lines += [
        "=" * 68,
    ]
    return "\n".join(lines)
