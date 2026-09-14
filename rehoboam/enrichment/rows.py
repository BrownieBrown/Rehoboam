"""Kickbase payloads → corpus rows, with no I/O.

Both corpus writers — the SQLite ``TrainingCorpus`` the offline tools read
and the store's ``CorpusStore`` the ingestion writes — persist the same
rows, so the parsing lives once, here. Every rule in this module was
verified against live responses (dates in the docstrings) and must not be
re-derived from field names.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from rehoboam.enrichment.corpus import _to_epoch
from rehoboam.match_parsing import parse_minutes

POSITIONS = {1: "Goalkeeper", 2: "Defender", 3: "Midfielder", 4: "Forward"}


def universe_rows(items: list[dict]) -> list[dict]:
    """Lineup-selection items → ``player_universe`` rows.

    The id is ``pi`` and there is no first-name field on this endpoint
    (measured live, 2026-07-29), so ``first_name`` is always None.
    """
    out = []
    for item in items:
        pid = item.get("pi")
        if pid is None:
            continue
        out.append(
            {
                "player_id": str(pid),
                "first_name": None,
                "last_name": item.get("n"),
                "position": POSITIONS.get(item.get("pos")),
                "team_id": item.get("tid"),
                "market_value": item.get("mv"),
                "average_points": item.get("ap"),
            }
        )
    return out


def match_history_rows(player_id: str, team_id: str | None, performance: dict) -> list[dict]:
    """Performance response → one row per placed match.

    ``pt`` is the player's team for that match and is the primary source for
    home/away and opponent; the caller's ``team_id`` is the *current* team and
    is wrong for every match before the player's last transfer, so it is only
    a fallback. ``ap``/``tp``/``asp`` are season-end totals stamped on every
    row (verified live) and are deliberately not stored — they would leak the
    season outcome into training rows.
    """
    rows: list[dict] = []
    fallback_team = str(team_id) if team_id is not None else None
    for season in performance.get("it") or []:
        title = season.get("ti")
        if not title:
            continue
        for m in season.get("ph") or []:
            day = m.get("day")
            if day is None:
                continue
            t1 = str(m.get("t1", "")) or None
            t2 = str(m.get("t2", "")) or None
            pt = m.get("pt")
            team = str(pt) if pt is not None else fallback_team
            is_home = 1 if team is not None and team == t1 else 0
            st = m.get("st")
            rows.append(
                {
                    "player_id": str(player_id),
                    "season": str(title),
                    "day_number": int(day),
                    "match_date": m.get("md"),
                    "points": int(m.get("p") or 0),
                    "minutes": parse_minutes(m.get("mp")),
                    "team_id": team,
                    "opponent_team_id": t2 if is_home else t1,
                    "is_home": is_home,
                    "status": int(st) if st is not None else None,
                }
            )
    return rows


def mv_series_rows(player_id: str, history: dict) -> list[dict]:
    """Market-value history → rows; ``dt`` is days since epoch; ``mv <= 0`` is a sentinel."""
    return [
        {
            "player_id": str(player_id),
            "snapshot_at": float(item["dt"]) * 86400.0,
            "market_value": int(item["mv"]),
        }
        for item in (history.get("it") or [])
        if item.get("dt") is not None and item.get("mv") and item["mv"] > 0
    ]


def transfer_rows(player_id: str, history: dict) -> list[dict]:
    """Transfer history → rows; ``dt`` here is ISO-8601 text (verified 2026-07-29)."""
    rows: list[dict] = []
    for item in history.get("it") or []:
        dt = item.get("dt")
        if not dt:
            continue
        try:
            transfer_at = _to_epoch(dt)
        except (ValueError, TypeError):
            continue
        counterparty_id = item.get("u")
        rows.append(
            {
                "player_id": str(player_id),
                "transfer_at": transfer_at,
                "price": item.get("trp"),
                "transfer_type": item.get("t"),
                "counterparty_id": (str(counterparty_id) if counterparty_id is not None else None),
                "counterparty_name": item.get("unm"),
            }
        )
    return rows


def _opt_int(value: Any) -> int | None:
    return int(value) if value is not None else None


def status_row(player_id: str, day: date, details: dict, fetched_at: float) -> dict:
    """League player details → one ``player_status_daily`` row.

    ``st`` is the injury/availability status (0 healthy), ``prob`` the lineup
    probability (1 starter … 5 unlikely) — the two fields the scorer never had
    day by day. Missing fields stay None rather than becoming a fake healthy
    starter.
    """
    tid = details.get("tid")
    return {
        "player_id": str(player_id),
        "day": day,
        "status": _opt_int(details.get("st")),
        "lineup_probability": _opt_int(details.get("prob")),
        "market_value": _opt_int(details.get("mv")),
        "team_id": str(tid) if tid is not None else None,
        "fetched_at": float(fetched_at),
    }
