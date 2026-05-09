"""Tests for rehoboam.azure_blob — fetch_state / push_state state-machine logic.

Tests patch ``_get_container`` so they don't depend on the live Azure SDK call
chain. ``MissingAzureCredentials`` is exercised separately by leaving
``connection_string`` empty.
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from rehoboam import azure_blob
from rehoboam.azure_blob import (
    DB_FILES,
    FETCH_SIDECAR,
    BlobChangedSinceFetch,
    MissingAzureCredentials,
    check_freshness,
    fetch_state,
    list_blobs,
    push_state,
)


def _props(last_modified: datetime, size: int) -> MagicMock:
    p = MagicMock()
    p.last_modified = last_modified
    p.size = size
    return p


def _blob_not_found() -> Exception:
    return Exception("BlobNotFound: The specified blob does not exist.")


def _make_container(per_blob: dict) -> MagicMock:
    """Build a fake ContainerClient.

    ``per_blob`` keys are blob names. Values are dicts with optional keys:
      - ``props``: MagicMock to return from ``get_blob_properties``
      - ``props_error``: Exception to raise from ``get_blob_properties``
      - ``data``: bytes to return from ``download_blob().readall()``
      - ``download_error``: Exception to raise from ``download_blob``
      - ``upload_error``: Exception to raise from ``upload_blob``
    """
    container = MagicMock()

    def get_blob_client(name):
        bc = MagicMock()
        spec = per_blob.get(name, {})

        if "props_error" in spec:
            bc.get_blob_properties.side_effect = spec["props_error"]
        else:
            bc.get_blob_properties.return_value = spec.get("props")

        if "download_error" in spec:
            bc.download_blob.side_effect = spec["download_error"]
        else:
            blob_obj = MagicMock()
            blob_obj.readall.return_value = spec.get("data", b"")
            bc.download_blob.return_value = blob_obj

        if "upload_error" in spec:
            bc.upload_blob.side_effect = spec["upload_error"]

        return bc

    container.get_blob_client.side_effect = get_blob_client
    return container


def _patch_container(monkeypatch, container):
    monkeypatch.setattr(azure_blob, "_get_container", lambda *a, **kw: container)


# --- credential handling --------------------------------------------------


def test_missing_credentials_raises():
    with pytest.raises(MissingAzureCredentials):
        list_blobs(connection_string=None, container_name="rehoboam-data")

    with pytest.raises(MissingAzureCredentials):
        list_blobs(connection_string="", container_name="rehoboam-data")


# --- fetch_state ----------------------------------------------------------


def test_fetch_state_downloads_all_files(monkeypatch, tmp_path):
    ts = datetime(2026, 5, 8, 8, 1, 32, tzinfo=timezone.utc)
    container = _make_container(
        {
            name: {"props": _props(ts, 1024 * (i + 1)), "data": f"db-{name}".encode()}
            for i, name in enumerate(DB_FILES)
        }
    )
    _patch_container(monkeypatch, container)

    results = fetch_state("conn", "rehoboam-data", tmp_path)

    assert [r.status for r in results] == ["downloaded"] * len(DB_FILES)
    for r in results:
        assert (tmp_path / r.db_file).read_bytes() == f"db-{r.db_file}".encode()
        assert r.backed_up_to is None
        assert r.blob.last_modified == ts


def test_fetch_state_backs_up_existing_local_file(monkeypatch, tmp_path):
    name = DB_FILES[0]
    existing = tmp_path / name
    existing.write_bytes(b"OLD-LOCAL-DATA")

    container = _make_container(
        {
            n: {"props": _props(datetime(2026, 5, 8, tzinfo=timezone.utc), 100), "data": b"NEW"}
            for n in DB_FILES
        }
    )
    _patch_container(monkeypatch, container)

    results = fetch_state("conn", "rehoboam-data", tmp_path, backup=True)

    first = next(r for r in results if r.db_file == name)
    assert first.status == "downloaded"
    assert first.backed_up_to == tmp_path / f"{name}.local-bak"
    assert first.backed_up_to.read_bytes() == b"OLD-LOCAL-DATA"
    assert (tmp_path / name).read_bytes() == b"NEW"


def test_fetch_state_no_backup_clobbers_local(monkeypatch, tmp_path):
    name = DB_FILES[0]
    (tmp_path / name).write_bytes(b"OLD")

    container = _make_container(
        {
            n: {"props": _props(datetime(2026, 5, 8, tzinfo=timezone.utc), 100), "data": b"NEW"}
            for n in DB_FILES
        }
    )
    _patch_container(monkeypatch, container)

    results = fetch_state("conn", "rehoboam-data", tmp_path, backup=False)
    first = next(r for r in results if r.db_file == name)

    assert first.backed_up_to is None
    assert not (tmp_path / f"{name}.local-bak").exists()
    assert (tmp_path / name).read_bytes() == b"NEW"


def test_fetch_state_dry_run_writes_no_files(monkeypatch, tmp_path):
    name = DB_FILES[0]
    (tmp_path / name).write_bytes(b"UNTOUCHED")
    ts = datetime(2026, 5, 8, tzinfo=timezone.utc)

    container = _make_container(
        {n: {"props": _props(ts, 555), "data": b"would-be-new"} for n in DB_FILES}
    )
    _patch_container(monkeypatch, container)

    results = fetch_state("conn", "rehoboam-data", tmp_path, dry_run=True)

    assert all(r.status == "skipped_dry_run" for r in results)
    assert (tmp_path / name).read_bytes() == b"UNTOUCHED"
    # Backup target reported only when local file exists.
    first = next(r for r in results if r.db_file == name)
    assert first.backed_up_to == tmp_path / f"{name}.local-bak"
    assert all(r.blob.size == 555 for r in results)


def test_fetch_state_handles_blob_not_found_per_file(monkeypatch, tmp_path):
    missing = DB_FILES[1]
    spec = {}
    for n in DB_FILES:
        if n == missing:
            spec[n] = {"props_error": _blob_not_found()}
        else:
            spec[n] = {
                "props": _props(datetime(2026, 5, 8, tzinfo=timezone.utc), 200),
                "data": b"ok",
            }
    container = _make_container(spec)
    _patch_container(monkeypatch, container)

    results = fetch_state("conn", "rehoboam-data", tmp_path)

    statuses = {r.db_file: r.status for r in results}
    assert statuses[missing] == "missing_in_blob"
    assert all(s == "downloaded" for n, s in statuses.items() if n != missing)
    assert not (tmp_path / missing).exists()


def test_fetch_state_isolates_download_failures(monkeypatch, tmp_path):
    failing = DB_FILES[2]
    spec = {}
    for n in DB_FILES:
        spec[n] = {
            "props": _props(datetime(2026, 5, 8, tzinfo=timezone.utc), 200),
            "data": b"ok",
        }
        if n == failing:
            spec[n]["download_error"] = RuntimeError("transient network error")
    container = _make_container(spec)
    _patch_container(monkeypatch, container)

    results = fetch_state("conn", "rehoboam-data", tmp_path)
    statuses = {r.db_file: r.status for r in results}

    assert statuses[failing] == "error"
    assert all(s == "downloaded" for n, s in statuses.items() if n != failing)
    bad = next(r for r in results if r.db_file == failing)
    assert "transient" in bad.error


# --- push_state -----------------------------------------------------------


def test_push_state_uploads_existing_files(monkeypatch, tmp_path):
    for n in DB_FILES:
        (tmp_path / n).write_bytes(b"local-" + n.encode())
    container = _make_container({n: {} for n in DB_FILES})
    _patch_container(monkeypatch, container)

    results = push_state("conn", "rehoboam-data", tmp_path)

    assert all(r.status == "uploaded" for r in results)
    assert container.get_blob_client.call_count == len(DB_FILES)


def test_push_state_skips_missing_local(monkeypatch, tmp_path):
    present = DB_FILES[0]
    (tmp_path / present).write_bytes(b"x")
    container = _make_container({n: {} for n in DB_FILES})
    _patch_container(monkeypatch, container)

    results = push_state("conn", "rehoboam-data", tmp_path)
    statuses = {r.db_file: r.status for r in results}

    assert statuses[present] == "uploaded"
    for n in DB_FILES:
        if n != present:
            assert statuses[n] == "missing_local"


def test_push_state_dry_run_does_not_upload(monkeypatch, tmp_path):
    for n in DB_FILES:
        (tmp_path / n).write_bytes(b"x")
    container = _make_container({n: {} for n in DB_FILES})
    _patch_container(monkeypatch, container)

    results = push_state("conn", "rehoboam-data", tmp_path, dry_run=True)

    assert all(r.status == "skipped_dry_run" for r in results)
    # get_blob_client may still be called for setup but upload_blob shouldn't fire
    for blob_client_call in container.get_blob_client.return_value.upload_blob.call_args_list:
        raise AssertionError(f"upload_blob called unexpectedly with {blob_client_call}")


def test_push_state_isolates_upload_failures(monkeypatch, tmp_path):
    failing = DB_FILES[1]
    for n in DB_FILES:
        (tmp_path / n).write_bytes(b"x")

    spec = {n: {} for n in DB_FILES}
    spec[failing]["upload_error"] = RuntimeError("upload boom")
    container = _make_container(spec)
    _patch_container(monkeypatch, container)

    results = push_state("conn", "rehoboam-data", tmp_path)
    statuses = {r.db_file: r.status for r in results}

    assert statuses[failing] == "error"
    assert all(s == "uploaded" for n, s in statuses.items() if n != failing)


# --- sidecar + freshness check (REH-39) -----------------------------------


def test_fetch_state_writes_sidecar(monkeypatch, tmp_path):
    ts = datetime(2026, 5, 9, 8, 0, 8, tzinfo=timezone.utc)
    container = _make_container({n: {"props": _props(ts, 1024), "data": b"x"} for n in DB_FILES})
    _patch_container(monkeypatch, container)

    fetch_state("conn", "rehoboam-data", tmp_path)

    sidecar = tmp_path / FETCH_SIDECAR
    assert sidecar.exists(), "fetch_state should write the .fetch_state.json sidecar"
    recorded = json.loads(sidecar.read_text())
    assert set(recorded) == set(DB_FILES)
    for v in recorded.values():
        assert v.startswith("2026-05-09T08:00:08")


def test_fetch_state_dry_run_does_not_write_sidecar(monkeypatch, tmp_path):
    ts = datetime(2026, 5, 9, tzinfo=timezone.utc)
    container = _make_container({n: {"props": _props(ts, 100), "data": b"x"} for n in DB_FILES})
    _patch_container(monkeypatch, container)

    fetch_state("conn", "rehoboam-data", tmp_path, dry_run=True)
    assert not (tmp_path / FETCH_SIDECAR).exists()


def test_check_freshness_empty_when_no_sidecar(monkeypatch, tmp_path):
    container = _make_container(
        {n: {"props": _props(datetime.now(timezone.utc), 100)} for n in DB_FILES}
    )
    _patch_container(monkeypatch, container)

    stale = check_freshness("conn", "rehoboam-data", tmp_path)
    assert stale == []


def test_check_freshness_passes_when_blob_unchanged(monkeypatch, tmp_path):
    ts = datetime(2026, 5, 9, 8, 0, 8, tzinfo=timezone.utc)
    container = _make_container({n: {"props": _props(ts, 100)} for n in DB_FILES})
    _patch_container(monkeypatch, container)

    sidecar = {n: ts.isoformat() for n in DB_FILES}
    (tmp_path / FETCH_SIDECAR).write_text(json.dumps(sidecar))

    assert check_freshness("conn", "rehoboam-data", tmp_path) == []


def test_check_freshness_detects_drift(monkeypatch, tmp_path):
    fetched_at = datetime(2026, 5, 9, 8, 0, 8, tzinfo=timezone.utc)
    later = fetched_at + timedelta(hours=12)
    drifting = DB_FILES[0]
    spec = {n: {"props": _props(fetched_at, 100)} for n in DB_FILES}
    spec[drifting] = {"props": _props(later, 100)}
    container = _make_container(spec)
    _patch_container(monkeypatch, container)

    sidecar = {n: fetched_at.isoformat() for n in DB_FILES}
    (tmp_path / FETCH_SIDECAR).write_text(json.dumps(sidecar))

    stale = check_freshness("conn", "rehoboam-data", tmp_path)
    assert len(stale) == 1
    assert stale[0].db_file == drifting
    assert stale[0].fetched_last_modified == fetched_at
    assert stale[0].current_last_modified == later


def test_push_state_refuses_on_drift(monkeypatch, tmp_path):
    for n in DB_FILES:
        (tmp_path / n).write_bytes(b"x")
    fetched_at = datetime(2026, 5, 9, 8, 0, 8, tzinfo=timezone.utc)
    later = fetched_at + timedelta(hours=12)
    spec = {n: {"props": _props(later, 100)} for n in DB_FILES}
    container = _make_container(spec)
    _patch_container(monkeypatch, container)

    sidecar = {n: fetched_at.isoformat() for n in DB_FILES}
    (tmp_path / FETCH_SIDECAR).write_text(json.dumps(sidecar))

    with pytest.raises(BlobChangedSinceFetch) as exc_info:
        push_state("conn", "rehoboam-data", tmp_path)
    assert len(exc_info.value.stale) == len(DB_FILES)


def test_push_state_force_bypasses_freshness(monkeypatch, tmp_path):
    for n in DB_FILES:
        (tmp_path / n).write_bytes(b"x")
    fetched_at = datetime(2026, 5, 9, 8, 0, 8, tzinfo=timezone.utc)
    later = fetched_at + timedelta(hours=12)
    container = _make_container({n: {"props": _props(later, 100)} for n in DB_FILES})
    _patch_container(monkeypatch, container)

    sidecar = {n: fetched_at.isoformat() for n in DB_FILES}
    (tmp_path / FETCH_SIDECAR).write_text(json.dumps(sidecar))

    # force=True: no exception, regular upload path runs
    results = push_state("conn", "rehoboam-data", tmp_path, force=True)
    assert all(r.status == "uploaded" for r in results)


def test_push_state_dry_run_still_checks_freshness(monkeypatch, tmp_path):
    """Dry-run should surface drift the same way a real push would so the
    user can preview the failure mode without committing."""
    for n in DB_FILES:
        (tmp_path / n).write_bytes(b"x")
    fetched_at = datetime(2026, 5, 9, tzinfo=timezone.utc)
    later = fetched_at + timedelta(hours=12)
    container = _make_container({n: {"props": _props(later, 100)} for n in DB_FILES})
    _patch_container(monkeypatch, container)
    (tmp_path / FETCH_SIDECAR).write_text(json.dumps({n: fetched_at.isoformat() for n in DB_FILES}))

    with pytest.raises(BlobChangedSinceFetch):
        push_state("conn", "rehoboam-data", tmp_path, dry_run=True)
