"""Derive decision thresholds from the v2 marginal-gain distribution.

The constants in ``config.py`` and ``bidding_strategy.py`` were calibrated
against a 0-100 index whose range was **87.1** points and whose median sat near
72. Real points span **196.7** with a median of 34.8, so the same numeric
constant now means something entirely different — ``marginal_ep_gain >= 20``
went from "rare must-have" to "more than half of all upgrade candidates".

The old firing rates cannot be recovered: ``predicted_eps.marginal_ep_gain`` is
NULL on all 351 production rows. So thresholds are derived from **rarity**,
which is the property the tiers were always meant to encode.

Rarity targets, expressed over candidates with a positive marginal gain:

    must_have       top 15%   — defend hard, worth an aggressive bid
    strong_upgrade  top 30%
    solid_upgrade   top 50%

Marginal gains must come from ``DecisionEngine.calculate_marginal_ep``, the same
function the bot uses live. A second implementation would drift.
"""

from __future__ import annotations

from dataclasses import dataclass

RARITY = {"must_have": 0.85, "strong_upgrade": 0.70, "solid_upgrade": 0.50}


@dataclass(frozen=True)
class ThresholdReport:
    """Measured marginal-gain distribution and the thresholds derived from it."""

    n_candidates: int
    gains: list[float]
    percentiles: dict[str, float]
    proposed: dict[str, float]


def percentile(values: list[float], p: float) -> float:
    """Value at percentile ``p`` (0-1). Returns 0.0 for an empty input."""
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(int(len(ordered) * p), len(ordered) - 1)]


def proposed_tiers(gains: list[float]) -> dict[str, float]:
    """Tier thresholds at the intended rarities."""
    if not gains:
        return dict.fromkeys(RARITY, 0.0)
    return {name: percentile(gains, p) for name, p in RARITY.items()}


def build_report(gains: list[float]) -> ThresholdReport:
    """Summarise a measured marginal-gain distribution."""
    positive = [g for g in gains if g > 0]
    return ThresholdReport(
        n_candidates=len(positive),
        gains=positive,
        percentiles={
            f"p{int(p * 100)}": percentile(positive, p)
            for p in (0.10, 0.25, 0.50, 0.70, 0.85, 0.95)
        },
        proposed=proposed_tiers(positive),
    )
