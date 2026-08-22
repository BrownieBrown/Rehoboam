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

# --- Serving-time availability overrides (REH-52 item 4) ------------------
#
# Kickbase's live status flag has no historical counterpart -- it does not
# publish what a player's status was on matchday 12 of 2023/24 -- so it cannot
# be fitted alongside the transitions above. It is applied here at serving
# time, explicitly unfitted, and kept separate from the fitted model.
#
# 0 = healthy, 2 = uncertain, 4 = short-term injury, 256 = long-term injury
# (kickbase_client.py:488, scorer.py:424). v1 applied -30/-20/-10 point
# penalties for these; the v2 migration dropped the signal entirely, which is
# how a long-term-injured player came to be scored 47.2 EP and named in the
# starting eleven on 2026-08-22.
OUT_STATUSES: frozenset[int] = frozenset({4, 256})
UNCERTAIN_STATUSES: frozenset[int] = frozenset({2})
DEFAULT_UNCERTAIN_START_MULTIPLIER = 0.5


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


def apply_availability_override(
    probs: dict[int, float],
    live_status: int | None,
    *,
    uncertain_start_multiplier: float = DEFAULT_UNCERTAIN_START_MULTIPLIER,
) -> dict[int, float]:
    """Shift probability mass toward "not in squad" from a live status flag.

    **Downward only, by construction.** ``rate.py`` records that quality is
    pooled across statuses, so ``rate(5)`` overstates a true starter's mean by
    ~24% and stays calibrated only because ``P(5)`` is the fitted ~82% rather
    than 100%. Forcing probability *up* would expose that overshoot; forcing it
    *down* drives EP toward zero and cannot inflate anything. That asymmetry is
    why this function can exist without renormalising quality within-status.

    An injury (4, 256) moves all mass to "not in squad". An uncertain flag (2)
    is a haircut, never a block: the same code covers a player returning to
    fitness and one falling out of favour -- on 2026-08-22 it covered Fuehrich,
    whose market value was rising 15.7% over 30 days, and Stark, whose was down
    36% -- so it must not be treated as a verdict.

    Anything else, including an unknown status, is returned unchanged: a details
    fetch that failed is not evidence of injury.
    """
    multiplier = min(max(uncertain_start_multiplier, 0.0), 1.0)

    if live_status in OUT_STATUSES:
        return {status: (1.0 if status == 1 else 0.0) for status in probs}

    if live_status in UNCERTAIN_STATUSES:
        adjusted = dict(probs)
        for status in (3, 5):
            if status in adjusted:
                adjusted[status] *= multiplier
        adjusted[1] = adjusted.get(1, 0.0) + (1.0 - sum(adjusted.values()))
        return adjusted

    return dict(probs)


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
