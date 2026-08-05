"""Model the live bot's profit-flip BUYS inside the replay (REH-71).

Nothing here reimplements a heuristic. `TrendService.analyze` and
`ProfitTrader.find_profit_opportunities` are called for real, exactly as
`driver.make_ep_bid_fn` calls the real `SmartBidding` -- so a change to either
shipped rule shows up in the replay instead of silently drifting from it.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from rehoboam.enrichment.corpus import TrainingCorpus


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


SECONDS_PER_DAY = 86400.0


def history_at(corpus: TrainingCorpus, player_id: str, at: float) -> dict:
    """A `TrendService.analyze`-shaped history, truncated strictly before ``at``.

    ``hmv``/``lmv`` are deliberately omitted rather than computed. ``analyze``
    derives ``peak_value = max(api_peak, data_peak, current)`` and
    ``low_value = min(v for v in [api_low, data_low, current] if v > 0)``, so an
    absent key drops out of both and the extremes come from the truncated series
    alone. Supplying the season-wide peak would leak the future into
    ``ProfitTrader``'s mean-reversion branch.

    ``snapshot_at`` is exactly ``dt * 86400`` (``corpus.record_mv_series``), so
    the round trip back to ``dt`` is lossless.
    """
    with sqlite3.connect(corpus.db_path) as conn:
        rows = conn.execute(
            "SELECT snapshot_at, market_value FROM mv_series "
            "WHERE player_id = ? AND snapshot_at < ? ORDER BY snapshot_at",
            (str(player_id), float(at)),
        ).fetchall()
    return {"it": [{"dt": int(snapshot / SECONDS_PER_DAY), "mv": int(mv)} for snapshot, mv in rows]}


# Statuses in which the player actually took the pitch: 3 = came on as a sub,
# 5 = started. Deliberately NARROWER than `driver.PLAYED_STATUSES`, which is
# (1, 3, 4, 5) because the availability model needs a fitted rate for every
# state including "not in squad". Kickbase's own average points is per
# APPEARANCE, so counting non-appearances here would understate every player.
APPEARANCE_STATUSES = (3, 5)


def average_points_at(
    corpus: TrainingCorpus, player_id: str, *, season: str, day_number: int
) -> float:
    """Mean points per appearance over matches strictly before ``day_number``.

    Reuses the v2 scorer's ``matches_before`` boundary rather than introducing a
    second truncation rule, so the flip path and the EP path cannot disagree
    about what was knowable at the decision instant.
    """
    from rehoboam.backtest.snapshot import matches_before

    history = matches_before(
        corpus.matches_for_player(player_id), season=season, day_number=day_number
    )
    played = [m for m in history if m.get("status") in APPEARANCE_STATUSES]
    if not played:
        return 0.0
    return sum(float(m["points"] or 0) for m in played) / len(played)


def flip_bid_ceiling(
    market_value: int, expected_appreciation: float, *, min_profit_pct: float
) -> int:
    """The most a flip can rationally cost and still clear its own margin.

    A flip bought at ``P`` exits at ``MV x (1 + a/100) x INSTANT_SELL_PCT``,
    where ``INSTANT_SELL_PCT`` is 1.00 as measured in REH-67. Requiring the
    round trip to still return ``min_profit_pct`` gives::

        P <= MV x (1 + a/100) / (1 + m/100)

    Bidding above this guarantees a loss even when the expected appreciation
    fully materialises, so losing a listing to a rival who paid more is the
    correct outcome, not a modelling failure.

    Deliberately NOT `SmartBidding.calculate_ep_bid`: that sizes an overbid from
    the marginal-gain tier, and a flip's marginal EP gain is ~0 by construction,
    so every flip would bid into the bottom tier and lose every contested
    listing -- reporting "flip buys do nothing" as an artifact of the bidder
    rather than a fact about flipping.
    """
    from rehoboam.replay.engine import INSTANT_SELL_PCT

    exit_value = market_value * (1.0 + expected_appreciation / 100.0) * INSTANT_SELL_PCT
    return int(exit_value / (1.0 + min_profit_pct / 100.0))
