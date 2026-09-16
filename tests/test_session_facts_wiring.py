"""Session facts, the integrity check, and the alert are wired into every
exit of `run_full_session` (spec 2026-09-15 PR D, Task 5).

Reuses `test_trading_mode.py`'s `_Api`/`_legal_squad` pattern rather than
importing it, because this file extends `_Api` with the endpoints
`Trader.next_kickoff` needs (`get_starting_eleven`, `get_competition_matchdays`)
plus `set_lineup`, which the shared `_Api` does not carry.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from rehoboam.auto_trader import AutoTrader, EPSessionContext, MatchdayPhase
from rehoboam.config import Settings
from rehoboam.kickbase_client import Player
from rehoboam.kickoff import NextKickoff
from rehoboam.learning.tracker import CostBasisReconciliation
from rehoboam.services.session_facts import SessionFacts
from rehoboam.store.calibration_store import CalibrationStore
from rehoboam.store.corpus_store import CorpusStore
from rehoboam.store.session_store import SessionStore

LEAGUE = SimpleNamespace(id="1933872", name="PUMARUDEL")


def _player(pid: str, position: str) -> Player:
    return Player(
        id=pid,
        first_name="F",
        last_name=f"P{pid}",
        position=position,
        team_id="1",
        team_name="T",
        market_value=1_000_000,
        points=0,
        average_points=50.0,
    )


def _legal_squad() -> list[Player]:
    return (
        [_player("gk", "Goalkeeper")]
        + [_player(f"d{i}", "Defender") for i in range(4)]
        + [_player(f"m{i}", "Midfielder") for i in range(4)]
        + [_player(f"f{i}", "Forward") for i in range(2)]
    )


class _Api:
    user = SimpleNamespace(id="3616202")
    # `Trader.__init__` stores `api.client` on its `TrendService` without
    # calling anything on it during context-building -- just needs to exist.
    client = SimpleNamespace()

    def __init__(self, squad):
        self._squad = squad
        self.lineups: list[tuple[str, list[str]]] = []

    def get_squad(self, league):
        return list(self._squad)

    def get_my_bids(self, league):
        return []

    def get_team_info(self, league):
        return {"budget": 5_868_658, "team_value": 149_641_186}

    def get_starting_eleven(self, league):
        soon = (
            (datetime.now(tz=timezone.utc) + timedelta(days=3)).isoformat().replace("+00:00", "Z")
        )
        return {"lp": [{"md": soon}], "nlp": []}

    def get_competition_matchdays(self, competition_id: str = "1"):
        return {}

    def set_lineup(self, league, formation, player_ids):
        self.lineups.append((formation, list(player_ids)))
        return {}


def _trader(store_dsn, squad, *, mode="lineup_only", dry_run=True, telegram=False):
    settings = Settings(
        kickbase_email="t@e.com",
        kickbase_password="x",
        trading_mode=mode,
        telegram_bot_token="tok" if telegram else "",
        telegram_chat_id="1" if telegram else "",
    )
    return AutoTrader(
        api=_Api(squad),
        settings=settings,
        dry_run=dry_run,
        app_name="cli",
        session_store=SessionStore(dsn=store_dsn),
    )


@pytest.fixture
def ctx_factory():
    """An `EPSessionContext` built the way `test_trading_mode.py`'s `_context` is."""

    def _make(squad=None, phase="moderate", days=4) -> EPSessionContext:
        squad = squad if squad is not None else _legal_squad()
        return EPSessionContext(
            ep_result={"squad_scores": [], "lineup_map": {}, "market_players": {}},
            matchday_phase=MatchdayPhase(
                days_until_match=days,
                phase=phase,
                max_trades=10,
                allow_flips=False,
                reason="t",
            ),
            my_bids=[],
            my_bid_amounts={},
            squad=list(squad),
            current_budget=5_868_658,
            team_value=149_641_186,
            flip_budget=0,
        )

    return _make


