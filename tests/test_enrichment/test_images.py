"""sync_images: our own copies of player photos and club crests (real store).

A fake `client` stands in for the network (CDN download + Storage upload) --
`sync_images` must never touch the real Kickbase CDN or Supabase Storage in a
test. The store side is the real, migrated PostgreSQL `store_dsn` gives every
test in this suite.
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image

from rehoboam.enrichment.images import (
    crest_content_type,
    player_dest_path,
    sync_images,
    team_dest_path,
)
from rehoboam.enrichment.ingest import IngestBudget
from rehoboam.store import connect
from rehoboam.store.corpus_store import CorpusStore
from rehoboam.store.league_store import LeagueStore


def _tiny_png() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (200, 200), color=(10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


TINY_PNG = _tiny_png()


def _budget(*, now: float = 1.0, exhausted: bool = False) -> IngestBudget:
    """A budget that is never exhausted by default -- every existing test in
    this file wants that. `exhausted=True` gives one that already is, for the
    tests that check `sync_images` stops instead of spending it."""
    deadline = now - 1 if exhausted else now + 1_000
    return IngestBudget(deadline=deadline, max_requests=1_000, now=lambda: now)


class FakeClient:
    """Stands in for both the CDN download and the Storage upload."""

    def __init__(self, *, fail_sources=(), payload: bytes | None = None):
        self.fetched: list[str] = []
        self.uploaded: list[tuple[str, str, bytes]] = []
        self._fail = set(fail_sources)
        self._payload = payload if payload is not None else TINY_PNG

    def fetch(self, source: str) -> bytes:
        self.fetched.append(source)
        if source in self._fail:
            raise RuntimeError("cdn unreachable")
        return self._payload

    def upload(self, dest_path: str, data: bytes, *, content_type: str) -> None:
        self.uploaded.append((dest_path, content_type, data))


def _player(dsn, player_id, *, image_source=None, image_path=None):
    CorpusStore(dsn=dsn).upsert_players([{"player_id": player_id, "position": "Forward"}])
    with connect(dsn) as conn:
        conn.execute(
            "UPDATE rehoboam.player_universe SET image_source = %s, image_path = %s "
            "WHERE player_id = %s",
            (image_source, image_path, player_id),
        )


def _team(dsn, team_id, *, crest_source=None, crest_path=None):
    LeagueStore(dsn=dsn).upsert_teams(
        [
            {
                "team_id": team_id,
                "name": team_id,
                "short_name": None,
                "updated_at": 1.0,
                "crest_source": crest_source,
            }
        ]
    )
    if crest_path is not None:
        with connect(dsn) as conn:
            conn.execute(
                "UPDATE rehoboam.teams SET crest_path = %s WHERE team_id = %s",
                (crest_path, team_id),
            )


def _read_player(dsn, player_id) -> dict:
    with connect(dsn) as conn:
        return conn.execute(
            "SELECT image_source, image_path FROM rehoboam.player_universe WHERE player_id = %s",
            (player_id,),
        ).fetchone()


def _read_team(dsn, team_id) -> dict:
    with connect(dsn) as conn:
        return conn.execute(
            "SELECT crest_source, crest_path FROM rehoboam.teams WHERE team_id = %s",
            (team_id,),
        ).fetchone()


def test_an_unchanged_row_is_skipped_and_nothing_is_downloaded(store_dsn):
    source = "content/file/abc.png"
    _player(store_dsn, "p1", image_source=source, image_path=player_dest_path("p1", source))
    client = FakeClient()

    out = sync_images(CorpusStore(dsn=store_dsn), client=client, limit=10, budget=_budget())

    assert out == {"players": 0, "teams": 0, "skipped": 1, "failed": 0}
    assert client.fetched == []
    assert client.uploaded == []


def test_a_new_source_is_downloaded_resized_uploaded_and_path_written(store_dsn):
    source = "content/file/new.png"
    _player(store_dsn, "p1", image_source=source, image_path=None)
    client = FakeClient()

    out = sync_images(CorpusStore(dsn=store_dsn), client=client, limit=10, budget=_budget())

    assert out == {"players": 1, "teams": 0, "skipped": 0, "failed": 0}
    assert client.fetched == [source]
    dest = player_dest_path("p1", source)
    assert len(client.uploaded) == 1
    uploaded_path, content_type, data = client.uploaded[0]
    assert uploaded_path == dest
    assert content_type == "image/png"
    # Resized -- the fixture is 200x200, sips -Z 160 caps the longest side at 160.
    with Image.open(BytesIO(data)) as img:
        assert max(img.size) <= 160
    assert _read_player(store_dsn, "p1")["image_path"] == dest


def test_a_changed_source_is_resynced_even_with_a_path_already_set(store_dsn):
    """A stored `image_path` only means "synced against SOME earlier source" --
    a source change must still trigger a re-sync, not be mistaken for current."""
    new_source = "content/file/changed.png"
    _player(store_dsn, "p1", image_source=new_source, image_path="players/p1-stale.png")
    client = FakeClient()

    out = sync_images(CorpusStore(dsn=store_dsn), client=client, limit=10, budget=_budget())

    assert out["players"] == 1
    assert client.fetched == [new_source]
    assert _read_player(store_dsn, "p1")["image_path"] == player_dest_path("p1", new_source)


def test_a_download_failure_increments_failed_and_leaves_path_null(store_dsn):
    source = "content/file/broken.png"
    _player(store_dsn, "p1", image_source=source, image_path=None)
    client = FakeClient(fail_sources={source})

    out = sync_images(CorpusStore(dsn=store_dsn), client=client, limit=10, budget=_budget())

    assert out == {"players": 0, "teams": 0, "skipped": 0, "failed": 1}
    assert _read_player(store_dsn, "p1")["image_path"] is None


def test_with_no_storage_key_returns_zeros_and_does_nothing(store_dsn):
    class PoisonStore:
        def connection(self):
            raise AssertionError("must not touch the store when there's no client")

    out = sync_images(PoisonStore(), client=None, limit=10, budget=_budget())

    assert out == {"players": 0, "teams": 0, "skipped": 0, "failed": 0}


def test_the_limit_is_respected(store_dsn):
    for i in range(5):
        _player(store_dsn, f"p{i}", image_source=f"content/file/{i}.png", image_path=None)
    client = FakeClient()

    out = sync_images(CorpusStore(dsn=store_dsn), client=client, limit=2, budget=_budget())

    assert out["players"] == 2
    assert len(client.fetched) == 2
    synced = sum(1 for i in range(5) if _read_player(store_dsn, f"p{i}")["image_path"] is not None)
    assert synced == 2


def test_a_changed_row_past_a_fixed_low_id_window_is_still_reached(store_dsn):
    """Regression: once every row already has SOME path, `ORDER BY (path IS
    NULL) DESC, id` collapses to plain id order. A SQL `LIMIT` there would
    return the exact same low-id rows on every run forever, starving a
    high-id row whose source later changes -- `sync_images` must scan past
    already-synced rows in Python instead, so a small `limit` still reaches
    real work anywhere in the table."""
    for i in range(10):
        source = f"content/file/{i:02d}.png"
        _player(
            store_dsn,
            f"p{i:02d}",
            image_source=source,
            image_path=player_dest_path(f"p{i:02d}", source),
        )
    changed_source = "content/file/changed.png"
    _player(
        store_dsn,
        "zz-high-id",
        image_source=changed_source,
        image_path="players/zz-high-id-stale.png",
    )
    client = FakeClient()

    out = sync_images(CorpusStore(dsn=store_dsn), client=client, limit=2, budget=_budget())

    assert out["players"] == 1
    assert client.fetched == [changed_source]
    assert _read_player(store_dsn, "zz-high-id")["image_path"] == player_dest_path(
        "zz-high-id", changed_source
    )


def test_team_crests_sync_without_resizing(store_dsn):
    source = "content/file/crest.svg"
    _team(store_dsn, "t1", crest_source=source, crest_path=None)
    svg_bytes = b"<svg>not a photo, must not be resized</svg>"
    client = FakeClient(payload=svg_bytes)

    out = sync_images(CorpusStore(dsn=store_dsn), client=client, limit=10, budget=_budget())

    assert out == {"players": 0, "teams": 1, "skipped": 0, "failed": 0}
    dest = team_dest_path("t1", source)
    assert client.uploaded == [(dest, "image/svg+xml", svg_bytes)]
    assert _read_team(store_dsn, "t1")["crest_path"] == dest


def test_an_unchanged_crest_is_skipped(store_dsn):
    source = "content/file/crest.svg"
    _team(store_dsn, "t1", crest_source=source, crest_path=team_dest_path("t1", source))
    client = FakeClient()

    out = sync_images(CorpusStore(dsn=store_dsn), client=client, limit=10, budget=_budget())

    assert out == {"players": 0, "teams": 0, "skipped": 1, "failed": 0}
    assert client.fetched == []


def test_crest_content_type_is_derived_from_the_source_extension():
    """Finding #6 (Minor): the upload's content type must track the same
    extension `team_dest_path` puts in the destination key, not a hardcoded
    SVG assumption -- Kickbase does not only serve SVG crests."""
    assert crest_content_type("content/file/crest.svg") == "image/svg+xml"
    assert crest_content_type("content/file/crest.png") == "image/png"
    assert crest_content_type("content/file/crest.PNG") == "image/png"
    assert crest_content_type("content/file/crest.unknown") == "application/octet-stream"


def test_a_non_svg_crest_uploads_with_a_matching_content_type(store_dsn):
    source = "content/file/crest.png"
    _team(store_dsn, "t1", crest_source=source, crest_path=None)
    client = FakeClient(payload=TINY_PNG)

    out = sync_images(CorpusStore(dsn=store_dsn), client=client, limit=10, budget=_budget())

    assert out == {"players": 0, "teams": 1, "skipped": 0, "failed": 0}
    dest = team_dest_path("t1", source)
    assert client.uploaded == [(dest, "image/png", TINY_PNG)]


def test_an_already_exhausted_budget_skips_the_whole_sync(store_dsn):
    """Finding #1 (Important): a run that already burned its wall-clock
    deadline before reaching image sync must not still make up to `limit`
    slow CDN-fetch + resize + Storage-upload round trips -- checked before
    starting at all, the same as the Kickbase per-player loop checks before
    its first request."""
    _player(store_dsn, "p1", image_source="content/file/new.png", image_path=None)
    client = FakeClient()

    out = sync_images(
        CorpusStore(dsn=store_dsn), client=client, limit=10, budget=_budget(exhausted=True)
    )

    assert out == {"players": 0, "teams": 0, "skipped": 0, "failed": 0}
    assert client.fetched == []
    assert _read_player(store_dsn, "p1")["image_path"] is None


def test_the_budget_running_out_mid_batch_stops_the_rest_of_the_rows(store_dsn):
    """Finding #1 (Important): unlike `limit`, the deadline can be crossed
    *during* the batch -- each row's fetch/resize/upload is real network
    time, not free. `sync_images` must re-check before every row, not only
    once at the top, so a run does not blow through the host's hard timeout
    doing the 41st, 50th, 60th round trip."""
    for i in range(3):
        _player(store_dsn, f"p{i}", image_source=f"content/file/{i}.png", image_path=None)
    clock = {"t": 0.0}

    def now() -> float:
        return clock["t"]

    client = FakeClient()
    real_fetch = client.fetch

    def fetch_and_tick(source: str) -> bytes:
        data = real_fetch(source)
        clock["t"] += 10.0  # each round trip burns real wall-clock time
        return data

    client.fetch = fetch_and_tick
    budget = IngestBudget(deadline=5.0, max_requests=1_000, now=now)

    out = sync_images(CorpusStore(dsn=store_dsn), client=client, limit=10, budget=budget)

    # Only the first row's round trip fit before the deadline (0.0 < 5.0);
    # the second row's pre-row check (10.0 >= 5.0) stops the batch there.
    assert out["players"] == 1
    assert len(client.fetched) == 1
    synced = sum(1 for i in range(3) if _read_player(store_dsn, f"p{i}")["image_path"] is not None)
    assert synced == 1


class _FailingUpdateConnection:
    """Wraps one real connection: the UPDATE naming `fail_player_id` raises;
    every other statement -- the read queries, every other row's UPDATE --
    reaches the real, migrated database untouched."""

    def __init__(self, conn, fail_player_id: str):
        self._conn = conn
        self._fail_player_id = fail_player_id

    def execute(self, sql, params=None):
        if params and self._fail_player_id in params and sql.strip().upper().startswith("UPDATE"):
            raise RuntimeError("simulated pooler drop")
        return self._conn.execute(sql, params)


class _FailingUpdateConnCtx:
    def __init__(self, inner_ctx, fail_player_id: str):
        self._inner_ctx = inner_ctx
        self._fail_player_id = fail_player_id

    def __enter__(self):
        conn = self._inner_ctx.__enter__()
        return _FailingUpdateConnection(conn, self._fail_player_id)

    def __exit__(self, *exc):
        return self._inner_ctx.__exit__(*exc)


class _FailingUpdateStore:
    """Wraps a real `CorpusStore`; `.connection()` returns a fresh connection
    each call (never pinned), matching how `sync_images` is called here
    (outside a `.session()`), so each row's write really is its own
    transaction -- exactly what finding #2 requires."""

    def __init__(self, inner, *, fail_player_id: str):
        self._inner = inner
        self._fail_player_id = fail_player_id

    def connection(self):
        return _FailingUpdateConnCtx(self._inner.connection(), self._fail_player_id)


def test_one_rows_failed_update_does_not_lose_another_rows_work(store_dsn):
    """Finding #2 (Important): the whole sync used to run inside one shared
    transaction on the :6543 pooler -- held open across every HTTP call, and
    once any `conn.execute` failed, psycopg marked it aborted, so every row
    after also counted as failed and every UPDATE already made rolled back.
    Committing per row means row 1 failing is isolated to its own
    transaction and row 2 still persists."""
    _player(store_dsn, "p1", image_source="content/file/1.png", image_path=None)
    _player(store_dsn, "p2", image_source="content/file/2.png", image_path=None)
    client = FakeClient()
    store = _FailingUpdateStore(CorpusStore(dsn=store_dsn), fail_player_id="p1")

    out = sync_images(store, client=client, limit=10, budget=_budget())

    assert out["failed"] == 1
    assert out["players"] == 1
    assert _read_player(store_dsn, "p1")["image_path"] is None
    assert _read_player(store_dsn, "p2")["image_path"] == player_dest_path(
        "p2", "content/file/2.png"
    )
