"""Tests for competition-level client endpoints used by the v2 corpus sweep."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from rehoboam.kickbase_client import KickbaseV4Client


def _client_with_response(status_code: int, payload: dict) -> KickbaseV4Client:
    client = KickbaseV4Client()
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload
    response.text = "error body"
    client.session = MagicMock()
    client.session.get.return_value = response
    return client


def test_get_competition_player_performance_returns_payload():
    client = _client_with_response(200, {"it": [{"ti": "2025/2026", "ph": []}]})
    result = client.get_competition_player_performance(player_id="42")

    assert result == {"it": [{"ti": "2025/2026", "ph": []}]}
    called_url = client.session.get.call_args[0][0]
    assert called_url.endswith("/v4/competitions/1/players/42/performance")


def test_get_competition_player_performance_raises_on_error():
    client = _client_with_response(404, {})
    with pytest.raises(Exception, match="Failed to fetch competition player performance"):
        client.get_competition_player_performance(player_id="42")


def test_get_competition_table_returns_payload():
    client = _client_with_response(200, {"it": [{"tid": "2", "pl": 1}]})
    result = client.get_competition_table()

    assert result == {"it": [{"tid": "2", "pl": 1}]}
    assert client.session.get.call_args[0][0].endswith("/v4/competitions/1/table")


def test_get_lineup_selection_sends_position_and_paging_params():
    """`position` is mandatory — without it the live endpoint returns zero
    items, which looks like an empty league rather than a bad request."""
    client = _client_with_response(200, {"it": [{"pi": "3019", "n": "Atubolu"}]})
    result = client.get_lineup_selection(league_id="1933872", position=1, start=50)

    assert result == {"it": [{"pi": "3019", "n": "Atubolu"}]}
    called_url = client.session.get.call_args[0][0]
    assert called_url.endswith("/v4/leagues/1933872/lineup/selection")

    params = client.session.get.call_args[1]["params"]
    assert params["position"] == 1
    assert params["start"] == 50


def test_get_lineup_selection_raises_on_error():
    client = _client_with_response(500, {})
    with pytest.raises(Exception, match="Failed to fetch lineup selection"):
        client.get_lineup_selection(league_id="1933872", position=1)
