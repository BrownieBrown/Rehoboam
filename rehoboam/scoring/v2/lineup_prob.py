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

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from rehoboam.scoring.v2.features import PLAYED_STATUSES

DEFAULT_LINEUP_SHRINKAGE_K = 20.0

#: Kickbase lineup-probability codes, as published (1 starter … 5 unlikely).
LINEUP_CODES: tuple[int, ...] = (1, 2, 3, 4, 5)

# ligainsider's verdict on a player, the stand-in for Kickbase's code since
# the code went behind Kickbase Premium (2026-10-21: `prob` absent from
# `/players/{id}` for every player). Fitted the same way, as its own counts —
# never hand-mapped onto Kickbase's scale.
OUTSIDE_SURE = 1  # in the eleven, no alternative listed beside him
OUTSIDE_CONTESTED = 2  # in the eleven, an alternative listed beside him
OUTSIDE_ALTERNATIVE = 3  # listed as the alternative to a starter
OUTSIDE_LEFT_OUT = 4  # his club was predicted and he is not on the page
OUTSIDE_MISSING = 5  # in the page's "Gegen … fehlen" list
OUTSIDE_NAMED = 6  # in the eleven, either kind — also rows stored before 2026-10-22

OUTSIDE_CODES: tuple[int, ...] = (
    OUTSIDE_SURE,
    OUTSIDE_CONTESTED,
    OUTSIDE_ALTERNATIVE,
    OUTSIDE_LEFT_OUT,
    OUTSIDE_MISSING,
    OUTSIDE_NAMED,
)
OUTSIDE_LABELS: dict[int, str] = {
    OUTSIDE_SURE: "named, sure",
    OUTSIDE_CONTESTED: "named, in doubt",
    OUTSIDE_ALTERNATIVE: "alternative",
    OUTSIDE_LEFT_OUT: "left out",
    OUTSIDE_MISSING: "missing",
    OUTSIDE_NAMED: "named (pooled)",
}

#: Fewest observations before "sure" / "in doubt" are served on their own
#: counts instead of the pooled "named" ones — about two matchdays of the split.
OUTSIDE_TIER_MIN_OBSERVATIONS = 100


@dataclass(frozen=True)
class LineupProbModel:
    """Observed counts of played status per lineup-probability code.

    ``outside`` is the same kind of model over ligainsider's codes
    (``OUTSIDE_*``), fitted from the same rows; None until a fit saw any.
    """

    counts: dict[int, dict[int, int]]
    shrinkage_k: float = DEFAULT_LINEUP_SHRINKAGE_K
    meta: dict = field(default_factory=dict)
    outside: LineupProbModel | None = None

    def observations(self, code: int) -> int:
        return sum(self.counts.get(code, {}).values())

    def predict_outside(
        self, outside_code: int | None, fallback: dict[int, float]
    ) -> dict[int, float]:
        """ligainsider's verdict blended onto ``fallback``, for a player with no Kickbase code.

        "Sure" and "in doubt" back off to the pooled "named" counts until each
        has `OUTSIDE_TIER_MIN_OBSERVATIONS`; no outside model or no verdict
        returns ``fallback`` unchanged.
        """
        if self.outside is None or outside_code is None:
            return dict(fallback)
        code = int(outside_code)
        if (
            code in (OUTSIDE_SURE, OUTSIDE_CONTESTED)
            and self.outside.observations(code) < OUTSIDE_TIER_MIN_OBSERVATIONS
        ):
            code = OUTSIDE_NAMED
        return self.outside.predict(code, fallback)

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
        payload = {
            "counts": {
                str(code): {str(s): int(n) for s, n in row.items()}
                for code, row in self.counts.items()
            },
            "shrinkage_k": self.shrinkage_k,
            "meta": dict(self.meta),
        }
        if self.outside is not None:
            payload["outside"] = self.outside.to_dict()
        return payload

    @classmethod
    def from_dict(cls, data: dict) -> LineupProbModel:
        outside = data.get("outside")
        return cls(
            counts={
                int(code): {int(s): int(n) for s, n in row.items()}
                for code, row in (data.get("counts") or {}).items()
            },
            shrinkage_k=float(data.get("shrinkage_k", DEFAULT_LINEUP_SHRINKAGE_K)),
            meta=dict(data.get("meta") or {}),
            outside=cls.from_dict(outside) if outside else None,
        )


def _count(pairs: Iterable[tuple[int | None, int | None]]) -> dict[int, dict[int, int]]:
    counts: dict[int, dict[int, int]] = {}
    for code, status in pairs:
        if code is None or status not in PLAYED_STATUSES:
            continue
        row = counts.setdefault(int(code), dict.fromkeys(PLAYED_STATUSES, 0))
        row[int(status)] += 1
    return counts


