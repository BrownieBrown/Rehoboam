"""Model the live bot's profit-flip BUYS inside the replay (REH-71).

Nothing here reimplements a heuristic. `TrendService.analyze` and
`ProfitTrader.find_profit_opportunities` are called for real, exactly as
`driver.make_ep_bid_fn` calls the real `SmartBidding` -- so a change to either
shipped rule shows up in the replay instead of silently drifting from it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CorpusMarketPlayer:
    """The attribute surface `ProfitTrader.find_profit_opportunities` reads.

    Deliberately a stand-in for `kickbase_client.MarketPlayer` rather than the
    real thing: the real one is built from a live API payload carrying dozens of
    fields the corpus cannot supply, and constructing it would mean inventing
    values that then look authoritative.

    `price == market_value` at every construction site is not an oversight --
    see `make_flip_buy_fn` for why feeding a real transaction price here
    silently disables the entire pass.
    """

    id: str
    price: int
    market_value: int
    average_points: float
    position: str
    status: int = 0
    first_name: str = ""
    last_name: str = ""
