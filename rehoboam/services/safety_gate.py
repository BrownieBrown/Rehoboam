"""Hard limits applied to anything that executes, autonomous or approved.

This is the last thing between a decision and real money. It is a pure
function so it can be exhaustively tested, and it collects ALL failing
reasons rather than short-circuiting on the first — a caller reporting to
Telegram should be able to say everything that is wrong at once.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class GateResult:
    """Outcome of a gate check. ``reasons`` is empty when ``ok`` is True."""

    ok: bool
    reasons: list[str] = field(default_factory=list)


def check_buy(
    *,
    player_id: str,
    bid: int,
    market_value: int,
    current_budget: int,
    free_slots: int,
    known_player_ids: Iterable[str],
    max_overbid_pct: float,
) -> GateResult:
    """Is this buy allowed to execute?

    ``known_player_ids`` is the set of ids from the data this decision was
    made against. An id outside it never reaches ``api.buy_player``: a forged
    webhook callback, or a stale proposal naming a player who has since left
    the market, must not be able to spend money.
    """
    reasons: list[str] = []

    if player_id not in set(known_player_ids):
        reasons.append(f"unknown player id {player_id!r} — not in the current market data")

    if current_budget - bid < 0:
        reasons.append(
            f"budget would go negative: EUR {current_budget:,} - EUR {bid:,} "
            f"= EUR {current_budget - bid:,} (zero points for the matchday)"
        )

    if market_value > 0:
        cap = market_value * (1.0 + max_overbid_pct / 100.0)
        if bid > cap:
            over = (bid / market_value - 1.0) * 100.0
            reasons.append(
                f"overbid {over:.1f}% exceeds the {max_overbid_pct:.1f}% cap "
                f"(bid EUR {bid:,} vs market value EUR {market_value:,})"
            )

    if free_slots <= 0:
        reasons.append("no free squad slot")

    return GateResult(ok=not reasons, reasons=reasons)
