"""Outside predicted elevens (`rehoboam.predicted_lineups`, migration 029).

One row per player a source names for a club and matchday: the eleven
(`in_xi`, slots 1–11), the alternatives listed beside them and the missing
players (slot NULL). The newest fetch of a club wins (`NEWEST_FETCH`).

An alternative's slot is `COVER_SLOT_BASE` + the slot of the starter he is
listed beside (2026-10-22). Rows written before carry 101 upward in page
order, not linked to a starter; `lineup_prob.outside_codes` tells the two apart.
"""

from __future__ import annotations

from typing import Any

from rehoboam.scoring.v2.lineup_prob import outside_codes, outside_in_xi

LIGAINSIDER = "ligainsider"

#: Alternative slot = COVER_SLOT_BASE + the covered starter's slot (201–211).
COVER_SLOT_BASE = 200

#: Only a club's newest fetch for the matchday counts. `write` upserts by name
#: and deletes nothing, so a player the page dropped since an earlier fetch
#: keeps that fetch's row (2–3 phantom "named" starters a matchday on MD5–6).
#: Every reader of the table filters through this, on an alias `x`.
NEWEST_FETCH = (
    "x.fetched_at = (SELECT MAX(y.fetched_at) FROM rehoboam.predicted_lineups y "
    "WHERE y.source = x.source AND y.season = x.season "
    "AND y.day_number = x.day_number AND y.team_id = x.team_id)"
)

_COLUMNS = (
    "source",
    "season",
    "day_number",
    "team_id",
    "player_name",
    "player_id",
    "in_xi",
    "slot",
    "source_updated_at",
    "fetched_at",
)


class PredictedLineupStore:
    def __init__(self, dsn: str | None = None):
        self.dsn = dsn

    def connection(self):
        from rehoboam.store import connect

        return connect(self.dsn)

    def write(self, rows: list[dict[str, Any]]) -> int:
        """Upsert on the source's (season, matchday, club, name) key."""
        if not rows:
            return 0
        cols = ", ".join(_COLUMNS)
        placeholders = ", ".join(["%s"] * len(_COLUMNS))
        updates = ", ".join(
            f"{c} = excluded.{c}"
            for c in _COLUMNS
            if c not in ("source", "season", "day_number", "team_id", "player_name")
        )
        with self.connection() as conn, conn.cursor() as cur:
            cur.executemany(
                f"INSERT INTO rehoboam.predicted_lineups ({cols}) VALUES ({placeholders}) "  # nosec B608
                f"ON CONFLICT (source, season, day_number, team_id, player_name) "
                f"DO UPDATE SET {updates}",
                [[r.get(c) for c in _COLUMNS] for r in rows],
            )
        return len(rows)

    def upcoming_matchday(self, now: float) -> dict[str, Any] | None:
        """The earliest matchday in `fixtures` that has not fully finished:
        `{season, day_number, first_kickoff, last_kickoff}`, or None."""
        with self.connection() as conn:
            row = conn.execute(
                "SELECT season, day_number, MIN(kickoff) AS first_kickoff, "
                "MAX(kickoff) AS last_kickoff FROM rehoboam.fixtures "
                "GROUP BY season, day_number HAVING MAX(kickoff) > %s "
                "ORDER BY MIN(kickoff) LIMIT 1",
                (now,),
            ).fetchone()
        return dict(row) if row else None

    def xi_for(self, *, season: str, day_number: int, source: str = LIGAINSIDER) -> dict[str, bool]:
        """player_id → named in the predicted eleven.

        A player of a club the source predicted who is not named is False; a
        player of a club without a prediction is absent (None to the scorer).
        Named rows without a resolved player id contribute nothing.
        """
        codes = self.codes_for(season=season, day_number=day_number, source=source)
        return {pid: bool(outside_in_xi(code)) for pid, code in codes.items()}

    def codes_for(
        self, *, season: str, day_number: int, source: str = LIGAINSIDER
    ) -> dict[str, int]:
        """player_id → the source's verdict (`lineup_prob.OUTSIDE_*`) for the matchday.

        Every member of a predicted club has one (`OUTSIDE_LEFT_OUT` when not
        on the page); a player of a club without a prediction is absent.
        """
        with self.connection() as conn:
            rows = conn.execute(
                # NEWEST_FETCH is a constant; every value is a bound parameter.
                "SELECT player_id, player_name, team_id, in_xi, slot "  # nosec B608
                "FROM rehoboam.predicted_lineups x "
                "WHERE source = %s AND season = %s AND day_number = %s AND " + NEWEST_FETCH,
                (source, season, day_number),
            ).fetchall()
            teams = sorted({r["team_id"] for r in rows})
            members = (
                conn.execute(
                    "SELECT player_id, team_id, last_name FROM rehoboam.player_universe "
                    "WHERE team_id = ANY(%s)",
                    (teams,),
                ).fetchall()
                if teams
                else []
            )
        return outside_codes(rows, members, cover_slot_base=COVER_SLOT_BASE)

    def team_kickoffs(self, *, season: str, day_number: int) -> dict[str, float]:
        """team_id → its own kickoff epoch in the matchday (`fixtures`)."""
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT home_team_id, away_team_id, kickoff FROM rehoboam.fixtures "
                "WHERE season = %s AND day_number = %s",
                (season, day_number),
            ).fetchall()
        out: dict[str, float] = {}
        for r in rows:
            for team in (r["home_team_id"], r["away_team_id"]):
                if team is not None:
                    out[str(team)] = min(float(r["kickoff"]), out.get(str(team), float("inf")))
        return out

    def coverage(
        self, *, season: str, day_number: int, source: str = LIGAINSIDER
    ) -> dict[str, float]:
        """team_id → newest fetch epoch for this matchday."""
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT team_id, MAX(fetched_at) AS at FROM rehoboam.predicted_lineups "
                "WHERE source = %s AND season = %s AND day_number = %s GROUP BY team_id",
                (source, season, day_number),
            ).fetchall()
        return {r["team_id"]: float(r["at"]) for r in rows}
