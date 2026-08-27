"""Findings 3 and 5 from the final whole-branch review (REH-85).

Finding 3: pacing must not apply during a formation emergency. The reserve
scales with empty slots but has no relation to affordability -- at the live
median it demands 10.8m per unfilled slot, so an under-strength squad (the
state that costs -100 pts/empty lineup slot at kickoff) is exactly the state
where pacing is most likely to zero every candidate. The emergency-fill
fallback (`_run_emergency_squad_fill`) only helps 2-5 days before kickoff; a
paced-to-zero recommendation is dropped from the candidate list entirely
outside that window, so this has to be fixed at the source: don't build a
pacing context at all while the squad can't field a legal eleven.

Finding 5: `_build_pacing_context`'s docstring promises it returns None
rather than raising, but the call to it at `trader.py`'s call site sat
outside any try -- so a malformed bid payload (Kickbase API contract
drift, corrupted cache, etc.) would abort the whole EP pipeline instead of
just disabling pacing. Best-effort learning: a learning-side failure must
never block the pipeline.

Both drive the real `Trader.get_ep_recommendations` end to end (rather than
`_build_pacing_context` in isolation, which is already covered by
`test_pacing_session.py`) because the defect in both cases lives in the
surrounding call site, not in `_build_pacing_context` itself.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from rehoboam.config import Settings
from rehoboam.trader import Trader


def _trader_with_mock_api(tmp_path, monkeypatch, *, recent_buy_prices=None):
    """Mirrors `test_pacing_session.py`'s `trader` fixture, but chdir's BEFORE
    constructing `Trader` -- `Trader.__init__` eagerly opens a real
    `ValueHistoryCache` sqlite file under `./logs/`, and constructing it
    before the chdir would create that file in the repo root instead of
    `tmp_path`.
    """
    monkeypatch.setenv("KICKBASE_EMAIL", "test@example.com")
    monkeypatch.setenv("KICKBASE_PASSWORD", "test")
    monkeypatch.chdir(tmp_path)
    api = MagicMock()
    learner = MagicMock()
    learner.recent_buy_prices.return_value = (
        recent_buy_prices if recent_buy_prices is not None else [10_800_000] * 9
    )
    trader = Trader(api=api, settings=Settings(), bid_learner=learner)
    return api, trader


def _squad_player(pid: str, position: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=pid,
        position=position,
        first_name="P",
        last_name=pid,
        team_id=f"club-{pid}",
        price=1_000_000,
        market_value=1_000_000,
    )


# 11 players meeting every FormationRequirements minimum (1 GK, 5 DEF, 4 MID,
# 1 FW) -- a normal, fieldable squad.
_FIELDABLE_SQUAD = [
    _squad_player("g1", "Goalkeeper"),
    *[_squad_player(f"d{i}", "Defender") for i in range(1, 6)],
    *[_squad_player(f"m{i}", "Midfielder") for i in range(1, 5)],
    _squad_player("f1", "Forward"),
]

# 9 Defenders: below the 11-player floor AND missing GK/MID/FW entirely --
# cannot field a legal eleven under any formation.
_EMERGENCY_SQUAD = [_squad_player(f"d{i}", "Defender") for i in range(1, 10)]


def _quiet_api(api) -> None:
    """Best-effort side calls the pipeline makes that this test doesn't care
    about -- raising lets their own try/except blocks short-circuit them
    cleanly instead of returning MagicMocks that real downstream code (e.g.
    MatchupAnalyzer) would choke on trying to interpret as real payloads.
    """
    api.get_market.return_value = []
    api.get_team_info.return_value = {"budget": 20_000_000, "team_value": 20_000_000}
    api.get_league_ranking.side_effect = RuntimeError("no network in this test")
    api.get_competition_matchdays.side_effect = RuntimeError("no network in this test")


class TestEmergencyExemption:
    """Finding 3."""

    def test_emergency_squad_gets_no_pacing_context(self, tmp_path, monkeypatch):
        api, trader = _trader_with_mock_api(tmp_path, monkeypatch)
        _quiet_api(api)
        api.get_squad.return_value = list(_EMERGENCY_SQUAD)
        api.get_my_bids.return_value = []

        result = trader.get_ep_recommendations(league=SimpleNamespace(id="L"))

        assert result["pacing"] is None

    def test_normal_squad_gets_a_pacing_context(self, tmp_path, monkeypatch):
        api, trader = _trader_with_mock_api(tmp_path, monkeypatch)
        _quiet_api(api)
        api.get_squad.return_value = list(_FIELDABLE_SQUAD)
        api.get_my_bids.return_value = []

        result = trader.get_ep_recommendations(league=SimpleNamespace(id="L"))

        assert result["pacing"] is not None


class TestFailOpenGuardCoversTheWholeBlock:
    """Finding 5."""

    def test_a_malformed_bid_payload_disables_pacing_rather_than_raising(
        self, tmp_path, monkeypatch
    ):
        """`user_offer_price="not-a-number"` makes `_build_pacing_context`'s
        `open_offers = sum(int(getattr(b, "user_offer_price", 0) or 0) ...)`
        line raise a ValueError. On a normal (non-emergency) squad, that call
        must be swallowed by the same try/except that already guards
        `api.get_my_bids` -- the whole EP pipeline must still complete and
        return a dict with pacing disabled, not propagate the exception.
        """
        api, trader = _trader_with_mock_api(tmp_path, monkeypatch)
        _quiet_api(api)
        api.get_squad.return_value = list(_FIELDABLE_SQUAD)
        api.get_my_bids.return_value = [SimpleNamespace(user_offer_price="not-a-number")]

        result = trader.get_ep_recommendations(league=SimpleNamespace(id="L"))

        assert result["pacing"] is None
