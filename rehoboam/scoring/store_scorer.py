"""Score a player from store rows through the same `compose_ep` the live path uses (PR E §1).

The live scorer reads a performance payload and a details payload; this one
reads the rows PR C1's ingestion wrote from the same endpoints. Both compose
through `compose_ep`, so the two numbers differ only by their inputs — which
is exactly what `predictions.live_ep` versus `predicted_ep` measures.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from rehoboam.scoring.v2.adapter import (
    MIN_SEASON_MATCHDAYS,
    PLAYED_OR_ABSENT_STATUSES,
    PLAYED_STATUSES_ON_PITCH,
    availability_probs,
    compose_ep,
    prev_status_from_history,
)
from rehoboam.scoring.v2.availability import AvailabilityModel
from rehoboam.scoring.v2.features import PLAYED_STATUSES
from rehoboam.scoring.v2.rate import RateModel


@dataclass(frozen=True)
class StoredPlayer:
    player_id: str
    position: str  # Goalkeeper | Defender | Midfielder | Forward
    team_id: str | None
    market_value: int | None
    live_status: int | None  # player_status_daily.status, None when no row
    lineup_probability: int | None  # stored, not used by the model
    status_fetched_at: float | None  # epoch of the status row, None when no row
    matches: list[dict[str, Any]]  # player_match_history rows, oldest first


@dataclass(frozen=True)
class StoredPrediction:
    player_id: str
    predicted_ep: float
    p_status: dict[int, float]
    rate: float
    prev_status: int | None
    data_grade: str


def played_share_from_rows(matches: list[dict[str, Any]]) -> tuple[int, int] | None:
    """`adapter.recent_played_share` over store rows.

    Same rule: the most recent season with at least `MIN_SEASON_MATCHDAYS`
    recorded matchdays (statuses 1, 3, 4, 5 — status 0 is a fixture not yet
    played, not evidence), returning `(on the pitch, recorded)`. Season titles
    are `YYYY/YYYY`, so their string order is their time order.
    """
    by_season: dict[str, list[dict[str, Any]]] = {}
    for m in matches:
        by_season.setdefault(str(m["season"]), []).append(m)
    for season in sorted(by_season, reverse=True):
        recorded = [m for m in by_season[season] if m.get("status") in PLAYED_OR_ABSENT_STATUSES]
        if len(recorded) < MIN_SEASON_MATCHDAYS:
            continue
        played = sum(1 for m in recorded if m["status"] in PLAYED_STATUSES_ON_PITCH)
        return played, len(recorded)
    return None


def score_stored(
    player: StoredPlayer,
    *,
    now: datetime,
    max_status_age_days: float,
    availability: AvailabilityModel,
    rate: RateModel,
) -> StoredPrediction:
    """The store-row twin of `score_player_v2`: same inputs, same composition.

    Mirrors `replay/driver.py`'s `_make_score_fn` and the adapter, in this
    order: previous played status from the rows (age-limited), the played-share
    prior only when that status is unusable, the live injury override, then
    `compose_ep`. No DGW multiplier: the Bundesliga has none, and a rescheduled
    double would show in the calibration report's bias, which is where it
    should be noticed.
    """
    ordered = sorted(player.matches, key=lambda m: (str(m["season"]), int(m["day_number"])))
    prev_status = prev_status_from_history(
        [(m.get("match_date"), m.get("status")) for m in ordered],
        now=now,
        max_age_days=max_status_age_days,
    )
    played_history = played_share_from_rows(ordered) if prev_status is None else None
    probs = availability_probs(
        prev_status,
        availability,
        live_status=player.live_status,
        played_history=played_history,
    )
    ep = compose_ep(
        player.player_id,
        prev_status,
        player.position,
        availability,
        rate,
        live_status=player.live_status,
        played_history=played_history,
    )
    mass = sum(probs[s] for s in PLAYED_STATUSES)
    conditional_rate = (
        sum(probs[s] * rate.predict(player.player_id, s, player.position) for s in PLAYED_STATUSES)
        / mass
        if mass > 0
        else 0.0
    )
    return StoredPrediction(
        player_id=player.player_id,
        predicted_ep=round(ep, 2),
        p_status={int(s): float(probs[s]) for s in PLAYED_STATUSES},
        rate=round(conditional_rate, 2),
        prev_status=prev_status,
        data_grade="A" if player.player_id in rate.quality else "C",
    )
