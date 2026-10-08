"""P(status | Kickbase lineup probability) — the second availability feature.

Kickbase publishes a lineup-probability code for every player (``prob`` on the
player details: 1 starter … 5 unlikely). The ingestion has stored it daily since
2026-09-14 (``player_status_daily.lineup_probability``) and the scorer ignored
it. Measured on matchday 4 of 2026/27 (reading the day before kickoff, n=462):

    code 1: 100% started      code 4:  9% started
    code 2:  88% started      code 5:  1% started
    code 3:  46% started

against the fitted Markov model's best claim, P(start | started last match) =
84%. The model under-priced code-1 players by 21 points each and over-priced
code-5 players by 11.

**Fitted, not a multiplier.** This module holds observed counts of played
status per code, taken from the store's join of the day-before status reading
and the match result (``rehoboam fit-lineup-prob``). At serving time the code's
observed distribution is shrunk toward the Markov row for the player's previous
status: ``(counts + k × markov) / (n + k)``. A code never observed, or a model
never fitted, leaves the Markov row untouched — so the season replay (which has
no historical codes) and every caller that passes no code score exactly as
before.

**Why this may push P(start) up** when ``availability_override`` may not:
``rate.py`` warns that quality is pooled across statuses, so raising P(5)
exposes a starter overshoot. The in-sample check on matchday 4 shows the
opposite for the codes that rise (1 and 2): the model was 21–25 points *under*
on them, so a higher P(5) closes a bias rather than opening one. The first
out-of-sample verdict is the next calibration report; read ``by_status`` and
the per-code bias before trusting the shrinkage constant.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from rehoboam.scoring.v2.features import PLAYED_STATUSES

DEFAULT_LINEUP_SHRINKAGE_K = 20.0

#: Kickbase lineup-probability codes, as published (1 starter … 5 unlikely).
LINEUP_CODES: tuple[int, ...] = (1, 2, 3, 4, 5)


@dataclass(frozen=True)
class LineupProbModel:
    """Observed counts of played status per lineup-probability code."""

    counts: dict[int, dict[int, int]]
    shrinkage_k: float = DEFAULT_LINEUP_SHRINKAGE_K
    meta: dict = field(default_factory=dict)

    def observations(self, code: int) -> int:
        return sum(self.counts.get(code, {}).values())

    def predict(self, code: int | None, fallback: dict[int, float]) -> dict[int, float]:
        """Blend the code's observed distribution with ``fallback``.

        ``fallback`` is the distribution the caller would use without the code
        (the Markov row, with any stale-history prior applied). Returned
        unchanged when the code is None, unknown, or never observed.
        """
        if code is None:
            return dict(fallback)
        row = self.counts.get(int(code))
        if not row:
            return dict(fallback)
        n = sum(row.values())
        if n <= 0:
            return dict(fallback)
        k = max(self.shrinkage_k, 0.0)
        denominator = n + k
        return {
            s: (row.get(s, 0) + k * fallback.get(s, 0.0)) / denominator for s in PLAYED_STATUSES
        }

    def to_dict(self) -> dict:
        return {
            "counts": {
                str(code): {str(s): int(n) for s, n in row.items()}
                for code, row in self.counts.items()
            },
            "shrinkage_k": self.shrinkage_k,
            "meta": dict(self.meta),
        }

    @classmethod
    def from_dict(cls, data: dict) -> LineupProbModel:
        return cls(
            counts={
                int(code): {int(s): int(n) for s, n in row.items()}
                for code, row in (data.get("counts") or {}).items()
            },
            shrinkage_k=float(data.get("shrinkage_k", DEFAULT_LINEUP_SHRINKAGE_K)),
            meta=dict(data.get("meta") or {}),
        )


def fit_lineup_prob(
    observations: Iterable[tuple[int | None, int | None]],
    *,
    shrinkage_k: float = DEFAULT_LINEUP_SHRINKAGE_K,
    meta: dict | None = None,
) -> LineupProbModel:
    """Count ``(lineup code, played status)`` pairs.

    Pairs with an unknown code or an unplayed status (0 / None: the fixture
    has not happened) are not evidence and are skipped.
    """
    counts: dict[int, dict[int, int]] = {}
    for code, status in observations:
        if code is None or status not in PLAYED_STATUSES:
            continue
        row = counts.setdefault(int(code), dict.fromkeys(PLAYED_STATUSES, 0))
        row[int(status)] += 1
    return LineupProbModel(counts=counts, shrinkage_k=shrinkage_k, meta=dict(meta or {}))


def effective_lineup_code(code: int | None, predicted_xi: bool | None) -> int | None:
    """Reconcile Kickbase's code with an outside predicted eleven.

    ligainsider's "Voraussichtliche Aufstellung" is a second opinion, used only
    where the two can disagree — codes 2 to 4. A player the outside source
    names in the eleven reads as at least a code 2 (88% started on matchday
    4); one it leaves out, while predicting his club, reads as at most a code
    4 (9%). Codes 1 and 5 are left alone: on matchday 4 they were right 100%
    and 99% of the time, and an outside source has nothing to add there.
    ``predicted_xi`` is None when there is no prediction for the club, which
    changes nothing.
    """
    if code is None or predicted_xi is None or code not in (2, 3, 4):
        return code
    return min(code, 2) if predicted_xi else max(code, 4)
