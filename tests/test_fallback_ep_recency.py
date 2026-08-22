"""REH-85 fix round 1: the lineup fallback path must obey the same
availability recency bound as the pipeline's own ``score_player_v2`` calls.

``AutoTrader._fallback_expected_points`` only fires for a mid-session
purchase (or an upstream pipeline failure) -- the highest-stakes case, since
a player who lands here just got bought for real money. Its result is merged
into the same ``ep_scores`` dict the pipeline's bounded scores populate and
ranked together by ``select_best_eleven``. If this path ignores
``max_status_age_days`` while the pipeline honours it, a freshly bought
player can still be scored off a stale end-of-last-season status and get
benched despite the bug being "fixed" everywhere else.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from rehoboam.auto_trader import AutoTrader
from rehoboam.config import Settings
from rehoboam.scoring.v2.adapter import compose_ep
from rehoboam.scoring.v2.coefficients import load_coefficients

# Three months before any plausible test run date -- well past even a
# generous max_status_age_days, so this fixture doesn't rot with the clock.
STALE_MATCH_DATE = "2024-01-01T12:00:00Z"
STALE_STATUS = 5  # started -- the strongest possible signal, chosen so an
# unbounded read and a bounded read disagree as loudly as possible.


def _perf(match_date: str, status: int) -> dict:
    return {
        "it": [
            {
                "ti": "2023/2024",
                "ph": [{"day": 1, "md": match_date, "st": status}],
            }
        ]
    }


@pytest.fixture
def settings(monkeypatch) -> Settings:
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "test")
    return Settings()


@pytest.fixture
def trader(tmp_path, settings, monkeypatch) -> AutoTrader:
    monkeypatch.chdir(tmp_path)  # ValueHistoryCache writes to ./logs -- isolate it
    api = MagicMock()
    api.client.get_player_performance.return_value = _perf(STALE_MATCH_DATE, STALE_STATUS)
    return AutoTrader(api=api, settings=settings, dry_run=True)


def test_fallback_ep_discards_stale_status(trader):
    """Pins the REH-85 fix: without max_age_days threaded through, this
    method returns the same value ``compose_ep`` would give for the stale
    STALE_STATUS -- which is what this test would observe if the kwarg
    threading regressed.
    """
    league = SimpleNamespace(id="1933872")
    player = SimpleNamespace(id="6080", position="Midfielder")

    assert trader.settings.max_status_age_days == 60.0, (
        "fixture assumes the shipped default; update STALE_MATCH_DATE's margin " "if this changes"
    )

    result = trader._fallback_expected_points(league, player)

    availability, rate, _meta = load_coefficients()
    bounded_expected = compose_ep(str(player.id), None, player.position, availability, rate)
    unbounded_regression = compose_ep(
        str(player.id), STALE_STATUS, player.position, availability, rate
    )

    # The two must differ, or this test can't tell a fixed path from a
    # regressed one.
    assert bounded_expected != unbounded_regression

    assert result == bounded_expected
    assert result != unbounded_regression
