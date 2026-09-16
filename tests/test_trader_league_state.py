"""The session keeps what it fetched so the store can have it without a second call (G1 Task 4)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from rehoboam.config import Settings
from rehoboam.kickbase_client import KickbaseV4Client, MarketPlayer
from rehoboam.trader import Trader

LEAGUE = SimpleNamespace(id="L", name="T")
RAW_MARKET = {
    "it": [
        {
            "i": "11",
            "tid": "7",
            "pos": 3,
            "prc": 1,
            "mv": 1,
            "exs": 100,
            "dt": "2026-09-15T16:05:06Z",
        }
    ]
}
RANKING = {"us": [{"i": "me", "n": "Marco"}, {"i": "m2", "n": "Rival"}], "day": 4}
SQUAD_M2 = {"it": [{"pi": "11", "mv": 1, "mvgl": 0, "iotm": True}]}


def test_client_get_market_keeps_the_raw_payload():
    client = KickbaseV4Client()
    response = SimpleNamespace(status_code=200, json=lambda: RAW_MARKET, text="")
    with patch.object(client.session, "get", return_value=response):
        players = client.get_market("L")
    assert [p.id for p in players] == ["11"] and client.last_market_payload == RAW_MARKET


class _Api:
    user = SimpleNamespace(id="me")

    def __init__(self):
        self.client = SimpleNamespace()
        self.last_market_payload = None
        self.calls = []

    def get_market(self, league):
        self.calls.append("market")
        self.last_market_payload = RAW_MARKET
        return [MarketPlayer.from_dict(i) for i in RAW_MARKET["it"]]

    def get_league_ranking(self, league):
        self.calls.append("ranking")
        return RANKING

    def get_manager_squad(self, league, manager_id):
        self.calls.append(f"squad:{manager_id}")
        return SQUAD_M2

    def get_squad(self, league):
        return []

    def get_team_info(self, league):
        return {"budget": 1_000_000, "team_value": 1}

    def get_my_bids(self, league):
        return []

    def get_competition_matchdays(self, competition_id="1"):
        return {}

    def get_starting_eleven(self, league):
        return {"lp": [], "nlp": []}


def test_trader_exposes_market_payload_ranking_and_competitor_squads():
    api = _Api()
    trader = Trader(api, Settings(kickbase_email="t@e.com", kickbase_password="x"))
    # `_fetch_player_data` is a closure inside `get_ep_recommendations`, not a
    # patchable attribute -- but every call it makes on `api.client` (a bare
    # SimpleNamespace here) is already wrapped in its own try/except, and the
    # scoring loop that calls it catches per-player failures too. So the fake
    # API above is enough for the pipeline to run to completion untouched.
    result = trader.get_ep_recommendations(LEAGUE)
    assert result["market_payload"] == RAW_MARKET
    assert result["ranking_payload"] == RANKING
    assert result["competitor_squads"] == {"m2": SQUAD_M2["it"]}
    assert api.calls.count("market") == 1 and "squad:me" not in api.calls
