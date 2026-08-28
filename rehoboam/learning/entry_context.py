"""What the market looked like when we bought a player (REH-104).

`flip_outcomes.trend_at_buy` has existed since the table was created and has
been NULL for every row. `LearningTracker.record_flip_outcome` runs at SELL
time, and `tracked_purchases` stores only price and date — the entry context
was simply never available to write. REH-34 ("which buy traits predict a
profitable flip?") has been blocked on that gap.

This reconstructs the context from `player_mv_history` instead of capturing it
at buy time. Two reasons. The buy path is the code that spends real money and
does not need another field threaded through it; and reconstruction can be
applied to flips that have ALREADY closed, so the 151 rows of history become
measurable now rather than after a season of new trades.

The definitions mirror `services/trend_service.py` deliberately: `trend_pct` is
the 14-day change and the direction thresholds are +/-5% (trend_service.py:328).
A reconstruction on any other basis would not correspond to the rule that
actually gated the buy, which is `profit_trader`'s `trend == "rising" and
trend_pct > 5`.

Pure — it takes rows and returns a value, so it is exhaustively testable and
the live path and the backfill cannot drift apart.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

DAY_SECONDS = 86400.0

#: How far a snapshot may sit from the instant we want before it stops being
#: evidence. Measured: all 151 historical flips have an MV point within 0.45
#: days of their buy, so 3 days is loose enough to never bind in practice and
#: tight enough that a stale value can never masquerade as the entry price.
DEFAULT_TOLERANCE_DAYS = 3.0

#: Trend lookback and peak window, both matching the live trend service.
TREND_LOOKBACK_DAYS = 14.0
PEAK_WINDOW_DAYS = 30.0

#: trend_service.py:328-333 — the same boundaries, so a reconstructed direction
#: means what the bot meant by it at decision time.
RISING_THRESHOLD_PCT = 5.0
FALLING_THRESHOLD_PCT = -5.0


@dataclass(frozen=True)
class EntryContext:
    """The entry conditions of one purchase. Every field is optional: thin MV
    history is normal for a newly listed player, and a missing field must read
    as "not known" rather than as a zero that would pollute the aggregates."""

    mv_at_buy: int | None = None
    trend_pct_at_buy: float | None = None
    trend_at_buy: str | None = None
    #: Distance from the highest market value in the 30 days up to the buy.
    #: Always <= 0. Zero means we bought AT the 30-day peak — the condition the
    #: REH-104 spike found in 30% of flips, whose median then fell 14.2%.
    pct_below_peak_30d_at_buy: float | None = None


def _nearest(rows: list[tuple[float, int]], target: float, tolerance_days: float) -> int | None:
    """Market value of the snapshot closest to `target`, or None if all are stale."""
    best: tuple[float, int] | None = None
    limit = tolerance_days * DAY_SECONDS
    for ts, mv in rows:
        gap = abs(ts - target)
        if gap <= limit and (best is None or gap < best[0]):
            best = (gap, mv)
    return best[1] if best else None


def entry_context(
    mv_rows: Iterable[tuple[float, int]],
    buy_date: float,
    *,
    tolerance_days: float = DEFAULT_TOLERANCE_DAYS,
) -> EntryContext:
    """Reconstruct the entry conditions of a purchase from recorded values.

    `mv_rows` is any iterable of ``(snapshot_epoch, market_value)``; order does
    not matter. Rows dated after `buy_date` are ignored for the peak window —
    including them would let the field see the outcome it is meant to predict.
    """
    rows = [(float(ts), int(mv)) for ts, mv in mv_rows]
    if not rows:
        return EntryContext()

    mv_at_buy = _nearest(rows, buy_date, tolerance_days)
    if mv_at_buy is None:
        return EntryContext()

    trend_pct: float | None = None
    trend: str | None = None
    reference = _nearest(rows, buy_date - TREND_LOOKBACK_DAYS * DAY_SECONDS, tolerance_days)
    if reference:  # a zero or missing reference has no meaningful percentage
        raw_pct = (mv_at_buy - reference) / reference * 100.0
        # Classify on the raw value and round only for storage. Rounding first
        # collapses +5.001% onto the 5.0 boundary and reports it as "stable",
        # which would mislabel exactly the marginal entries this field exists
        # to study.
        if raw_pct > RISING_THRESHOLD_PCT:
            trend = "rising"
        elif raw_pct < FALLING_THRESHOLD_PCT:
            trend = "falling"
        else:
            trend = "stable"
        trend_pct = round(raw_pct, 2)

    pct_below_peak: float | None = None
    window_start = buy_date - PEAK_WINDOW_DAYS * DAY_SECONDS
    in_window = [mv for ts, mv in rows if window_start <= ts <= buy_date]
    peak = max(in_window, default=0)
    if peak > 0:
        pct_below_peak = round((mv_at_buy - peak) / peak * 100.0, 2)

    return EntryContext(
        mv_at_buy=mv_at_buy,
        trend_pct_at_buy=trend_pct,
        trend_at_buy=trend,
        pct_below_peak_30d_at_buy=pct_below_peak,
    )
