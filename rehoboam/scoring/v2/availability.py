"""Availability model — P(status | previous status).

The largest single effect in the game. Measured across the corpus:

    status 1 (not in squad)  mean   0.0 pts
    status 4 (unused sub)    mean   1.3 pts
    status 3 (came on)       mean  18.5 pts
    status 5 (started)       mean  85.0 pts

A ~85-point swing driven purely by whether the player is on the pitch. The v1
scorer expressed this as a ±20 bonus on a 0-100 index.

A first-order Markov model captures most of it — starters start again 82% of the
time, unused subs stay unused 71% of the time. Transition counts are shrunk
toward the marginal prior because the rare states are sparse: status 1 has 1,853
observed transitions against status 5's 19,748.

Note this model is fitted only on *historical* signals. Kickbase's live lineup
probability (`prob`) and injury status have no historical counterpart — Kickbase
does not publish what a player's lineup probability was two seasons ago — so they
cannot be fitted here. They belong at serving time as explicit, documented
overrides.
"""

from __future__ import annotations

from dataclasses import dataclass

from rehoboam.scoring.v2.features import PLAYED_STATUSES, FeatureRow

DEFAULT_SHRINKAGE_K = 20.0


@dataclass(frozen=True)
class AvailabilityModel:
    """Fitted transition probabilities, plus a marginal prior for cold start."""

    transitions: dict[int, dict[int, float]]
    prior: dict[int, float]
    shrinkage_k: float

    def predict(self, prev_status: int | None) -> dict[int, float]:
        """P(status) given the player's previous match status.

        Falls back to the marginal prior when the previous status is unknown
        (a player's first match) or was never observed in training.
        """
        if prev_status is None:
            return dict(self.prior)
        return dict(self.transitions.get(prev_status, self.prior))

    def to_dict(self) -> dict:
        return {
            "transitions": {
                str(prev): {str(nxt): p for nxt, p in row.items()}
                for prev, row in self.transitions.items()
            },
            "prior": {str(s): p for s, p in self.prior.items()},
            "shrinkage_k": self.shrinkage_k,
        }

    @classmethod
    def from_dict(cls, data: dict) -> AvailabilityModel:
        return cls(
            transitions={
                int(prev): {int(nxt): float(p) for nxt, p in row.items()}
                for prev, row in data["transitions"].items()
            },
            prior={int(s): float(p) for s, p in data["prior"].items()},
            shrinkage_k=float(data["shrinkage_k"]),
        )


def fit_availability(
    rows: list[FeatureRow], *, shrinkage_k: float = DEFAULT_SHRINKAGE_K
) -> AvailabilityModel:
    """Fit transition probabilities from feature rows.

    Args:
        rows: training rows. Only those with a ``target_status`` count.
        shrinkage_k: pseudo-count pulling each transition row toward the
            marginal prior. 0.0 gives raw frequencies.
    """
    marginal = dict.fromkeys(PLAYED_STATUSES, 0)
    counts: dict[int, dict[int, int]] = {
        prev: dict.fromkeys(PLAYED_STATUSES, 0) for prev in PLAYED_STATUSES
    }

    for row in rows:
        if row.target_status not in PLAYED_STATUSES:
            continue
        marginal[row.target_status] += 1
        if row.prev_status in PLAYED_STATUSES:
            counts[row.prev_status][row.target_status] += 1

    total = sum(marginal.values())
    if total == 0:
        uniform = 1.0 / len(PLAYED_STATUSES)
        prior = dict.fromkeys(PLAYED_STATUSES, uniform)
        return AvailabilityModel(transitions={}, prior=prior, shrinkage_k=shrinkage_k)

    prior = {s: marginal[s] / total for s in PLAYED_STATUSES}

    transitions: dict[int, dict[int, float]] = {}
    for prev in PLAYED_STATUSES:
        row_total = sum(counts[prev].values())
        if row_total == 0:
            continue
        denominator = row_total + shrinkage_k
        transitions[prev] = {
            s: (counts[prev][s] + shrinkage_k * prior[s]) / denominator for s in PLAYED_STATUSES
        }

    return AvailabilityModel(transitions=transitions, prior=prior, shrinkage_k=shrinkage_k)
