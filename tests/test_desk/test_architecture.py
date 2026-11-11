"""I-A1 (spec v3 §6): the v3 packages never import the legacy decision layer.

One predicate per concept is enforced by the import graph. If `desk` or `sim`
could reach `bidding_strategy` or `services/budget`, the copies would start to
drift again.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2] / "rehoboam"
V3_PACKAGES = ("desk", "sim")
LEGACY = {
    "rehoboam.auto_trader",
    "rehoboam.trader",
    "rehoboam.bidding_strategy",
    "rehoboam.profit_trader",
    "rehoboam.bid_evaluator",
    "rehoboam.squad_optimizer",
    "rehoboam.league_compliance",
    "rehoboam.services.books",
    "rehoboam.services.budget",
    "rehoboam.services.debt_recovery",
    "rehoboam.services.flip_breaker",
    "rehoboam.services.flip_entry",
    "rehoboam.services.flip_exit",
    "rehoboam.services.flip_sizing",
    "rehoboam.services.flip_window",
    "rehoboam.services.rank_sell",
    "rehoboam.services.entry_direction",
    "rehoboam.services.value_bid",
    "rehoboam.services.pacing",
    "rehoboam.services.bid_ceiling",
    "rehoboam.services.profit_curve",
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def _v3_modules() -> list[Path]:
    return [p for pkg in V3_PACKAGES for p in sorted((ROOT / pkg).rglob("*.py"))]


@pytest.mark.parametrize("module", _v3_modules(), ids=lambda p: str(p.relative_to(ROOT)))
def test_v3_modules_do_not_import_the_legacy_decision_layer(module):
    assert _imports(module) & LEGACY == set()
