"""Pull player photos and club crests into our own storage (spec 2026-09-18
player panel, task 10). The dashboard hotlinks nobody else's CDN -- it
serves these from a public Supabase Storage bucket.

Mirrors `enrichment/mv_forecast.run_mv_forecast`'s error discipline: the
whole step never raises. A per-row failure only increments `failed` and the
run that called it stands regardless.

`player_universe.image_source` and `teams.crest_source` are Kickbase's own
CDN-relative paths (`content/file/<hash>.png` / `.svg`), already written by
`record_status_daily` / `upsert_teams`. There is no extra "last synced
source" column: the destination path we upload to is *derived from* the
source path (`players/<id>-<digest>.<ext>`), so comparing the stored
`*_path` against what we would compute for the CURRENT `*_source` is enough
to tell, with no extra state, whether a row is already synced. A changed
source produces a different destination path, so it's re-synced; an
unchanged one produces the same path, so it's skipped.
"""

from __future__ import annotations

import hashlib
import logging

logger = logging.getLogger(__name__)

CDN_BASE = "https://kickbase.b-cdn.net/"

#: Not secret -- same value web/.env.example's NEXT_PUBLIC_SUPABASE_URL uses.
#: `Settings.supabase_url` carries the live value; `client_from_settings`
#: builds the Storage endpoint from it. Only the write credential
#: (SUPABASE_STORAGE_KEY) is a secret worth its own field.
STORAGE_BUCKET = "kickbase"

# sips -Z 160 measured 2026-09-18: 15-18 KB with no visible loss at the sizes
# the dashboard shows (66px panel, 34px list, 30px table row).
PHOTO_MAX_PX = 160


def _digest(source: str) -> str:
    # Not a security use -- just a short, stable filename component that
    # changes when the source path changes. sha256 over sha1/md5 purely to
    # keep static scanners quiet.
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]


def _dest_path(prefix: str, id_: str, source: str, *, ext: str) -> str:
    return f"{prefix}/{id_}-{_digest(source)}.{ext}"


def _source_ext(source: str, *, default: str) -> str:
    return source.rsplit(".", 1)[-1].lower() if "." in source else default


def player_dest_path(player_id: str, source: str) -> str:
    # `_resize_photo` always re-encodes to PNG regardless of the source's own
    # extension, so the key's extension must say PNG too -- not whatever
    # Kickbase happened to serve it as.
    return _dest_path("players", player_id, source, ext="png")


def team_dest_path(team_id: str, source: str) -> str:
    # Crests pass through unmodified, so the key's extension should match
    # what Kickbase actually served.
    return _dest_path("teams", team_id, source, ext=_source_ext(source, default="svg"))


def _needs_sync(source: str | None, path: str | None, dest: str) -> bool:
    """A row needs syncing when it has a source at all and the path on file
    doesn't already match what that source would produce."""
    return bool(source) and path != dest


class SupabaseImageClient:
    """The real network collaborator: unauthenticated GET from Kickbase's CDN,
    authenticated upload to the Supabase Storage REST API. Constructing one
    does no I/O -- only `fetch`/`upload` touch the network."""

    def __init__(self, storage_key: str, *, base_url: str, session=None, timeout: float = 10.0):
        self._key = storage_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        if session is not None:
            self._session = session
        else:
            import requests

            self._session = requests.Session()

    def fetch(self, source: str) -> bytes:
        resp = self._session.get(CDN_BASE + source, timeout=self._timeout)
        resp.raise_for_status()
        return bytes(resp.content)

    def upload(self, dest_path: str, data: bytes, *, content_type: str) -> None:
        url = f"{self._base_url}/storage/v1/object/{STORAGE_BUCKET}/{dest_path}"
        resp = self._session.post(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {self._key}",
                "apikey": self._key,
                "Content-Type": content_type,
                "x-upsert": "true",
            },
            timeout=self._timeout,
        )
        resp.raise_for_status()


def client_from_settings(settings) -> SupabaseImageClient | None:
    """None when SUPABASE_STORAGE_KEY isn't set yet -- `sync_images` no-ops on
    a None client, which is exactly what lets this merge and deploy before
    the owner creates the bucket and adds the key. `supabase_url` isn't a
    secret (same value web/.env.example's NEXT_PUBLIC_SUPABASE_URL uses), so
    its absence doesn't gate this the way the key does -- Settings always
    carries a default."""
    key = getattr(settings, "supabase_storage_key", "")
    if not key:
        return None
    return SupabaseImageClient(key, base_url=settings.supabase_url)


