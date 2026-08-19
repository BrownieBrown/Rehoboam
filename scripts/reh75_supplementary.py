#!/usr/bin/env python3
"""REH-75: every figure in the results document that `diagnose-flips` does NOT print.

Roughly two thirds of the quantitative content of
`docs/superpowers/specs/2026-08-19-reh-75-flip-diagnosis-results.md` was
computed outside the `diagnose-flips` command -- sections 6, 7 and 9 in their
entirety, which drive recommendations 2, 3 and 6 in section 11. The design doc
justified building a CLI command precisely so that could not happen: *"a
one-shot script that produces a headline number is what nobody can re-check
later."* This file closes that gap for everything the command does not cover.

It needs no tests. Its correctness criterion is that it reproduces the numbers
already published, from the same two databases, pinned by digest:

    76e55eba3c68aa147809c09467336166951935662d800954209a6bc1472f18ce  logs/bid_learning.db
    0af472a7ac5a9193348def8bfa8cb53cf83f3650fe2373b1971a4b9314b62999  logs/training_corpus.db

STRICTLY READ-ONLY. Every connection is opened `mode=ro`, and the aggregation
runs over `flip_diagnosis.run_diagnosis`, which opens its corpus `read_only=True`.
Nothing here writes, migrates, or creates a database.

Usage: uv run python scripts/reh75_supplementary.py
       uv run python scripts/reh75_supplementary.py --learner-db X --corpus-db Y
"""

from __future__ import annotations

import argparse
import sqlite3
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rehoboam.diagnostics.flip_branches import ELIGIBLE_BRANCHES  # noqa: E402
from rehoboam.diagnostics.flip_diagnosis import (  # noqa: E402
    HEADLINE_HORIZON,
    SECONDS_PER_DAY,
    TEMPORAL_BOUNDARY_ISO,
    mv_nearest,
    run_diagnosis,
)

# The corpus ends 2026-07-31 but the season ends here; horizon windows crossing
# this date land in the off-season, where market values deflate for reasons
# unrelated to player selection (results section 5, Q3 robustness check).
SEASON_END_ISO = "2026-05-16"


