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

from bisect import bisect_left
from collections.abc import Iterable
from statistics import median

MIN_SAMPLE = 20  # rival buys a band needs before its median is trusted
VALUE_CAP_QUANTILE = 0.90  # the value bid never pays more than the band's p90 rival
OUTBID_STEP = 0.001  # a rival's premium must be beaten strictly


def ceiling(rival_premiums: Iterable[float]) -> float | None:
    """The band's median rival premium, floored at zero; None when too thin to trust."""
    premiums = list(rival_premiums)
    if len(premiums) < MIN_SAMPLE:
        return None
    return max(0.0, median(premiums))


def value_premium(rival_premiums: Iterable[float], drift: float) -> float | None:
    """The premium maximising ``F(p)·(drift − p)`` (M2-15).

    ``F(p)`` is the share of the band's past rival buys below ``p``: the odds of
    winning a contested listing. Listings nobody else bought are won at the
    value anyway, so F understates the odds and the bid errs low. Candidates are
    each rival premium plus `OUTBID_STEP`, up to the band's p90, and zero.
    """
    premiums = sorted(rival_premiums)
    if len(premiums) < MIN_SAMPLE:
        return None
    cap = premiums[round(VALUE_CAP_QUANTILE * (len(premiums) - 1))]
    n = len(premiums)
    best, best_value = 0.0, bisect_left(premiums, 0.0) / n * drift
    for r in premiums:
        if r > cap:
            break
        p = max(0.0, r) + OUTBID_STEP
        value = bisect_left(premiums, p) / n * (drift - p)
        if value > best_value:
            best, best_value = p, value
    return best
