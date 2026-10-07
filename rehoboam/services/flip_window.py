"""Whether the market is one to open a flip in (2026-10-07).

A flip is a bet on the market's drift as much as on the player. Over the
October break the cap-weighted market fell 0.3 to 1% every night for
twelve nights (about -7%; the median player about -14%), and across five
seasons the league's buys made in October and November resold at a median
+4.3% / +7.1% while January, April and May buys resold at -3.9% / -8.9% /
-16.2%. Two reads before opening one: the calendar (days to the next
kickoff — a long gap is a break) and the drift itself (the median nightly
move of the universe over the last nights). Unknowns close nothing. Pure.
"""

from __future__ import annotations


def flip_gate_reason(
    *,
    days_until_match: int | None,
    market_drift_pct: float | None,
    max_days_to_kickoff: int,
    drift_min_pct: float,
) -> str | None:
    """Why no new flip should be opened now, or None when the window is open."""
    if days_until_match is not None and days_until_match > max_days_to_kickoff:
        return (
            f"kickoff in {days_until_match}d (> {max_days_to_kickoff}): "
            "the market drifts down through a break"
        )
    if market_drift_pct is not None and market_drift_pct < drift_min_pct:
        return f"the market fell a median {market_drift_pct:+.1f}% a night over the last nights"
    return None
