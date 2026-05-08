"""Shared Azure Blob Storage helpers for SQLite state persistence.

The bot runs on Azure Functions and persists its SQLite databases to Azure Blob
Storage between invocations. This module is the single source of truth for
which DBs are persisted (`DB_FILES`) and exposes `fetch_state` / `push_state`
used by both the Azure Function and the `rehoboam fetch-azure-state` /
`rehoboam push-azure-state` CLI commands for prod debugging.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

DB_FILES: tuple[str, ...] = (
    "bid_learning.db",
    "value_tracking.db",
    "market_prices.db",
    "player_history.db",
)

BACKUP_SUFFIX = ".local-bak"

FetchStatus = Literal["downloaded", "missing_in_blob", "skipped_dry_run", "error"]
PushStatus = Literal["uploaded", "missing_local", "skipped_dry_run", "error"]


class MissingAzureCredentials(RuntimeError):
    """Raised when AZURE_STORAGE_CONNECTION_STRING is not configured."""


@dataclass(frozen=True)
class BlobInfo:
    name: str
    last_modified: datetime | None
    size: int | None


@dataclass(frozen=True)
class FetchResult:
    db_file: str
    blob: BlobInfo
    local_path: Path
    backed_up_to: Path | None
    status: FetchStatus
    error: str | None = None


@dataclass(frozen=True)
class PushResult:
    db_file: str
    local_path: Path
    local_size: int | None
    status: PushStatus
    error: str | None = None


def _backup_path(local_path: Path) -> Path:
    return local_path.with_name(local_path.name + BACKUP_SUFFIX)


def _get_container(connection_string: str | None, container_name: str):
    if not connection_string:
        raise MissingAzureCredentials(
            "AZURE_STORAGE_CONNECTION_STRING is not set. "
            "Add it to your .env to fetch or push prod state."
        )

    from azure.storage.blob import BlobServiceClient

    return BlobServiceClient.from_connection_string(connection_string).get_container_client(
        container_name
    )


def _probe_blob(container, name: str) -> BlobInfo:
    try:
        props = container.get_blob_client(name).get_blob_properties()
        return BlobInfo(name=name, last_modified=props.last_modified, size=props.size)
    except Exception as e:  # noqa: BLE001
        if "BlobNotFound" in str(e):
            return BlobInfo(name=name, last_modified=None, size=None)
        raise


def list_blobs(connection_string: str | None, container_name: str) -> list[BlobInfo]:
    container = _get_container(connection_string, container_name)
    return [_probe_blob(container, name) for name in DB_FILES]


def fetch_state(
    connection_string: str | None,
    container_name: str,
    dest_dir: Path,
    *,
    backup: bool = True,
    dry_run: bool = False,
) -> list[FetchResult]:
    """Download each DB blob into ``dest_dir``.

    For each file:
    - Missing in blob → status ``missing_in_blob``.
    - ``dry_run`` → status ``skipped_dry_run``, no file I/O.
    - Otherwise: when ``backup`` and the local file exists, rename it to
      ``<name>.local-bak`` (overwriting any prior backup), then write the blob.
    """
    container = _get_container(connection_string, container_name)
    dest_dir.mkdir(parents=True, exist_ok=True)
    results: list[FetchResult] = []

    for name in DB_FILES:
        blob = _probe_blob(container, name)
        local_path = dest_dir / name

        if blob.last_modified is None:
            results.append(
                FetchResult(
                    db_file=name,
                    blob=blob,
                    local_path=local_path,
                    backed_up_to=None,
                    status="missing_in_blob",
                )
            )
            continue

        if dry_run:
            would_back_up = _backup_path(local_path) if (backup and local_path.exists()) else None
            results.append(
                FetchResult(
                    db_file=name,
                    blob=blob,
                    local_path=local_path,
                    backed_up_to=would_back_up,
                    status="skipped_dry_run",
                )
            )
            continue

        backup_path: Path | None = None
        try:
            if backup and local_path.exists():
                backup_path = _backup_path(local_path)
                local_path.replace(backup_path)
            data = container.get_blob_client(name).download_blob().readall()
            local_path.write_bytes(data)
            results.append(
                FetchResult(
                    db_file=name,
                    blob=blob,
                    local_path=local_path,
                    backed_up_to=backup_path,
                    status="downloaded",
                )
            )
            logger.info("Downloaded %s (%d bytes)", name, len(data))
        except Exception as e:  # noqa: BLE001
            results.append(
                FetchResult(
                    db_file=name,
                    blob=blob,
                    local_path=local_path,
                    backed_up_to=backup_path,
                    status="error",
                    error=str(e),
                )
            )
            logger.warning("Failed to download %s: %s", name, e)

    return results


def push_state(
    connection_string: str | None,
    container_name: str,
    source_dir: Path,
    *,
    dry_run: bool = False,
) -> list[PushResult]:
    """Upload each local DB to the corresponding blob (overwrite).

    Files missing locally are skipped (status ``missing_local``).
    """
    container = _get_container(connection_string, container_name)
    results: list[PushResult] = []

    for name in DB_FILES:
        local_path = source_dir / name

        if not local_path.exists():
            results.append(
                PushResult(
                    db_file=name,
                    local_path=local_path,
                    local_size=None,
                    status="missing_local",
                )
            )
            continue

        size = local_path.stat().st_size

        if dry_run:
            results.append(
                PushResult(
                    db_file=name,
                    local_path=local_path,
                    local_size=size,
                    status="skipped_dry_run",
                )
            )
            continue

        try:
            with open(local_path, "rb") as f:
                container.get_blob_client(name).upload_blob(f, overwrite=True)
            results.append(
                PushResult(
                    db_file=name,
                    local_path=local_path,
                    local_size=size,
                    status="uploaded",
                )
            )
            logger.info("Uploaded %s (%d bytes)", name, size)
        except Exception as e:  # noqa: BLE001
            results.append(
                PushResult(
                    db_file=name,
                    local_path=local_path,
                    local_size=size,
                    status="error",
                    error=str(e),
                )
            )
            logger.warning("Failed to upload %s: %s", name, e)

    return results
