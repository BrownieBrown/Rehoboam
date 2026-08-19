"""REH-75: the plain-text report `diagnose-flips` prints.

Layout follows the task-5 brief's ordered content contract: population
header, horizon sweep, headline mechanism, per-branch table (preceded by the
label-semantics sentence verbatim), temporal split, and the floor group
reported separately. `format_report` computes nothing new — every number
comes from `flip_diagnosis`'s pure aggregation functions applied to a
`DiagnosisResult` that `run_diagnosis` already built.
"""

from __future__ import annotations

from datetime import datetime, timezone

from rehoboam.diagnostics.flip_diagnosis import (
    FLOOR_PRICE,
    HEADLINE_HORIZON,
    TEMPORAL_BOUNDARY_ISO,
    DiagnosisResult,
    dominant_mechanism,
    temporal_split,
    totals_by_branch,
    totals_by_horizon,
)

# Asserted verbatim by tests/test_diagnostics/test_flip_report.py — do not
# reword. "flip-eligible at buy time" is the only claim a branch label can
# support; whether the flip path actually bought the player is a provenance
# question this data cannot answer before 2026-01-03. See the design doc's
# "Branch labelling" section.
LABEL_SEMANTICS = (
    "Branch labels mean flip-eligible at buy time. They do not mean the flip "
    "path bought the player — provenance is unrecorded before 2026-01-03."
)

_RULE = "-" * 72
_DRULE = "=" * 72


