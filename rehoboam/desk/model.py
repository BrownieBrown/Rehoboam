"""What the planner sees in one session (spec v3 §5). Frozen; built by an adapter."""

from __future__ import annotations

from dataclasses import dataclass

POSITIONS = ("Goalkeeper", "Defender", "Midfielder", "Forward")
MAX_SQUAD = 15


@dataclass(frozen=True)
class Player:
    """One player, valued this session (ADR-0002). Nothing about his role is stored."""

    player_id: str
    position: str  # one of POSITIONS
    market_value: int  # held: the value in force now; listed: the forecast value at expiry
    ep_next: float  # expected points at the next kickoff
    drift: float  # expected fractional value change over the horizon, from when we'd hold him


@dataclass(frozen=True)
class Offer:
    """A Kickbase listing the plan may bid on, at a premium over its value at expiry."""

    player: Player
    premium: float
    # Does the auction settle before the next kickoff? A player who arrives after
    # the lock cannot play that matchday: his drift counts, his points don't.
    arrives_before_kickoff: bool = True

    @property
    def bid(self) -> int:
        return round(self.player.market_value * (1 + self.premium))

    @property
    def premium_eur(self) -> int:
        return self.bid - self.player.market_value


@dataclass(frozen=True)
class Book:
    """The squad, the cash and the bids already open (counted as won for cash, lost for the eleven)."""

    held: tuple[Player, ...]
    cash: int
    committed: int = 0  # the sum of our open bids
    open_bids: int = 0  # how many


@dataclass(frozen=True)
class Plan:
    sells: tuple[Player, ...]
    bids: tuple[Offer, ...]
    utility: float  # U of the planned squad, in points (spec §5.2)
    kickoff_ep: float  # EP of its best legal eleven
