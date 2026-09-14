"""Weekly insurance: every store table as gzip CSV in the Blob container (spec §1).

The free plan's backup policy is not something to depend on; a CSV per table
restores onto any Postgres. ``COPY ... TO STDOUT`` streams, so a 160k-row
table never sits in memory twice.
"""

from __future__ import annotations

import gzip
import io
from collections.abc import Callable
from datetime import date

import psycopg
from psycopg import sql

from rehoboam.store import SCHEMA


def blob_uploader(connection_string: str, container: str) -> Callable[[str, bytes], None]:
    """An ``upload(name, data)`` callable against one Blob container.

    The Function app and the ``rehoboam export`` CLI command both need this;
    living here instead of in the Function app means the CLI command doesn't
    import an Azure Functions module to get it. ``azure.storage.blob``
    imports inside the function, same reason every heavy import in this
    codebase does: importable without the dependency installed unless
    actually exporting.
    """
    from azure.storage.blob import BlobServiceClient

    client = BlobServiceClient.from_connection_string(connection_string).get_container_client(
        container
    )

    def upload(name: str, data: bytes) -> None:
        client.upload_blob(name=name, data=data, overwrite=True)

    return upload


def store_tables(conn: psycopg.Connection) -> list[str]:
    rows = conn.execute(
        "select table_name from information_schema.tables "
        "where table_schema = %s and table_type = 'BASE TABLE' order by table_name",
        (SCHEMA,),
    ).fetchall()
    return [r["table_name"] for r in rows]


def export_tables(
    conn: psycopg.Connection,
    upload: Callable[[str, bytes], None],
    *,
    day: date,
    tables: list[str] | None = None,
) -> dict[str, int]:
    """Upload ``exports/<day>/<table>.csv.gz`` for each table; return bytes per table."""
    sizes: dict[str, int] = {}
    for table in tables or store_tables(conn):
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb") as gz, conn.cursor() as cur:
            with cur.copy(
                sql.SQL("COPY (SELECT * FROM {}.{}) TO STDOUT WITH (FORMAT csv, HEADER)").format(
                    sql.Identifier(SCHEMA), sql.Identifier(table)
                )
            ) as copy:
                for chunk in copy:
                    gz.write(bytes(chunk))
        data = buf.getvalue()
        upload(f"exports/{day.isoformat()}/{table}.csv.gz", data)
        sizes[table] = len(data)
    return sizes
