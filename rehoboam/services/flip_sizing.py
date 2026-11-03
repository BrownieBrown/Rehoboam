"""How big a flip may be (2026-10-07): a slice of the float, never debt.

The Tietz bid was one 19.9m name on a -9.6m wallet. With a measured 68%
win rate, a median +10.9% and a 25th-percentile -5.5% on the league's
rising-night entries, a fractional-Kelly stake is a modest slice of free
capital spread over a few names. The float is free cash plus what is
already parked in marked flips (capital in a flip is still flip capital);
a negative wallet is no float at all. Pure.
"""

from __future__ import annotations


def flip_size_reason(
    price: int,
    *,
    cash: int,
    held_flip_value: int,
    open_flips: int,
    max_fraction: float,
    max_open: int,
    first_flip_fraction: float | None = None,
) -> str | None:
    """Why this flip is too big or one too many, or None when it fits.

    ``first_flip_fraction`` (2026-11-03) is the slice the first open flip may
    take. With nothing open the float is the bare wallet, and 34% of a 14m
    wallet is below every 5m+ starter the momentum entry passes; one name at
    a larger slice is still a bounded loss (stop-loss × stake), while the
    second and later flips are back at ``max_fraction``. It only ever widens
    the rule: a value below ``max_fraction`` changes nothing.
    """
    if open_flips >= max_open:
        return f"{open_flips} flips already open (max {max_open})"
    float_ = max(0, int(cash)) + max(0, int(held_flip_value))
    if float_ <= 0:
        return f"no float: the wallet is EUR {int(cash):,} and nothing is parked in flips"
    fraction = float(max_fraction)
    if open_flips == 0 and first_flip_fraction is not None:
        fraction = max(fraction, float(first_flip_fraction))
    cap = int(float_ * fraction)
    if int(price) > cap:
        return (
            f"EUR {int(price):,} is more than {fraction:.0%} of the EUR {float_:,} float "
            f"(max EUR {cap:,})"
        )
    return None
