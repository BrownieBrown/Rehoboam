"""The replay must exercise pacing, or the measurement means nothing (REH-85)."""

from __future__ import annotations

import inspect

from rehoboam.replay.driver import _make_median_move_fn, make_ep_bid_fn


def test_bid_fn_takes_squad_size():
    """Pacing needs slots-to-fill, which needs the squad size."""
    fn = make_ep_bid_fn(
        mv_fn=lambda pid, at: 10_000_000,
        score_fn=lambda pid, at: 80.0,
        median_move_fn=lambda at: 10_800_000,
        in_season_min_moves=2,
    )
    assert len(inspect.signature(fn).parameters) == 6


def test_a_short_squad_is_capped_below_an_unaffordable_signing():
    """9 players = 6 slots to fill = 5 moves after this buy = a EUR 54.0m
    reserve on EUR 80m (the slots-minus-one discount: this buy itself fills
    one of the 6 slots, so only 5 remain to be protected for)."""
    fn = make_ep_bid_fn(
        mv_fn=lambda pid, at: 40_000_000,
        score_fn=lambda pid, at: 80.0,
        median_move_fn=lambda at: 10_800_000,
        in_season_min_moves=2,
    )
    assert fn("p1", 44_000_000, 0.0, 90.0, 80_000_000, 9) == 0


def test_an_affordable_signing_still_goes_through():
    fn = make_ep_bid_fn(
        mv_fn=lambda pid, at: 5_000_000,
        score_fn=lambda pid, at: 80.0,
        median_move_fn=lambda at: 10_800_000,
        in_season_min_moves=2,
    )
    assert fn("p1", 5_000_000, 0.0, 90.0, 80_000_000, 9) >= 5_000_000


def test_pacing_enabled_false_bypasses_the_reserve():
    """--no-pacing is the genuinely unpaced code path (pacing=None), not a
    reserve pinned to zero — the same short-squad signing that gets capped to
    0 when paced (see above) must clear when pacing is off."""
    fn = make_ep_bid_fn(
        mv_fn=lambda pid, at: 40_000_000,
        score_fn=lambda pid, at: 80.0,
        median_move_fn=lambda at: 10_800_000,
        in_season_min_moves=2,
        pacing_enabled=False,
    )
    assert fn("p1", 44_000_000, 0.0, 90.0, 80_000_000, 9) >= 44_000_000


def test_median_move_fn_is_called_with_the_decision_instant():
    """The reserve must be able to move matchday to matchday, not freeze at a
    single pre-season constant for the whole season (review finding 2)."""
    seen: list[float] = []

    def median_move_fn(at: float) -> int:
        seen.append(at)
        return 10_800_000

    fn = make_ep_bid_fn(
        mv_fn=lambda pid, at: 40_000_000,
        score_fn=lambda pid, at: 80.0,
        median_move_fn=median_move_fn,
        in_season_min_moves=2,
    )
    fn("p1", 44_000_000, 123.0, 90.0, 80_000_000, 9)

    assert seen == [123.0]


class _FakeCorpus:
    """A stand-in for TrainingCorpus that just records the query window."""

    def __init__(self, rows: list[dict]):
        self._rows = rows
        self.calls: list[tuple[float, float]] = []

    def transfers_between(self, lo: float, hi: float) -> list[dict]:
        self.calls.append((lo, hi))
        return self._rows


def test_make_median_move_fn_windows_and_memoises_by_at():
    """One query per distinct decision instant, bounded at (at - window, at) —
    never past `at`, which is already the leak boundary upstream."""
    corpus = _FakeCorpus([{"price": 5_000_000}, {"price": 15_000_000}, {"price": 10_000_000}])
    median_move_at = _make_median_move_fn(corpus, window_days=7, floor_eur=1_000_000)

    first = median_move_at(1_000_000.0)
    second = median_move_at(1_000_000.0)  # same `at` -> memoised, no new query
    third = median_move_at(2_000_000.0)  # different `at` -> a new query

    assert first == 10_000_000  # median of [5m, 10m, 15m]
    assert second == 10_000_000
    assert third == 10_000_000
    assert corpus.calls == [
        (1_000_000.0 - 7 * 86400.0, 1_000_000.0),
        (2_000_000.0 - 7 * 86400.0, 2_000_000.0),
    ]


def test_make_median_move_fn_applies_the_floor_on_an_empty_window():
    corpus = _FakeCorpus([])
    median_move_at = _make_median_move_fn(corpus, window_days=7, floor_eur=3_000_000)

    assert median_move_at(1_000_000.0) == 3_000_000
