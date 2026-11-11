"""The bar and the rolling freeze (ADR-0007): pure parts of `sim/backtest`."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from rehoboam.sim import backtest
from rehoboam.sim.backtest import EnsembleReport, Window
from rehoboam.sim.history import Round

UTC = timezone.utc


class _World:
    """Only what `window_rounds` and `holdout_starts` read."""

    def __init__(self, kickoffs):
        self.rounds = tuple(Round(i + 10, k) for i, k in enumerate(kickoffs))


MD10 = datetime(2026, 11, 20, 19, 30, tzinfo=UTC)
MD11 = datetime(2026, 11, 27, 19, 30, tzinfo=UTC)


def test_only_finished_rounds_inside_the_window_count():
    world = _World([MD10, MD11])

    # MD11 kicked off but is not over four days later: it does not count yet.
    rounds = backtest.window_rounds(
        world, Window(MD10 - timedelta(days=9), MD11 + timedelta(days=2))
    )

    assert [r.day_number for r in rounds] == [10]


def test_holdout_paths_run_from_the_freeze_to_the_first_round_after_it():
    frozen = datetime(2026, 11, 11, 18, 5, tzinfo=UTC)

    starts = backtest.holdout_starts(_World([MD10]), frozen)

    assert starts[0] == datetime(2026, 11, 11, 20, 0, tzinfo=UTC)  # the first session after it
    assert all(b - a == backtest.PATH_SPACING for a, b in zip(starts, starts[1:], strict=False))
    assert starts[-1] < MD10 <= starts[-1] + backtest.PATH_SPACING


def test_no_holdout_before_a_round_after_the_freeze_has_finished():
    frozen = datetime(2026, 11, 11, 18, 5, tzinfo=UTC)

    result = backtest.holdout(
        _World([MD10]), None, None, frozen_at=frozen, now=MD10 + timedelta(days=1)
    )

    assert result is None


def _report(money_vs_real, points_vs_real, money=0, points=0.0, pre_reset=True, violations=0):
    return EnsembleReport(
        name="x",
        matchdays=(10,),
        money_excess=(money,),
        points_excess=(points,),
        violations=violations,
        money_vs_real=(money_vs_real,),
        points_vs_real=(points_vs_real,),
        pre_reset=pre_reset,
    )


def test_before_the_reset_the_bar_is_our_own_record_on_both_axes():
    assert _report(+1, +1.0, money=-50, points=-200.0).passes
    assert not _report(+22_000_000, -27.0).passes  # more money, fewer points: not enough


def test_from_the_reset_the_bar_is_the_rivals_median():
    assert _report(-1, -1.0, money=+1, points=+1.0, pre_reset=False).passes
    assert not _report(+1, +1.0, money=+1, points=-1.0, pre_reset=False).passes


def test_a_violation_fails_the_bar_whatever_the_numbers():
    assert not _report(+1, +1.0, violations=1).passes
