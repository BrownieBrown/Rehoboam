"""Session facts and integrity failures in the store (spec §3)."""

from __future__ import annotations

import time
from typing import Any

from psycopg.types.json import Jsonb

from rehoboam.services.session_facts import IntegrityFailure, SessionFacts

_COLUMNS = (
    "session_id",
    "app",
    "mode",
    "dry_run",
    "started_at",
    "duration_s",
    "phase",
    "next_kickoff",
    "next_kickoff_source",
    "squad_gk",
    "squad_def",
    "squad_mid",
    "squad_fw",
    "fieldable_count",
    "legal_formation",
    "budget",
    "sellable_value",
    "open_offers_total",
    "open_offers_manual",
    "cost_basis_missing",
    "predictions_written",
    "lineup_result",
    "errors",
    "error_text",
    "extra",
)


class SessionStore:
    def __init__(self, dsn: str | None = None):
        self.dsn = dsn

    def connection(self):
        from rehoboam.store import connect

        return connect(self.dsn)

    def record(self, facts: SessionFacts) -> None:
        """Upsert: a session writes its row early and again at every exit, so
        the last write wins and a run that died mid-way still left a row."""
        row = facts.as_row()
        # extra=None must go over the wire as SQL NULL, not Jsonb(None) —
        # psycopg would otherwise serialize it as the *string* "null" and
        # facts()["extra"] would come back as the JSON value null, not None.
        row["extra"] = Jsonb(row["extra"]) if row["extra"] is not None else None
        cols = ", ".join(_COLUMNS)
        placeholders = ", ".join(["%s"] * len(_COLUMNS))
        updates = ", ".join(f"{c} = excluded.{c}" for c in _COLUMNS if c != "session_id")
        with self.connection() as conn:
            conn.execute(
                f"INSERT INTO rehoboam.session_facts ({cols}) VALUES ({placeholders}) "
                f"ON CONFLICT (session_id) DO UPDATE SET {updates}",
                [row[c] for c in _COLUMNS],
            )

    def record_failures(
        self,
        session_id: str,
        failures: list[IntegrityFailure],
        *,
        at: float | None = None,
    ) -> int:
        if not failures:
            return 0
        stamp = at if at is not None else time.time()
        with self.connection() as conn, conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO rehoboam.integrity_failures (session_id, rule, detail, created_at) "
                "VALUES (%s, %s, %s, %s)",
                [(session_id, f.rule, f.detail, stamp) for f in failures],
            )
        return len(failures)

    def last_ingest_completed_at(self) -> float | None:
        """When the ingestion app last finished a run without errors (rule I7)."""
        with self.connection() as conn:
            row = conn.execute(
                "SELECT MAX(started_at + duration_s) AS at FROM rehoboam.session_facts "
                "WHERE app = 'external' AND mode = 'ingest' AND errors = 0"
            ).fetchone()
        return float(row["at"]) if row and row["at"] is not None else None

    def facts(self, session_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM rehoboam.session_facts WHERE session_id = %s",
                (session_id,),
            ).fetchone()
        return dict(row) if row else None

    def failures(self, session_id: str) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT rule, detail, created_at FROM rehoboam.integrity_failures "
                "WHERE session_id = %s ORDER BY id",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]
