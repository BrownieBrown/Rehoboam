"""Propose price-band overbid caps from what the league's buyers paid (`derive-ceilings`).

Pure: the command reads the store, hands the rows here, prints the report and
changes nothing. The values go into `OVERBID_PRICE_BANDS` by hand.

Rival offers are never visible on Kickbase. What is visible, afterwards, is
every transfer: buyer, price and date. Priced against the market value in
force that day (`rehoboam.transfer_premiums`, migration 024) each buy says
what it took to win that listing. That is the evidence here — not only the
auctions WE entered (`auction_outcomes`), which are printed beside it so our
own bidding can be compared with the league's.

A band's proposal is the 75th percentile of the premium winners paid in that
band — a bid there beats three of four winning bids — and the whole proposal
is withheld below `min_winners` priced buys, because a thin sample must not
move real-money caps without someone reading `n`. The table is printed either
way.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from statistics import median


@dataclass(frozen=True)
class WinnerRow:
    """One completed buy in the league, priced against the day's market value."""

    market_value: int
    premium_pct: float


@dataclass(frozen=True)
class OurBid:
    """One auction we entered, from `auction_outcomes`."""

    market_value: int
    our_overbid_pct: float
    won: bool


@dataclass(frozen=True)
class BandReport:
    lower: int
    n_winners: int
    winner_p25: float | None
    winner_p50: float | None
    winner_p75: float | None
    n_our_bids: int
    n_our_wins: int
    our_median: float | None


@dataclass(frozen=True)
class CeilingReport:
    bands: tuple[BandReport, ...]
    n_winners: int
    min_winners: int
    proposal: dict[int, float] | None

    @property
    def env_line(self) -> str | None:
        if not self.proposal:
            return None
        body = ",".join(f"{lower}:{pct:.1f}" for lower, pct in sorted(self.proposal.items()))
        return f"OVERBID_PRICE_BANDS={body}"


def _percentile(values: Sequence[float], q: float) -> float:
    """Linear interpolation between order statistics (numpy's default)."""
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def band_of(market_value: int, lowers: Sequence[int]) -> int | None:
    """The band a market value falls in; None below the first band."""
    chosen: int | None = None
    for lower in lowers:
        if market_value >= lower:
            chosen = lower
    return chosen


def derive_price_bands(
    winners: Sequence[WinnerRow],
    *,
    bands: Sequence[int],
    min_winners: int,
    our_bids: Sequence[OurBid] = (),
) -> CeilingReport:
    lowers = sorted({int(b) for b in bands})
    if not lowers:
        raise ValueError("at least one band lower bound is required")

    won: dict[int, list[float]] = {lower: [] for lower in lowers}
    ours: dict[int, list[OurBid]] = {lower: [] for lower in lowers}
    for w in winners:
        band = band_of(w.market_value, lowers)
        if band is not None:
            won[band].append(w.premium_pct)
    for b in our_bids:
        band = band_of(b.market_value, lowers)
        if band is not None:
            ours[band].append(b)

    reports: list[BandReport] = []
    for lower in lowers:
        premiums = won[lower]
        mine = ours[lower]
        reports.append(
            BandReport(
                lower=lower,
                n_winners=len(premiums),
                winner_p25=_percentile(premiums, 0.25) if premiums else None,
                winner_p50=_percentile(premiums, 0.50) if premiums else None,
                winner_p75=_percentile(premiums, 0.75) if premiums else None,
                n_our_bids=len(mine),
                n_our_wins=sum(1 for b in mine if b.won),
                our_median=median(b.our_overbid_pct for b in mine) if mine else None,
            )
        )

    n_winners = sum(r.n_winners for r in reports)
    proposal: dict[int, float] | None = None
    if n_winners >= min_winners:
        proposal = {r.lower: r.winner_p75 for r in reports if r.winner_p75 is not None}

    return CeilingReport(
        bands=tuple(reports),
        n_winners=n_winners,
        min_winners=min_winners,
        proposal=proposal,
    )
