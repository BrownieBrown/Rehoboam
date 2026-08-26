"""The replay must exercise pacing, or the measurement means nothing (REH-85)."""

from __future__ import annotations

import inspect

from rehoboam.replay.driver import make_ep_bid_fn


def test_bid_fn_takes_squad_size():
    """Pacing needs slots-to-fill, which needs the squad size."""
    fn = make_ep_bid_fn(
        mv_fn=lambda pid, at: 10_000_000,
        score_fn=lambda pid, at: 80.0,
        median_move=10_800_000,
        in_season_min_moves=2,
    )
    assert len(inspect.signature(fn).parameters) == 6


def test_a_short_squad_is_capped_below_an_unaffordable_signing():
    """9 players = 6 slots to fill = a EUR 64.8m reserve on EUR 80m."""
    fn = make_ep_bid_fn(
        mv_fn=lambda pid, at: 40_000_000,
        score_fn=lambda pid, at: 80.0,
        median_move=10_800_000,
        in_season_min_moves=2,
    )
    assert fn("p1", 44_000_000, 0.0, 90.0, 80_000_000, 9) == 0


def test_an_affordable_signing_still_goes_through():
    fn = make_ep_bid_fn(
        mv_fn=lambda pid, at: 5_000_000,
        score_fn=lambda pid, at: 80.0,
        median_move=10_800_000,
        in_season_min_moves=2,
    )
    assert fn("p1", 5_000_000, 0.0, 90.0, 80_000_000, 9) >= 5_000_000