def fit_lineup_prob(
    observations: Iterable[tuple[int | None, int | None]],
    *,
    shrinkage_k: float = DEFAULT_LINEUP_SHRINKAGE_K,
    meta: dict | None = None,
    outside_observations: Iterable[tuple[int | None, int | None]] = (),
) -> LineupProbModel:
    """Count ``(lineup code, played status)`` pairs, and ``(outside code, status)`` pairs.

    Pairs with an unknown code or an unplayed status (0 / None: the fixture
    has not happened) are not evidence and are skipped. A "sure" or "in doubt"
    outside pair also counts toward the pooled "named" row, which is what the
    serving backoff reads while either is thin.
    """
    outside_pairs: list[tuple[int | None, int | None]] = []
    for code, status in outside_observations:
        outside_pairs.append((code, status))
        if code in (OUTSIDE_SURE, OUTSIDE_CONTESTED):
            outside_pairs.append((OUTSIDE_NAMED, status))
    outside_counts = _count(outside_pairs)
    return LineupProbModel(
        counts=_count(observations),
        shrinkage_k=shrinkage_k,
        meta=dict(meta or {}),
        outside=(
            LineupProbModel(counts=outside_counts, shrinkage_k=shrinkage_k)
            if outside_counts
            else None
        ),
    )


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


def outside_in_xi(outside_code: int | None) -> bool | None:
    """The outside verdict as the older named / not-named flag (`predicted_xi`)."""
    if outside_code is None:
        return None
    return int(outside_code) in (OUTSIDE_SURE, OUTSIDE_CONTESTED, OUTSIDE_NAMED)


def _name_words(name: str) -> set[str]:
    """Lower-cased words of a name, initials and punctuation dropped."""
    return {w for w in re.split(r"[^\w]+", name.lower()) if len(w) > 1}


def outside_codes(
    rows: Iterable[dict], members: Iterable[dict], *, cover_slot_base: int
) -> dict[str, int]:
    """`predicted_lineups` rows for one matchday → player_id → outside code.

    ``members`` are `{player_id, team_id, last_name}` of every club; a member
    of a club with any row that is not on the page is `OUTSIDE_LEFT_OUT`, a
    member of a club without rows has no verdict. A starter is "in doubt" when
    a row of his club has slot ``cover_slot_base + his slot``; a club whose
    alternatives carry the pre-2026-10-22 slots (101 upward, not linked to a
    starter) cannot tell, and its starters are `OUTSIDE_NAMED`. A player on
    the page twice keeps the strongest verdict.

    A member whose last name is a word of a name on his club's page that the
    matcher could not resolve has no verdict rather than "left out": he may
    be that name ("T. Becker" on a club with two Beckers and no first names,
    2026-10-22 — calling him left out cut a predicted starter from 69 to 13).
    """
    rows = list(rows)
    covered: set[tuple[str, int]] = set()
    unlinked: set[str] = set()
    unresolved: dict[str, set[str]] = {}
    for r in rows:
        if not r.get("player_id") and r.get("player_name"):
            unresolved.setdefault(str(r["team_id"]), set()).update(_name_words(r["player_name"]))
        slot = r.get("slot")
        if r.get("in_xi") or slot is None or slot <= 100:
            continue
        if slot > cover_slot_base:
            covered.add((str(r["team_id"]), slot - cover_slot_base))
        else:
            unlinked.add(str(r["team_id"]))

    predicted = {str(r["team_id"]) for r in rows}
    out = {
        str(m["player_id"]): OUTSIDE_LEFT_OUT
        for m in members
        if str(m["team_id"]) in predicted
        and not (_name_words(m.get("last_name") or "") & unresolved.get(str(m["team_id"]), set()))
    }
    rank = {
        OUTSIDE_SURE: 0,
        OUTSIDE_CONTESTED: 0,
        OUTSIDE_NAMED: 0,
        OUTSIDE_ALTERNATIVE: 1,
        OUTSIDE_MISSING: 2,
        OUTSIDE_LEFT_OUT: 3,
    }
    for r in rows:
        pid = r.get("player_id")
        if not pid:
            continue
        team = str(r["team_id"])
        if r.get("in_xi"):
            if team in unlinked:
                code = OUTSIDE_NAMED
            elif (team, r.get("slot")) in covered:
                code = OUTSIDE_CONTESTED
            else:
                code = OUTSIDE_SURE
        elif r.get("slot") is None:
            code = OUTSIDE_MISSING
        else:
            code = OUTSIDE_ALTERNATIVE
        current = out.get(str(pid))
        if current is None or rank[code] < rank[current]:
            out[str(pid)] = code
    return out


def scorer_inputs(
    outside_code: int | None, *, xi_enabled: bool, fallback_enabled: bool
) -> tuple[bool | None, int | None]:
    """ligainsider's code → the scorer's `(predicted_xi, outside_code)`, per flag.

    `predicted_xi` (moves a Kickbase code 2–4) only under `PREDICTED_XI_ENABLED`;
    `outside_code` (stands in where Kickbase sent none) only under
    `OUTSIDE_LINEUP_FALLBACK`. Both callers — the live pipeline and the
    league-wide predictions — read the flags through here.
    """
    return (
        outside_in_xi(outside_code) if xi_enabled else None,
        outside_code if fallback_enabled else None,
    )
