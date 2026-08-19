"""REH-79: the live sell path must use the MEASURED instant-sell ratio.

REH-67 measured it and fixed the replay only. Live code kept `market_value *
0.95`, an assumption REH-51 asserted in a plan and nobody ever checked. The
measurement: across all 151 real flips joined to `player_mv_history` within a
day of the sale, the sell/MV ratio has a hard mode of 41 rows at exactly 1.00
and ZERO rows at 0.95.

The understatement is not conservative in a useful direction. Trade pairs
compute `net_cost = bid - sell_recovery`, so understating recovery INFLATES
every pair's net cost and rejects upgrades the bot can actually afford --
`bidding_strategy.py` also derives `budget_ceiling` from it, capping the bid
itself.

It does not weaken the kickoff guard: `execution.py`'s REH-11 block tests
`(current_budget - price) < 0` against ACTUAL budget and ignores sell recovery
entirely, because buy-first-sell-after means the sale has not happened yet.
"""

from __future__ import annotations

import ast
from pathlib import Path

from rehoboam.config import INSTANT_SELL_PCT

# Every module that turns a market value into expected sell proceeds.
SELL_PATH_MODULES = (
    "rehoboam/trader.py",
    "rehoboam/auto_trader.py",
    "rehoboam/scoring/decision.py",
)


def test_the_measured_ratio_is_one():
    """Pinning the measurement itself. If this ever changes it must be because
    someone re-measured, not because a caller found 1.0 inconvenient."""
    assert INSTANT_SELL_PCT == 1.0


def test_the_replay_and_the_live_path_share_one_definition():
    """The replay's agreement with live code must be structural, not a
    coincidence between two literals that can drift apart."""
    from rehoboam.replay import engine

    assert engine.INSTANT_SELL_PCT is INSTANT_SELL_PCT


def _float_literals(path: Path) -> list[float]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, float)
    ]


def test_no_sell_path_module_still_hardcodes_the_old_haircut():
    """A source-level guard, because the defect this ticket fixes was a literal
    copied into seven places and then corrected in only one of them. A future
    edit that reintroduces `* 0.95` anywhere in the sell path fails here."""
    repo_root = Path(__file__).resolve().parent.parent
    offenders = {
        module: literals
        for module in SELL_PATH_MODULES
        if (literals := [v for v in _float_literals(repo_root / module) if v == 0.95])
    }
    assert not offenders, f"stale 0.95 instant-sell literal still present: {offenders}"
