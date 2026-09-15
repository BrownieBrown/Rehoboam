"""Score a player from store rows through the same `compose_ep` the live path uses (PR E §1).

The live scorer reads a performance payload and a details payload; this one
reads the rows PR C1's ingestion wrote from the same endpoints. Both compose
through `compose_ep`, so the two numbers differ only by their inputs — which
is exactly what `predictions.live_ep` versus `predicted_ep` measures.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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
