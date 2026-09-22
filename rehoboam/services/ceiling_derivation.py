"""Propose price-band overbid caps from the auction ledger (`derive-ceilings`).

Pure: the command reads `auction_outcomes`, hands the rows here, prints the
report and changes nothing. The values go into `OVERBID_PRICE_BANDS` by hand.

A band's proposal is the 75th percentile of what winners paid over market
value in that band — a bid there beats three of four winning bids — and the
whole proposal is withheld below `min_winners` auctions carrying a winner's
price, because a thin week's sample must not move real-money caps without
someone reading `n`. The table is printed either way.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from statistics import median


@dataclass(frozen=True)
class AuctionRow:
    market_value: int
    our_overbid_pct: float
    won: bool
    winning_overbid_pct: float | None


@dataclass(frozen=True)
class BandReport:
    lower: int
    n_auctions: int
    n_won: int
    n_with_winner: int
    our_median: float | None
    winner_p25: float | None
    winner_p50: float | None
    winner_p75: float | None


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


def derive_price_bands(
    rows: Sequence[AuctionRow],
    *,
    bands: Sequence[int],
    min_winners: int,
) -> CeilingReport:
    lowers = sorted({int(b) for b in bands})
    if not lowers:
        raise ValueError("at least one band lower bound is required")

    def band_of(mv: int) -> int:
        chosen = lowers[0]
        for lower in lowers:
            if mv >= lower:
                chosen = lower
        return chosen

    grouped: dict[int, list[AuctionRow]] = {lower: [] for lower in lowers}
    for row in rows:
        if row.market_value < lowers[0]:
            continue
        grouped[band_of(row.market_value)].append(row)

    reports: list[BandReport] = []
    n_winners = 0
    for lower in lowers:
        group = grouped[lower]
        winners = [r.winning_overbid_pct for r in group if r.winning_overbid_pct is not None]
        ours = [r.our_overbid_pct for r in group]
        n_winners += len(winners)
        reports.append(
            BandReport(
                lower=lower,
                n_auctions=len(group),
                n_won=sum(1 for r in group if r.won),
                n_with_winner=len(winners),
                our_median=median(ours) if ours else None,
                winner_p25=_percentile(winners, 0.25) if winners else None,
                winner_p50=_percentile(winners, 0.50) if winners else None,
                winner_p75=_percentile(winners, 0.75) if winners else None,
            )
        )

    proposal: dict[int, float] | None = None
    if n_winners >= min_winners:
        proposal = {b.lower: b.winner_p75 for b in reports if b.winner_p75 is not None}

    return CeilingReport(
        bands=tuple(reports),
        n_winners=n_winners,
        min_winners=min_winners,
        proposal=proposal,
    )
