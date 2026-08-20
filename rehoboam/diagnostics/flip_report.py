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
    agreement_label,
    dominant_loss_mechanisms,
    dominant_mechanism,
    signed_contributions,
    temporal_split,
    totals_at_hold,
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

# Asserted by tests/test_diagnostics/test_flip_report.py. The pre-registered
# rule ranks by MAGNITUDE of a signed contribution, so it can name a term that
# GAINED money -- as it does on the real 2025/26 data, where exit timing is
# +EUR177.7M. The results document spends two pages qualifying that; a reader
# who only runs the tool must not get the verdict bare.
POSITIVE_WINNER_NOTE = (
    "(note: this term's contribution is POSITIVE — the rule ranks by "
    "magnitude, so the named term is the largest number, not necessarily the "
    "cause of the loss)"
)

# Printed on the REH-75 headline block. That block is kept verbatim so the
# re-run stays line-comparable against the results document's appendix; this
# note is what stops a reader taking its verdict as current.
SUPERSEDED_NOTE = (
    "  (superseded: this rule could not name selection — see the registered "
    "verdict below and REH-78)"
)

_DISPLAY = {
    "selection": "selection",
    "exit_timing": "exit timing",
    "entry_premium": "entry premium",
}


def _verdict_text(terms: tuple[str, ...]) -> str:
    if not terms:
        return "no loss to explain"
    named = " + ".join(_DISPLAY[t] for t in terms)
    return f"{named} (co-dominant)" if len(terms) > 1 else named


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
        f"{len(scored)} scored below; the remaining {len(floor_rows)} are the "
        f"EUR {FLOOR_PRICE:,} floor group, reported separately at the end.",
        "Divide by the scored count, never by the population count.",
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
        f"{'n':>7}{'Censored':>10}",
    ]
    for h in result.horizons:
        d = horizon_totals[h]
        n = sum(1 for r in scored if h in r.by_horizon)
        lines.append(
            f"{f'{h}d':<9}{_eur(d.selection):>18}{_eur(d.exit_timing):>18}"
            f"{_eur(d.entry_premium):>18}{_eur(d.total):>18}{n:>7}{result.censored[h]:>10}"
        )
    # The decomposition is an identity with no residual bucket, so every Total
    # above MUST equal the realised P&L of the same rows. Printing the ground
    # truth beside them is what makes this artifact self-validating on a re-run
    # by someone who no longer has the databases that produced it.
    realised = sum(r.trip.realised for r in scored)
    # Compared per horizon against the rows that actually contributed there:
    # a censored row is legitimately absent from that horizon's Total, so
    # comparing every horizon against the whole-population figure would raise a
    # false alarm the moment censoring is non-zero.
    mismatched = [
        h
        for h in result.horizons
        if horizon_totals[h].total != sum(r.trip.realised for r in scored if h in r.by_horizon)
    ]
    lines += [
        f"Ground truth: sum(realised) over the {len(scored)} scored round trips "
        f"= {_eur(realised)}",
        (
            "  Every Total above equals the realised P&L of the rows behind it — "
            "the identity closes with no residual."
            if not mismatched
            else f"  *** IDENTITY DOES NOT CLOSE at H={mismatched} — this report is broken ***"
        ),
        f"Rows with no market value at buy: {len(no_mv_at_buy)} "
        "(fully censored, unlabelled — not the floor group)",
        "",
    ]

    lines += [
        f"Headline at H={HEADLINE_HORIZON}d: dominant mechanism = {mechanism}",
    ]
    # A named term whose signed contribution is positive is a term that HELPED;
    # the rule still names it because it ranks on magnitude alone.
    if signed_contributions(headline).get(mechanism, 0) > 0:
        lines.append(f"  {POSITIVE_WINNER_NOTE}")
    lines += [
        f"  Selection:      {_eur(headline.selection)}",
        f"  Exit timing:    {_eur(headline.exit_timing)}",
        f"  Entry premium:  {_eur(headline.entry_premium)}  (paid over market value, unnegated)",
        f"  Total:          {_eur(headline.total)}",
        SUPERSEDED_NOTE,
    ]

    registered = dominant_loss_mechanisms(headline)
    hold = totals_at_hold(result)
    hold_verdict = dominant_loss_mechanisms(hold)
    # `hold_censored` only increments when mv_buy resolved and mv_sell did
    # not -- a row that never got mv_buy in the first place (excluded from
    # `scored()`'s hold view via `at_hold is None`, but never counted here)
    # is invisible to that counter. Printing the rows that actually fed the
    # totals below means the block cannot silently under-report its
    # population the way Censored alone could.
    hold_n = sum(1 for r in scored if r.at_hold is not None)
    lines += [
        "",
        f"Registered verdict at H={HEADLINE_HORIZON}d (REH-78): {_verdict_text(registered)}",
        "  Only negative contributions are eligible; a term that reduced the "
        "loss cannot be its cause.",
        "",
        "Dominance by horizon (REH-78 rule)",
        _RULE,
    ]
    for h in result.horizons:
        lines.append(f"{f'{h}d':<9}{_verdict_text(dominant_loss_mechanisms(horizon_totals[h]))}")
    lines += [
        "",
        "Supplementary — the identity at each trip's realised hold "
        "(NOT the registered instrument: the sale date is bot-chosen, so "
        "selection is conditioned on the outcome)",
        _RULE,
        f"  n: {hold_n} scored round trips contribute to the totals below",
        f"  Selection:      {_eur(hold.selection)}",
        f"  Exit timing:    {_eur(hold.exit_timing)}",
        f"  Entry premium:  {_eur(hold.entry_premium)}  (paid over market value, unnegated)",
        f"  Total:          {_eur(hold.total)}",
        f"  Verdict: {_verdict_text(hold_verdict)}",
        f"  Agreement with the registered verdict: " f"{agreement_label(registered, hold_verdict)}",
        f"  Censored (no market value at or before the sale): {result.hold_censored}",
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
