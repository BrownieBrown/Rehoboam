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
    drift_low: float = 0.0  # its p10: the haircut when he is a named sale before kickoff
    # Bought inside the drift horizon and still expected to rise: the plan paid a
    # premium for the whole horizon, so it may not sell him to fund a swap. Only
    # the solvency repair may (M2-14). Derived each session, never stored.
    committed: bool = False


@dataclass(frozen=True)
class Offer:
    """A Kickbase listing the plan may bid on, at a premium over its value at expiry."""

    player: Player
    premium: float
    # Does the auction settle before the next kickoff? A player who arrives after
    # the lock cannot play that matchday: his drift counts, his points don't.
    arrives_before_kickoff: bool = True
    # Does it settle early enough to be sold in a session before the kickoff?
    # Only then can it cover the debt it creates (solvency, M2-13).
    sellable_before_kickoff: bool = False

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
    # True: no debt may be carried past this session (the last before a kickoff,
    # or planned debt switched off). False: debt the bench covers is allowed.
    last_before_kickoff: bool = True
    # What the open bids that settle before the last session raise if sold, at
    # their p10. A bid's cover rides along until it settles (M2-13).
    open_bid_cover: int = 0


@dataclass(frozen=True)
class Plan:
    sells: tuple[Player, ...]
    bids: tuple[Offer, ...]
    utility: float  # U of the planned squad, in points (spec §5.2)
    kickoff_ep: float  # EP of its best legal eleven
