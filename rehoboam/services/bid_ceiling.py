"""How far above market value a bid may go. One definition, two callers.

`SmartBidding` sizes a bid and `safety_gate.check_buy` verifies one. Before
REH-99 they used different constants — 20%/30% in the bidder, 8% in the gate,
never connected — so Telegram offered buys the gate would refuse and the
Approve button could not work. Both now call `max_allowed_bid`, which is why
the number a proposal shows is always a number that can execute.

    max_allowed_bid = market_value + max(floor, market_value x tier_pct)

**The floor exists because a percentage is the wrong unit at the cheap end.**
Fabio Chiarodia, market value EUR 591,389, was lost to a winner paying
EUR 156,085 over while an 8% cap held the bot to EUR 47,311 — a EUR 109k gap
against a EUR 62M budget.

**The tier percentage exists because the round-trip toll is not universal.**
REH-64 measured a 12.2% toll and capped every buy at 8% on the strength of it,
but that toll is flip economics: it only bites if you sell. A must-have held to
score points all season has no round trip and amortises the premium over 30+
matchdays. A marginal churn candidate genuinely does pay it, and stays tight.

Across the 12 lost auctions carrying `winning_overbid_pct`, a flat 8% would
have won none; the lowest winning overbid was 8.4%.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

#: Price bands: ``((lower_market_value, max_pct), ...)`` sorted by lower bound.
#: The band a market value falls in caps the tier percentage from above.
PriceBands = tuple[tuple[int, float], ...]


def parse_price_bands(raw: str) -> PriceBands:
    """Parse ``OVERBID_PRICE_BANDS``: ``"5000000:35,15000000:20"``.

    Each entry is ``<lower market value in euros>:<max overbid pct>``. Empty
    means no bands — the tier percentage alone. Order does not matter; the
    result is sorted by lower bound. Malformed input raises ``ValueError`` so a
    typo fails at startup, not at bid time.
    """
    text = (raw or "").strip()
    if not text:
        return ()
    bands: dict[int, float] = {}
    for entry in text.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            lower_s, pct_s = entry.split(":")
            lower, pct = int(lower_s.strip()), float(pct_s.strip())
        except ValueError as e:
            raise ValueError(
                f"OVERBID_PRICE_BANDS entry {entry!r} is not '<market value>:<pct>'"
            ) from e
        if lower < 0 or pct < 0:
            raise ValueError(f"OVERBID_PRICE_BANDS entry {entry!r} must not be negative")
        if lower in bands:
            raise ValueError(f"OVERBID_PRICE_BANDS names the band at {lower} twice")
        bands[lower] = pct
    return tuple(sorted(bands.items()))


def price_band_pct(market_value: int, price_bands: Sequence[tuple[int, float]]) -> float | None:
    """The band cap for this market value, or None when no band covers it."""
    cap: float | None = None
    for lower, pct in price_bands:
        if market_value >= lower:
            cap = pct
    return cap


class Tier(str, Enum):
    """Marginal-EP bands, mirroring the ones `SmartBidding` already assigns."""

    MARGINAL = "marginal"
    SOLID = "solid_upgrade"
    STRONG = "strong_upgrade"
    MUST_HAVE = "must_have"


#: Applied when the tier is unknown — a stale or corrupted proposal must not
#: buy itself a larger ceiling by losing its tier.
FALLBACK_TIER = Tier.MARGINAL


def tier_for_marginal_gain(
    marginal_ep_gain: float,
    *,
    must_have: float,
    strong: float,
    solid: float,
) -> Tier:
    """Band a marginal EP gain, using the thresholds `SmartBidding` is given.

    Thresholds are passed in rather than read from config so this stays pure
    and so the bidder and the gate cannot drift onto different bands.
    """
    if marginal_ep_gain >= must_have:
        return Tier.MUST_HAVE
    if marginal_ep_gain >= strong:
        return Tier.STRONG
    if marginal_ep_gain >= solid:
        return Tier.SOLID
    return Tier.MARGINAL


def max_allowed_bid(
    *,
    market_value: int,
    tier: Tier | str | None,
    floor_eur: int,
    tier_pcts: Mapping[Tier, float],
    price_bands: Sequence[tuple[int, float]] = (),
) -> int:
    """The highest bid permitted for this player, in euros.

    Returns 0 for a non-positive market value: `check_buy` reports that as its
    own failure, and this must not hand back a spendable ceiling computed from
    a nonsense input.

    **The price band caps the tier from above.** The tier says how much a
    player is worth to us; the band says what it takes to win at his price.
    Measured 2026-09-21 on `auction_outcomes`: at 15m+ we bid +24–26% and the
    winners paid a median of +9%; under 5m we bid +13–15% and winners paid
    +59%. A band only ever lowers a ceiling, so the tiers keep their
    discipline at the cheap end and the euro floor stays.
    """
    if market_value <= 0:
        return 0

    resolved = FALLBACK_TIER
    if tier is not None:
        try:
            resolved = Tier(tier)
        except ValueError:
            resolved = FALLBACK_TIER

    pct = tier_pcts.get(resolved, tier_pcts[FALLBACK_TIER])
    band = price_band_pct(market_value, price_bands)
    if band is not None:
        pct = min(pct, band)
    return market_value + max(floor_eur, int(market_value * pct / 100.0))


@dataclass(frozen=True)
class BidCeilingPolicy:
    """The configured ceiling, carried as one value instead of loose numbers.

    Built from `Settings.bid_ceiling_policy()` and handed to both
    `SmartBidding` and `safety_gate.check_buy`, so neither can be constructed
    with half the policy or a stale copy of it.
    """

    floor_eur: int
    tier_pcts: Mapping[Tier, float]
    price_bands: PriceBands = ()

    def max_bid(self, market_value: int, tier: Tier | str | None) -> int:
        """The highest bid permitted for this player, in euros."""
        return max_allowed_bid(
            market_value=market_value,
            tier=tier,
            floor_eur=self.floor_eur,
            tier_pcts=self.tier_pcts,
            price_bands=self.price_bands,
        )