def test_a_session_leaves_a_facts_row_with_its_exit(store_dsn, ctx_factory):
    trader = _trader(store_dsn, _legal_squad())
    with patch.object(AutoTrader, "_build_session_context", return_value=ctx_factory()):
        session = trader.run_full_session(LEAGUE)
    row = SessionStore(dsn=store_dsn).facts(session.session_id)
    assert row["app"] == "cli" and row["mode"] == "lineup_only" and row["dry_run"] == 1
    assert row["lineup_result"] == "dry_run" and row["duration_s"] >= 0
    assert row["errors"] == 0


def test_context_facts_come_from_the_real_context_builder(store_dsn):
    trader = _trader(store_dsn, _legal_squad())
    with patch(
        "rehoboam.trader.Trader.get_ep_recommendations_with_trends",
        return_value={"squad_scores": [], "lineup_map": {}, "market_players": {}},
    ):
        ctx = trader._build_session_context(LEAGUE)
    f = trader._facts
    assert (f.squad_gk, f.squad_def, f.squad_mid, f.squad_fw) == (1, 4, 4, 2)
    assert f.next_kickoff_source == "myeleven" and f.phase == "moderate"
    assert f.budget == 5_868_658 and f.open_offers_total == 0 and f.sellable_value == 11_000_000
    assert ctx.session_refusal is None


def test_full_mode_i3_failure_sets_the_session_refusal(store_dsn):
    trader = _trader(store_dsn, _legal_squad(), mode="full")
    trader.api.get_team_info = lambda league: {"budget": -50_000_000, "team_value": 1}
    with patch(
        "rehoboam.trader.Trader.get_ep_recommendations_with_trends",
        return_value={"squad_scores": [], "lineup_map": {}, "market_players": {}},
    ):
        ctx = trader._build_session_context(LEAGUE)
    assert ctx.session_refusal.startswith("I3")


def test_lineup_only_i3_failure_only_alerts(store_dsn):
    trader = _trader(store_dsn, _legal_squad(), mode="lineup_only")
    trader.api.get_team_info = lambda league: {"budget": -50_000_000, "team_value": 1}
    with patch(
        "rehoboam.trader.Trader.get_ep_recommendations_with_trends",
        return_value={"squad_scores": [], "lineup_map": {}, "market_players": {}},
    ):
        ctx = trader._build_session_context(LEAGUE)
    assert ctx.session_refusal is None


def test_failures_are_recorded_and_sent_once(store_dsn, ctx_factory):
    # dry_run=False -- a dry run stays quiet on Telegram (Task 2 below), and
    # this test is specifically about the alert being sent.
    trader = _trader(store_dsn, _legal_squad(), telegram=True, dry_run=False)
    with (
        patch.object(AutoTrader, "_build_session_context", return_value=ctx_factory()),
        patch("rehoboam.auto_trader.send_message", return_value=True) as send,
    ):
        session = trader.run_full_session(LEAGUE)
    rules = [f["rule"] for f in SessionStore(dsn=store_dsn).failures(session.session_id)]
    assert "I7" in rules  # no ingest row exists in a fresh database
    assert send.call_count == 1 and "I7" in send.call_args.args[2]
    assert [f.rule for f in session.integrity_failures] == rules


