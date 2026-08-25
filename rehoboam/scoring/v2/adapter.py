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

from collections.abc import Sequence
from datetime import datetime, timezone
from functools import lru_cache

from rehoboam.scoring.models import DataQuality, PlayerData, PlayerScore
from rehoboam.scoring.v2.availability import (
    DEFAULT_STALE_SHRINKAGE_K,
    DEFAULT_UNCERTAIN_START_MULTIPLIER,
    OUT_STATUSES,
    UNCERTAIN_STATUSES,
    AvailabilityModel,
    apply_availability_override,
    apply_stale_history_prior,
)
from rehoboam.scoring.v2.coefficients import load_coefficients
from rehoboam.scoring.v2.features import PLAYED_STATUSES
from rehoboam.scoring.v2.rate import RateModel

DGW_MULTIPLIER = 1.8

# Statuses that describe a match the player was actually assessed for. Status 0
# means the fixture has not been played, so it is not evidence either way.
PLAYED_OR_ABSENT_STATUSES: frozenset[int] = frozenset({1, 3, 4, 5})
# Of those, the ones where he was on the pitch.
PLAYED_STATUSES_ON_PITCH: frozenset[int] = frozenset({3, 5})

# Fewest recorded matchdays for a season to count as availability evidence.
MIN_SEASON_MATCHDAYS = 5


@lru_cache(maxsize=1)
def _models() -> tuple[AvailabilityModel, RateModel, dict]:
    """Load fitted coefficients once per process."""
    return load_coefficients()


def prev_status_from_history(
    history: Sequence[tuple[str | None, int | None]],
    *,
    now: datetime | None = None,
    max_age_days: float | None = None,
) -> int | None:
    """Most recent *played* status from ``(match_date, status)`` pairs.

    Input is ordered oldest-first. Unplayed fixtures (status 0 or absent)
    describe a match that has not happened, not a state the player was in, so
    they are skipped.

    When ``max_age_days`` is set, a status older than that is discarded and
    None is returned instead. The availability model treats None as "no
    evidence" and falls back to its marginal prior, which is a weaker claim
    than a stale transition rather than a stronger one.

    Why this matters: without a bound, the last matchday of the previous season
    drives availability for the whole of the next one. End-of-season status is
    a bad prior -- squads rotate through dead rubbers -- and it persists for the
    entire off-season.

    A date that is missing or unparseable is treated as stale. This guard
    exists for the case we cannot see, so it fails closed.

    This is the single implementation of that rule. The live scorer and the
    season replay both call it.
    """
    latest: int | None = None
    for match_date, status in history:
        if status not in PLAYED_STATUSES:
            continue
        if max_age_days is not None:
            parsed = _parse_iso(match_date)
            reference = now or datetime.now(tz=timezone.utc)
            if parsed is None or (reference - parsed).total_seconds() > max_age_days * 86400:
                continue
        latest = int(status)
    return latest


def _parse_iso(value: str | None) -> datetime | None:
    """Parse a Kickbase ISO match date, or None if it cannot be read."""
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def last_played_status(
    performance: dict | None,
    *,
    now: datetime | None = None,
    max_age_days: float | None = None,
) -> int | None:
    """The player's status in his most recent *played* match.

    Returns None when there is no played history, which the availability model
    handles by falling back to its marginal prior.
    """
    if not performance:
        return None

    ordered: list[tuple[tuple[str, int], str | None, int | None]] = []
    for season in performance.get("it") or []:
        title = season.get("ti") or ""
        for match in season.get("ph") or []:
            day = match.get("day")
            if day is None:
                continue
            ordered.append(((title, int(day)), match.get("md"), match.get("st")))
    ordered.sort(key=lambda row: row[0])
    return prev_status_from_history(
        [(md, st) for _key, md, st in ordered], now=now, max_age_days=max_age_days
    )


