"""`score_stored` must equal `score_player_v2` on the same matches (PR E §1, Task 2)."""

from __future__ import annotations

from datetime import datetime, timezone

from rehoboam.kickbase_client import MarketPlayer
from rehoboam.scoring.models import PlayerData
from rehoboam.scoring.store_scorer import (
    StoredPlayer,
    played_share_from_rows,
    score_stored,
)
from rehoboam.scoring.v2.adapter import score_player_v2
from rehoboam.scoring.v2.coefficients import load_coefficients

NOW = datetime(2026, 9, 15, 20, 0, tzinfo=timezone.utc)


def _payload_match(day, md, st, p, mp="90"):
    return {"day": day, "md": md, "st": st, "p": p, "mp": mp}


def _row(day, md, st, p, minutes=90, season="2026/2027"):
    return {
        "season": season,
        "day_number": day,
        "match_date": md,
        "status": st,
        "points": p,
        "minutes": minutes,
    }


HISTORY = [
    (1, "2026-08-22T13:30:00Z", 5, 80),
    (2, "2026-08-29T13:30:00Z", 4, 0),
    (3, "2026-09-12T13:30:00Z", 3, 25),
    (4, "2026-09-19T13:30:00Z", 0, 0),
]


def _market_player(pid="1", position="Midfielder"):
    return MarketPlayer(
        id=pid,
        first_name="Test",
        last_name="Player",
        position=position,
        team_id="2",
        team_name="T",
        market_value=1_000_000,
        price=1_000_000,
        points=0,
        average_points=0.0,
        status=0,
    )


def _live(pid, position, history):
    perf = {"it": [{"ti": "2026/2027", "ph": [_payload_match(*h) for h in history]}]}
    data = PlayerData(
        player=_market_player(pid, position),
        performance=perf,
        player_details=None,
        team_strength=None,
        opponent_strength=None,
        is_dgw=False,
    )
    return score_player_v2(data, now=NOW, max_status_age_days=60.0)


def _stored(pid, position, history, live_status=None):
    return StoredPlayer(
        player_id=pid,
        position=position,
        team_id="1",
        market_value=1_000_000,
        live_status=live_status,
        lineup_probability=None,
        status_fetched_at=None,
        matches=[_row(*h) for h in history],
    )


def test_matches_the_live_scorer_on_the_same_history():
    availability, rate, _ = load_coefficients()
    for pid in ("1", next(iter(rate.quality))):  # one unfitted id, one fitted id
        live = _live(pid, "Midfielder", HISTORY)
        stored = score_stored(
            _stored(pid, "Midfielder", HISTORY),
            now=NOW,
            max_status_age_days=60.0,
            availability=availability,
            rate=rate,
        )
        assert round(stored.predicted_ep, 2) == live.expected_points
        assert stored.data_grade == live.data_quality.grade
        assert stored.prev_status == 3


def test_unsorted_rows_are_ordered_before_the_status_is_read():
    availability, rate, _ = load_coefficients()
    shuffled = [HISTORY[2], HISTORY[0], HISTORY[3], HISTORY[1]]
    stored = score_stored(
        _stored("1", "Forward", shuffled),
        now=NOW,
        max_status_age_days=60.0,
        availability=availability,
        rate=rate,
    )
    assert stored.prev_status == 3


def test_stale_status_falls_back_to_the_played_share_prior():
    availability, rate, _ = load_coefficients()
    old = [
        (d, f"2026-03-{d + 1:02d}T13:30:00Z", st, p)
        for d, st, p in [(20, 5, 50), (21, 5, 40), (22, 4, 0), (23, 1, 0), (24, 5, 60)]
    ]
    stored = score_stored(
        _stored("1", "Midfielder", old),
        now=NOW,
        max_status_age_days=60.0,
        availability=availability,
        rate=rate,
    )
    assert stored.prev_status is None
    assert played_share_from_rows([_row(*h) for h in old]) == (3, 5)
    live = _live("1", "Midfielder", old)
    assert round(stored.predicted_ep, 2) == live.expected_points


def test_played_share_needs_five_recorded_matchdays_in_one_season():
    rows = [_row(d, f"2026-08-{d + 20:02d}T13:30:00Z", 5, 10) for d in range(1, 5)]
    assert played_share_from_rows(rows) is None
    rows.append(_row(5, "2026-09-05T13:30:00Z", 0, 0))  # future rows are not evidence
    assert played_share_from_rows(rows) is None
    rows.append(_row(6, "2026-09-12T13:30:00Z", 4, 0))
    assert played_share_from_rows(rows) == (4, 5)


def test_injured_live_status_moves_ep_toward_zero():
    availability, rate, _ = load_coefficients()
    healthy = score_stored(
        _stored("1", "Midfielder", HISTORY, live_status=0),
        now=NOW,
        max_status_age_days=60.0,
        availability=availability,
        rate=rate,
    )
    injured = score_stored(
        _stored("1", "Midfielder", HISTORY, live_status=4),
        now=NOW,
        max_status_age_days=60.0,
        availability=availability,
        rate=rate,
    )
    assert injured.predicted_ep < healthy.predicted_ep
    assert abs(sum(injured.p_status.values()) - 1.0) < 1e-9


def test_no_matches_scores_from_the_prior():
    availability, rate, _ = load_coefficients()
    stored = score_stored(
        _stored("1", "Goalkeeper", []),
        now=NOW,
        max_status_age_days=60.0,
        availability=availability,
        rate=rate,
    )
    assert stored.prev_status is None and stored.predicted_ep > 0
