"""Model the live bot's profit-flip BUYS inside the replay (REH-71).

Nothing here reimplements a heuristic. `TrendService.analyze` and
`ProfitTrader.find_profit_opportunities` are called for real, exactly as
`driver.make_ep_bid_fn` calls the real `SmartBidding` -- so a change to either
shipped rule shows up in the replay instead of silently drifting from it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
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
    """Mean points per appearance in ``season``, strictly before ``day_number``.

    Reuses the v2 scorer's ``matches_before`` boundary rather than introducing a
    second truncation rule, so the flip path and the EP path cannot disagree
    about what was knowable at the decision instant.

    THEN narrows to ``season`` (REH-77). ``matches_before`` deliberately keeps
    every earlier season in full, which is right for the scorer -- it should use
    all the history it has -- and wrong here. This function stands in for
    Kickbase's ``ap`` field, which ``ProfitTrader``'s ladder gates on at 20/30/40
    (profit_trader.py:126-180), and ``ap`` is a SEASON-to-date mean. The two
    callers want different windows from the same truncation; assuming one window
    served both is what made this wrong.

    Measured, not assumed: against the eight ``flip_outcomes`` rows that recorded
    a real ``ap`` at buy time, season-to-date tracks the recorded value within
    ~1-2 points on all eight (mean absolute error ~1.0), while a career mean is
    off by up to 19.5 in BOTH directions (mean absolute error ~9.9) -- Engelhardt
    66.0 recorded against 65.5 season and 83.7 career; Da Costa 74.0 against 75.5
    season and 54.5 career.
    """
    from rehoboam.backtest.snapshot import matches_before

    history = matches_before(
        corpus.matches_for_player(player_id), season=season, day_number=day_number
    )
    played = [
        m for m in history if m.get("status") in APPEARANCE_STATUSES and m.get("season") == season
    ]
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


# Thresholds read from `Trader.find_profit_opportunities`'s call site
# (trader.py:715-721), NOT from `ProfitTrader.__init__`'s defaults, which no
# caller in the codebase actually uses.
FLIP_MIN_PROFIT_PCT = 8.0
FLIP_MAX_HOLD_DAYS = 7
FLIP_MAX_RISK_SCORE = 60.0
# The live path scores only the first 50 market entries (trader.py:711).
FLIP_MARKET_SCAN_LIMIT = 50


def shipped_max_debt_pct() -> float:
    """The debt ceiling the live flip path actually passes (REH-71 review).

    ``ProfitTrader.find_profit_opportunities`` *defaults* ``max_debt_pct`` to
    60.0, and ``Settings.max_debt_pct_of_team_value`` is also 60.0 today — but
    the live call site forwards the Settings field explicitly
    (``trader.py:728``), so the agreement is a coincidence, not a contract.
    Leaning on it is exactly the config drift this harness reads shipped
    defaults to avoid: re-tune the field in production and the replay would go
    on describing a bot nobody deployed.

    Reuses ``driver._shipped_default`` rather than a fourth copy of the same
    two-line read. No import cycle: ``driver`` imports this module lazily,
    inside ``run_replay``.
    """
    from rehoboam.replay.driver import _shipped_default

    return _shipped_default("max_debt_pct_of_team_value")


@dataclass(frozen=True)
class FlipCandidate:
    """A player worth buying for appreciation, with what he is worth paying."""

    player_id: str
    market_value: int
    expected_appreciation: float
    max_bid: int


def make_flip_buy_fn(
    corpus: TrainingCorpus,
    *,
    season: str,
    day_fn: Callable[[float], int],
    position_fn: Callable[[str], str | None],
) -> Callable[[list, float, int, int], list[FlipCandidate]]:
    """Rank flip candidates with the bot's own profit-trading logic (REH-71).

    DECISION PRICE vs EXECUTION PRICE. The adapter reports
    ``price == market_value`` while the engine pays a bid derived from
    ``flip_bid_ceiling``. This is not a fudge. The live bot only ever flips
    ``is_kickbase_seller()`` listings (trader.py:685), where the two are equal by
    construction, and ``ProfitTrader`` *branches* on that equality
    (profit_trader.py:121). Feeding it a real transaction price -- which averages
    1.117x market value -- sends every candidate down the non-Kickbase branch,
    where ``value_gap`` is negative and the candidate is dropped at
    profit_trader.py:194. The pass would look modelled and never fire once.

    ``status`` is pinned to 0 (available) because the corpus's per-match status
    is participation, not injury. Nothing is therefore skipped as injured, so
    this buys MORE flips than the live bot would -- an upper bound on flip
    activity, and hence on flip harm.
    """
    from rehoboam.profit_trader import ProfitTrader
    from rehoboam.services.trend_service import TrendService

    trader = ProfitTrader(
        min_profit_pct=FLIP_MIN_PROFIT_PCT,
        max_hold_days=FLIP_MAX_HOLD_DAYS,
        max_risk_score=FLIP_MAX_RISK_SCORE,
    )
    max_debt_pct = shipped_max_debt_pct()

    def candidates(listings: list, at: float, budget: int, team_value: int) -> list[FlipCandidate]:
        day = day_fn(at)
        players: list[CorpusMarketPlayer] = []
        trends: dict[str, dict] = {}

        for listing in listings[:FLIP_MARKET_SCAN_LIMIT]:
            pid = listing.player_id
            market_value = corpus.market_value_at(pid, at)
            position = position_fn(pid)
            if not market_value or not position:
                continue
            players.append(
                CorpusMarketPlayer(
                    id=pid,
                    price=market_value,
                    market_value=market_value,
                    average_points=average_points_at(corpus, pid, season=season, day_number=day),
                    position=position,
                )
            )
            trends[pid] = TrendService.analyze(history_at(corpus, pid, at), market_value).to_dict()

        opportunities = trader.find_profit_opportunities(
            market_players=players,
            current_budget=budget,
            player_trends=trends,
            team_value=team_value,
            max_debt_pct=max_debt_pct,
        )
        return [
            FlipCandidate(
                # `ProfitOpportunity.player` is annotated `any` -- the builtin
                # function, not `typing.Any` -- in shipped code this module does
                # not own (profit_trader.py:14), so mypy cannot see `.id` on it.
                # Scoped to this expression rather than fixed here: retyping a
                # shipped dataclass is a separate change with its own blast
                # radius, and REH-71 is not the ticket for it.
                player_id=o.player.id,  # type: ignore[attr-defined]
                market_value=o.market_value,
                expected_appreciation=o.expected_appreciation,
                max_bid=flip_bid_ceiling(
                    o.market_value,
                    o.expected_appreciation,
                    min_profit_pct=FLIP_MIN_PROFIT_PCT,
                ),
            )
            for o in opportunities
        ]

    return candidates
