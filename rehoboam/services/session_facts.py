"""What one run knew about itself, as data (spec §3).

Pure: the trading session fills this in as it goes and hands it to the
store and to the integrity rules at the end. Every field is optional except
the identity, so a run that dies early still leaves a row that says so.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class IntegrityFailure:
    rule: str  # I1 … I7
    detail: str


@dataclass
class SessionFacts:
    session_id: str
    app: str  # function | cli | external
    mode: str  # full | lineup_only | ingest | export
    started_at: float
    dry_run: bool = False
    duration_s: float = 0.0
    phase: str | None = None
    next_kickoff: float | None = None  # epoch seconds
    next_kickoff_source: str | None = None  # schedule | myeleven | none
    squad_gk: int | None = None
    squad_def: int | None = None
    squad_mid: int | None = None
    squad_fw: int | None = None
    fieldable_count: int | None = None
    legal_formation: str | None = None
    budget: int | None = None
    sellable_value: int | None = None
    open_offers_total: int | None = None
    open_offers_manual: int | None = None
    cost_basis_missing: int | None = None
    predictions_written: int | None = None
    lineup_result: str | None = None  # set | dry_run | illegal | failed | skipped
    errors: int = 0
    error_text: str = ""
    extra: dict[str, Any] | None = None

    def as_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["dry_run"] = 1 if self.dry_run else 0
        return row

    @property
    def squad_size(self) -> int | None:
        parts = [self.squad_gk, self.squad_def, self.squad_mid, self.squad_fw]
        return None if any(p is None for p in parts) else sum(parts)
