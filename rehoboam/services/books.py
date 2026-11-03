"""Two books, one wallet: the core the eleven is built on, and everything else.

On 2026-10-27 the bot bought Coufal (23.6m) on debt, taking the wallet from
-5.7m to -29.3m; the locked window of 10-28 repaid it by selling Raum (29.6m,
126.6 points a match afterwards) and Kuruçay. Pavlović (10-07, 199.8 a match
after the sale) went the same way. Each rule did its job -- the allowance
permitted the debt, the recovery cleared it -- and together they sold the
squad's producers to repay what the bot had borrowed.

The fix is the trading desk's: positions bought on credit are repaid out of
the trading book, never the core. Two halves:

* **At the bid** (`debt_cover_reason`, read by the safety gate): a buy that
  leaves the wallet below zero -- counting every open offer as won, rule I3's
  definition -- is allowed only while the non-core holdings, the new player
  included, could repay that debt at instant-sell value. A non-core buy is
  its own collateral.
* **At the repayment** (`_run_debt_recovery`): a core player sells after
  every non-core one. An ordering, never a block (Marco, 2026-09-22): a
  negative wallet at kickoff is zero points for the whole matchday, so in a
  crisis the core is still sellable -- it is just the last thing sold.

The core is who the league's leaders keep (`core_ids`): measured 2026-11-01,
their holds of 28 days or more number 4-7 and rank by points per appearance.

Pure: callers pass the holdings in.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ..config import INSTANT_SELL_PCT


@dataclass(frozen=True)
class Holding:
    """A player we hold, or will hold if an open offer wins."""

    player_id: str
    market_value: int
    #: This matchday's expected points (the scorer's number).
    expected_points: float
    #: Season points per appearance; None before his first appearance.
    avg_points: float | None = None

    @property
    def sell_value(self) -> int:
        return int(int(self.market_value) * INSTANT_SELL_PCT)


def core_ids(holdings: Sequence[Holding], *, size: int) -> frozenset[str]:
    """The players the debt may never be repaid with — at most ``size`` of them.

    Called on the squad as it would look after the buy, so a new star can
    enter the core and push the weakest member out into the trading book.

    Ranked by season points per appearance, as the league's leaders hold
    (2026-11-01: their 28-day+ holds sit in the top ~20 at the position by
    points per appearance whatever this week's EP says — Querfeld 15th by
    average, 68th by EP, held; every leader lost money selling a top-10
    player by choice). A player without an appearance competes on expected
    points, the model's view of the same quantity. The league's Top-5 forced
    sale is not gated by this; the core is recomputed each session.
    """
    if size <= 0:
        return frozenset()

    def quality(h: Holding) -> float:
        return float(h.avg_points) if h.avg_points is not None else float(h.expected_points)

    ranked = sorted(holdings, key=lambda h: (quality(h), h.expected_points), reverse=True)
    return frozenset(h.player_id for h in ranked[:size])


def liquid_value(holdings: Iterable[Holding], core: frozenset[str]) -> int:
    """What the trading book raises if every non-core holding is sold now."""
    return sum(h.sell_value for h in holdings if h.player_id not in core)


def core_buy(
    candidate: Holding,
    holdings: Sequence[Holding],
    *,
    size: int,
    appearances: int | None,
    min_appearances: int,
) -> bool:
    """Would this buy enter the core book? (2026-11-03)

    A core buy is held for points, so the bid is sized to win it, not to
    resell it (`SmartBidding.calculate_ep_bid(core_buy=…)`). It must rank in
    the top ``size`` of the squad after the buy by points per appearance,
    on an average of at least ``min_appearances`` matches — a player with
    no average, or too few matches to trust one, is a trading-book buy.
    Unlike `core_ids`, expected points never stand in for the average here:
    the EP model's view is what the trading book already bids on.
    """
    if size <= 0 or candidate.avg_points is None:
        return False
    if appearances is None or int(appearances) < int(min_appearances):
        return False
    after = [h for h in holdings if h.player_id != candidate.player_id] + [candidate]
    return candidate.player_id in core_ids(after, size=size)


def debt_cover_reason(
    *,
    holdings: Sequence[Holding],
    candidate: Holding,
    bid: int,
    wallet: int,
    open_offers: int,
    core_size: int,
) -> str | None:
    """Why this bid would leave a debt only the core could repay, or None.

    ``holdings`` is the squad plus the players of our open offers;
    ``open_offers`` their total. The debt is measured as if every open offer
    and this bid win: that is what the locked window will have to repay.
    """
    debt = int(open_offers) + int(bid) - int(wallet)
    if debt <= 0:
        return None
    after = [h for h in holdings if h.player_id != candidate.player_id] + [candidate]
    core = core_ids(after, size=core_size)
    cover = liquid_value(after, core)
    if debt <= cover:
        return None
    return (
        f"core book: a debt of EUR {debt:,} after this bid, but the non-core "
        f"holdings raise only EUR {cover:,} — the rest would be repaid by selling "
        f"the core ({len(core)} player(s))"
    )


@dataclass(frozen=True)
class CoreBookCheck:
    """`debt_cover_reason` with everything but the bid bound — what a
    `BuyGate` carries, because the bid is only known when the gate runs."""

    holdings: tuple[Holding, ...]
    candidate: Holding
    wallet: int
    open_offers: int
    core_size: int

    def reason(self, bid: int) -> str | None:
        return debt_cover_reason(
            holdings=self.holdings,
            candidate=self.candidate,
            bid=bid,
            wallet=self.wallet,
            open_offers=self.open_offers,
            core_size=self.core_size,
        )
