"""sync_images: our own copies of player photos and club crests (real store).

A fake `client` stands in for the network (CDN download + Storage upload) --
`sync_images` must never touch the real Kickbase CDN or Supabase Storage in a
test. The store side is the real, migrated PostgreSQL `store_dsn` gives every
test in this suite.
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image

from rehoboam.enrichment.images import player_dest_path, sync_images, team_dest_path
from rehoboam.store import connect
from rehoboam.store.corpus_store import CorpusStore
from rehoboam.store.league_store import LeagueStore


def _tiny_png() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (200, 200), color=(10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


TINY_PNG = _tiny_png()


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

    out = sync_images(CorpusStore(dsn=store_dsn), client=client, limit=10, now=1.0)

    assert out == {"players": 0, "teams": 0, "skipped": 1, "failed": 0}
    assert client.fetched == []
    assert client.uploaded == []


def test_a_new_source_is_downloaded_resized_uploaded_and_path_written(store_dsn):
    source = "content/file/new.png"
    _player(store_dsn, "p1", image_source=source, image_path=None)
    client = FakeClient()

    out = sync_images(CorpusStore(dsn=store_dsn), client=client, limit=10, now=1.0)

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

    out = sync_images(CorpusStore(dsn=store_dsn), client=client, limit=10, now=1.0)

    assert out["players"] == 1
    assert client.fetched == [new_source]
    assert _read_player(store_dsn, "p1")["image_path"] == player_dest_path("p1", new_source)


def test_a_download_failure_increments_failed_and_leaves_path_null(store_dsn):
    source = "content/file/broken.png"
    _player(store_dsn, "p1", image_source=source, image_path=None)
    client = FakeClient(fail_sources={source})

    out = sync_images(CorpusStore(dsn=store_dsn), client=client, limit=10, now=1.0)

    assert out == {"players": 0, "teams": 0, "skipped": 0, "failed": 1}
    assert _read_player(store_dsn, "p1")["image_path"] is None


def test_with_no_storage_key_returns_zeros_and_does_nothing(store_dsn):
    class PoisonStore:
        def connection(self):
            raise AssertionError("must not touch the store when there's no client")

    out = sync_images(PoisonStore(), client=None, limit=10, now=1.0)

    assert out == {"players": 0, "teams": 0, "skipped": 0, "failed": 0}


def test_the_limit_is_respected(store_dsn):
    for i in range(5):
        _player(store_dsn, f"p{i}", image_source=f"content/file/{i}.png", image_path=None)
    client = FakeClient()

    out = sync_images(CorpusStore(dsn=store_dsn), client=client, limit=2, now=1.0)

    assert out["players"] == 2
    assert len(client.fetched) == 2
    synced = sum(1 for i in range(5) if _read_player(store_dsn, f"p{i}")["image_path"] is not None)
    assert synced == 2


def test_team_crests_sync_without_resizing(store_dsn):
    source = "content/file/crest.svg"
    _team(store_dsn, "t1", crest_source=source, crest_path=None)
    svg_bytes = b"<svg>not a photo, must not be resized</svg>"
    client = FakeClient(payload=svg_bytes)

    out = sync_images(CorpusStore(dsn=store_dsn), client=client, limit=10, now=1.0)

    assert out == {"players": 0, "teams": 1, "skipped": 0, "failed": 0}
    dest = team_dest_path("t1", source)
    assert client.uploaded == [(dest, "image/svg+xml", svg_bytes)]
    assert _read_team(store_dsn, "t1")["crest_path"] == dest


def test_an_unchanged_crest_is_skipped(store_dsn):
    source = "content/file/crest.svg"
    _team(store_dsn, "t1", crest_source=source, crest_path=team_dest_path("t1", source))
    client = FakeClient()

    out = sync_images(CorpusStore(dsn=store_dsn), client=client, limit=10, now=1.0)

    assert out == {"players": 0, "teams": 0, "skipped": 1, "failed": 0}
    assert client.fetched == []
