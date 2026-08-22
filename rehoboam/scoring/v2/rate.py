"""Rate model — points scored, given that the player is in a given state.

    rate = base_rate[status] × quality(player)

``base_rate`` is the league-wide average points for that availability state
(started ≈ 85, came on ≈ 18.5, unused sub ≈ 1.3, not in squad ≈ 0). ``quality``
is the player's own scoring relative to the league, shrunk toward his position's
average.

Shrinkage is what makes cold start work without a special case: a player with
three matches is pulled hard toward the position prior, one with thirty stands on
his own record. The v1 scorer approximated this with a grade-F halving rule.

The defect this replaces: v1's ``base_points = min(avg_pts * 2.0, 40.0)``
saturated at 20 points per game, and 93.1% of Bundesliga players exceed that — so
the one component meant to express player quality was a constant for everyone
worth owning.

Output is in real Kickbase points, never a 0-100 index.

CAVEAT — quality is pooled, base_rate is per-status, and predict() is NOT a
calibrated within-status estimate. ``quality`` normalises a player's scoring
against a single reference averaged over statuses 3 and 5 pooled together, but
``base_rate`` is a separate mean for each status. Multiplying a pooled quality by
a per-status rate means quality absorbs each player's start share along with his
skill: a pure starter's ``predict(..., 5, ...)`` overshoots his own historical
mean by ~23-24%, and a pure substitute's ``predict(..., 3, ...)`` undershoots by
~52% (measured on train). The *composed* prediction
``Σ_status P(status) × rate(status)`` is well calibrated (+1.3% vs actual on
train) because the availability model was fitted as a paired component and the
two errors cancel — but the individual factors, taken alone, are not. Refitting
quality within-status looks "more correct" and is actually worse for the
composed output (train MAE rises from 43.79 to 44.95), because the availability
model conditions only on ``prev_status`` with no player effect, so pooled quality
is doing double duty as a crude player-level correction for start share.

Anyone overriding ``P(status)`` at serving time (e.g. forcing a confirmed
starter's ``P(5) = 1.0`` from live lineup data) MUST renormalise quality
within-status first, or this ~24% starter bias — currently cancelled by the
paired availability model — will leak straight into the output.
"""

from __future__ import annotations

from dataclasses import dataclass

from rehoboam.scoring.v2.features import PLAYED_STATUSES, FeatureRow

DEFAULT_SHRINKAGE_K = 5.0


@dataclass(frozen=True)
class RateModel:
    """League base rates plus per-player and per-position quality multipliers."""

    base_rate: dict[int, float]
    quality: dict[str, float]
    position_prior: dict[str, float]
    shrinkage_k: float

    def predict(self, player_id: str | None, status: int, position: str | None) -> float:
        """Expected points for this player in this availability state.

        NOT a calibrated within-status expectation. ``quality`` is normalised
        against a reference pooled across statuses 3 and 5, while ``base_rate``
        is per-status — so this multiplies a pooled quality by a per-status
        rate, and quality absorbs each player's start share as well as his
        skill (see module docstring for measured error sizes). Only the
        *composed* prediction ``Σ_status P(status) × predict(status)`` is
        calibrated; do not treat a single call's return value as a calibrated
        per-status estimate. If you override ``P(status)`` at serving time
        (e.g. a confirmed-starter lineup override), you must renormalise
        ``quality`` within-status first or you will reintroduce the ~24%
        starter overshoot this composition currently cancels out.
        """
        base = self.base_rate.get(status, 0.0)
        if base == 0.0:
            return 0.0

        multiplier = self.quality.get(player_id)
        if multiplier is None:
            multiplier = self.position_prior.get(position or "", 1.0)
        return base * multiplier

    def to_dict(self) -> dict:
        return {
            "base_rate": {str(s): r for s, r in self.base_rate.items()},
            "quality": dict(self.quality),
            "position_prior": dict(self.position_prior),
            "shrinkage_k": self.shrinkage_k,
        }

    @classmethod
    def from_dict(cls, data: dict) -> RateModel:
        return cls(
            base_rate={int(s): float(r) for s, r in data["base_rate"].items()},
            quality={str(p): float(q) for p, q in data["quality"].items()},
            position_prior={str(p): float(q) for p, q in data["position_prior"].items()},
            shrinkage_k=float(data["shrinkage_k"]),
        )


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def fit_rate(
    rows: list[FeatureRow],
    positions: dict[str, str],
    *,
    shrinkage_k: float = DEFAULT_SHRINKAGE_K,
) -> RateModel:
    """Fit base rates and shrunk per-player quality multipliers.

    Args:
        rows: training rows with a ``target_status`` and ``target_points``.
        positions: player_id → position, for the position priors.
        shrinkage_k: pseudo-count pulling a player's quality toward his
            position's prior. 0.0 uses raw per-player averages.
    """
    by_status: dict[int, list[float]] = {s: [] for s in PLAYED_STATUSES}
    per_player: dict[str, list[float]] = {}

    for row in rows:
        if row.target_status not in PLAYED_STATUSES:
            continue
        by_status[row.target_status].append(float(row.target_points))
        # Quality is measured on states where scoring is actually possible.
        if row.target_status in (3, 5):
            per_player.setdefault(row.player_id, []).append(float(row.target_points))

    base_rate = {s: _mean(by_status[s]) for s in PLAYED_STATUSES}

    scoring_reference = _mean([p for s in (3, 5) for p in by_status[s]])
    if scoring_reference == 0.0:
        return RateModel(
            base_rate=base_rate, quality={}, position_prior={}, shrinkage_k=shrinkage_k
        )

    raw_quality = {pid: _mean(pts) / scoring_reference for pid, pts in per_player.items()}

    by_position: dict[str, list[float]] = {}
    for pid, q in raw_quality.items():
        position = positions.get(pid)
        if position:
            by_position.setdefault(position, []).append(q)
    position_prior = {pos: _mean(qs) for pos, qs in by_position.items()}

    quality: dict[str, float] = {}
    for pid, points in per_player.items():
        n = len(points)
        prior = position_prior.get(positions.get(pid, ""), 1.0)
        quality[pid] = (n * raw_quality[pid] + shrinkage_k * prior) / (n + shrinkage_k)

    return RateModel(
        base_rate=base_rate,
        quality=quality,
        position_prior=position_prior,
        shrinkage_k=shrinkage_k,
    )