def _epoch(iso: str) -> float:
    return datetime.strptime(iso, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def pct(xs: list[float], p: float) -> float:
    """The percentile convention used throughout the results document.

    Index `int(n * p)` into the sorted sample -- a nearest-rank variant, NOT
    `statistics.quantiles` and NOT linear interpolation. Stated explicitly
    because the five entry-premium percentiles in section 6 differ by up to
    0.6 points between conventions, and a reader re-deriving them with numpy's
    default would conclude the document was wrong.
    """
    ordered = sorted(xs)
    return ordered[min(int(len(ordered) * p), len(ordered) - 1)]


def eur(n: float) -> str:
    return "EUR " + format(int(round(n)), "+,")


def head(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def mv_at_or_before(corpus_db: Path, player_id: str, at: float) -> tuple[float, int] | None:
    """(snapshot_at, market_value) of the most recent snapshot at or before `at`.

    The at-or-before rule, matching `TrainingCorpus.market_value_at`. Used for
    both PRICING INSTANTS in this analysis -- the buy and the sale -- because
    only data at or before a transaction may enter a measure of that
    transaction (design doc, task-5 amendment).
    """
    with _connect_ro(corpus_db) as conn:
        row = conn.execute(
            "SELECT snapshot_at, market_value FROM mv_series "
            "WHERE player_id = ? AND snapshot_at <= ? ORDER BY snapshot_at DESC LIMIT 1",
            (str(player_id), float(at)),
        ).fetchone()
    return (float(row[0]), int(row[1])) if row else None


def mv_after(corpus_db: Path, player_id: str, at: float) -> int | None:
    """Market value at the first snapshot strictly AFTER `at`.

    Leaks future information by construction. Used only to bracket snapshot
    staleness from the opposite side -- never as an estimate.
    """
    with _connect_ro(corpus_db) as conn:
        row = conn.execute(
            "SELECT market_value FROM mv_series "
            "WHERE player_id = ? AND snapshot_at > ? ORDER BY snapshot_at ASC LIMIT 1",
            (str(player_id), float(at)),
        ).fetchone()
    return int(row[0]) if row else None


def section_1(result, corpus_db: Path, learner_db: Path) -> None:
    head("Section 1 -- evidence handling: horizon resolution and the second MV source")
    scored = result.scored()

    gaps: list[float] = []
    with _connect_ro(corpus_db) as conn:
        for row in scored:
            for h in result.horizons:
                target = row.trip.buy_date + h * SECONDS_PER_DAY
                snap = conn.execute(
                    "SELECT snapshot_at FROM mv_series WHERE player_id = ? "
                    "ORDER BY ABS(snapshot_at - ?) LIMIT 1",
                    (str(row.trip.player_id), target),
                ).fetchone()
                if snap:
                    gaps.append(abs(float(snap[0]) - target) / SECONDS_PER_DAY)
    print(f"  horizon endpoints resolved: n={len(gaps)}")
    print(f"  max gap to target:          {max(gaps):.2f} days")
    print(f"  mean gap to target:         {statistics.mean(gaps):.2f} days")
    print("  (the design doc's 0.99/0.46 measures a BACKWARDS-ONLY lookup; this")
    print("   is the NEAREST lookup mv_nearest performs, capped at half a day.)")

    diffs: list[float] = []
    covered = 0
    with _connect_ro(learner_db) as conn:
        for row in scored:
            hit = conn.execute(
                "SELECT market_value FROM player_mv_history WHERE player_id = ? "
                "AND snapshot_at <= ? ORDER BY snapshot_at DESC LIMIT 1",
                (str(row.trip.player_id), float(row.trip.buy_date)),
            ).fetchone()
            if not hit:
                continue
            covered += 1
            diffs.append(abs(int(hit[0]) - row.mv_buy) / row.mv_buy * 100)
    print(f"\n  player_mv_history has a value at/before buy for: {covered} / {len(scored)}")
    print(f"  |difference| vs corpus mv_buy -- median: {statistics.median(diffs):.2f}%")
    print(f"                                     mean: {statistics.mean(diffs):.2f}%")
    print(f"                                      max: {max(diffs):.2f}%")
    print(
        f"                            within 2%: {sum(1 for d in diffs if d <= 2)} / {len(diffs)}"
    )


def section_4(result, corpus_db: Path) -> None:
    head("Section 4 -- the identity at H = each trip's REALISED hold (supplementary view)")
    scored = result.scored()
    selection = exit_timing = premium = 0
    for row in scored:
        at_sell = mv_at_or_before(corpus_db, row.trip.player_id, row.trip.sell_date)
        if at_sell is None:
            continue
        mv_sell = at_sell[1]
        selection += mv_sell - row.mv_buy
        exit_timing += row.trip.sell_price - mv_sell
        premium += row.trip.buy_price - row.mv_buy
    print("  lookup at the sale: AT-OR-BEFORE (a sale is a pricing instant)")
    print(f"  Selection      {eur(selection)}")
    print(f"  Exit timing    {eur(exit_timing)}")
    print(f"  Entry premium  {eur(premium)}")
    print(f"  Total          {eur(selection + exit_timing - premium)}")


def section_5_q1(result) -> None:
    head("Section 5, Q1 -- Selection per trip, as % of buy-day market value")
    print(f"  {'Horizon':<9}{'Mean':>10}{'Median':>10}{'Share negative':>18}{'n':>6}")
    for h in result.horizons:
        pcts = [
            r.by_horizon[h].selection / r.mv_buy * 100 for r in result.scored() if h in r.by_horizon
        ]
        neg = sum(1 for p in pcts if p < 0)
        print(
            f"  {f'{h}d':<9}{statistics.mean(pcts):>+9.2f}%{statistics.median(pcts):>+9.2f}%"
            f"{f'{neg}/{len(pcts)} = {neg / len(pcts) * 100:.0f}%':>18}{len(pcts):>6}"
        )


def section_5_q2(result, corpus_db: Path) -> None:
    head("Section 5, Q2 -- the contemporaneous exit measure, and its staleness bracket")
    scored = result.scored()

    exits: list[int] = []
    ratios: list[float] = []
    ages: list[float] = []
    above = 0
    for row in scored:
        hit = mv_at_or_before(corpus_db, row.trip.player_id, row.trip.sell_date)
        if hit is None:
            continue
        snapshot_at, mv_sell = hit
        ages.append((row.trip.sell_date - snapshot_at) / SECONDS_PER_DAY)
        exits.append(row.trip.sell_price - mv_sell)
        ratios.append(row.trip.sell_price / mv_sell)
        above += row.trip.sell_price > mv_sell
    print("  PRIMARY -- mv(sell) = most recent snapshot AT OR BEFORE sell_date")
    print(f"    sum(s - mv_sell):  {eur(sum(exits))}   (n={len(exits)})")
    print(f"    mean ratio:        {statistics.mean(ratios):.4f}")
    print(f"    median ratio:      {statistics.median(ratios):.4f}")
    print(f"    sold above market: {above} / {len(exits)}")
    print(f"    snapshot age -- median {statistics.median(ages):.2f} d, max {max(ages):.2f} d")

    near_exits = []
    near_ratios = []
    near_above = 0
    for row in scored:
        mv_sell = mv_nearest(corpus_db, row.trip.player_id, row.trip.sell_date)
        if mv_sell is None:
            continue
        near_exits.append(row.trip.sell_price - mv_sell)
        near_ratios.append(row.trip.sell_price / mv_sell)
        near_above += row.trip.sell_price > mv_sell
    print("\n  NOT USED -- nearest snapshot (bidirectional; leaks post-sale price action)")
    print(
        f"    sum(s - mv_sell):  {eur(sum(near_exits))}   mean ratio {statistics.mean(near_ratios):.4f}"
        f"   above {near_above}/{len(near_exits)}"
    )

    after_exits = [
        row.trip.sell_price - mv
        for row in scored
        if (mv := mv_after(corpus_db, row.trip.player_id, row.trip.sell_date)) is not None
    ]
    print("\n  STALENESS BRACKET -- the opposite extreme, first snapshot AFTER the sale")
    print(f"    sum(s - mv_next):  {eur(sum(after_exits))}   (n={len(after_exits)})")
    print(f"    bracket:           [{eur(sum(exits))}, {eur(sum(after_exits))}]")
    print(f"    bracket width:     {eur(abs(sum(after_exits) - sum(exits)))}")

    peaks = [r for r in scored if r.peak_during_hold is not None]
    no_peak = [r for r in scored if r.peak_during_hold is None]
    gaps = [r.peak_during_hold - r.trip.sell_price for r in peaks]
    boundary = _epoch(TEMPORAL_BOUNDARY_ISO)
    before = [r.peak_during_hold - r.trip.sell_price for r in peaks if r.trip.buy_date < boundary]
    after = [r.peak_during_hold - r.trip.sell_price for r in peaks if r.trip.buy_date >= boundary]
    print("\n  REH-33 SUB-MEASURE -- peak_during_hold - sell_price (an EX-POST oracle)")
    print(f"    aggregate: {eur(sum(gaps))}   n={len(peaks)}")
    print(
        f"    median per trip: {eur(statistics.median(gaps))}   mean {sum(gaps) / len(gaps):,.2f}"
    )
    print(
        f"    rows with no in-hold snapshot: {len(no_peak)}"
        f" (all hold_days == 0: {all(r.trip.hold_days == 0 for r in no_peak)})"
    )
    print(
        f"    sold at or above the in-hold peak: "
        f"{sum(1 for r in peaks if r.trip.sell_price >= r.peak_during_hold)} / {len(peaks)}"
    )
    print(
        f"    before {TEMPORAL_BOUNDARY_ISO}: {eur(sum(before))} (n={len(before)});"
        f"  on/after: {eur(sum(after))} (n={len(after)})"
    )


def section_5_q3(result) -> None:
    head("Section 5, Q3 -- hold windows, and the off-season robustness check")
    scored = result.scored()
    season_end = _epoch(SEASON_END_ISO)

    for label, rows in (("all 151 rows", result.rows), ("136 scored rows", scored)):
        holds = [r.trip.hold_days for r in rows]
        u30 = sum(1 for h in holds if h < 30)
        u21 = sum(1 for h in holds if h < 21)
        print(
            f"  {label:<16} median hold {statistics.median(holds):>5}   "
            f"sold before day 30: {u30}/{len(holds)} = {u30 / len(holds) * 100:.1f}%   "
            f"under 21 days: {u21}/{len(holds)} = {u21 / len(holds) * 100:.1f}%"
        )
    print(f"  (the results document quotes the {len(scored)}-row figures)")

    late = [r for r in scored if r.trip.buy_date + 60 * SECONDS_PER_DAY > season_end]
    print(
        f"\n  H=60 windows ending after {SEASON_END_ISO}: {len(late)} / {len(scored)}, "
        f"contributing {eur(sum(r.by_horizon[60].selection for r in late if 60 in r.by_horizon))} of Selection"
    )
    panel = [r for r in scored if r.trip.buy_date + 60 * SECONDS_PER_DAY <= season_end]
    print(f"  balanced in-season panel: n={len(panel)}")
    for h in result.horizons:
        print(
            f"    {h}d Selection: {eur(sum(r.by_horizon[h].selection for r in panel if h in r.by_horizon))}"
        )


def section_6(result, corpus_db: Path) -> None:
    head("Section 6 -- the entry premium")
    scored = result.scored()
    sum_b = sum(r.trip.buy_price for r in scored)
    sum_mv = sum(r.mv_buy for r in scored)
    ratios = [r.trip.buy_price / r.mv_buy for r in scored]
    all_ratios = [r.trip.buy_price / r.mv_buy for r in result.rows if r.mv_buy]

    print(f"  premium sum(b - mv_buy):        {eur(sum_b - sum_mv)}")
    print(f"  market value deployed sum(mv):  {sum_mv:,}")
    print(f"  aggregate sum(b)/sum(mv):       {sum_b / sum_mv:.4f}")
    print(f"  mean per-trip ratio ({len(scored)}):      {statistics.mean(ratios):.4f}")
    print(f"  median per-trip ratio:          {statistics.median(ratios):.4f}")
    print(f"  mean per-trip ratio (all {len(all_ratios)}):  {statistics.mean(all_ratios):.4f}")
    print(
        f"  paid ABOVE market value:        {sum(1 for r in scored if r.trip.buy_price > r.mv_buy)} / {len(scored)}"
    )
    print(
        f"  paid BELOW market value:        {sum(1 for r in scored if r.trip.buy_price < r.mv_buy)} / {len(scored)}"
    )
    print(
        f"  paid EXACTLY market value:      {sum(1 for r in scored if r.trip.buy_price == r.mv_buy)} / {len(scored)}"
    )

    premium_pct = [(r.trip.buy_price / r.mv_buy - 1.0) * 100 for r in scored]
    parts = " / ".join(f"{pct(premium_pct, p):+.1f}%" for p in (0.10, 0.25, 0.50, 0.75, 0.90))
    print(f"  percentiles p10/p25/p50/p75/p90: {parts}   [convention: sorted[int(n*p)]]")

    ages = []
    drift = []
    next_premium = 0
    for row in scored:
        hit = mv_at_or_before(corpus_db, row.trip.player_id, row.trip.buy_date)
        if hit:
            ages.append((row.trip.buy_date - hit[0]) / SECONDS_PER_DAY)
        nxt = mv_after(corpus_db, row.trip.player_id, row.trip.buy_date)
        if nxt is not None:
            drift.append((nxt - row.mv_buy) / row.mv_buy * 100)
            next_premium += row.trip.buy_price - nxt
    print(
        f"\n  STALENESS -- mv_buy snapshot age: median {statistics.median(ages):.2f} d,"
        f" max {max(ages):.2f} d"
    )
    print(f"  median day-over-day drift to the next snapshot: {statistics.median(drift):+.2f}%")
    print(f"  premium recomputed against the NEXT snapshot (a leaky floor): {eur(next_premium)}")
    print(
        f"  staleness can account for at most: {eur(sum_b - sum_mv - next_premium)}"
        f" of {eur(sum_b - sum_mv)} ({(sum_b - sum_mv - next_premium) / (sum_b - sum_mv) * 100:.0f}%)"
    )

    same_day = [r for r in scored if r.trip.hold_days == 0]
    sd_realised = sum(r.trip.realised for r in same_day)
    sd_premium = sum(r.trip.buy_price - r.mv_buy for r in same_day)
    print(f"\n  SAME-DAY round trips: {len(same_day)}")
    print(f"    realised P&L:  {eur(sd_realised)}")
    print(f"    entry premium: {eur(sd_premium)}")
    print(f"    they differ by {abs(sd_realised + sd_premium) / abs(sd_realised) * 100:.1f}%")

    by_premium = sorted(scored, key=lambda r: r.trip.buy_price - r.mv_buy, reverse=True)
    top10 = sum(r.trip.buy_price - r.mv_buy for r in by_premium[:10])
    worst = by_premium[0]
    print(
        f"\n  FAT TAIL -- top 10 trips by premium: {eur(top10)}"
        f" = {top10 / (sum_b - sum_mv) * 100:.0f}% of the total"
    )
    print(
        f"    largest single: {worst.trip.player_name} {eur(worst.trip.buy_price - worst.mv_buy)}"
        f" ({(worst.trip.buy_price / worst.mv_buy - 1) * 100:.1f}% over market value)"
    )


def section_7(result) -> None:
    head("Section 7 -- loss concentration")
    scored = result.scored()
    total = sum(r.trip.realised for r in scored)
    by_loss = sorted(scored, key=lambda r: r.trip.realised)
    for k in (1, 3, 5, 10):
        cohort = sum(r.trip.realised for r in by_loss[:k])
        print(f"  worst {k:<2}: {eur(cohort):>16}   {cohort / total * 100:.0f}% of {eur(total)}")
    rest = by_loss[10:]
    rest_pnl = sum(r.trip.realised for r in rest)
    rest_mv = sum(r.mv_buy for r in rest)
    rest_b = sum(r.trip.buy_price for r in rest)
    print(
        f"  the other {len(rest)}: {eur(rest_pnl)} on {rest_mv:,} of market value deployed"
        f" = {rest_pnl / rest_mv * 100:+.2f}%, at a {rest_b / rest_mv:.4f} entry-premium ratio"
    )
    print(
        f"  entry premium carried by the worst 10: "
        f"{eur(sum(r.trip.buy_price - r.mv_buy for r in by_loss[:10]))}"
    )
    print(
        f"\n  {'Player':<14}{'Buy date':<12}{'Paid':>13}{'Sold':>13}{'Realised':>14}"
        f"{'Premium':>13}{'Hold':>6}  Branch"
    )
    for r in by_loss[:10]:
        day = datetime.fromtimestamp(r.trip.buy_date, tz=timezone.utc).strftime("%Y-%m-%d")
        print(
            f"  {r.trip.player_name:<14}{day:<12}{r.trip.buy_price:>13,}{r.trip.sell_price:>13,}"
            f"{r.trip.realised:>+14,}{r.trip.buy_price - r.mv_buy:>+13,}{r.trip.hold_days:>6}  {r.branch}"
        )


def section_8(result) -> None:
    head(f"Section 8 -- per-branch premium ratios and median holds (H={HEADLINE_HORIZON}d)")
    scored = [r for r in result.scored() if HEADLINE_HORIZON in r.by_horizon]
    groups: dict[str, list] = {}
    for row in scored:
        groups.setdefault(row.branch, []).append(row)
    print(f"  {'Branch':<24}{'Trips':>7}{'sum(b)/sum(mv)':>17}{'Median hold':>13}{'Total':>18}")
    for branch, rows in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        sb = sum(r.trip.buy_price for r in rows)
        smv = sum(r.mv_buy for r in rows)
        print(
            f"  {branch:<24}{len(rows):>7}{sb / smv:>17.4f}"
            f"{statistics.median([r.trip.hold_days for r in rows]):>13}"
            f"{eur(sum(r.by_horizon[HEADLINE_HORIZON].total for r in rows)):>18}"
        )
    for label, rows in (
        ("flip-eligible", [r for r in scored if r.branch in ELIGIBLE_BRANCHES]),
        ("not flip-eligible", [r for r in scored if r.branch not in ELIGIBLE_BRANCHES]),
    ):
        sb = sum(r.trip.buy_price for r in rows)
        smv = sum(r.mv_buy for r in rows)
        print(
            f"  {label:<24}{len(rows):>7}{sb / smv:>17.4f}{'--':>13}"
            f"{eur(sum(r.by_horizon[HEADLINE_HORIZON].total for r in rows)):>18}"
            f"   premium {eur(sb - smv)}"
        )

    head("Section 0 / C1 -- why 108 bounds the SET and not the loss")
    eligible = [r for r in scored if r.branch in ELIGIBLE_BRANCHES]
    print(
        f"  all {len(eligible)} eligible trips net: "
        f"{eur(sum(r.by_horizon[HEADLINE_HORIZON].total for r in eligible))}"
    )
    for branch in sorted(ELIGIBLE_BRANCHES):
        rows = groups.get(branch, [])
        if rows:
            print(
                f"    rung {branch:<24} n={len(rows):>3}  "
                f"{eur(sum(r.by_horizon[HEADLINE_HORIZON].total for r in rows))}"
            )
    minus_rising = [r for r in eligible if r.branch != "rising"]
    print(
        f"  a rung-level SUBSET (eligible minus `rising`, n={len(minus_rising)}) nets: "
        f"{eur(sum(r.by_horizon[HEADLINE_HORIZON].total for r in minus_rising))}"
    )
    losers = [r for r in eligible if r.by_horizon[HEADLINE_HORIZON].total < 0]
    print(
        f"  loss-makers only within the eligible set (n={len(losers)}): "
        f"{eur(sum(r.by_horizon[HEADLINE_HORIZON].total for r in losers))}"
    )
    print("  => a subset sum is NOT bounded by its superset when the superset")
    print("     carries positive terms. 108 bounds the set, not the loss.")


def section_9(result) -> None:
    head(f"Section 9 -- the temporal split at {TEMPORAL_BOUNDARY_ISO} (H={HEADLINE_HORIZON}d)")
    boundary = _epoch(TEMPORAL_BOUNDARY_ISO)
    scored = [r for r in result.scored() if HEADLINE_HORIZON in r.by_horizon]
    cohorts = (
        (f"before {TEMPORAL_BOUNDARY_ISO}", [r for r in scored if r.trip.buy_date < boundary]),
        ("on/after           ", [r for r in scored if r.trip.buy_date >= boundary]),
    )
    for label, rows in cohorts:
        sb = sum(r.trip.buy_price for r in rows)
        smv = sum(r.mv_buy for r in rows)
        d = [r.by_horizon[HEADLINE_HORIZON] for r in rows]
        s_minus_mv = sum(r.trip.sell_price - r.mv_buy for r in rows)
        print(f"  {label}  n={len(rows)}")
        print(
            f"    Selection {eur(sum(x.selection for x in d))}   "
            f"Exit {eur(sum(x.exit_timing for x in d))}   "
            f"Entry premium {eur(sum(x.entry_premium for x in d))}"
        )
        print(f"    Total {eur(sum(x.total for x in d))}   sum(b)/sum(mv) {sb / smv:.4f}")
        print(
            f"    sum(s - mv_buy) {eur(s_minus_mv)} = {s_minus_mv / smv * 100:+.1f}% of sum(mv_buy)"
        )


def section_10(result, corpus_db: Path, learner_db: Path) -> None:
    head("Section 10, caveat 7 -- career vs season average_points (REH-77)")
    from rehoboam.backtest.snapshot import matches_before
    from rehoboam.enrichment.corpus import TrainingCorpus
    from rehoboam.replay.driver import LEAGUE_ID, SEASON, day_for_kickoff, load_calendar
    from rehoboam.replay.flip_buys import APPEARANCE_STATUSES, average_points_at

    corpus = TrainingCorpus(corpus_db, read_only=True)
    kickoffs = load_calendar(learner_db, league_id=LEAGUE_ID)
    players = {r.trip.player_id for r in result.rows}
    with_history = sum(
        1 for pid in players if any(m["season"] < SEASON for m in corpus.matches_for_player(pid))
    )
    print(f"  distinct flipped players: {len(players)}")
    print(f"  carrying pre-{SEASON} match history: {with_history} / {len(players)}")

    differing = 0
    for row in result.scored():
        day = day_for_kickoff(kickoffs, row.trip.buy_date)
        career = average_points_at(corpus, row.trip.player_id, season=SEASON, day_number=day)
        in_season = [
            m
            for m in matches_before(
                corpus.matches_for_player(row.trip.player_id), season=SEASON, day_number=day
            )
            if m["season"] == SEASON and m.get("status") in APPEARANCE_STATUSES
        ]
        season_avg = (
            sum(float(m["points"] or 0) for m in in_season) / len(in_season) if in_season else 0.0
        )
        differing += abs(career - season_avg) > 10
    print(
        f"  scored trips whose career and season-to-date averages differ by >10 points:"
        f" {differing} / {len(result.scored())}"
    )
    print("  (the ladder's points gates are at 20/30/40, so >10 is a full step)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--learner-db", type=Path, default=Path("logs/bid_learning.db"))
    parser.add_argument("--corpus-db", type=Path, default=Path("logs/training_corpus.db"))
    args = parser.parse_args()

    result = run_diagnosis(args.learner_db, args.corpus_db)
    print(
        f"population: {len(result.rows)} round trips, {len(result.scored())} scored "
        f"(floor group {len(result.rows) - len(result.scored())})"
    )

    section_1(result, args.corpus_db, args.learner_db)
    section_4(result, args.corpus_db)
    section_5_q1(result)
    section_5_q2(result, args.corpus_db)
    section_5_q3(result)
    section_6(result, args.corpus_db)
    section_7(result)
    section_8(result)
    section_9(result)
    section_10(result, args.corpus_db, args.learner_db)


if __name__ == "__main__":
    main()
