"""MvForecastStore: readings in, forecasts and outcomes out, against a real store."""

from __future__ import annotations

from datetime import date, datetime

from rehoboam.services.mv_forecast import BERLIN, Forecast, Score
from rehoboam.store import connect
from rehoboam.store.corpus_store import CorpusStore
from rehoboam.store.mv_forecast_store import MvForecastStore

D16 = date(2026, 9, 16)
D17 = date(2026, 9, 17)
MORNING_17 = datetime(2026, 9, 17, 7, 0, tzinfo=BERLIN).timestamp()


def _universe(dsn, *ids):
    CorpusStore(dsn=dsn).upsert_players(
        [{"player_id": i, "last_name": i, "position": "Forward", "team_id": "3"} for i in ids]
    )


def _reading(dsn, pid, day, mv, change, at=MORNING_17):
    details = {"mv": mv} if change is None else {"mv": mv, "tfhmvt": change}
    CorpusStore(dsn=dsn).record_status_daily(pid, day, details, at)


def _forecast(pid, day, base=10_000_000, change=90_000):
    return Forecast(pid, day, base, 100_000, change, change / base)


def _rows(dsn):
    with connect(dsn) as conn:
        return {
            (r["player_id"], r["target_day"]): r
            for r in conn.execute("select * from rehoboam.mv_forecasts").fetchall()
        }


def test_status_rows_returns_only_readings_with_a_value_and_a_change(store_dsn):
    _universe(store_dsn, "a", "b", "c")
    _reading(store_dsn, "a", D17, 10_000_000, 100_000)
    _reading(store_dsn, "b", D17, 5_000_000, None)
    _reading(store_dsn, "c", D16, 5_000_000, 10)
    rows = MvForecastStore(dsn=store_dsn).status_rows(D17)
    assert rows == [
        {
            "player_id": "a",
            "market_value": 10_000_000,
            "mv_change": 100_000,
            "fetched_at": MORNING_17,
        }
    ]


def test_status_readings_keys_by_player_and_keeps_nulls(store_dsn):
    _universe(store_dsn, "a", "b", "c")
    _reading(store_dsn, "a", D17, 10_000_000, 100_000)
    _reading(store_dsn, "b", D17, 5_000_000, None)
    readings = MvForecastStore(dsn=store_dsn).status_readings(D17, ["a", "b", "z"])
    assert set(readings) == {"a", "b"}
    assert readings["b"]["mv_change"] is None


def test_upsert_writes_then_rewrites_only_unscored_rows(store_dsn):
    store = MvForecastStore(dsn=store_dsn)
    written = store.upsert_forecasts(
        [_forecast("a", D17), _forecast("b", D16)], made_at=1.0, method="m"
    )
    assert written == 2
    store.record_outcomes([("b", D16, Score("scored", 50_000, 0.005))], scored_at=2.0)
    store.upsert_forecasts(
        [_forecast("a", D17, change=1), _forecast("b", D16, change=1)],
        made_at=3.0,
        method="m",
    )
    rows = _rows(store_dsn)
    assert (rows[("a", D17)]["predicted_change"], rows[("a", D17)]["made_at"]) == (
        1,
        3.0,
    )
    assert (rows[("b", D16)]["predicted_change"], rows[("b", D16)]["made_at"]) == (
        90_000,
        1.0,
    )


def test_upsert_of_nothing_writes_nothing(store_dsn):
    assert MvForecastStore(dsn=store_dsn).upsert_forecasts([], made_at=1.0, method="m") == 0


def test_pending_lists_unscored_forecasts_before_a_day(store_dsn):
    store = MvForecastStore(dsn=store_dsn)
    store.upsert_forecasts(
        [_forecast("a", D16), _forecast("b", D16), _forecast("c", D17)],
        made_at=1.0,
        method="m",
    )
    store.record_outcomes([("b", D16, Score("unscorable", None, None))], scored_at=2.0)
    assert store.pending(before=D17) == [
        {"player_id": "a", "target_day": D16, "base_mv": 10_000_000}
    ]


def test_record_outcomes_fills_the_row_once(store_dsn):
    store = MvForecastStore(dsn=store_dsn)
    store.upsert_forecasts([_forecast("a", D16)], made_at=1.0, method="m")
    assert store.record_outcomes([("a", D16, Score("scored", -20_000, -0.002))], scored_at=5.0) == 1
    assert store.record_outcomes([("a", D16, Score("scored", 1, 1.0))], scored_at=6.0) == 0
    row = _rows(store_dsn)[("a", D16)]
    assert (
        row["outcome"],
        row["actual_change"],
        row["actual_pct"],
        row["scored_at"],
    ) == (
        "scored",
        -20_000,
        -0.002,
        5.0,
    )


def test_daily_series_splits_each_player_at_gaps(store_dsn):
    _universe(store_dsn, "a", "b")
    corpus = CorpusStore(dsn=store_dsn)
    corpus.record_mv_series(
        "a",
        {
            "it": [
                {"dt": 20000, "mv": 1},
                {"dt": 20001, "mv": 2},
                {"dt": 20003, "mv": 3},
            ]
        },
    )
    corpus.record_mv_series("b", {"it": [{"dt": 20000, "mv": 7}, {"dt": 20001, "mv": 8}]})
    assert MvForecastStore(dsn=store_dsn).daily_series() == [[1, 2], [3], [7, 8]]
