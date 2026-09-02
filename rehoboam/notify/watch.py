"""Tracking a player Marco wants toward the day he can actually buy him.

Olise on 2026-09-02 asked EUR 64,976,131 against a 33% cap of EUR 51,117,765.
Unbuyable — and the only way to find that out was to place the bid and read
`err 5050 ThirtyThreePercentRuleExceeded` off a 500.

For a season-long target a refusal at bid time is the wrong moment to learn
it. What is useful is the size of the gap and which way it is moving.

The gap closes by GROWING total worth. It cannot be closed by selling: a sale
moves money from team value into budget and leaves worth, and therefore the
cap, exactly where it was (REH-118). So the number reported is the shortfall
in *worth* — the thing that actually has to change.

Pure, so the line can be asserted directly.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WatchTarget:
    """A player being tracked toward affordability."""

    player_id: str
    name: str
    ask: int


def parse_watch_ids(raw: str | None) -> list[str]:
    """Player ids from the comma-separated `WATCH_PLAYER_IDS` setting."""
    if not raw:
        return []
    return [part.strip() for part in str(raw).split(",") if part.strip()]


def worth_needed(ask: int, max_pct: float) -> int:
    """Total worth required before Kickbase will allow a purchase at `ask`.

    Rounded up: at exactly `ask / pct` the cap floors to one euro under the
    asking price, and reporting a target that still refuses is worse than
    reporting one euro too many.
    """
    if max_pct <= 0:
        return 0
    # Exact ceiling of ask / (max_pct/100), done in integers so a fractional
    # percentage cannot drift. An earlier form rounded to hundreds of euros
    # and overstated the shortfall by 33.
    return -(-ask * 10_000 // int(round(max_pct * 100)))


def render_watch_line(target: WatchTarget, *, total_worth: int | None, max_pct: float) -> str:
    """One line for the daily summary's WATCH section."""
    if not total_worth or total_worth <= 0:
        return f"{target.name} EUR {target.ask:,} — affordability unknown (team value unreadable)"

    cap = int(total_worth * max_pct / 100.0)
    if target.ask <= cap:
        return (
            f"{target.name} EUR {target.ask:,} — WITHIN REACH "
            f"(cap EUR {cap:,} on worth EUR {total_worth:,})"
        )

    needed = worth_needed(target.ask, max_pct)
    return (
        f"{target.name} EUR {target.ask:,} — out of reach, cap is EUR {cap:,}. "
        f"Need EUR {needed - total_worth:,} more team value "
        f"(selling will not help)"
    )
