"""The weekly export is one gzip CSV per table, header included, readable back."""

from __future__ import annotations

import csv
import gzip
import io
from datetime import date

from rehoboam.store import connect
from rehoboam.store.export import export_tables, facts_for_export, store_tables


def test_export_writes_every_table_as_gzip_csv_with_header(store_dsn):
    uploaded: dict[str, bytes] = {}
    with connect(store_dsn) as conn:
        conn.execute(
            "insert into rehoboam.team_value_history (snapshot_at, league_id, team_value, budget, "
            "squad_size) values (1.5, 'L', 100, 10, 15)"
        )
        sizes = export_tables(conn, uploaded.__setitem__, day=date(2026, 9, 14))
        names = store_tables(conn)
    assert "player_status_daily" in names and "schema_migrations" in names
    assert set(sizes) == set(names)
    blob = uploaded["exports/2026-09-14/team_value_history.csv.gz"]
    rows = list(csv.DictReader(io.StringIO(gzip.decompress(blob).decode())))
    assert rows[0]["league_id"] == "L" and rows[0]["team_value"] == "100"
    assert sizes["team_value_history"] == len(blob)


def test_facts_for_export_summarizes_table_count_and_total_bytes():
    sizes = {"a": 100, "b": 250, "c": 0}
    facts = facts_for_export(
        sizes, app="cli", session_id="xyz789", started_at=1_000.0, duration_s=5.5
    )
    assert facts.session_id == "xyz789"
    assert facts.app == "cli"
    assert facts.mode == "export"
    assert facts.started_at == 1_000.0
    assert facts.duration_s == 5.5
    assert facts.errors == 0
    assert facts.error_text == ""
    assert facts.extra == {"tables": 3, "bytes": 350}
