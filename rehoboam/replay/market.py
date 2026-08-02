"""Reconstructed market inventory for the season replay.

A player is treated as buyable at time T if a real manager-to-manager
transaction (``transfer_type = 2``) for that player settled within the
trailing window ending at T, at that transaction's actual price.

This is a *lower bound* on what was really available — players nobody traded
are invisible to the replay — but every price in it is a price someone really
paid. It models no bid competition: if the bot wants a listed player it gets
them, which is optimistic and must be reported as such.
"""

from __future__ import annotations

from dataclasses import dataclass

from rehoboam.enrichment.corpus import TrainingCorpus

SECONDS_PER_DAY = 86400.0


@dataclass(frozen=True)
class MarketListing:
    """A player who was buyable, and what they really cost."""

    player_id: str
    price: int
    transfer_at: float


class ReplayMarket:
    """What was on the market, reconstructed from real transactions."""

    def __init__(self, corpus: TrainingCorpus, *, window_days: int = 7) -> None:
        self.corpus = corpus
        self.window_days = window_days

    def available_before(self, at: float) -> list[MarketListing]:
        """Listings visible at ``at``, most recent price per player.

        Strictly excludes transactions at or after ``at`` — this is a leak
        boundary, not a convenience filter.
        """
        lo = at - self.window_days * SECONDS_PER_DAY
        rows = self.corpus.transfers_between(lo, at)
        latest: dict[str, MarketListing] = {}
        for row in rows:
            if row["transfer_at"] >= at:
                continue
            pid = str(row["player_id"])
            listing = MarketListing(
                player_id=pid,
                price=int(row["price"]),
                transfer_at=float(row["transfer_at"]),
            )
            existing = latest.get(pid)
            if existing is None or listing.transfer_at > existing.transfer_at:
                latest[pid] = listing
        return sorted(latest.values(), key=lambda x: x.player_id)
