"""Composition root for the full-bot season replay.

Wires the corpus, the fitted v2 scorer, the reconstructed market and the real
standings into one run. This is the only place that knows about file paths and
the verified starting state, and the only place the leak boundary is applied.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from rehoboam.backtest.snapshot import matches_before
from rehoboam.enrichment.corpus import TrainingCorpus
from rehoboam.replay.attribution import LeagueStanding, format_report
from rehoboam.replay.engine import (
    Matchday,
    SeasonResult,
    run_season,
    shipped_min_ep_gain,
)
from rehoboam.replay.market import ReplayMarket
from rehoboam.replay.state import initial_state
from rehoboam.scoring.v2.adapter import compose_ep, prev_status_from_history
from rehoboam.scoring.v2.coefficients import load_coefficients
from rehoboam.services.bid_ceiling import BidCeilingPolicy, Tier
from rehoboam.services.pacing import (
    PacingContext,
    available_squad_slots,
    capital_reserve,
    median_move_price,
)

SEASON = "2025/2026"
LEAGUE_ID = "1933872"
MANAGER_ID = "3616202"
ASSIGNED_ON = 1754661947.0  # 2025-08-08T14:05:47Z, verified from /v4/leagues/{id}/overview
STARTING_BUDGET = 80_000_000


def _parse(dt: str) -> float:
    return datetime.strptime(dt, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


def load_calendar(learning_db_path: Path, *, league_id: str) -> dict[int, float]:
    """Authoritative kickoff per matchday, as unix epochs.

    ``player_match_history`` cannot supply this: it holds 36 distinct team ids
    for 2025/2026 against a league of 18, so two competitions with different
    calendars share one ``day_number`` space and day 1 spans 2025-08-01 to
    2025-08-24. ``matchday_lineup_results`` is our league's own fixture list —
    exactly one row per matchday, with the date the matchday actually started.
    """
    with sqlite3.connect(learning_db_path) as conn:
        rows = conn.execute(
            "SELECT day_number, matchday_date FROM matchday_lineup_results "
            "WHERE league_id = ? AND matchday_date IS NOT NULL",
            (league_id,),
        ).fetchall()
    return {int(day): _parse(str(date)) for day, date in rows}


def day_for_kickoff(kickoffs: dict[int, float], at: float) -> int:
    """The matchday being predicted at ``at``: the next one still to kick off.

    Resolving this from match dates instead (``MIN(day_number)`` over unplayed
    fixtures) lags badly whenever a low-numbered fixture is postponed — it put
    matchdays 21 through 24 all on a cutoff of 17, starving the scorer of four
    matchdays of history. A per-league calendar has no such overlap.
    """
    upcoming = [day for day, kickoff in kickoffs.items() if kickoff > at]
    if upcoming:
        return min(upcoming)
    # Past the final whistle of the season: every matchday is history.
    return max(kickoffs, default=0) + 1


def build_matchdays(
    corpus: TrainingCorpus, *, season: str, kickoffs: dict[int, float] | None = None
) -> list[Matchday]:
    """One ``Matchday`` per day_number, with real points and its kickoff.

    Points come from ``player_match_history`` keyed by ``day_number``; those
    reconcile exactly with the official league totals, so only the dates were
    ever wrong. Pass ``kickoffs`` (see ``load_calendar``) to date the matchdays
    from our league's own fixture list. Without it the kickoff falls back to
    the earliest match recorded under that day_number, which is only correct
    for a corpus holding a single competition.
    """
    with sqlite3.connect(corpus.db_path) as conn:
        rows = conn.execute(
            "SELECT day_number, player_id, points, match_date FROM player_match_history "
            "WHERE season = ? AND match_date IS NOT NULL ORDER BY day_number",
            (season,),
        ).fetchall()

    by_day: dict[int, dict[str, float]] = {}
    earliest: dict[int, float] = {}
    for day, pid, points, match_date in rows:
        day = int(day)
        by_day.setdefault(day, {})[str(pid)] = float(points or 0)
        at = _parse(match_date)
        if day not in earliest or at < earliest[day]:
            earliest[day] = at

    return [
        Matchday(
            day_number=day,
            kickoff=(kickoffs or {}).get(day, earliest[day]),
            points=by_day[day],
        )
        for day in sorted(by_day)
    ]


def load_standings(
    learning_db_path: Path, *, league_id: str, exclude_manager_id: str
) -> list[LeagueStanding]:
    """Final season totals for every manager except ours."""
    with sqlite3.connect(learning_db_path) as conn:
        rows = conn.execute(
            "SELECT manager_id, MAX(day_number), total_points FROM league_rank_history "
            "WHERE league_id = ? AND manager_id != ? GROUP BY manager_id",
            (league_id, exclude_manager_id),
        ).fetchall()
    return [
        LeagueStanding(manager_id=str(r[0]), name=str(r[0]), total_points=int(r[2] or 0))
        for r in rows
    ]


def _make_score_fn(
    corpus: TrainingCorpus, season: str, kickoffs: dict[int, float]
) -> Callable[[str, float], float]:
    """Score a player using only matches before the current matchday.

    The leak boundary lives here: ``matches_before`` truncates history to
    strictly earlier matchdays, and the cutoff is derived from the decision
    timestamp via the league calendar, never from the matchday being predicted.
    """
    availability, rate, _meta = load_coefficients()
    positions: dict[str, str] = {}

    def score(player_id: str, at: float) -> float:
        day = day_for_kickoff(kickoffs, at)
        history = matches_before(
            corpus.matches_for_player(player_id), season=season, day_number=day
        )
        # REH-84's rule applies to this traversal too, not just to `compose_ep`:
        # deriving "most recent played status" here separately is a second
        # implementation that would drift from the live scorer.
        prev_status = prev_status_from_history(
            [(m.get("match_date"), m.get("status")) for m in history],
            now=datetime.fromtimestamp(at, tz=timezone.utc),
            max_age_days=_shipped_default("max_status_age_days"),
        )
        if player_id not in positions:
            positions.update(corpus.positions_for([player_id]))
        position = positions.get(player_id)
        # REH-84: compose through `compose_ep`, never a local copy of its body.
        # This function used to inline `sum(probs[s] * rate.predict(...))`, which
        # agreed with the live scorer until REH-80 added a cold-start discount
        # inside `compose_ep`. After that the replay silently kept scoring
        # unfitted players 30% higher (79.3 against 61.1), and a season replay
        # run either side of that change printed an identical 26,960 points --
        # the harness could not see the very change it existed to evaluate.
        # `scoring/v2/thresholds.py` already states the rule: "A second
        # implementation would drift."
        #
        # The leak boundary stays here: `prev_status` above is derived from
        # `matches_before`, so only pre-matchday history reaches the model.
        return compose_ep(player_id, prev_status, position, availability, rate)

    return score


def _shipped_default(name: str) -> float:
    """A Settings field default without instantiating Settings.

    Instantiation requires KICKBASE credentials; the replay must stay runnable
    offline and deterministic against the DBs alone.
    """
    from rehoboam.config import Settings

    return float(Settings.model_fields[name].default)


def _make_median_move_fn(
    corpus: TrainingCorpus, *, window_days: int, floor_eur: int
) -> Callable[[float], int]:
    """Trailing-window median buy price, recomputed per decision instant (REH-85).

    Production (`Trader._build_pacing_context`) recomputes this once per
    session from a trailing `pacing_window_days` window over recent buys, so
    the reserve tracks in-season price drift. A single pre-season constant
    frozen for all 34 matchdays would silently stop reflecting the season's
    real price level — this is what the replay must NOT do, or a "does this
    knob matter" sweep over `pacing_window_days` would report an identical
    number regardless of the window, when in truth the window was never
    wired to move anything.

    The replay's analogue of "one session" is one matchday's worth of
    decisions: every listing considered for the same matchday shares the same
    `at` (`decide_at` from `run_season`), so memoising by `at` reproduces
    production's cadence — one query per matchday, not one per bid.

    `at` is already the leak boundary (`decide_at = kickoff -
    DECISION_LEAD_SECONDS`, applied upstream in `engine.py`), so bounding the
    trailing window at `at` costs nothing extra here: nothing later than the
    decision instant can enter the window.
    """
    window_seconds = window_days * 86400.0
    cache: dict[float, int] = {}

    def median_move_at(at: float) -> int:
        if at not in cache:
            prices = [
                int(row["price"]) for row in corpus.transfers_between(at - window_seconds, at)
            ]
            cache[at] = median_move_price(prices, floor_eur=floor_eur)
        return cache[at]

    return median_move_at


def buy_quota_from(result: SeasonResult) -> dict[int, int]:
    """Per-matchday buy counts, for holding a control's trading tempo fixed.

    Matched matchday by matchday rather than season-wide: a season-wide budget
    would let the control front-load its buys into whichever weeks happened to
    be cheap, which is a different policy, not a different chooser.
    """
    return {o.day_number: o.buys for o in result.outcomes}


def run_replay(
    *,
    corpus_path: Path,
    learning_db_path: Path,
    min_ep_gain: float | None = None,
    buy_rank_fn: Callable[[str, float], float] | None = None,
    buy_quota: dict[int, int] | None = None,
    with_competition: bool = False,
    with_flips: bool = False,
    with_flip_buys: bool = False,
    pacing_enabled: bool = True,
    pacing_min_moves: int | None = None,
    pacing_window_days: int | None = None,
    pacing_max_reserve_fraction: float | None = None,
    pacing_min_spendable_moves: float | None = None,
) -> tuple[SeasonResult, str]:
    """Replay the whole season and return the result plus a formatted report.

    ``min_ep_gain`` defaults to whatever the live bot ships with, so the replay
    describes the bot on main rather than an agent nobody deployed. Pass it
    explicitly only to answer a "what if the floor were X" question — and label
    any such run as a sensitivity check, not a counterfactual result.

    ``pacing_enabled``/``pacing_min_moves``/``pacing_window_days`` (REH-85) are
    only meaningful together with ``with_competition`` — pacing caps a bid,
    and without bid competition every listing is bought at the real price
    regardless. ``pacing_min_moves``/``pacing_window_days`` default to the
    shipped ``Settings`` values when left ``None``, so a sweep that overrides
    them explicitly is comparing against the same bot main ships.
    """
    corpus = TrainingCorpus(corpus_path)
    kickoffs = load_calendar(learning_db_path, league_id=LEAGUE_ID)
    matchdays = build_matchdays(corpus, season=SEASON, kickoffs=kickoffs)
    state = initial_state(
        corpus,
        manager_id=MANAGER_ID,
        assigned_on=ASSIGNED_ON,
        starting_budget=STARTING_BUDGET,
    )
    market = ReplayMarket(corpus)
    score_fn = _make_score_fn(corpus, SEASON, kickoffs)
    positions_cache: dict[str, str] = {}
    teams_cache: dict[str, str] = {}

    def position_fn(pid: str) -> str | None:
        if pid not in positions_cache:
            positions_cache.update(corpus.positions_for([pid]))
        return positions_cache.get(pid)

    def team_fn(pid: str) -> str | None:
        if pid not in teams_cache:
            teams_cache.update(corpus.team_ids_for([pid]))
        return teams_cache.get(pid)

    gain_floor = shipped_min_ep_gain() if min_ep_gain is None else min_ep_gain

    flip_buy_fn = None
    if with_flip_buys:
        from rehoboam.replay.flip_buys import make_flip_buy_fn

        flip_buy_fn = make_flip_buy_fn(
            corpus,
            season=SEASON,
            # The SAME resolver the scorer uses, so the flip path and the EP
            # path cannot disagree about which matchday is being predicted.
            day_fn=lambda at: day_for_kickoff(kickoffs, at),
            position_fn=position_fn,
        )

    result = run_season(
        state=state,
        market=market,
        matchdays=matchdays,
        score_fn=score_fn,
        mv_fn=corpus.market_value_at,
        position_fn=position_fn,
        team_fn=team_fn,
        min_ep_gain=gain_floor,
        buy_rank_fn=buy_rank_fn,
        buy_quota=buy_quota,
        bid_fn=(
            make_ep_bid_fn(
                mv_fn=corpus.market_value_at,
                score_fn=score_fn,
                median_move_fn=_make_median_move_fn(
                    corpus,
                    window_days=(
                        pacing_window_days
                        if pacing_window_days is not None
                        else int(_shipped_default("pacing_window_days"))
                    ),
                    floor_eur=int(_shipped_default("pacing_median_floor_eur")),
                ),
                in_season_min_moves=(
                    pacing_min_moves
                    if pacing_min_moves is not None
                    else int(_shipped_default("pacing_in_season_min_moves"))
                ),
                max_reserve_fraction=(
                    pacing_max_reserve_fraction
                    if pacing_max_reserve_fraction is not None
                    else float(_shipped_default("pacing_max_reserve_fraction"))
                ),
                min_spendable_moves=(
                    pacing_min_spendable_moves
                    if pacing_min_spendable_moves is not None
                    else float(_shipped_default("pacing_min_spendable_moves"))
                ),
                pacing_enabled=pacing_enabled,
            )
            if with_competition
            else None
        ),
        # Live trade-side thresholds, read from the shipped Settings defaults
        # for the same reason shipped_min_ep_gain is (REH-66): a production
        # re-tune must not leave the harness describing a bot nobody deployed.
        profit_take_pct=_shipped_default("min_sell_profit_pct") if with_flips else None,
        loss_cut_pct=_shipped_default("max_loss_pct") if with_flips else None,
        flip_buy_fn=flip_buy_fn,
        # Wired unconditionally: this guards how the bot trades, not which
        # experiment arm is running. `_flip_buys` is the only consumer, so it
        # is a no-op whenever flip_buy_fn is None (REH-71 review of REH-66/68).
        wash_trade_block_seconds=_shipped_default("wash_trade_block_hours") * 3600.0,
    )

    with sqlite3.connect(learning_db_path) as conn:
        actual_rows = conn.execute(
            "SELECT day_number, MAX(total_points), MAX(matchday_points) "
            "FROM league_rank_history WHERE is_self = 1 GROUP BY day_number",
            (),
        ).fetchall()
    actual_per_matchday = {int(r[0]): int(r[2] or 0) for r in actual_rows}
    actual_total = max((int(r[1] or 0) for r in actual_rows), default=0)

    standings = load_standings(learning_db_path, league_id=LEAGUE_ID, exclude_manager_id=MANAGER_ID)
    report = format_report(
        result,
        actual_total=actual_total,
        actual_per_matchday=actual_per_matchday,
        standings=standings,
        min_ep_gain=gain_floor,
        with_competition=with_competition,
        with_flips=with_flips,
        with_flip_buys=with_flip_buys,
    )
    return result, report


def run_buy_control(*, corpus_path: Path, learning_db_path: Path) -> str:
    """REH-67: does the v2 scorer pick better than the market prices?

    Runs the shipped bot, then re-runs it with one thing changed — candidates
    ranked by market value instead of expected points, at the same per-matchday
    trading tempo. Lineup and sell decisions keep using EP in both, so the
    difference isolates the buy side.

    Market value is the sharpest available control because it already embeds
    every manager's opinion of a player. Beating chance is necessary; beating
    the crowd's own pricing is what would justify the scorer.

    Returns a comparison, not a verdict. This is a labelled control run and must
    never be reported as the counterfactual season result.
    """
    reference, _ = run_replay(corpus_path=corpus_path, learning_db_path=learning_db_path)
    quota = buy_quota_from(reference)

    corpus = TrainingCorpus(corpus_path)

    def mv_rank(player_id: str, at: float) -> float:
        return float(corpus.market_value_at(player_id, at) or 0)

    control, _ = run_replay(
        corpus_path=corpus_path,
        learning_db_path=learning_db_path,
        buy_rank_fn=mv_rank,
        buy_quota=quota,
    )

    delta = reference.total_points - control.total_points
    ref_buys = sum(o.buys for o in reference.outcomes)
    ctl_buys = sum(o.buys for o in control.outcomes)
    lines = [
        "=" * 68,
        "BUY-SIDE CONTROL - EP ranking vs market-value ranking",
        "=" * 68,
        "",
        f"EP-ranked (shipped):      {reference.total_points:>8,} points   " f"{ref_buys} buys",
        f"Market-value-ranked:      {control.total_points:>8,} points   {ctl_buys} buys",
        f"EP advantage:             {delta:>+8,} points",
        "",
        "Only the buy chooser differs. Trading tempo is matched per matchday;",
        "lineup and sell decisions use EP in both runs.",
        "",
    ]
    if ctl_buys != ref_buys:
        lines.append(
            f"NOTE: tempo not fully matched ({ref_buys} vs {ctl_buys} buys) - the "
            "control could not always afford its preferred candidate."
        )
        lines.append("")
    lines += [
        "A labelled control, not a season result. Bid competition is still",
        "absent from both runs, so both buy sides remain upper bounds.",
        "=" * 68,
    ]
    return "\n".join(lines)


def run_flip_policy(*, corpus_path: Path, learning_db_path: Path) -> str:
    """REH-71: the 2x2 over flip buys x profit sells.

    Every arm runs with bid competition on, because the whole question is what
    flipping is worth when rivals contest the same listings. Nothing else varies
    between arms.
    """
    from rehoboam.replay.attribution import format_flip_policy

    arms = {}
    for key, flip_buys, profit_sells in (
        ("A", False, False),
        ("B", False, True),
        ("C", True, False),
        ("D", True, True),
    ):
        arms[key], _report = run_replay(
            corpus_path=corpus_path,
            learning_db_path=learning_db_path,
            with_competition=True,
            with_flips=profit_sells,
            with_flip_buys=flip_buys,
        )

    with sqlite3.connect(learning_db_path) as conn:
        actual_total = conn.execute(
            "SELECT MAX(total_points) FROM league_rank_history WHERE is_self = 1"
        ).fetchone()[0]

    return format_flip_policy(arms, actual_total=int(actual_total or 0))


def make_ep_bid_fn(
    *,
    mv_fn: Callable[[str, float], int | None],
    score_fn: Callable[[str, float], float],
    median_move_fn: Callable[[float], int],
    in_season_min_moves: int,
    max_reserve_fraction: float,
    # Defaulted, unlike `max_reserve_fraction` above: omitting it yields the
    # SHIPPED behaviour, not the REH-107 bug, so the "require it so a new call
    # site cannot reintroduce the defect by omission" rule does not apply here.
    # `run_replay` always passes it explicitly from the shipped setting.
    min_spendable_moves: float = 1.0,
    pacing_enabled: bool = True,
) -> Callable[[str, int, float, float, int, int], int]:
    """Bid with the bot's own bidding strategy (REH-68).

    Until now the replay bought at the listed price and never called
    ``SmartBidding`` at all, so the tier thresholds REH-55 re-tuned (70/53/43)
    and the entire overbid stack were unexercised by the harness. Wiring
    ``calculate_ep_bid`` in as the bidder is what makes them matter: a
    must-have candidate now bids high enough to outbid the manager who really
    signed him, while a marginal one does not.

    Tiers are read from the shipped ``Settings`` defaults for the same reason
    ``shipped_min_ep_gain`` is — so a production re-tune cannot leave the
    harness describing a bot nobody deployed — and without instantiating
    ``Settings``, which would require KICKBASE credentials.

    Two inputs the live bot has and the replay does not, both pinned to neutral
    and both making this an UPPER bound on our willingness to pay:
    ``offer_count=0`` (we cannot know how many rivals bid on a given listing)
    and ``trend_change_pct=0.0`` (no market-value trend is fed in). Confidence
    is fixed at 0.8 rather than derived from data quality grading.

    ``median_move_fn`` is called with the decision instant on every bid rather
    than closed over as a constant, so the reserve tracks a trailing window
    the way production does instead of freezing a pre-season number for the
    whole replayed season (REH-85). ``pacing_enabled=False`` passes
    ``pacing=None`` into ``calculate_ep_bid`` rather than a ``PacingContext``
    pinned to ``reserve=0`` — mirroring how production actually represents
    "pacing off" (`Trader._build_pacing_context` returns ``None`` when
    ``settings.pacing_enabled`` is ``False``), and skipping the median-price
    query entirely rather than computing an answer only to zero it out.
    """
    from rehoboam.bidding_strategy import SmartBidding
    from rehoboam.config import Settings

    def _default(name: str) -> float:
        return float(Settings.model_fields[name].default)

    # REH-99: the same ceiling the live bidder and the safety gate use. Built
    # from the shipped defaults, like the tiers above — if the replay bid
    # differently from live it would stop measuring the bot that actually runs.
    ceiling_policy = BidCeilingPolicy(
        floor_eur=int(Settings.model_fields["overbid_floor_eur"].default),
        tier_pcts={
            Tier.MARGINAL: _default("overbid_pct_marginal"),
            Tier.SOLID: _default("overbid_pct_solid"),
            Tier.STRONG: _default("overbid_pct_strong"),
            Tier.MUST_HAVE: _default("overbid_pct_must_have"),
        },
    )

    bidding = SmartBidding(
        tier_must_have=_default("bid_tier_must_have"),
        tier_strong_upgrade=_default("bid_tier_strong_upgrade"),
        tier_solid_upgrade=_default("bid_tier_solid_upgrade"),
        full_commit_gain=_default("bid_full_commit_gain"),
        ceiling_policy=ceiling_policy,
    )

    def bid(
        player_id: str, price: int, at: float, gain: float, budget: int, squad_size: int
    ) -> int:
        # REH-85: the reserve the live bot applies. Without it the harness
        # measures a bidder nobody deploys — the same reason the tiers and the
        # REH-99 ceiling are read from the shipped defaults above.
        pacing_ctx = None
        if pacing_enabled:
            reserve = capital_reserve(
                slots_to_fill=available_squad_slots(squad_size, 0),
                in_season_min_moves=in_season_min_moves,
                median_move=median_move_fn(at),
                budget=budget,
                max_reserve_fraction=max_reserve_fraction,
                min_spendable_moves=min_spendable_moves,
            )
            pacing_ctx = PacingContext(reserve=reserve, open_offers=0)
        rec = bidding.calculate_ep_bid(
            asking_price=price,
            market_value=mv_fn(player_id, at) or price,
            expected_points=score_fn(player_id, at),
            marginal_ep_gain=gain,
            confidence=0.8,
            current_budget=budget,
            sell_plan=None,
            trend_change_pct=0.0,
            pacing=pacing_ctx,
        )
        return int(rec.recommended_bid)

    return bid
