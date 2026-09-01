"""A steeply falling market value is not proposed at all (REH-117).

Marco: "the players the bot recommended were mostly garbage and had a falling
mv trend." Measured over 21 proposals, four were badly falling when sent:

    Trimmel  -20.1%    Ndiaye   -19.5%
    Itten    -27.0%    Stalmach -32.1%

The bot printed the number and proposed anyway — Itten went out at -27.0%/7d
with a data-quality-C warning attached.

Two thresholds, deliberately different. Below `FALLING_TREND_PCT` (-10%) the
overview flags it and still asks, because a mild slide on a good player is a
judgement call and Marco should get to make it. Below
`max_falling_trend_pct_to_buy` (-20%) it is not proposed at all: the squad
buys hold for points across a season, and a market value in free-fall is the
league's verdict on availability arriving before ours does.
"""

from __future__ import annotations

import pytest

from rehoboam.auto_trader import _is_too_falling_to_propose
from rehoboam.config import Settings

SETTINGS = Settings(kickbase_email="test@example.com", kickbase_password="x")


@pytest.mark.parametrize(
    ("name", "trend"),
    [("Itten", -27.0), ("Stalmach", -32.1), ("Trimmel", -20.1), ("steep", -99.0)],
)
def test_a_steeply_falling_player_is_blocked(name, trend):
    assert _is_too_falling_to_propose(trend, SETTINGS) is True


@pytest.mark.parametrize("trend", [-19.5, -10.0, -3.0, 0.0, 12.0, 121.8])
def test_everything_shallower_is_still_proposed(trend):
    """-19.5% (Ndiaye) is flagged in the message, not blocked."""
    assert _is_too_falling_to_propose(trend, SETTINGS) is False


def test_an_unknown_trend_does_not_block():
    """Most candidates have no MV history at all — absence is not evidence."""
    assert _is_too_falling_to_propose(None, SETTINGS) is False


def test_the_threshold_is_configurable():
    tighter = Settings(
        kickbase_email="t@e.com", kickbase_password="x", max_falling_trend_pct_to_buy=-5.0
    )

    assert _is_too_falling_to_propose(-6.0, tighter) is True
    assert _is_too_falling_to_propose(-6.0, SETTINGS) is False
