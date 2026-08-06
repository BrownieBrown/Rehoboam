"""REH-71: the flip verdict must be honoured from .env, without a deploy."""

from __future__ import annotations

import inspect

from rehoboam.auto_trader import AutoTrader
from rehoboam.config import Settings


def test_both_switches_exist():
    assert "enable_flip_buys" in Settings.model_fields
    assert "enable_profit_sells" in Settings.model_fields


def test_the_flip_buy_block_is_gated_on_its_switch():
    source = inspect.getsource(AutoTrader.run_unified_trade_phase)

    assert "enable_flip_buys" in source


def test_the_profit_sell_phase_is_gated_on_its_switch():
    source = inspect.getsource(AutoTrader.run_profit_sell_phase)

    assert "enable_profit_sells" in source
