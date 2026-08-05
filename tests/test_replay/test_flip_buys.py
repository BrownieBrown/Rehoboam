"""REH-71: model the live bot's profit-flip BUYS inside the replay.

The replay already models profit-taking sells (`engine._flip_sells`). The live
bot also buys purely for expected appreciation (`auto_trader.py:342-392` ->
`Trader.find_profit_opportunities` -> `ProfitTrader`), and the real
-EUR 55.3M came from both halves. Deciding the flip policy from the sell half
alone answers a question nobody asked.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

from rehoboam.replay.flip_buys import CorpusMarketPlayer


def _attributes_read_off(name: str, *functions) -> set[str]:
    """Every `<name>.<attr>` read anywhere in the given functions."""
    found: set[str] = set()
    for fn in functions:
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        found |= {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == name
        }
    return found


def test_the_adapter_satisfies_every_attribute_profit_trader_reads():
    """A contract test, not a formality. ProfitTrader is shipped code this
    module does not own. If it grows a new attribute read, the replay would
    raise AttributeError deep inside a season run, most likely on one matchday
    of thirty-four. Fail here instead.
    """
    from rehoboam.profit_trader import ProfitTrader

    read = _attributes_read_off(
        "player",
        ProfitTrader.find_profit_opportunities,
        ProfitTrader._calculate_risk,
    )

    missing = read - set(CorpusMarketPlayer.__dataclass_fields__)
    assert not missing, f"ProfitTrader reads attributes the adapter lacks: {missing}"
