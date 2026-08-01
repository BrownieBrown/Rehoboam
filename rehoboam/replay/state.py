"""Squad and budget state through a replayed season."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from rehoboam.enrichment.corpus import TrainingCorpus

# One assignment batch lands on a single timestamp, within a second or two of
# league creation. The tolerance must stay tight: our league drew a squad on
# 2025-08-07 20:40:08 and then re-drew on 2025-08-08 14:05:48, and only the
# later batch is real. A day-wide window silently merges both into a 25-player
# phantom squad, so this stays in the minutes, not hours.
ASSIGNMENT_WINDOW_SECONDS = 300.0


@dataclass(frozen=True)
class ReplayPlayer:
    """A squad member. ``id`` and ``position`` satisfy ``select_best_eleven``."""

    id: str
    position: str
    team_id: str | None = None


@dataclass
class ReplayState:
    """Mutable squad + budget. Budget may go negative between kickoffs."""

    budget: int
    squad: dict[str, ReplayPlayer] = field(default_factory=dict)

    @property
    def squad_size(self) -> int:
        return len(self.squad)

    @property
    def player_ids(self) -> list[str]:
        return list(self.squad)

    @property
    def players(self) -> list[ReplayPlayer]:
        return list(self.squad.values())

    def buy(self, player: ReplayPlayer, price: int) -> None:
        self.squad[player.id] = player
        self.budget -= int(price)

    def sell(self, player_id: str, proceeds: int) -> None:
        del self.squad[player_id]
        self.budget += int(proceeds)

    def team_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for p in self.squad.values():
            if p.team_id:
                counts[p.team_id] = counts.get(p.team_id, 0) + 1
        return counts


def initial_state(
    corpus: TrainingCorpus,
    *,
    manager_id: str,
    assigned_on: float,
    starting_budget: int,
) -> ReplayState:
    """The squad Kickbase randomly assigned at season start, plus chosen budget.

    Assignments are ``transfer_type = 0`` rows naming this manager. The table
    spans multiple seasons, so only rows within one day of ``assigned_on``
    count as this season's assignment.
    """
    lo = assigned_on - ASSIGNMENT_WINDOW_SECONDS
    hi = assigned_on + ASSIGNMENT_WINDOW_SECONDS
    with sqlite3.connect(corpus.db_path) as conn:
        rows = conn.execute(
            "SELECT player_id FROM player_transfers WHERE transfer_type = 0 "
            "AND counterparty_id = ? AND transfer_at >= ? AND transfer_at <= ?",
            (str(manager_id), lo, hi),
        ).fetchall()
    pids = [str(r[0]) for r in rows]
    positions = corpus.positions_for(pids)
    teams = corpus.team_ids_for(pids)
    squad = {
        pid: ReplayPlayer(id=pid, position=positions[pid], team_id=teams.get(pid))
        for pid in pids
        if pid in positions
    }
    return ReplayState(budget=int(starting_budget), squad=squad)
