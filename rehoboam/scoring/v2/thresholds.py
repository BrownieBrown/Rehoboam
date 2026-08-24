"""Derive decision thresholds from the v2 marginal-gain distribution.

The constants in ``config.py`` and ``bidding_strategy.py`` were calibrated
against a 0-100 index whose range was **87.1** points and whose median sat near
72. Real points span **196.7** with a median of 34.8, so the same numeric
constant now means something entirely different — ``marginal_ep_gain >= 20``
went from "rare must-have" to "more than half of all upgrade candidates".

The old firing rates cannot be recovered: ``predicted_eps.marginal_ep_gain`` is
NULL on all 351 production rows. So thresholds are derived from **rarity**,
which is the property the tiers were always meant to encode.

Rarity targets:

    must_have       top 15%   — defend hard, worth an aggressive bid
    strong_upgrade  top 30%
    solid_upgrade   top 50%

**Rarity is measured over QUALIFYING candidates, not all positive ones.** The
tiers only ever apply to a player the bot is already willing to buy, so
measuring across everything with a positive gain proposes tiers that sit at or
below ``min_ep_upgrade_threshold`` — which would classify essentially every
purchase as ``must_have`` and put maximum overbid on all of them. Measured
2026-08-24: rarity over all 16 positive candidates proposed 26.1/23.9/21.5
against a buy threshold of 25.0, i.e. three tiers that no longer discriminate.

A live market can be thin, and percentiles over a handful of points are noise.
Below ``MIN_SAMPLE_FOR_RARITY`` qualifying candidates the tiers fall back to
multiples of the buy threshold, which stays stable and self-consistent when the
threshold itself is re-tuned. The report says which method was used.

Marginal gains must come from ``DecisionEngine.calculate_marginal_ep``, the same
function the bot uses live. A second implementation would drift.
"""

from __future__ import annotations

from dataclasses import dataclass

RARITY = {"must_have": 0.85, "strong_upgrade": 0.70, "solid_upgrade": 0.50}

# Percentiles over fewer points than this are noise, not rarity.
MIN_SAMPLE_FOR_RARITY = 8

# Fallback tiers as multiples of the buy threshold: a player at the threshold is
# a solid upgrade by definition, and one worth 2.5x that is in another class.
THRESHOLD_MULTIPLES = {"must_have": 2.5, "strong_upgrade": 1.5, "solid_upgrade": 1.0}


@dataclass(frozen=True)
class ThresholdReport:
    """Measured marginal-gain distribution and the thresholds derived from it."""

    n_candidates: int
    gains: list[float]
    percentiles: dict[str, float]
    proposed: dict[str, float]
    n_qualifying: int = 0
    method: str = "rarity"


def percentile(values: list[float], p: float) -> float:
    """Value at percentile ``p`` (0-1). Returns 0.0 for an empty input."""
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(int(len(ordered) * p), len(ordered) - 1)]


def proposed_tiers(gains: list[float], min_gain: float) -> tuple[dict[str, float], str]:
    """Tier thresholds, and which method produced them.

    ``gains`` is the full positive-gain sample; only those at or above
    ``min_gain`` can ever reach a bid, so only those inform the tiers.
    """
    qualifying = [g for g in gains if g >= min_gain]
    if len(qualifying) >= MIN_SAMPLE_FOR_RARITY:
        return {name: percentile(qualifying, p) for name, p in RARITY.items()}, "rarity"
    return (
        {name: round(min_gain * mult, 1) for name, mult in THRESHOLD_MULTIPLES.items()},
        "threshold-multiples (too few qualifying candidates for rarity)",
    )


def build_report(gains: list[float], min_gain: float = 0.0) -> ThresholdReport:
    """Summarise a measured marginal-gain distribution."""
    positive = [g for g in gains if g > 0]
    proposed, method = proposed_tiers(positive, min_gain)
    return ThresholdReport(
        n_candidates=len(positive),
        gains=positive,
        percentiles={
            f"p{int(p * 100)}": percentile(positive, p)
            for p in (0.10, 0.25, 0.50, 0.70, 0.85, 0.95)
        },
        proposed=proposed,
        n_qualifying=len([g for g in positive if g >= min_gain]),
        method=method,
    )
