"""The premium beyond which a buy stops paying back: the cost of winning.

`transfer_outcomes` (migration 024) pairs every buy in the league with the
same manager's next sale. Bucketed by the premium paid over market value,
the median resale profit says where paying more stops being an investment
and becomes a knowing loss. Measured 2026-09-23 on the league's buys:

    15m+   : break-even under +3%  — above it 9-18% of resales profit,
             median -5% to -20% of price, and points do not rise with premium
    5-15m  : break-even about +12%
    under 5m: no crossing — a high premium marks the rising player, and
             median resale profit climbs WITH the premium (+22% at +9..12)

The win curve says what it takes to win; this says what winning costs. A
buy made for points (a must-have) may knowingly pay past break-even and the
board names the expected loss; anything bought with resale in mind — a
profit flip, a solid or strong upgrade that will be churned — is capped here.

Pure. A band needs `min_sample` resold buys per bucket before a bucket can
declare the crossing; thinner buckets are skipped, and a band with no
negative bucket has no cap (`None`).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from statistics import median

from rehoboam.services.ceiling_derivation import band_of


@dataclass(frozen=True)
class OutcomeRow:
    """One league buy: its price band, the premium paid, and the resale result."""

    market_value: int
    premium_pct: float
    profit_pct: float | None  # realised profit as % of price; None when never resold


@dataclass(frozen=True)
class BreakEven:
    premium_pct: float  # the highest premium up to which the median resale still pays back
    band_lower: int
    sample: int  # resold buys the crossing rests on
    resale_profit_pct_at: float  # median resale profit just past the crossing


class ProfitCurve:
    def __init__(
        self,
        *,
        bands: Sequence[int],
        step_pct: float = 3.0,
        min_sample: int = 10,
        max_premium_pct: float = 45.0,
    ) -> None:
        self.bands = tuple(sorted({int(b) for b in bands}))
        if not self.bands:
            raise ValueError("at least one band lower bound is required")
        self.step_pct = float(step_pct)
        self.min_sample = int(min_sample)
        self.max_premium_pct = float(max_premium_pct)
        self._rows: dict[int, list[OutcomeRow]] = {lower: [] for lower in self.bands}

    @classmethod
    def from_rows(
        cls,
        rows: Iterable[OutcomeRow],
        *,
        bands: Sequence[int],
        step_pct: float = 3.0,
        min_sample: int = 10,
        max_premium_pct: float = 45.0,
    ) -> ProfitCurve:
        curve = cls(
            bands=bands, step_pct=step_pct, min_sample=min_sample, max_premium_pct=max_premium_pct
        )
        for row in rows:
            band = band_of(row.market_value, curve.bands)
            if band is not None and 0.0 <= row.premium_pct < curve.max_premium_pct:
                curve._rows[band].append(row)
        return curve

    def _buckets(self, band: int) -> list[tuple[float, list[float]]]:
        """[(bucket lower premium, resale profits of resold buys in it)], ascending."""
        by_step: dict[int, list[float]] = {}
        for row in self._rows[band]:
            if row.profit_pct is None:
                continue
            by_step.setdefault(int(row.premium_pct // self.step_pct), []).append(row.profit_pct)
        return [(k * self.step_pct, by_step[k]) for k in sorted(by_step)]

    def break_even(self, market_value: int) -> BreakEven | None:
        """The premium past which the median resale in this band loses money.

        Walks the buckets upward and stops at the first one, with at least
        `min_sample` resold buys, whose median resale profit is negative;
        the crossing is that bucket's lower edge. No such bucket: no cap.
        """
        band = band_of(int(market_value), self.bands)
        if band is None:
            return None
        for lower, profits in self._buckets(band):
            if len(profits) < self.min_sample:
                continue
            loss = median(profits)
            if loss < 0:
                return BreakEven(
                    premium_pct=lower,
                    band_lower=band,
                    sample=len(profits),
                    resale_profit_pct_at=loss,
                )
        return None

    def expected_resale_pct(self, market_value: int, premium_pct: float) -> float | None:
        """Median resale profit (% of price) of buys in this band at this premium."""
        band = band_of(int(market_value), self.bands)
        if band is None or premium_pct < 0:
            return None
        for lower, profits in self._buckets(band):
            if lower <= premium_pct < lower + self.step_pct and len(profits) >= self.min_sample:
                return median(profits)
        return None
