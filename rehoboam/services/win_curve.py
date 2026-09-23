"""The premium it takes to win, read off what the league actually paid.

Rival offers are never visible on Kickbase. What is visible, afterwards, is
every completed transfer, priced against the market value in force that day
(`rehoboam.transfer_premiums`, migration 024). Sorted per price band, those
premiums ARE the distribution of winning bids: a bid at the band's 75th
percentile beats three of four listings that changed hands there.

`SmartBidding` asks this curve for a premium by tier — must-have at p75,
strong at p50, solid at p25, marginal nothing beyond the euro floor — instead
of stacking typed constants. Measured 2026-09-23 on 1,397 buys: the median
winning premium is +8–10% in every band, the p75 is +31.7% under 5m, +21.1%
at 5–15m and +18.4% at 15m+, while our must-have ceiling was 35%.

Pure. A band with fewer than `min_sample` buys borrows the pooled curve; a
pooled curve with fewer than `min_sample` says nothing (`None`), and the
bidder falls back to its static stack. No curve is ever extrapolated.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from rehoboam.services.ceiling_derivation import WinnerRow, band_of, percentile


@dataclass(frozen=True)
class CurvePoint:
    """One premium read off the curve, with the evidence behind it."""

    premium_pct: float
    quantile: float
    band_lower: int
    sample: int
    pooled: bool  # True when the band was thin and the whole league's curve was used


class WinCurve:
    def __init__(
        self,
        *,
        bands: Sequence[int],
        premiums_by_band: Mapping[int, Sequence[float]],
        min_sample: int,
    ) -> None:
        self.bands = tuple(sorted({int(b) for b in bands}))
        if not self.bands:
            raise ValueError("at least one band lower bound is required")
        self.min_sample = int(min_sample)
        self._by_band = {
            lower: tuple(sorted(float(p) for p in premiums_by_band.get(lower, ())))
            for lower in self.bands
        }
        self._pooled = tuple(sorted(p for ps in self._by_band.values() for p in ps))

    @classmethod
    def from_rows(
        cls, rows: Iterable[WinnerRow], *, bands: Sequence[int], min_sample: int
    ) -> WinCurve:
        lowers = sorted({int(b) for b in bands})
        grouped: dict[int, list[float]] = {lower: [] for lower in lowers}
        for row in rows:
            band = band_of(row.market_value, lowers)
            if band is not None:
                grouped[band].append(float(row.premium_pct))
        return cls(bands=lowers, premiums_by_band=grouped, min_sample=min_sample)

    @property
    def total(self) -> int:
        return len(self._pooled)

    def sample(self, market_value: int) -> int:
        band = band_of(int(market_value), self.bands)
        return len(self._by_band[band]) if band is not None else 0

    def _evidence(self, market_value: int) -> tuple[int, Sequence[float], bool] | None:
        band = band_of(int(market_value), self.bands)
        if band is None:
            return None
        own = self._by_band[band]
        if len(own) >= self.min_sample:
            return band, own, False
        if len(self._pooled) >= self.min_sample:
            return band, self._pooled, True
        return None

    def premium_at(self, market_value: int, quantile: float) -> CurvePoint | None:
        """The premium at which `quantile` of winning bids in this band were at or below.

        Never below zero: a negative winning premium (a listing that cleared
        under market value) is evidence a cheap bid can win, not a reason to
        bid under market value ourselves — the market-value floor governs.
        """
        evidence = self._evidence(market_value)
        if evidence is None:
            return None
        band, premiums, pooled = evidence
        q = min(1.0, max(0.0, float(quantile)))
        return CurvePoint(
            premium_pct=max(0.0, percentile(premiums, q)),
            quantile=q,
            band_lower=band,
            sample=len(premiums),
            pooled=pooled,
        )

    def win_share(self, market_value: int, premium_pct: float) -> float | None:
        """The share of winning bids in this band at or below `premium_pct`."""
        evidence = self._evidence(market_value)
        if evidence is None:
            return None
        _band, premiums, _pooled = evidence
        return sum(1 for p in premiums if p <= premium_pct) / len(premiums)