def _resize_photo(data: bytes) -> tuple[bytes, str]:
    from io import BytesIO

    from PIL import Image

    with Image.open(BytesIO(data)) as img:
        img.thumbnail((PHOTO_MAX_PX, PHOTO_MAX_PX))
        out = BytesIO()
        img.convert("RGBA").save(out, format="PNG", optimize=True)
    return out.getvalue(), "image/png"


def _player_candidates(conn) -> list[dict]:
    """Every row with a source, never-synced first. No SQL `LIMIT`: the tables
    are small (~600 players), and a fixed low-id window here would mean that
    once every row has SOME path (about a week in), the `ORDER BY` collapses
    to plain id order and a `LIMIT` would return the exact same rows on every
    run forever -- starving any row past that window whose source later
    changes. `sync_images` caps the *work*, in Python, with `remaining`."""
    rows = conn.execute(
        "SELECT player_id, image_source, image_path FROM rehoboam.player_universe "
        "WHERE image_source IS NOT NULL "
        "ORDER BY (image_path IS NULL) DESC, player_id"
    ).fetchall()
    return [dict(r) for r in rows]


def _team_candidates(conn) -> list[dict]:
    """Same reasoning as `_player_candidates` -- teams too, though the risk is
    smaller with only ~20 rows."""
    rows = conn.execute(
        "SELECT team_id, crest_source, crest_path FROM rehoboam.teams "
        "WHERE crest_source IS NOT NULL "
        "ORDER BY (crest_path IS NULL) DESC, team_id"
    ).fetchall()
    return [dict(r) for r in rows]


def sync_images(store, *, client, limit: int, now: float) -> dict[str, int]:
    """Sync up to `limit` player photos and club crests, stalest (never
    synced) first. Never raises -- a failure is counted in `failed` and
    logged, and everything else this call already did stands.

    `now` is accepted for symmetry with the other ingestion steps
    (`run_mv_forecast`) and to leave room for a future staleness re-check;
    nothing currently reads it beyond that.
    """
    result = {"players": 0, "teams": 0, "skipped": 0, "failed": 0}
    if client is None:
        logger.info("sync_images: SUPABASE_STORAGE_KEY not set, skipping")
        return result
    del now  # unused today; see docstring
    try:
        remaining = max(int(limit), 0)
        with store.connection() as conn:
            for row in _player_candidates(conn):
                if remaining <= 0:
                    break
                dest = player_dest_path(row["player_id"], row["image_source"])
                if not _needs_sync(row["image_source"], row["image_path"], dest):
                    result["skipped"] += 1
                    continue
                remaining -= 1
                try:
                    data = client.fetch(row["image_source"])
                    data, content_type = _resize_photo(data)
                    client.upload(dest, data, content_type=content_type)
                    conn.execute(
                        "UPDATE rehoboam.player_universe SET image_path = %s "
                        "WHERE player_id = %s",
                        (dest, row["player_id"]),
                    )
                    result["players"] += 1
                except Exception as e:  # noqa: BLE001 -- one bad row must not stop the batch
                    result["failed"] += 1
                    logger.warning("image sync failed for player %s: %s", row["player_id"], e)

            for row in _team_candidates(conn):
                if remaining <= 0:
                    break
                dest = team_dest_path(row["team_id"], row["crest_source"])
                if not _needs_sync(row["crest_source"], row["crest_path"], dest):
                    result["skipped"] += 1
                    continue
                remaining -= 1
                try:
                    data = client.fetch(row["crest_source"])
                    client.upload(dest, data, content_type="image/svg+xml")
                    conn.execute(
                        "UPDATE rehoboam.teams SET crest_path = %s WHERE team_id = %s",
                        (dest, row["team_id"]),
                    )
                    result["teams"] += 1
                except Exception as e:  # noqa: BLE001 -- one bad row must not stop the batch
                    result["failed"] += 1
                    logger.warning("image sync failed for team %s: %s", row["team_id"], e)
    except Exception:  # noqa: BLE001 -- report, don't raise (mirrors run_mv_forecast)
        logger.exception("sync_images failed")
    logger.info(
        "sync_images players=%d teams=%d skipped=%d failed=%d",
        result["players"],
        result["teams"],
        result["skipped"],
        result["failed"],
    )
    return result
