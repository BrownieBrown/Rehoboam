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


def test_get_competition_player_details_returns_payload():
    """Competition-scoped, so it resolves players no longer in our league's
    /lineup/selection view — the only endpoint of the three the v2 corpus
    sweep uses that carries a player's position and real first name."""
    client = _client_with_response(
        200,
        {"i": "10006", "fn": "James", "ln": "Sands", "pos": 3, "tid": "39", "mv": 500_000},
    )
    result = client.get_competition_player_details(player_id="10006")

    assert result == {
        "i": "10006",
        "fn": "James",
        "ln": "Sands",
        "pos": 3,
        "tid": "39",
        "mv": 500_000,
    }
    called_url = client.session.get.call_args[0][0]
    assert called_url.endswith("/v4/competitions/1/players/10006")


def test_get_competition_player_details_raises_on_error():
    """A nonexistent player id returns HTTP 500 with an err/errMsg body, not
    404 — verified by live probe (2026-07-29). Any non-200 must still raise
    so the sweep's per-player try/except can count it as unresolved."""
    client = _client_with_response(500, {"err": 2, "errMsg": "NotFound", "svcs": []})
    with pytest.raises(Exception, match="Failed to fetch competition player details"):
        client.get_competition_player_details(player_id="99999999")


def test_get_player_transfer_history_returns_payload():
    """League-scoped (unlike the other endpoints in this file), but tested
    alongside them since it feeds the same v2 corpus sweep. Verified live
    (2026-07-29): top-level key is ``it``, items carry ``u``/``unm``
    (counterparty), ``dt`` (ISO-8601 timestamp), ``trp`` (price), ``t``
    (transfer type, meaning unconfirmed)."""
    client = _client_with_response(
        200,
        {
            "it": [
                {
                    "u": "1911002",
                    "unm": "Eduard",
                    "dt": "2025-08-22T20:55:39Z",
                    "trp": 6519598,
                    "t": 2,
                }
            ]
        },
    )
    result = client.get_player_transfer_history(league_id="1933872", player_id="10006")

    assert result == {
        "it": [
            {
                "u": "1911002",
                "unm": "Eduard",
                "dt": "2025-08-22T20:55:39Z",
                "trp": 6519598,
                "t": 2,
            }
        ]
    }
    called_url = client.session.get.call_args[0][0]
    assert called_url.endswith("/v4/leagues/1933872/players/10006/transferHistory")


def test_get_player_transfer_history_raises_on_error():
    client = _client_with_response(500, {})
    with pytest.raises(Exception, match="Failed to fetch player transfer history"):
        client.get_player_transfer_history(league_id="1933872", player_id="10006")
