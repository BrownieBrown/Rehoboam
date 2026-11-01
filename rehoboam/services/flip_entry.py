"""Which Kickbase listings to flip: the start of a run, not its end.

Measured 2026-11-01 on every Kickbase listing since 2026-09-16 (the market
value 14 days after the listing expired, against the value at listing):

* "rose last night and +5% or more over three nights" was the signal: under
  5m a median +46.9% (82 listings), 5-15m with lineup code 1-2 +19.8% (33);
  every other listing about 0%. It held in October (+16.9%, 67% rose) as in
  September (+57.0%, 83%).
* The old filters waited for evidence that only exists once the run is over.
  A 14-day trend above 5% dropped 25 of those listings (median +40.2%; at
  5-15m 13 of 13 rose), a season average under 20 dropped 45 (+42.4%). We
  bid on 10 of the 82.
* A season average under 20 is no reason to skip a cheap player who has just
  entered the eleven (October, code 1-2: +45%, none lost 10% -- three
  listings, the thinnest evidence here), and is a reason to skip the rest
  (code 3-5: +3.6%, 29% lost 10%).
* 5-15m without code 1-2 lost money in both months; 15m+ listings rose a
  median 2.2% and two of 38 rose 10% -- not a flip.

The night-level guards from the break review stay: a turned last move or a
falling forecast vetoes, a last move under the floor or at under half the
night before is a run at its end.

Pure: the caller passes the numbers in. Each refusal has a short name so the
search can count what it drops (`FlipEntry.reason`).
"""

from __future__ import annotations

from dataclasses import dataclass

#: Kickbase lineup codes that mean "in the eleven" (1 starter, 2 likely).
STARTER_CODES = frozenset({1, 2})


@dataclass(frozen=True)
class FlipEntry:
    ok: bool
    #: "ok", or the name of the rule that refused.
    reason: str
    #: The appreciation the flip is priced on; 0.0 when refused.
    expected_pct: float = 0.0


def flip_entry(
    *,
    market_value: int,
    average_points: float | None,
    lineup_code: int | None,
    last_move_pct: float | None,
    prev_move_pct: float | None,
    move_3d_pct: float | None,
    forecast_pct: float | None,
    min_3d_move_pct: float,
    min_last_move_pct: float,
    max_decel_ratio: float,
    expected_gain_pct: float,
    min_average_points: float,
    starter_band_from: int,
    max_market_value: int,
) -> FlipEntry:
    """Is this Kickbase listing the start of a run worth flipping?"""
    mv = int(market_value or 0)
    if mv <= 0 or mv >= int(max_market_value):
        return FlipEntry(False, "price_band")
    if last_move_pct is None or move_3d_pct is None:
        return FlipEntry(False, "no_history")
    if last_move_pct < 0 or (forecast_pct is not None and forecast_pct < 0):
        return FlipEntry(False, "turned")
    if last_move_pct < min_last_move_pct:
        return FlipEntry(False, "weak_last_move")
    if (
        max_decel_ratio > 0
        and prev_move_pct is not None
        and prev_move_pct > 0
        and last_move_pct < max_decel_ratio * prev_move_pct
    ):
        return FlipEntry(False, "decelerating")
    if move_3d_pct < min_3d_move_pct:
        return FlipEntry(False, "no_3d_momentum")

    starter = lineup_code in STARTER_CODES
    if mv >= int(starter_band_from):
        if not starter:
            return FlipEntry(False, "not_starter")
    elif float(average_points or 0.0) < min_average_points and not starter:
        return FlipEntry(False, "low_average")
    return FlipEntry(True, "ok", float(expected_gain_pct))
