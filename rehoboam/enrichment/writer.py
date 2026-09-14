"""The corpus-writer contract shared by ``sweep`` and ``ingest``.

Both ``TrainingCorpus`` (SQLite, offline) and ``CorpusStore`` (Postgres, the
store — Task 2) implement this shape. It lives in its own module rather than
in ``ingest.py`` because ``sweep.py`` needs the annotation too and ``ingest``
imports ``fetch_universe`` from ``sweep`` — putting the protocol in
``ingest.py`` would make that import circular.
"""

from __future__ import annotations

from typing import Protocol


class CorpusWriter(Protocol):
    def upsert_players(self, players: list[dict]) -> int: ...
    def ensure_players(self, player_ids: list[str]) -> int: ...
    def record_match_history(
        self, player_id: str, team_id: str | None, performance: dict
    ) -> int: ...
    def record_mv_series(self, player_id: str, history: dict) -> int: ...
    def record_player_transfers(self, player_id: str, history: dict) -> int: ...
    def mark_fetched(
        self,
        player_id: str,
        *,
        performance: bool = ...,
        mv: bool = ...,
        transfers: bool = ...,
    ) -> None: ...
    def clear_performance_fetched(self, player_ids: list[str] | None = None) -> int: ...
    def players_needing_fetch(self, kind: str) -> list[str]: ...
    def players_missing_position(self, player_ids: list[str]) -> list[str]: ...
    def positions_for(self, player_ids: list[str]) -> dict[str, str]: ...
