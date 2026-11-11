"""Would our bid have won? (spec v3 §8.2)

Bids are blind and settle at expiry. If a league manager bought the listing, we
win only by bidding strictly above his premium: equal bids go to the earliest
bidder, and we assume that was not us. If nobody in the league bought it, a bid
at or above the value at expiry wins, because Kickbase declines anything below
that.

Our own real buys are not rivals: the policy under test replaces what we did. A
listing we really bought has an unknown runner-up, so its result is unknown,
never a free win.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta

from rehoboam.sim.history import BUY, Listing, Transfer, ValuePaths, value_day

SETTLE_GRACE = timedelta(hours=6)  # a buy is logged up to a few hours after expiry


@dataclass(frozen=True)
class Rival:
    """What it took to win a listing. ``premium`` is None when nobody bid."""

    premium: float | None
    known: bool = True  # False: we really bought it, the runner-up is unknown
    # When we really bought it: our winning premium. Matching it beats the
    # runner-up for certain. Below it, the outcome is unknown.
    beaten_at: float | None = None


NO_RIVAL = Rival(premium=None)
UNKNOWN = Rival(premium=None, known=False)


def buys_by_player(transfers: Iterable[Transfer]) -> dict[str, tuple[Transfer, ...]]:
    grouped: dict[str, list[Transfer]] = defaultdict(list)
    for t in transfers:
        if t.kind == BUY and t.price > 0:
            grouped[t.player_id].append(t)
    return {pid: tuple(sorted(ts, key=lambda t: t.at)) for pid, ts in grouped.items()}


def rival_for(
    listing: Listing,
    buys: Mapping[str, Sequence[Transfer]],
    paths: ValuePaths,
    self_ids: frozenset[str],
) -> Rival:
    for t in buys.get(listing.player_id, ()):
        if not listing.listed_at <= t.at <= listing.expires_at + SETTLE_GRACE:
            continue
        value = paths.get(t.player_id, {}).get(value_day(t.at))
        if not value:
            return UNKNOWN
        if t.manager_id in self_ids:
            return Rival(premium=None, known=False, beaten_at=t.price / value - 1)
        return Rival(premium=t.price / value - 1)
    return NO_RIVAL


def wins(bid_premium: float, rival: Rival) -> bool | None:
    """True or False, or None when the outcome cannot be known."""
    if not rival.known:
        if rival.beaten_at is not None and bid_premium >= rival.beaten_at:
            return True
        return None
    if rival.premium is None:
        return bid_premium >= 0.0
    return bid_premium > rival.premium
