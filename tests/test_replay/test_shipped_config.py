"""REH-66: the replay must run the bot that was actually shipped.

REH-51's result was produced with the engine's own ``min_ep_gain=5.0`` default
because ``run_replay`` never passed the argument. REH-55 shipped a marginal-gain
floor of 40.0. A replay gated at 5.0 describes a strictly more trigger-happy
agent than the one on main, so its headline number answers a question nobody
asked.
"""

from __future__ import annotations

import inspect

from rehoboam.config import Settings
from rehoboam.replay import driver
from rehoboam.replay.attribution import format_report
from rehoboam.replay.engine import SeasonResult


def test_replay_gain_floor_is_read_from_the_shipped_settings():
    """Not hard-coded. If production re-tunes the threshold and the replay does
    not follow, the harness silently drifts back out of sync — which is the
    exact defect REH-66 exists to close.
    """
    shipped = Settings.model_fields["min_ep_upgrade_threshold"].default

    assert driver.shipped_min_ep_gain() == shipped


def test_replay_gain_floor_is_not_the_engine_default():
    """The regression guard proper: 5.0 is the engine's own parameter default,
    and it is what REH-51 unknowingly ran with."""
    assert driver.shipped_min_ep_gain() != 5.0


def test_run_replay_passes_the_gain_floor_to_the_engine():
    """A resolved floor that never reaches run_season changes nothing."""
    source = inspect.getsource(driver.run_replay)

    assert "min_ep_gain=" in source


def test_run_replay_accepts_an_explicit_gain_floor():
    """So a run is reproducible and can be pinned from the CLI."""
    assert "min_ep_gain" in inspect.signature(driver.run_replay).parameters


def test_report_states_the_gain_floor_it_used():
    """A headline that depends on a threshold has to print that threshold,
    or the number cannot be interpreted six months from now."""
    report = format_report(
        SeasonResult(),
        actual_total=0,
        actual_per_matchday={},
        standings=[],
        min_ep_gain=40.0,
    )

    assert "40" in report
    assert "gain" in report.lower()


def test_the_flip_switches_preserve_the_shipped_behaviour():
    """Both default True — the behaviour that ran before these switches existed.

    They are a control surface, not a verdict. REH-71 briefly set them False on
    the argument that flipping is structurally unprofitable; that argument was
    withdrawn once the toll it rested on turned out to have been measured on
    manager-to-manager transfers rather than the Kickbase listings the bot
    actually flips (REH-76). Whether to switch either off is REH-72/REH-75 work,
    and until then this asserts the switches change nothing by default.
    """
    assert Settings.model_fields["enable_flip_buys"].default is True
    assert Settings.model_fields["enable_profit_sells"].default is True
