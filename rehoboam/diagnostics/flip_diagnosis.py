"""REH-75: where the money went across every completed round trip.

`flip_outcomes` is a table of ROUND TRIPS, not of flips. `backfill.py`'s
`_pair_flips` FIFO-pairs every buy against every later sell per `player_id`,
and `LearningTracker.record_flip_outcome` fires on every instant sell. Neither
consults the motive for the buy, so an EP-driven squad buy that was later sold
is indistinguishable here from a `ProfitTrader` flip. Nothing in this module
may attribute a sum to the flip channel -- see the design doc's opening
section for why that claim is not available from this data.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RoundTrip:
    """One completed buy->sell pair, as `flip_outcomes` records it."""

    trip_id: int
    player_id: str
    player_name: str
    buy_price: int
    sell_price: int
    buy_date: float
    sell_date: float
    hold_days: int

    @property
    def realised(self) -> int:
        return self.sell_price - self.buy_price


@dataclass(frozen=True)
class Decomposition:
    """The identity's three terms, in euros.

    `entry_premium` is stored UNNEGATED -- what we paid above market value --
    and enters `total` negated, exactly as it enters the identity. The
    pre-registered dominance rule compares `-entry_premium` against the other
    two terms, so storing it pre-negated would silently flip the winner.
    """

    selection: int
    exit_timing: int
    entry_premium: int

    @property
    def total(self) -> int:
        return self.selection + self.exit_timing - self.entry_premium

    def __add__(self, other: Decomposition) -> Decomposition:
        return Decomposition(
            selection=self.selection + other.selection,
            exit_timing=self.exit_timing + other.exit_timing,
            entry_premium=self.entry_premium + other.entry_premium,
        )


def decompose(trip: RoundTrip, *, mv_buy: int, mv_h: int) -> Decomposition:
    """Split a round trip's realised P&L into SELECTION + EXIT - ENTRY PREMIUM.

    An identity, not an estimate: the terms cancel to `sell_price - buy_price`.
    """
    return Decomposition(
        selection=mv_h - mv_buy,
        exit_timing=trip.sell_price - mv_h,
        entry_premium=trip.buy_price - mv_buy,
    )


SECONDS_PER_DAY = 86400.0

# Corpus snapshots are daily, and every round trip in scope resolves to within
# 0.99 days of every horizon (measured during design). Three days is therefore
# a guard against a future rerun over sparser data, not a threshold this run
# relies on.
DEFAULT_MAX_GAP_DAYS = 3.0


def mv_nearest(
    db_path: Path,
    player_id: str,
    at: float,
    *,
    max_gap_days: float = DEFAULT_MAX_GAP_DAYS,
) -> int | None:
    """Market value at the snapshot nearest ``at``, or None if too far away.

    Deliberately NOT `TrainingCorpus.market_value_at`, which takes the most
    recent snapshot at or before ``at``. For a horizon endpoint the nearest
    snapshot may be the following day's, and a backwards-only lookup would
    silently substitute a value up to a day stale in one direction only.

    Returns None rather than 0 when nothing is close enough: a fabricated zero
    would enter the SELECTION term as a full loss of market value.
    """
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT snapshot_at, market_value FROM mv_series WHERE player_id = ? "
            "AND snapshot_at BETWEEN ? AND ? ORDER BY ABS(snapshot_at - ?) LIMIT 1",
            (
                str(player_id),
                at - max_gap_days * SECONDS_PER_DAY,
                at + max_gap_days * SECONDS_PER_DAY,
                at,
            ),
        ).fetchall()
    return int(rows[0][1]) if rows else None


def peak_between(db_path: Path, player_id: str, start: float, end: float) -> int | None:
    """Highest market value over the CLOSED interval ``[start, end]``.

    Feeds the `peak_during_hold - sell_price` sub-measure (REH-33's angle):
    how much of the appreciation we did capture was given back before selling.
    """
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT MAX(market_value) FROM mv_series "
            "WHERE player_id = ? AND snapshot_at BETWEEN ? AND ?",
            (str(player_id), float(start), float(end)),
        ).fetchone()
    return int(row[0]) if row and row[0] is not None else None


HORIZONS = (14, 21, 30, 45, 60)
HEADLINE_HORIZON = 30

# Kickbase's price floor. Round trips at buy == sell == this value have market
# value pinned, so all three terms are structurally zero -- and during design
# that exact pattern produced a false "15 flips at market value, EUR 0 P&L"
# reading. They are separated, never silently mixed in.
FLOOR_PRICE = 500_000

TEMPORAL_BOUNDARY_ISO = "2026-01-03"


@dataclass(frozen=True)
class TripRow:
    """One round trip with everything the diagnosis needs about it."""

    trip: RoundTrip
    mv_buy: int | None
    branch: str
    expected_appreciation: float
    by_horizon: dict[int, Decomposition]
    peak_during_hold: int | None
    is_floor_trip: bool


@dataclass(frozen=True)
class DiagnosisResult:
    rows: list[TripRow]
    horizons: tuple[int, ...]
    censored: dict[int, int]

    def scored(self) -> list[TripRow]:
        """Rows carried in the headline totals: everything but the floor group."""
        return [r for r in self.rows if not r.is_floor_trip]


def load_round_trips(learner_db: Path) -> list[RoundTrip]:
    """Every completed round trip in `flip_outcomes`, oldest first.

    NOT "every flip" -- see this module's docstring.
    """
    with sqlite3.connect(learner_db) as conn:
        rows = conn.execute(
            "SELECT id, player_id, player_name, buy_price, sell_price, "
            "buy_date, sell_date, hold_days FROM flip_outcomes ORDER BY buy_date"
        ).fetchall()
    return [
        RoundTrip(
            trip_id=int(r[0]),
            player_id=str(r[1]),
            player_name=str(r[2]),
            buy_price=int(r[3]),
            sell_price=int(r[4]),
            buy_date=float(r[5]),
            sell_date=float(r[6]),
            hold_days=int(r[7]),
        )
        for r in rows
    ]


def _sum(decompositions: list[Decomposition]) -> Decomposition:
    total = Decomposition(selection=0, exit_timing=0, entry_premium=0)
    for d in decompositions:
        total = total + d
    return total


def totals_by_horizon(result: DiagnosisResult) -> dict[int, Decomposition]:
    return {
        h: _sum([r.by_horizon[h] for r in result.scored() if h in r.by_horizon])
        for h in result.horizons
    }


def totals_by_branch(result: DiagnosisResult, horizon: int) -> dict[str, Decomposition]:
    totals: dict[str, Decomposition] = {}
    for row in result.scored():
        if horizon not in row.by_horizon:
            continue
        current = totals.get(row.branch)
        totals[row.branch] = (
            row.by_horizon[horizon] if current is None else current + row.by_horizon[horizon]
        )
    return totals


def temporal_split(
    result: DiagnosisResult, horizon: int, boundary: float
) -> dict[str, Decomposition]:
    before = [
        r.by_horizon[horizon]
        for r in result.scored()
        if r.trip.buy_date < boundary and horizon in r.by_horizon
    ]
    after = [
        r.by_horizon[horizon]
        for r in result.scored()
        if r.trip.buy_date >= boundary and horizon in r.by_horizon
    ]
    return {"before": _sum(before), "after": _sum(after)}


def dominant_mechanism(totals: Decomposition, *, tie_band: float = 0.20) -> str:
    """Apply REH-75's pre-registered rule. Fixed before any real number existed.

    Contributions are compared as the magnitude of each term's SIGNED sum, with
    entry premium entering negated exactly as it does in the identity.
    """
    contributions = {
        "selection": totals.selection,
        "exit_timing": totals.exit_timing,
        "entry_premium": -totals.entry_premium,
    }
    ranked = sorted(contributions.items(), key=lambda kv: abs(kv[1]), reverse=True)
    (winner, top), (_, second) = ranked[0], ranked[1]
    if abs(top) == 0:
        return "no single dominant mechanism"
    if (abs(top) - abs(second)) <= tie_band * abs(top):
        return "no single dominant mechanism"
    return winner


def run_diagnosis(
    learner_db: Path, corpus_db: Path, *, horizons: tuple[int, ...] = HORIZONS
) -> DiagnosisResult:
    """Replay every completed round trip through the decomposition and the
    branch-ladder mirror.

    Read-only: no API calls, no login, no writes. `label_for` (not
    `reconstruct_branch` alone) supplies the branch, because it is the only
    function that reconciles the mirror against the shipped `ProfitTrader`
    verdict -- see `flip_branches.label_for`'s docstring. Calling
    `profit_trader_accepts` directly on unfiltered rows would also trigger its
    small-sample console warning with blank player names.
    """
    from rehoboam.diagnostics.flip_branches import label_for, reconstruct_branch
    from rehoboam.enrichment.corpus import TrainingCorpus
    from rehoboam.replay.driver import LEAGUE_ID, SEASON, day_for_kickoff, load_calendar
    from rehoboam.replay.flip_buys import average_points_at, history_at
    from rehoboam.services.trend_service import TrendService

    trips = load_round_trips(learner_db)
    corpus = TrainingCorpus(corpus_db)
    kickoffs = load_calendar(learner_db, league_id=LEAGUE_ID)
    censored = dict.fromkeys(horizons, 0)
    rows: list[TripRow] = []

    for trip in trips:
        # `mv_buy` stands for a DECISION instant, not a measurement endpoint:
        # only what was knowable at-or-before `buy_date` may enter it, so this
        # is `market_value_at` (most recent <= at), never `mv_nearest`
        # (nearest, which can resolve to a snapshot AFTER buy_date and leak
        # future price action into both the branch label and the entry
        # premium baseline). `mv_nearest` stays correct for `mv_h` below and
        # for `peak_between`, because a horizon endpoint `buy + H` is an
        # arbitrary instant that daily snapshots straddle on both sides, not
        # a decision this diagnosis is honouring. Do not unify the two.
        mv_buy = corpus.market_value_at(trip.player_id, trip.buy_date)
        peak = peak_between(corpus_db, trip.player_id, trip.buy_date, trip.sell_date)
        is_floor = trip.buy_price == trip.sell_price == FLOOR_PRICE

        if mv_buy is None:
            # No usable market value at the buy instant: SELECTION and ENTRY
            # PREMIUM both need it, so every horizon is censored for this row
            # (floor trips excepted -- see below), and the branch cannot be
            # honestly reconstructed either -- label it the same way the
            # ladder already labels "no history to go on" rather than
            # inventing a new bucket.
            if not is_floor:
                # The EUR 500k floor group is reported separately and never
                # mixed into the headline totals (`DiagnosisResult.scored()`
                # already excludes it) -- so it must not be mixed into
                # Censored either, which lives in the same headline sweep.
                for h in horizons:
                    censored[h] += 1
            rows.append(
                TripRow(
                    trip=trip,
                    mv_buy=None,
                    branch="no_trend_data",
                    expected_appreciation=0.0,
                    by_horizon={},
                    peak_during_hold=peak,
                    is_floor_trip=is_floor,
                )
            )
            continue

        history = history_at(corpus, trip.player_id, trip.buy_date)
        trend = TrendService.analyze(history, mv_buy).to_dict()
        day_number = day_for_kickoff(kickoffs, trip.buy_date)
        avg_points = average_points_at(corpus, trip.player_id, season=SEASON, day_number=day_number)
        branch = label_for(trend, avg_points, mv_buy)
        _, expected_appreciation = reconstruct_branch(trend, avg_points, market_value=mv_buy)

        by_horizon: dict[int, Decomposition] = {}
        for h in horizons:
            mv_h = mv_nearest(corpus_db, trip.player_id, trip.buy_date + h * SECONDS_PER_DAY)
            if mv_h is None:
                if not is_floor:
                    censored[h] += 1
                continue
            by_horizon[h] = decompose(trip, mv_buy=mv_buy, mv_h=mv_h)

        rows.append(
            TripRow(
                trip=trip,
                mv_buy=mv_buy,
                branch=branch,
                expected_appreciation=expected_appreciation,
                by_horizon=by_horizon,
                peak_during_hold=peak,
                is_floor_trip=is_floor,
            )
        )

    return DiagnosisResult(rows=rows, horizons=horizons, censored=censored)
