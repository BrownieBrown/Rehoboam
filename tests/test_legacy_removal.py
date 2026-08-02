"""The legacy scoring path is gone and its fallback is served by v2."""

from __future__ import annotations

import importlib

import pytest


def test_legacy_modules_are_deleted():
    for name in ("rehoboam.expected_points", "rehoboam.value_calculator"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(name)


def test_auto_trader_has_no_legacy_fallback():
    import inspect

    from rehoboam import auto_trader

    source = inspect.getsource(auto_trader)
    assert "_legacy_expected_points" not in source
    assert "calculate_expected_points" not in source


def test_a_player_missing_from_the_pipeline_still_gets_scored():
    """The fallback's job: an unscored player must not silently vanish from
    lineup selection — that is the -100 empty-slot failure mode."""
    from rehoboam.scoring.v2.adapter import compose_ep
    from rehoboam.scoring.v2.coefficients import load_coefficients

    availability, rate, _ = load_coefficients()
    ep = compose_ep("never-seen-player", None, "Midfielder", availability, rate)
    assert ep > 0.0, "cold-start fallback must produce a usable score"
