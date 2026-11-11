"""The best legal eleven of a squad, by expected points (spec v3 §5.3 C1).

Kickbase's ten formations (`formation.LEGAL_FORMATIONS`, from `/v4/config`). With
the counts fixed, the best eleven takes the top players by EP at each position,
so trying every formation is exact. An empty slot costs the -100 Kickbase
charges, so a squad that cannot field eleven is strongly worse, never infeasible.
"""

from __future__ import annotations

from collections.abc import Iterable

from rehoboam.desk.model import POSITIONS, Player
from rehoboam.formation import LEGAL_FORMATIONS

EMPTY_SLOT_POINTS = -100.0
ELEVEN = 11


def best_eleven(players: Iterable[Player]) -> tuple[float, tuple[Player, ...]]:
    """(EP of the best legal eleven including empty-slot penalties, the eleven)."""
    by_position: dict[str, list[Player]] = {pos: [] for pos in POSITIONS}
    for p in players:
        if p.position in by_position:
            by_position[p.position].append(p)
    for group in by_position.values():
        group.sort(key=lambda p: (-p.ep_next, p.player_id))  # ties: by id, never by input order

    best: tuple[float, tuple[Player, ...]] | None = None
    for defenders, midfielders, forwards in sorted(LEGAL_FORMATIONS):
        need = dict(zip(POSITIONS, (1, defenders, midfielders, forwards), strict=True))
        eleven = tuple(p for pos in POSITIONS for p in by_position[pos][: need[pos]])
        score = sum(p.ep_next for p in eleven) + EMPTY_SLOT_POINTS * (ELEVEN - len(eleven))
        if best is None or score > best[0]:
            best = (score, eleven)
    assert best is not None  # LEGAL_FORMATIONS is never empty
    return best