def recent_played_share(performance: dict | None) -> tuple[int, int] | None:
    """``(matchdays on the pitch, matchdays recorded)`` from the latest season.

    Feeds ``apply_stale_history_prior`` when the last-played status is too old
    to use (REH-98). Reads the ``performance`` dict the scorer has already
    fetched, so it costs no extra HTTP traffic.

    The denominator is matchdays *recorded*, not appearances: a matchday spent
    as an unused sub (status 4) or out of the squad (status 1) is exactly the
    evidence this exists to capture. Fixtures not yet played carry status 0 and
    are excluded — they describe a match that has not happened, not a state the
    player was in, the same rule ``prev_status_from_history`` applies. That
    exclusion is what lets a not-yet-started season fall through to the last
    real one during the pre-season.

    **Most recent qualifying season only.** A blend of the last two smears the
    signal this exists to read: a player whose role is changing. Uzun finished
    2025/26 with four consecutive starts after half a season out of the squad.

    **2. Bundesliga counts.** REH-90 establishes that 2.BL scoring *rate* must
    not be read as Bundesliga rate, but availability is a different quantity —
    a secure role travels across divisions in a way points-per-match does not.

    Returns None when no season qualifies, which the caller treats as "no
    evidence" and leaves the marginal prior untouched.
    """
    if not performance:
        return None

    for season in reversed(performance.get("it") or []):
        recorded = [
            match.get("st")
            for match in season.get("ph") or []
            if match.get("st") in PLAYED_OR_ABSENT_STATUSES
        ]
        if len(recorded) < MIN_SEASON_MATCHDAYS:
            continue
        return sum(1 for st in recorded if st in PLAYED_STATUSES_ON_PITCH), len(recorded)

    return None


def has_top_flight_history(player_details: dict | None) -> bool:
    """Has this player ever recorded Bundesliga scoring?

    Kickbase omits ``ap``/``tp`` entirely for players with no top-flight
    appearances. On 2026-08-22 that was true of seven of 22 buyable listings —
    six from newly promoted clubs (Elversberg, Schalke, Paderborn) and one
    Mainz backup keeper. The keeper is why the signal is "no top-flight
    history" rather than "promoted club": it is broader, and it needs no
    annually-maintained list of who came up.

    ``player_details=None`` means "unknown", not "no top-flight history" —
    it is the shape of a transient lookup failure (rate limit, timeout;
    see ``Trader._fetch_player_data``), which can hit any player including
    established regulars. This fails OPEN on that case: ``None`` returns
    True, so the caller keeps the fitted quality coefficient instead of
    withholding it on a data hiccup. Only a *present* ``player_details``
    that actually lacks ``ap``/``tp`` (the real Elversberg case) returns
    False.
    """
    if player_details is None:
        return True
    return bool(player_details.get("ap") or player_details.get("tp"))


def availability_probs(
    prev_status: int | None,
    availability: AvailabilityModel,
    *,
    live_status: int | None = None,
    uncertain_start_multiplier: float = DEFAULT_UNCERTAIN_START_MULTIPLIER,
    played_history: tuple[int, int] | None = None,
    stale_shrinkage_k: float = DEFAULT_STALE_SHRINKAGE_K,
) -> dict[int, float]:
    """Fitted transition probabilities with any serving-time override applied.

    One implementation, so the composed EP and the note reporting P(start) can
    never disagree about what the model believes.

    ``played_history`` is the player's own ``(played, matchdays)`` record and is
    consulted **only when ``prev_status`` is None** (REH-98). A fresh in-season
    status is real per-player evidence and outranks a season-long average; this
    path exists for the window where that evidence has aged out and every
    player would otherwise share the league marginal.

    Order is deliberate: the stale prior runs first, the live injury override
    second, so a player flagged out is out whatever last season says.
    """
    probs = availability.predict(prev_status)

    if prev_status is None and played_history is not None:
        played, matchdays = played_history
        probs = apply_stale_history_prior(
            probs,
            played=played,
            matchdays=matchdays,
            prior_played_share=sum(
                availability.prior.get(s, 0.0) for s in PLAYED_STATUSES_ON_PITCH
            ),
            shrinkage_k=stale_shrinkage_k,
        )

    return apply_availability_override(
        probs, live_status, uncertain_start_multiplier=uncertain_start_multiplier
    )


