"""The bid: the premium is a cost with a ceiling (spec v3 §5.5, ADR-0004).

The ceiling is the league's median winning premium in the price band, ours
excluded. v1 bids the ceiling itself on every listing the plan wants. In M1
that won 45 of 74 top-decile listings at a median +38% return.

The refinement `argmax F(p)·(V − p·MV)` needs a listing win curve F that can be
estimated without looking ahead. It is an ablation once paper trading has
produced one. The floor (Kickbase declines a bid below the value at expiry) is
met by construction: the bid's base is the forecast value at expiry and the
premium is never negative.
"""

from __future__ import annotations

from collections.abc import Iterable
from statistics import median

MIN_SAMPLE = 20  # rival buys a band needs before its median is trusted


def ceiling(rival_premiums: Iterable[float]) -> float | None:
    """The band's median rival premium, floored at zero; None when too thin to trust."""
    premiums = list(rival_premiums)
    if len(premiums) < MIN_SAMPLE:
        return None
    return max(0.0, median(premiums))