def test_no_failures_means_no_message(store_dsn, ctx_factory):
    store = SessionStore(dsn=store_dsn)
    store.record(
        SessionFacts(
            session_id="i",
            app="external",
            mode="ingest",
            started_at=time.time() - 60,
            duration_s=30,
        )
    )
    trader = _trader(store_dsn, _legal_squad(), telegram=True)
    # Everything I1-I6 read comes from `_facts_from_context`, which the real
    # `_build_session_context` calls but a patched one does not -- so the
    # patch's side_effect calls it itself with a kickoff 3 days out (outside
    # the 48h I6 window). Cost basis and predictions are mocked directly
    # rather than seeded through the real tables: `reconcile_squad_cost_basis`
    # would otherwise report all 11 fresh-fixture players as missing a basis
    # (I4), and `_write_league_predictions`'s return value -- the league
    # write, not the legacy `snapshot_predictions` -- is what
    # `_facts.predictions_written` ends up holding, since step 2a runs after
    # the patched builder and overwrites it.
    trader.tracker.reconcile_squad_cost_basis = lambda *a, **k: CostBasisReconciliation(
        recovered=0, still_missing=[]
    )
    trader._write_league_predictions = lambda *a, **k: 1

    nk = NextKickoff(
        at=datetime.now(tz=timezone.utc) + timedelta(days=3),
        source="schedule",
        cross_check=None,
        matchday_in_progress=False,
    )

    def _build(league):
        ctx = ctx_factory()
        trader._facts_from_context(ctx, nk)
        return ctx

    with (
        patch.object(AutoTrader, "_build_session_context", side_effect=_build),
        patch("rehoboam.auto_trader.send_message") as send,
    ):
        session = trader.run_full_session(LEAGUE)
    assert session.integrity_failures == [] and send.call_count == 0


def test_check_integrity_still_runs_when_the_store_is_unreachable(store_dsn, ctx_factory):
    """A store outage must not look like a clean bill of health: I7 has to
    fail (on "never", since the read is what failed) rather than the whole
    check being skipped because it shared a try with the failing read."""
    trader = _trader(store_dsn, _legal_squad())
    with (
        patch.object(AutoTrader, "_build_session_context", return_value=ctx_factory()),
        patch.object(SessionStore, "last_ingest_completed_at", side_effect=RuntimeError("down")),
    ):
        session = trader.run_full_session(LEAGUE)
    i7 = next(f for f in session.integrity_failures if f.rule == "I7")
    assert "store unreachable" in i7.detail
    # record_failures itself did not raise (only last_ingest_completed_at
    # did), so the failure still made it to the store, not just memory.
    rows = SessionStore(dsn=store_dsn).failures(session.session_id)
    assert any(r["rule"] == "I7" and "store unreachable" in r["detail"] for r in rows)


def test_dry_run_does_not_page_telegram(store_dsn, ctx_factory):
    trader = _trader(store_dsn, _legal_squad(), telegram=True, dry_run=True)
    with (
        patch.object(AutoTrader, "_build_session_context", return_value=ctx_factory()),
        patch("rehoboam.auto_trader.send_message", return_value=True) as send,
    ):
        session = trader.run_full_session(LEAGUE)
    assert session.integrity_failures  # I7 fails on a fresh database
    assert send.call_count == 0


def test_non_dry_run_pages_telegram_once(store_dsn, ctx_factory):
    trader = _trader(store_dsn, _legal_squad(), telegram=True, dry_run=False)
    with (
        patch.object(AutoTrader, "_build_session_context", return_value=ctx_factory()),
        patch("rehoboam.auto_trader.send_message", return_value=True) as send,
    ):
        session = trader.run_full_session(LEAGUE)
    assert session.integrity_failures
    assert send.call_count == 1


def test_finish_facts_refreshes_budget_from_a_fresh_team_info_call(store_dsn):
    """I3's pre-flight refusal (in `_facts_from_context`) reads budget as it
    stood before the session spent anything -- deliberately. The end-of-
    session I3 report is not a gate, and must reflect what the session
    actually left behind, so `_finish_facts` re-reads it."""
    trader = _trader(store_dsn, _legal_squad(), mode="full")
    with patch(
        "rehoboam.trader.Trader.get_ep_recommendations_with_trends",
        return_value={"squad_scores": [], "lineup_map": {}, "market_players": {}},
    ):
        ctx = trader._build_session_context(LEAGUE)
    assert trader._facts.budget == 5_868_658
    trader.api.get_team_info = lambda league: {"budget": 1_234_567, "team_value": 1}
    trader._finish_facts([], time.time(), ctx.matchday_phase.phase, LEAGUE)
    assert trader._facts.budget == 1_234_567
    row = SessionStore(dsn=store_dsn).facts(trader._session_batch_id)
    assert row["budget"] == 1_234_567


