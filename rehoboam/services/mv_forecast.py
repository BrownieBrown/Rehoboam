"""The market-value forecast (spec docs/superpowers/specs/2026-09-17-mv-forecast-design.md).

Pure: no I/O. Kickbase updates every market value once a day at about 22:00
Berlin time. Over a year of daily values, the next update moved like the last
one, a little weaker: `momentum × last move`, with the last move capped,
missed by about a third as much as "no change" and had the direction right
about 95 % of the time. What it cannot see is a turn; the scoring below is
what makes a better rule measurable.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

METHOD = "momentum-v1"
BERLIN = ZoneInfo("Europe/Berlin")

#: Kickbase moves market values "gegen 22 Uhr"; a reading between these two
#: times could be on either side of that move, so it is used for nothing.
PRE_CUTOFF = time(21, 45)
POST_START = time(22, 30)

PRE = "pre"
POST = "post"

SCORED = "scored"
UNSCORABLE = "unscorable"


@dataclass(frozen=True)
class Forecast:
    player_id: str
    target_day: date
    base_mv: int
    last_change: int
    predicted_change: int
    predicted_pct: float  # fraction of base_mv


def forecast(
    player_id: str,
    market_value: int,
    last_change: int,
    target_day: date,
    *,
    momentum: float,
    cap: float,
) -> Forecast | None:
    """The next update's change, from the last one. None without a positive
    value both before and after the last update."""
    previous = market_value - last_change
    if market_value <= 0 or previous <= 0:
        return None
    last_pct = last_change / previous
    pct = momentum * max(-cap, min(cap, last_pct))
    return Forecast(
        player_id=str(player_id),
        target_day=target_day,
        base_mv=int(market_value),
        last_change=int(last_change),
        predicted_change=round(market_value * pct),
        predicted_pct=pct,
    )


def berlin_today(now: float) -> date:
    return datetime.fromtimestamp(now, tz=BERLIN).date()


def reading_window(fetched_at: float) -> tuple[date, str] | None:
    """Which update a reading sits against: `(day, PRE)` for a reading taken
    before that Berlin day's update, `(day, POST)` for one taken after it, and
    None in the ambiguous window between. A reading after midnight belongs to
    the new day and is PRE for it — which is also "after yesterday's update"."""
    local = datetime.fromtimestamp(fetched_at, tz=BERLIN)
    t = local.time()
    if t < PRE_CUTOFF:
        return (local.date(), PRE)
    if t >= POST_START:
        return (local.date(), POST)
    return None


@dataclass(frozen=True)
class Score:
    outcome: str  # SCORED or UNSCORABLE
    actual_change: int | None
    actual_pct: float | None  # fraction of base_mv


def score(base_mv: int, next_market_value: int, next_change: int) -> Score:
    """Score a forecast with the first reading after its update.

    That reading's value minus its change is the value before the update,
    which must be the forecast's base; otherwise the days did not line up and
    the reading proves nothing about this forecast.
    """
    if base_mv <= 0 or next_market_value - next_change != base_mv:
        return Score(UNSCORABLE, None, None)
    return Score(SCORED, int(next_change), next_change / base_mv)


@dataclass(frozen=True)
class BacktestResult:
    momentum: float
    cap: float
    forecasts: int
    directional: int  # forecast and real change both non-zero
    direction_hits: int
    mae_pp: float  # mean |real − forecast|, percentage points
    baseline_mae_pp: float  # the same for "no change"

    @property
    def direction_rate(self) -> float | None:
        return self.direction_hits / self.directional if self.directional else None


def backtest(series: Iterable[Sequence[int]], *, momentum: float, cap: float) -> BacktestResult:
    """Replay `forecast` over each run of consecutive daily values (oldest first)."""
    n = directional = hits = 0
    miss = baseline = 0.0
    for values in series:
        for i in range(1, len(values) - 1):
            f = forecast(
                "",
                values[i],
                values[i] - values[i - 1],
                date.min,
                momentum=momentum,
                cap=cap,
            )
            if f is None:
                continue
            actual_change = values[i + 1] - values[i]
            actual_pct = actual_change / f.base_mv
            n += 1
            miss += abs(actual_pct - f.predicted_pct)
            baseline += abs(actual_pct)
            if f.predicted_change != 0 and actual_change != 0:
                directional += 1
                hits += (f.predicted_change > 0) == (actual_change > 0)
    return BacktestResult(
        momentum=momentum,
        cap=cap,
        forecasts=n,
        directional=directional,
        direction_hits=hits,
        mae_pp=100 * miss / n if n else 0.0,
        baseline_mae_pp=100 * baseline / n if n else 0.0,
    )