def compose_ep(
    player_id: str | None,
    prev_status: int | None,
    position: str | None,
    availability: AvailabilityModel,
    rate: RateModel,
    *,
    live_status: int | None = None,
    uncertain_start_multiplier: float = DEFAULT_UNCERTAIN_START_MULTIPLIER,
    played_history: tuple[int, int] | None = None,
    stale_shrinkage_k: float = DEFAULT_STALE_SHRINKAGE_K,
) -> float:
    """Probability-weighted expected points, in real Kickbase points.

    ``live_status`` is Kickbase's current injury flag. It defaults to None so
    the season replay -- which has per-match history but no historical injury
    flags -- keeps its existing behaviour and does not silently acquire a
    signal it cannot evaluate.
    """
    probs = availability_probs(
        prev_status,
        availability,
        live_status=live_status,
        uncertain_start_multiplier=uncertain_start_multiplier,
        played_history=played_history,
        stale_shrinkage_k=stale_shrinkage_k,
    )
    return sum(probs[s] * rate.predict(player_id, s, position) for s in PLAYED_STATUSES)


def score_player_v2(
    data: PlayerData,
    *,
    max_status_age_days: float | None = None,
    uncertain_start_multiplier: float = DEFAULT_UNCERTAIN_START_MULTIPLIER,
    stale_shrinkage_k: float = DEFAULT_STALE_SHRINKAGE_K,
    now: datetime | None = None,
) -> PlayerScore:
    """Score a player with the fitted v2 models. Pure — no I/O beyond cached load."""
    availability, rate, _meta = _models()
    player = data.player
    position = player.position or None

    prev_status = last_played_status(data.performance, now=now, max_age_days=max_status_age_days)

    # REH-98: with no usable status the fitted model returns the league
    # marginal, which pre-season is identical for every player in the game.
    # The player's own record is the only availability signal left.
    played_history = recent_played_share(data.performance) if prev_status is None else None

    # Withhold the fitted quality coefficient from players whose fitted record
    # is not top-flight: `rate.predict` then falls back to the position prior,
    # which is exactly the cold-start path an unfitted player already takes.
    # This is NOT a discount multiplier — REH-80's blanket cold-start discount
    # was reverted for costing 782 points. It declines to apply a coefficient
    # fitted on inapplicable data.
    quality_key = player.id if has_top_flight_history(data.player_details) else None

    # Kickbase's live injury flag: unfitted, applied at serving time.
    # Downward-only — see apply_availability_override for why that matters.
    live_status = (data.player_details or {}).get("st")

    ep = compose_ep(
        quality_key,
        prev_status,
        position,
        availability,
        rate,
        live_status=live_status,
        uncertain_start_multiplier=uncertain_start_multiplier,
        played_history=played_history,
        stale_shrinkage_k=stale_shrinkage_k,
    )

    dgw_multiplier = DGW_MULTIPLIER if data.is_dgw else 1.0
    ep *= dgw_multiplier

    probs = availability_probs(
        prev_status,
        availability,
        live_status=live_status,
        uncertain_start_multiplier=uncertain_start_multiplier,
        played_history=played_history,
        stale_shrinkage_k=stale_shrinkage_k,
    )
    notes = [
        f"v2: availability P(start)={probs[5]:.0%} "
        f"(prev status {prev_status if prev_status is not None else 'unknown'}), "
        f"rate={rate.predict(quality_key, 5, position):.0f} pts if started"
    ]
    if quality_key not in rate.quality:
        notes.append("No fitted quality — using position prior (cold start)")
    if live_status in OUT_STATUSES:
        notes.append(f"UNAVAILABLE — Kickbase status {live_status} (injured)")
    elif live_status in UNCERTAIN_STATUSES:
        notes.append(f"Status uncertain ({live_status}) — start probability reduced")
    if data.is_dgw:
        notes.append("DOUBLE GAMEWEEK ×1.8")

    return PlayerScore(
        player_id=player.id,
        expected_points=round(ep, 2),
        data_quality=DataQuality(
            grade="A" if quality_key in rate.quality else "C",
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