def test_pipeline_failure_exit_still_records_and_logs_session_end(store_dsn, caplog):
    trader = _trader(store_dsn, _legal_squad())
    with (
        patch.object(AutoTrader, "_build_session_context", side_effect=RuntimeError("boom")),
        caplog.at_level(logging.INFO, logger="rehoboam.auto_trader"),
    ):
        session = trader.run_full_session(LEAGUE)
    row = SessionStore(dsn=store_dsn).facts(session.session_id)
    assert row["errors"] >= 1 and "boom" in row["error_text"]
    assert any(m.startswith("session-end") for m in caplog.messages)


def _seed_league(dsn, *, fetched_at):
    """Three scorable players (one owned, one listed, one neither) and one stale."""
    corpus = CorpusStore(dsn=dsn)
    corpus.upsert_players(
        [
            {
                "player_id": pid,
                "first_name": None,
                "last_name": pid,
                "position": pos,
                "team_id": "1",
                "market_value": 1_000_000,
                "average_points": 30.0,
            }
            for pid, pos in (
                ("gk", "Goalkeeper"),
                ("lst", "Forward"),
                ("x", "Midfielder"),
                ("stale", "Defender"),
            )
        ]
    )
    perf = {
        "it": [
            {
                "ti": "2026/2027",
                "ph": [
                    {
                        "day": 1,
                        "md": "2026-08-22T13:30:00Z",
                        "st": 5,
                        "p": 40,
                        "mp": "90",
                    }
                ],
            }
        ]
    }
    for pid in ("gk", "lst", "x", "stale"):
        corpus.record_match_history(pid, "1", perf)
        corpus.record_status_daily(
            pid,
            date.today(),
            {"st": 0, "prob": 1},
            fetched_at - (10 * 86400 if pid == "stale" else 0),
        )
    return corpus


def test_a_session_writes_one_prediction_per_fresh_player(store_dsn, ctx_factory):
    _seed_league(store_dsn, fetched_at=time.time())
    trader = _trader(store_dsn, _legal_squad())
    ctx = ctx_factory()
    ctx.ep_result["market_players"] = {"lst": object()}
    ctx.ep_result["lineup_map"] = {p.id: 10.0 for p in ctx.squad}
    ctx.ep_result["squad_scores"] = [SimpleNamespace(player_id="gk", expected_points=33.3)]
    nk = NextKickoff(
        at=datetime.now(tz=timezone.utc) + timedelta(days=3),
        source="schedule",
        cross_check=None,
        matchday_in_progress=False,
        day_number=4,
    )

    def _build(league):
        trader._next_kickoff = nk
        trader._facts_from_context(ctx, nk)
        return ctx

    with patch.object(AutoTrader, "_build_session_context", side_effect=_build):
        session = trader.run_full_session(LEAGUE)
    with CalibrationStore(dsn=store_dsn).connection() as conn:
        rows = {
            r["player_id"]: r
            for r in conn.execute(
                "SELECT * FROM rehoboam.predictions WHERE session_id = %s",
                (session.session_id,),
            ).fetchall()
        }
    assert set(rows) == {"gk", "lst", "x"}  # `stale` has a 10-day-old status row
    assert rows["gk"]["owned"] and rows["gk"]["in_best_11"] and rows["gk"]["live_ep"] == 33.3
    assert rows["lst"]["listed"] and not rows["lst"]["owned"] and rows["lst"]["live_ep"] is None
    assert rows["x"]["day_number"] == 4 and rows["x"]["app"] == "cli" and rows["x"]["dry_run"]
    assert rows["x"]["backfill"] is False and rows["x"]["season"] == "2026/2027"
    assert SessionStore(dsn=store_dsn).facts(session.session_id)["predictions_written"] == 3


