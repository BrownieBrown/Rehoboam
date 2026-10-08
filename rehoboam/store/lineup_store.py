"""Outside predicted elevens (`rehoboam.predicted_lineups`, migration 029).

One row per player a source names for a club and matchday: the eleven
(`in_xi`) and the alternatives listed beside them. The newest fetch wins.
"""

from __future__ import annotations

from typing import Any

LIGAINSIDER = "ligainsider"

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
        with self.connection() as conn:
            named = conn.execute(
                "SELECT player_id, team_id, in_xi FROM rehoboam.predicted_lineups "
                "WHERE source = %s AND season = %s AND day_number = %s",
                (source, season, day_number),
            ).fetchall()
            teams = sorted({r["team_id"] for r in named})
            members = (
                conn.execute(
                    "SELECT player_id FROM rehoboam.player_universe WHERE team_id = ANY(%s)",
                    (teams,),
                ).fetchall()
                if teams
                else []
            )
        out = {m["player_id"]: False for m in members}
        for r in named:
            if r["player_id"]:
                out[r["player_id"]] = bool(r["in_xi"]) or out.get(r["player_id"], False)
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
