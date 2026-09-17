"""run_mv_forecast: score yesterday's forecasts, then write today's (real store)."""

from __future__ import annotations

from datetime import date, datetime

from rehoboam.enrichment.mv_forecast import MvForecastOutcome, run_mv_forecast
from rehoboam.services.mv_forecast import BERLIN, METHOD
from rehoboam.store import connect
from rehoboam.store.corpus_store import CorpusStore
from rehoboam.store.mv_forecast_store import MvForecastStore

D15 = date(2026, 9, 15)
D16 = date(2026, 9, 16)
D17 = date(2026, 9, 17)


def _at(day, hh, mm=0):
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=BERLIN).timestamp()


def _universe(dsn, *ids):
    CorpusStore(dsn=dsn).upsert_players(
        [{"player_id": i, "last_name": i, "position": "Forward", "team_id": "3"} for i in ids]
    )


def _reading(dsn, pid, day, mv, change, at):
    CorpusStore(dsn=dsn).record_status_daily(pid, day, {"mv": mv, "tfhmvt": change}, at)


def _forecasts(dsn):
    with connect(dsn) as conn:
        return {
            (r["player_id"], r["target_day"]): r
            for r in conn.execute("select * from rehoboam.mv_forecasts").fetchall()
        }


def _run(dsn, now):
    return run_mv_forecast(MvForecastStore(dsn=dsn), now=now, momentum=0.9, cap=0.2)


def test_a_morning_run_forecasts_today_from_todays_readings(store_dsn):
    _universe(store_dsn, "a", "b")
    _reading(store_dsn, "a", D16, 9_900_000, 50, _at(D16, 7))
    _reading(store_dsn, "a", D17, 10_000_000, 100_000, _at(D17, 7))
    _reading(store_dsn, "b", D17, 5_000_000, -100_000, _at(D17, 7))
    out = _run(store_dsn, _at(D17, 7, 9))
    assert out == MvForecastOutcome(written=2, scored=0, unscorable=0, error=None)
    rows = _forecasts(store_dsn)
    assert set(rows) == {("a", D17), ("b", D17)}
    a = rows[("a", D17)]
    assert (a["method"], a["base_mv"], a["last_change"]) == (
        METHOD,
        10_000_000,
        100_000,
    )
    assert a["predicted_change"] == round(10_000_000 * 0.9 * 100_000 / 9_900_000)
    assert rows[("b", D17)]["predicted_change"] < 0


def test_a_reading_taken_after_the_cutoff_is_not_forecast(store_dsn):
    _universe(store_dsn, "a")
    _reading(store_dsn, "a", D17, 10_000_000, 100_000, _at(D17, 21, 50))
    out = _run(store_dsn, _at(D17, 21, 55))
    assert out.written == 0
    assert _forecasts(store_dsn) == {}


def test_the_next_morning_scores_yesterday_then_forecasts_today(store_dsn):
    _universe(store_dsn, "a", "b")
    _reading(store_dsn, "a", D16, 10_000_000, 100_000, _at(D16, 7))
    _reading(store_dsn, "b", D16, 5_000_000, 50_000, _at(D16, 7))
    _run(store_dsn, _at(D16, 7, 9))
    # the 22:00 update on the 16th: a +80,000, b's reading does not line up
    _reading(store_dsn, "a", D17, 10_080_000, 80_000, _at(D17, 7))
    _reading(store_dsn, "b", D17, 5_000_000, 30_000, _at(D17, 7))
    out = _run(store_dsn, _at(D17, 7, 9))
    assert (out.scored, out.unscorable, out.written, out.error) == (1, 1, 2, None)
    rows = _forecasts(store_dsn)
    a16 = rows[("a", D16)]
    assert (a16["outcome"], a16["actual_change"]) == ("scored", 80_000)
    assert a16["actual_pct"] == 80_000 / 10_000_000
    assert rows[("b", D16)]["outcome"] == "unscorable"
    assert rows[("a", D17)]["base_mv"] == 10_080_000


def test_a_missing_next_day_reading_waits_for_the_afternoon_run(store_dsn):
    _universe(store_dsn, "a")
    _reading(store_dsn, "a", D16, 10_000_000, 100_000, _at(D16, 7))
    _run(store_dsn, _at(D16, 7, 9))
    out = _run(store_dsn, _at(D17, 7, 9))  # no reading for a on the 17th yet
    assert (out.scored, out.unscorable) == (0, 0)
    assert _forecasts(store_dsn)[("a", D16)]["scored_at"] is None


def test_a_forecast_whose_next_day_passed_unread_becomes_unscorable(store_dsn):
    _universe(store_dsn, "a")
    _reading(store_dsn, "a", D15, 10_000_000, 100_000, _at(D15, 7))
    _run(store_dsn, _at(D15, 7, 9))
    out = _run(store_dsn, _at(D17, 7, 9))  # the 16th was never read
    assert (out.scored, out.unscorable) == (0, 1)
    assert _forecasts(store_dsn)[("a", D15)]["outcome"] == "unscorable"


def test_a_late_next_day_reading_is_not_used_to_score(store_dsn):
    _universe(store_dsn, "a")
    _reading(store_dsn, "a", D15, 10_000_000, 100_000, _at(D15, 7))
    _run(store_dsn, _at(D15, 7, 9))
    _reading(store_dsn, "a", D16, 10_080_000, 80_000, _at(D16, 22, 30))
    out = _run(store_dsn, _at(D17, 7, 9))
    assert (out.scored, out.unscorable) == (0, 1)


def test_a_failing_store_is_reported_not_raised():
    class Broken:
        def pending(self, before):
            raise RuntimeError("pooler down")

    out = run_mv_forecast(Broken(), now=_at(D17, 7), momentum=0.9, cap=0.2)
    assert out.written == 0 and out.error == "pooler down"
