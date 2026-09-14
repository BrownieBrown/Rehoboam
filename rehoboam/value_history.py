"""Per-player API response cache on the store (rehoboam.api_cache).

One row per (kind, player, league, key), payload as jsonb. Market-value
history is kind ``mv`` keyed by the timeframe in days; performance is kind
``performance`` with an empty key — the same mapping ``store.import_sqlite``
used to fold the SQLite-era files in, so imported rows and live rows are
the same rows. Read one row at a time: the point of the table is that a
session never downloads a 27 MB cache file to look up a dozen players.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from psycopg.types.json import Jsonb


class ValueHistoryCache:
    """Caches per-player API responses to minimize Kickbase calls."""

    def __init__(self, dsn: str | None = None):
        self.dsn = dsn

    def connection(self):
        from rehoboam.store import connect

        return connect(self.dsn)

    def _get(self, kind: str, player_id: str, league_id: str, key: str, cutoff: float):
        with self.connection() as conn:
            row = conn.execute(
                "SELECT payload FROM rehoboam.api_cache "
                "WHERE kind = %s AND player_id = %s AND league_id = %s AND key = %s "
                "AND fetched_at >= %s",
                (kind, str(player_id), str(league_id), key, cutoff),
            ).fetchone()
        return row["payload"] if row else None

    def _put(self, kind: str, player_id: str, league_id: str, key: str, data: dict[str, Any]):
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO rehoboam.api_cache "
                "(kind, player_id, league_id, key, fetched_at, payload) "
                "VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (kind, player_id, league_id, key) DO UPDATE SET "
                "fetched_at = excluded.fetched_at, payload = excluded.payload",
                (
                    kind,
                    str(player_id),
                    str(league_id),
                    key,
                    datetime.now().timestamp(),
                    Jsonb(data),
                ),
            )

    def get_cached_history(
        self,
        player_id: str,
        league_id: str,
        timeframe: int = 30,
        max_age_hours: int = 24,
    ) -> dict[str, Any] | None:
        cutoff = (datetime.now() - timedelta(hours=max_age_hours)).timestamp()
        return self._get("mv", player_id, league_id, str(timeframe), cutoff)

    def cache_history(self, player_id: str, league_id: str, timeframe: int, data: dict[str, Any]):
        self._put("mv", player_id, league_id, str(timeframe), data)

    def get_trend_analysis(
        self, history_data: dict[str, Any], current_market_value: int = 0
    ) -> dict[str, Any]:
        """DEPRECATED: use TrendService.analyze(); kept for remaining callers."""
        from .services.trend_service import TrendService

        return TrendService.analyze(history_data, current_market_value).to_dict()

    def get_cached_performance(
        self, player_id: str, league_id: str, max_age_hours: int = 6
    ) -> dict[str, Any] | None:
        cutoff = (datetime.now() - timedelta(hours=max_age_hours)).timestamp()
        return self._get("performance", player_id, league_id, "", cutoff)

    def cache_performance(self, player_id: str, league_id: str, data: dict[str, Any]):
        self._put("performance", player_id, league_id, "", data)

    def cleanup_old_cache(self, days_to_keep: int = 7) -> int:
        cutoff = (datetime.now() - timedelta(days=days_to_keep)).timestamp()
        with self.connection() as conn:
            cur = conn.execute("DELETE FROM rehoboam.api_cache WHERE fetched_at < %s", (cutoff,))
            return cur.rowcount