def test_no_matchday_number_means_no_predictions_and_i5_fails(store_dsn, ctx_factory):
    _seed_league(store_dsn, fetched_at=time.time())
    trader = _trader(store_dsn, _legal_squad())
    nk = NextKickoff(
        at=datetime.now(tz=timezone.utc) + timedelta(days=3),
        source="myeleven",
        cross_check=None,
        matchday_in_progress=False,
    )

    def _build(league):
        trader._next_kickoff = nk
        ctx = ctx_factory()
        trader._facts_from_context(ctx, nk)
        return ctx

    with patch.object(AutoTrader, "_build_session_context", side_effect=_build):
        session = trader.run_full_session(LEAGUE)
    assert "I5" in [f.rule for f in session.integrity_failures]
    assert SessionStore(dsn=store_dsn).facts(session.session_id)["predictions_written"] == 0


def test_a_store_failure_while_predicting_never_stops_the_session(store_dsn, ctx_factory):
    trader = _trader(store_dsn, _legal_squad())
    trader._calibration_store = CalibrationStore(dsn="postgresql://nobody@127.0.0.1:1/nope")
    nk = NextKickoff(
        at=datetime.now(tz=timezone.utc) + timedelta(days=3),
        source="schedule",
        cross_check=None,
        matchday_in_progress=False,
        day_number=4,
    )

    def _build(league):
        trader._next_kickoff = nk
        ctx = ctx_factory()
        trader._facts_from_context(ctx, nk)
        return ctx

    with patch.object(AutoTrader, "_build_session_context", side_effect=_build):
        session = trader.run_full_session(LEAGUE)
    assert session.session_id
    assert SessionStore(dsn=store_dsn).facts(session.session_id)["predictions_written"] == 0


from rehoboam.store.league_store import LeagueStore  # noqa: E402

RAW_MARKET = {
    "it": [
        {
            "i": "lst",
            "tid": "8",
            "pos": 4,
            "prc": 3_000_000,
            "mv": 2_900_000,
            "exs": 500,
            "dt": "2026-09-15T16:05:06Z",
            "u": {"i": "m2"},
        }
    ]
}
RANKING = {"us": [{"i": "3616202", "n": "Marco"}, {"i": "m2", "n": "Rival"}]}


def test_a_session_writes_listings_managers_and_squads(store_dsn, ctx_factory):
    trader = _trader(store_dsn, _legal_squad())
    ctx = ctx_factory()
    ctx.ep_result.update(
        {
            "market_payload": RAW_MARKET,
            "ranking_payload": RANKING,
            "competitor_squads": {"m2": [{"pi": "r1", "mv": 5, "mvgl": 1, "iotm": False}]},
        }
    )
    with patch.object(AutoTrader, "_build_session_context", return_value=ctx):
        session = trader.run_full_session(LEAGUE)
    league = LeagueStore(dsn=store_dsn)
    assert [r["player_id"] for r in league.latest_market()] == ["lst"]
    assert (
        league.latest_market()[0]["seller_id"] == "m2"
        and league.latest_market()[0]["source"] == "session"
    )
    owners = league.owner_of(["r1", "gk", "lst"])
    assert owners == {"r1": "Rival", "gk": "Marco", "lst": "market"}
    facts = SessionStore(dsn=store_dsn).facts(session.session_id)
    assert facts["extra"]["league_state"] == {
        "listings": 1,
        "managers": 2,
        "squads": 12,
    }


def test_missing_payloads_write_nothing_and_do_not_fail(store_dsn, ctx_factory):
    trader = _trader(store_dsn, _legal_squad())
    with patch.object(AutoTrader, "_build_session_context", return_value=ctx_factory()):
        session = trader.run_full_session(LEAGUE)
    assert LeagueStore(dsn=store_dsn).latest_market() == []
    facts = SessionStore(dsn=store_dsn).facts(session.session_id)
    assert facts["errors"] == 0