def _eur(n: int) -> str:
    return "EUR " + format(n, "+,")


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _boundary_epoch(iso_date: str) -> float:
    return datetime.strptime(iso_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()


def format_report(result: DiagnosisResult) -> str:
    """Plain text: everything `diagnose-flips` prints, in one string.

    Read-only over an already-computed `DiagnosisResult` — no DB access, and
    no computation beyond calling `flip_diagnosis`'s pure aggregators.
    """
    if HEADLINE_HORIZON not in result.horizons:
        # The headline line and the per-branch table are both anchored at
        # HEADLINE_HORIZON (the pre-registered dominance rule names it
        # explicitly) — a `result` swept over a horizon set that omits it has
        # nothing for either section to show, so fail clearly here instead of
        # a bare KeyError three sections down.
        raise ValueError(
            f"format_report requires HEADLINE_HORIZON={HEADLINE_HORIZON} in "
            f"result.horizons, got {result.horizons}"
        )

    scored = result.scored()
    floor_rows = [r for r in result.rows if r.is_floor_trip]
    divergent = [r for r in result.rows if r.branch == "mirror_divergence"]
    # Distinct from an ordinary per-horizon gap: these rows have no usable
    # market value at the buy instant AT ALL, so `run_diagnosis` censors
    # every configured horizon for them (floor trips excepted — they are
    # never folded into Censored; see `run_diagnosis`). Surfaced separately
    # so a reader of the Censored column cannot mistake "gapped at this one
    # horizon" for "this row contributed nothing anywhere".
    no_mv_at_buy = [r for r in result.rows if r.mv_buy is None and not r.is_floor_trip]

    horizon_totals = totals_by_horizon(result)
    headline = horizon_totals[HEADLINE_HORIZON]
    mechanism = dominant_mechanism(headline)

    branch_totals = totals_by_branch(result, HEADLINE_HORIZON)
    branch_counts: dict[str, int] = {}
    for row in scored:
        if HEADLINE_HORIZON in row.by_horizon:
            branch_counts[row.branch] = branch_counts.get(row.branch, 0) + 1

    boundary = _boundary_epoch(TEMPORAL_BOUNDARY_ISO)
    split = temporal_split(result, HEADLINE_HORIZON, boundary)
    split_counts = {
        "before": sum(
            1 for r in scored if r.trip.buy_date < boundary and HEADLINE_HORIZON in r.by_horizon
        ),
        "after": sum(
            1 for r in scored if r.trip.buy_date >= boundary and HEADLINE_HORIZON in r.by_horizon
        ),
    }

    lines = [
        _DRULE,
        "FLIP LOSS DIAGNOSIS — REH-75",
        _DRULE,
        "",
        f"{len(result.rows)} completed ROUND TRIPS (not flips — see REH-75 design §1)",
        "",
    ]

    # Surfaced immediately, ahead of every table: a non-zero count here is a
    # defect in the branch reconstruction, not a market outcome, and must not
    # be discoverable only by reading the per-branch table further down.
    if divergent:
        lines += [
            f"*** MIRROR DIVERGENCE: {len(divergent)} / {len(result.rows)} rows ***",
            "The branch reconstruction disagrees with the shipped ProfitTrader",
            "ladder on at least one row. This is a DEFECT in flip_branches.py,",
            "not a market outcome — see label_for's docstring. Every per-branch",
            "number below is unreliable until this is fixed.",
            "",
        ]
    else:
        lines += [
            "Mirror divergence: 0 rows (expected — the branch reconstruction "
            "agrees with the shipped ProfitTrader ladder on every labelled row).",
            "",
        ]

    lines += [
        f"Horizon sweep (population totals; the EUR {FLOOR_PRICE:,} floor group is excluded)",
        _RULE,
        f"{'Horizon':<9}{'Selection':>18}{'Exit':>18}{'Entry premium':>18}{'Total':>18}"
        f"{'Censored':>10}",
    ]
    for h in result.horizons:
        d = horizon_totals[h]
        lines.append(
            f"{f'{h}d':<9}{_eur(d.selection):>18}{_eur(d.exit_timing):>18}"
            f"{_eur(d.entry_premium):>18}{_eur(d.total):>18}{result.censored[h]:>10}"
        )
    lines += [
        f"Rows with no market value at buy: {len(no_mv_at_buy)} "
        "(fully censored, unlabelled — not the floor group)",
        "",
    ]

    lines += [
        f"Headline at H={HEADLINE_HORIZON}d: dominant mechanism = {mechanism}",
        f"  Selection:      {_eur(headline.selection)}",
        f"  Exit timing:    {_eur(headline.exit_timing)}",
        f"  Entry premium:  {_eur(headline.entry_premium)}  (paid over market value, unnegated)",
        f"  Total:          {_eur(headline.total)}",
        "",
    ]

    lines += [
        LABEL_SEMANTICS,
        "",
        f"Per-branch decomposition at H={HEADLINE_HORIZON}d",
        _RULE,
        f"{'Branch':<24}{'Selection':>18}{'Exit':>18}{'Entry premium':>18}{'Total':>18}"
        f"{'Trips':>8}",
    ]
    for branch in sorted(branch_totals):
        d = branch_totals[branch]
        n = branch_counts.get(branch, 0)
        lines.append(
            f"{branch:<24}{_eur(d.selection):>18}{_eur(d.exit_timing):>18}"
            f"{_eur(d.entry_premium):>18}{_eur(d.total):>18}{n:>8}"
        )
    lines.append("")

    lines += [
        f"Temporal split at {TEMPORAL_BOUNDARY_ISO} (boundary on buy_date, H={HEADLINE_HORIZON}d)",
        _RULE,
        f"  Before {TEMPORAL_BOUNDARY_ISO}:    {_eur(split['before'].total)}  "
        f"({_plural(split_counts['before'], 'trip')})",
        f"  On/after {TEMPORAL_BOUNDARY_ISO}: {_eur(split['after'].total)}  "
        f"({_plural(split_counts['after'], 'trip')})",
        "",
    ]

    floor_pnl = sum(r.trip.realised for r in floor_rows)
    lines += [
        f"EUR {FLOOR_PRICE:,} floor group (reported separately — never mixed into",
        "the headline totals above)",
        _RULE,
        f"  Trips: {len(floor_rows)}",
        f"  P&L:   {_eur(floor_pnl)}",
        "",
        _DRULE,
    ]

    return "\n".join(lines)
