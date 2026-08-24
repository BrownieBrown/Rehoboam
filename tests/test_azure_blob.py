"""Tests for rehoboam.azure_blob — fetch_state / push_state state-machine logic.

Tests patch ``_get_container`` so they don't depend on the live Azure SDK call
chain. ``MissingAzureCredentials`` is exercised separately by leaving
``connection_string`` empty.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from rehoboam import azure_blob
from rehoboam.azure_blob import (
    DB_FILES,
    FAILED_FETCH_KEY,
    FETCH_SIDECAR,
    BlobChangedSinceFetch,
    MissingAzureCredentials,
    PushResult,
    check_freshness,
    fetch_state,
    learning_db_synced,
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
    assert len([r for r in results if r.status == "uploaded"]) == len(DB_FILES)


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


# --- learning_db_synced -----------------------------------------------------


def _push_result(db_file: str, status: str) -> PushResult:
    return PushResult(
        db_file=db_file, local_path=Path(f"/tmp/{db_file}"), local_size=1, status=status
    )


def test_learning_db_synced_true_when_uploaded():
    results = [_push_result(n, "uploaded") for n in DB_FILES]
    assert learning_db_synced(results) is True


def test_learning_db_synced_false_when_bid_learning_errors():
    results = [
        _push_result(n, "error") if n == "bid_learning.db" else _push_result(n, "uploaded")
        for n in DB_FILES
    ]
    assert learning_db_synced(results) is False


def test_learning_db_synced_true_when_bid_learning_missing_local():
    """No local bid_learning.db means no claim was written locally either —
    nothing was lost by not uploading it."""
    results = [
        _push_result(n, "missing_local") if n == "bid_learning.db" else _push_result(n, "uploaded")
        for n in DB_FILES
    ]
    assert learning_db_synced(results) is True


def test_learning_db_synced_true_when_bid_learning_absent_from_results():
    """No matching row at all (e.g. an empty results list) can't prove the
    claim was lost, so this must not be treated as a failure."""
    assert learning_db_synced([]) is True


def test_fetch_state_only_restricts_to_the_named_files(monkeypatch, tmp_path):
    """The approval webhook syncs just bid_learning.db.

    Pulling the rest on every Telegram tap (player_history.db alone is 20MB+)
    risks exceeding the webhook timeout, and a retried callback can buy twice.
    """
    ts = datetime(2026, 5, 8, 8, 1, 32, tzinfo=timezone.utc)
    container = _make_container(
        {name: {"props": _props(ts, 1024), "data": b"x"} for name in DB_FILES}
    )
    _patch_container(monkeypatch, container)

    results = fetch_state("conn", "rehoboam-data", tmp_path, only={"bid_learning.db"})

    assert [r.db_file for r in results] == ["bid_learning.db"]
    assert (tmp_path / "bid_learning.db").exists()
    for other in (n for n in DB_FILES if n != "bid_learning.db"):
        assert not (tmp_path / other).exists()


def test_fetch_state_only_none_is_unchanged(monkeypatch, tmp_path):
    ts = datetime(2026, 5, 8, 8, 1, 32, tzinfo=timezone.utc)
    container = _make_container(
        {name: {"props": _props(ts, 1024), "data": b"x"} for name in DB_FILES}
    )
    _patch_container(monkeypatch, container)

    results = fetch_state("conn", "rehoboam-data", tmp_path, only=None)

    assert [r.db_file for r in results] == list(DB_FILES)


def test_push_state_only_restricts_to_the_named_files(monkeypatch, tmp_path):
    for n in DB_FILES:
        (tmp_path / n).write_bytes(b"local-" + n.encode())
    container = _make_container({n: {} for n in DB_FILES})
    _patch_container(monkeypatch, container)

    results = push_state("conn", "rehoboam-data", tmp_path, only={"bid_learning.db"})

    assert [r.db_file for r in results] == ["bid_learning.db"]
    assert len([r for r in results if r.status == "uploaded"]) == 1


def test_push_state_only_none_is_unchanged(monkeypatch, tmp_path):
    for n in DB_FILES:
        (tmp_path / n).write_bytes(b"local-" + n.encode())
    container = _make_container({n: {} for n in DB_FILES})
    _patch_container(monkeypatch, container)

    results = push_state("conn", "rehoboam-data", tmp_path, only=None)

    assert [r.db_file for r in results] == list(DB_FILES)
    assert len([r for r in results if r.status == "uploaded"]) == len(DB_FILES)


def test_push_state_only_ignores_drift_in_files_it_is_not_pushing(monkeypatch, tmp_path):
    """A stale sidecar entry for a file we aren't pushing must not block us.

    The approval webhook syncs only bid_learning.db, but on a warm Azure
    instance the sidecar still carries entries the timer wrote for the other
    files. Without scoping the freshness check, an unrelated blob moving would
    raise and the owner would be told "NOT SAVED - do not tap again" after a
    purchase that saved perfectly well.
    """
    old = datetime(2026, 5, 8, 8, 0, 0, tzinfo=timezone.utc)
    new = datetime(2026, 5, 8, 9, 0, 0, tzinfo=timezone.utc)
    # Sidecar says every file was fetched at `old`; every blob has since moved.
    (tmp_path / FETCH_SIDECAR).write_text(json.dumps({n: old.isoformat() for n in DB_FILES}))
    for n in DB_FILES:
        (tmp_path / n).write_bytes(b"x")
    container = _make_container({n: {"props": _props(new, 10)} for n in DB_FILES})
    _patch_container(monkeypatch, container)

    # Unscoped: the drift is detected and the push is refused.
    with pytest.raises(BlobChangedSinceFetch):
        push_state("conn", "rehoboam-data", tmp_path)

    # Scoped to a file whose blob also moved: still refused (drift is real).
    with pytest.raises(BlobChangedSinceFetch):
        push_state("conn", "rehoboam-data", tmp_path, only={"bid_learning.db"})

    # Scoped to a file with no drift: the other files' drift is irrelevant.
    (tmp_path / FETCH_SIDECAR).write_text(
        json.dumps({**{n: old.isoformat() for n in DB_FILES}, "bid_learning.db": new.isoformat()})
    )
    results = push_state("conn", "rehoboam-data", tmp_path, only={"bid_learning.db"})
    assert [r.db_file for r in results] == ["bid_learning.db"]


class TestAFailedDownloadCannotDestroyTheBlob:
    """REH-94: the chain that silently replaced the learning DB with an empty one.

    fetch_state swallows a per-file download failure. On a cold container there
    is then no local file, and BidLearner's CREATE TABLE IF NOT EXISTS turns
    that into a valid EMPTY database. push_state uploaded it and reported
    "uploaded" — success — having destroyed every learning table.
    """

    def _container_where_one_download_fails(self, failing: str):
        ts = datetime(2026, 8, 24, 8, 0, 0, tzinfo=timezone.utc)
        payload = b"real-data" * 100
        spec = {n: {"props": _props(ts, len(payload)), "data": payload} for n in DB_FILES}
        container = _make_container(spec)
        original = container.get_blob_client

        def _client(name):
            client = original(name)
            if name == failing:
                client.download_blob.side_effect = OSError("transient azure error")
            return client

        container.get_blob_client = MagicMock(side_effect=_client)
        return container

    def test_a_failed_download_is_recorded_in_the_sidecar(self, monkeypatch, tmp_path):
        container = self._container_where_one_download_fails("bid_learning.db")
        _patch_container(monkeypatch, container)

        fetch_state("conn", "rehoboam-data", tmp_path, backup=False)

        recorded = json.loads((tmp_path / FETCH_SIDECAR).read_text())
        assert recorded[FAILED_FETCH_KEY] == ["bid_learning.db"]

    def test_push_refuses_to_upload_a_file_it_failed_to_download(self, monkeypatch, tmp_path):
        container = self._container_where_one_download_fails("bid_learning.db")
        _patch_container(monkeypatch, container)
        fetch_state("conn", "rehoboam-data", tmp_path, backup=False)

        # What BidLearner does next: a missing file becomes a valid empty one.
        (tmp_path / "bid_learning.db").write_bytes(b"empty-db")

        results = push_state("conn", "rehoboam-data", tmp_path)
        by_file = {r.db_file: r.status for r in results}
        assert by_file["bid_learning.db"] == "skipped_fetch_failed"
        assert by_file["value_tracking.db"] == "uploaded"

    def test_a_later_successful_download_clears_the_mark(self, monkeypatch, tmp_path):
        container = self._container_where_one_download_fails("bid_learning.db")
        _patch_container(monkeypatch, container)
        fetch_state("conn", "rehoboam-data", tmp_path, backup=False)

        ts = datetime(2026, 8, 24, 9, 0, 0, tzinfo=timezone.utc)
        payload = b"real-data" * 100
        healthy = _make_container(
            {n: {"props": _props(ts, len(payload)), "data": payload} for n in DB_FILES}
        )
        _patch_container(monkeypatch, healthy)
        fetch_state("conn", "rehoboam-data", tmp_path, backup=False)

        recorded = json.loads((tmp_path / FETCH_SIDECAR).read_text())
        assert FAILED_FETCH_KEY not in recorded

    def test_force_still_allows_a_deliberate_push(self, monkeypatch, tmp_path):
        """The escape hatch stays open for an operator who means it."""
        sidecar = {FAILED_FETCH_KEY: ["bid_learning.db"]}
        (tmp_path / FETCH_SIDECAR).write_text(json.dumps(sidecar))
        for n in DB_FILES:
            (tmp_path / n).write_bytes(b"x" * 4096)
        ts = datetime(2026, 8, 24, 8, 0, 0, tzinfo=timezone.utc)
        _patch_container(
            monkeypatch, _make_container({n: {"props": _props(ts, 4096)} for n in DB_FILES})
        )

        results = push_state("conn", "rehoboam-data", tmp_path, force=True)
        assert {r.db_file: r.status for r in results}["bid_learning.db"] == "uploaded"


class TestTheShrinkGuard:
    """Defence in depth: every other route to uploading a truncated file."""

    def _blob_of(self, size):
        ts = datetime(2026, 8, 24, 8, 0, 0, tzinfo=timezone.utc)
        return _make_container({n: {"props": _props(ts, size)} for n in DB_FILES})

    def test_a_drastically_smaller_local_file_is_refused(self, monkeypatch, tmp_path):
        for n in DB_FILES:
            (tmp_path / n).write_bytes(b"x" * 100)
        _patch_container(monkeypatch, self._blob_of(1_000_000))

        results = push_state("conn", "rehoboam-data", tmp_path)
        assert all(r.status == "skipped_shrunk" for r in results)

    def test_normal_growth_and_minor_shrinkage_still_upload(self, monkeypatch, tmp_path):
        for n in DB_FILES:
            (tmp_path / n).write_bytes(b"x" * 900)
        _patch_container(monkeypatch, self._blob_of(1000))

        results = push_state("conn", "rehoboam-data", tmp_path)
        assert all(r.status == "uploaded" for r in results)

    def test_a_blob_that_does_not_exist_yet_is_not_a_shrink(self, monkeypatch, tmp_path):
        for n in DB_FILES:
            (tmp_path / n).write_bytes(b"x" * 10)
        _patch_container(monkeypatch, _make_container({n: {} for n in DB_FILES}))

        results = push_state("conn", "rehoboam-data", tmp_path)
        assert all(r.status == "uploaded" for r in results)
