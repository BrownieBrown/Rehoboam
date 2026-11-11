"""Solvency: the only debt rule (spec v3 §5.3 C3/C4).

v1 plans no debt. After the session's sales and bids, the cash still covers
every open bid as if it wins. A negative budget at kickoff scores zero for the
whole matchday, and with this rule there is never a debt to repay.

Planned debt through named sales (spec §5.6) is the first extension, and only
if the backtest shows the cash limit binds. The allowance would then live
here, and only here.
"""

from __future__ import annotations

from collections.abc import Iterable

from rehoboam.desk.model import Book, Offer, Player


def cash_after(book: Book, sells: Iterable[Player], bids: Iterable[Offer]) -> int:
    return (
        book.cash + sum(p.market_value for p in sells) - book.committed - sum(o.bid for o in bids)
    )


def solvent(book: Book, sells: Iterable[Player], bids: Iterable[Offer]) -> bool:
    return cash_after(book, sells, bids) >= 0
