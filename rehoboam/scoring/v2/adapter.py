"""Compose the fitted v2 models into the existing ``PlayerScore`` contract.

    EP = Σ_status P(status | previous status) × rate(player, status)

Deliberate choices, each with a reason:

**``PlayerScore`` keeps its shape.** It carries v1's decomposition
(``base_points``, ``consistency_bonus``, ``lineup_bonus``, ``fixture_bonus``,
``form_bonus``, ``minutes_bonus``) which has no v2 counterpart. Changing the
dataclass would ripple through ``decision.py``, ``trader.py`` and
``learning/tracker.py`` for no behavioural gain, so those fields are set to 0.0
and the real decomposition is recorded in ``notes``. ``expected_points`` is the
only field any decision actually reads.

**No calibration multiplier.** ``scoring.scorer.score_player`` accepts one from
REH-20's position calibration, fitted against the old 0-100 index to correct
what was in fact a unit mismatch. Applying it to real points would reintroduce
a correction for a defect that no longer exists.

**No serving-time overrides.** Live lineup probability and injury status are not
consulted. ``rate.predict`` is not a calibrated within-status estimate — quality
absorbs start-share as well as skill — and the composed model is only calibrated
because availability and rate were fitted as a coupled pair. Overriding
``P(status)`` breaks that coupling and exposes a ~24% starter bias. See REH-55's
ticket notes before adding overrides.
"""

from __future__ import annotations

from functools import lru_cache

from rehoboam.scoring.models import DataQuality, PlayerData, PlayerScore
from rehoboam.scoring.v2.availability import AvailabilityModel
from rehoboam.scoring.v2.coefficients import load_coefficients
from rehoboam.scoring.v2.features import PLAYED_STATUSES
from rehoboam.scoring.v2.rate import RateModel

DGW_MULTIPLIER = 1.8


@lru_cache(maxsize=1)
def _models() -> tuple[AvailabilityModel, RateModel, dict]:
    """Load fitted coefficients once per process."""
    return load_coefficients()


def last_played_status(performance: dict | None) -> int | None:
    """The player's status in his most recent *played* match.

    Unplayed fixtures (status 0 or absent) are skipped — they describe a match
    that has not happened, not a state the player was in. Returns None when
    there is no played history, which the availability model handles by falling
    back to its marginal prior.
    """
    if not performance:
        return None

    latest: tuple[str, int] | None = None
    latest_status: int | None = None
    for season in performance.get("it") or []:
        title = season.get("ti") or ""
        for match in season.get("ph") or []:
            status = match.get("st")
            day = match.get("day")
            if status not in PLAYED_STATUSES or day is None:
                continue
            key = (title, int(day))
            if latest is None or key > latest:
                latest, latest_status = key, int(status)
    return latest_status


def compose_ep(
    player_id: str,
    prev_status: int | None,
    position: str | None,
    availability: AvailabilityModel,
    rate: RateModel,
) -> float:
    """Probability-weighted expected points, in real Kickbase points."""
    probs = availability.predict(prev_status)
    return sum(probs[s] * rate.predict(player_id, s, position) for s in PLAYED_STATUSES)


def score_player_v2(data: PlayerData) -> PlayerScore:
    """Score a player with the fitted v2 models. Pure — no I/O beyond cached load."""
    availability, rate, _meta = _models()
    player = data.player
    position = player.position or None

    prev_status = last_played_status(data.performance)
    ep = compose_ep(player.id, prev_status, position, availability, rate)

    dgw_multiplier = DGW_MULTIPLIER if data.is_dgw else 1.0
    ep *= dgw_multiplier

    probs = availability.predict(prev_status)
    notes = [
        f"v2: availability P(start)={probs[5]:.0%} "
        f"(prev status {prev_status if prev_status is not None else 'unknown'}), "
        f"rate={rate.predict(player.id, 5, position):.0f} pts if started"
    ]
    if player.id not in rate.quality:
        notes.append("No fitted quality — using position prior (cold start)")
    if data.is_dgw:
        notes.append("DOUBLE GAMEWEEK ×1.8")

    return PlayerScore(
        player_id=player.id,
        expected_points=round(ep, 2),
        data_quality=DataQuality(
            grade="A" if player.id in rate.quality else "C",
            games_played=0,
            consistency=0.0,
            has_fixture_data=False,
            has_lineup_data=False,
            warnings=[],
        ),
        # v1 decomposition — no v2 counterpart; see module docstring.
        base_points=0.0,
        consistency_bonus=0.0,
        lineup_bonus=0.0,
        fixture_bonus=0.0,
        form_bonus=0.0,
        minutes_bonus=0.0,
        dgw_multiplier=dgw_multiplier,
        is_dgw=data.is_dgw,
        next_opponent=(
            data.upcoming_opponent_strengths[0].team_name
            if data.upcoming_opponent_strengths
            else None
        ),
        notes=notes,
        current_price=getattr(player, "price", player.market_value),
        market_value=player.market_value,
        average_points=player.average_points or 0.0,
        position=player.position or "",
        lineup_probability=None,
        minutes_trend=None,
    )
