"""Tests for REH-26: player_mv_history persistence.

The bot fetches /player/{id}/marketValue history every session via
TrendService (cached 24h) and discarded the result after trend computation.
record_player_mv_snapshot() persists today's MV plus 30-day peak/trough so
goal 2 (loss avoidance) becomes measurable on slow drift, and REH-33's
"sold X% off peak" calculation has historical data to join against.
"""

from rehoboam.bid_learner import BidLearner
from rehoboam.store import connect


def _row(
    player_id: str,
    *,
    snapshot_at: float = 1_000.0,
    market_value: int = 10_000_000,
    peak_mv_30d: int | None = 11_000_000,
    trough_mv_30d: int | None = 9_500_000,
) -> dict:
    return {
        "player_id": player_id,
        "snapshot_at": snapshot_at,
        "market_value": market_value,
        "peak_mv_30d": peak_mv_30d,
        "trough_mv_30d": trough_mv_30d,
    }


class TestPlayerMvSnapshot:
    def test_round_trip_one_row(self, store_dsn):
        learner = BidLearner(dsn=store_dsn)
        n = learner.record_player_mv_snapshot([_row("p1")])
        assert n == 1
        with connect(store_dsn) as conn:
            row = conn.execute(
                "SELECT player_id, snapshot_at, market_value, "
                "peak_mv_30d, trough_mv_30d FROM rehoboam.player_mv_history"
            ).fetchone()
        assert (
            row["player_id"],
            row["snapshot_at"],
            row["market_value"],
            row["peak_mv_30d"],
            row["trough_mv_30d"],
        ) == ("p1", 1_000.0, 10_000_000, 11_000_000, 9_500_000)

    def test_bulk_insert_squad(self, store_dsn):
        learner = BidLearner(dsn=store_dsn)
        rows = [
            _row("p1", market_value=10_000_000),
            _row("p2", market_value=12_000_000),
            _row("p3", market_value=8_000_000),
        ]
        learner.record_player_mv_snapshot(rows)

        with connect(store_dsn) as conn:
            count = conn.execute("SELECT COUNT(*) AS n FROM rehoboam.player_mv_history").fetchone()[
                "n"
            ]
        assert count == 3

    def test_empty_input_is_noop(self, store_dsn):
        learner = BidLearner(dsn=store_dsn)
        assert learner.record_player_mv_snapshot([]) == 0

    def test_collision_at_same_pk_silently_dropped(self, store_dsn):
        learner = BidLearner(dsn=store_dsn)
        # PK = (player_id, snapshot_at). Same-second retry on the same player
        # → second write is dropped, first preserved.
        learner.record_player_mv_snapshot([_row("p1", snapshot_at=42.0, market_value=10_000_000)])
        learner.record_player_mv_snapshot([_row("p1", snapshot_at=42.0, market_value=99_999_999)])

        with connect(store_dsn) as conn:
            rows = conn.execute(
                "SELECT player_id, market_value FROM rehoboam.player_mv_history"
            ).fetchall()
        assert [(r["player_id"], r["market_value"]) for r in rows] == [("p1", 10_000_000)]

    def test_peak_and_trough_optional(self, store_dsn):
        learner = BidLearner(dsn=store_dsn)
        # Newly-listed players may have no history → peak/trough come back
        # as None. Schema must accept NULLs without raising.
        learner.record_player_mv_snapshot(
            [
                _row(
                    "p1",
                    peak_mv_30d=None,
                    trough_mv_30d=None,
                )
            ]
        )
        with connect(store_dsn) as conn:
            row = conn.execute(
                "SELECT peak_mv_30d, trough_mv_30d FROM rehoboam.player_mv_history"
            ).fetchone()
        assert (row["peak_mv_30d"], row["trough_mv_30d"]) == (None, None)

    def test_float_market_value_coerced_to_int(self, store_dsn):
        learner = BidLearner(dsn=store_dsn)
        # MV history occasionally returns floats; INTEGER column should
        # round-trip cleanly via int() coercion in the writer.
        learner.record_player_mv_snapshot(
            [
                _row(
                    "p1",
                    market_value=10_500_000.7,  # type: ignore[arg-type]
                )
            ]
        )
        with connect(store_dsn) as conn:
            row = conn.execute("SELECT market_value FROM rehoboam.player_mv_history").fetchone()
        assert row["market_value"] == 10_500_000

    def test_multiple_snapshots_same_player_different_times(self, store_dsn):
        learner = BidLearner(dsn=store_dsn)
        # The whole point of the table — daily series for one player.
        for i, ts in enumerate([100.0, 200.0, 300.0]):
            learner.record_player_mv_snapshot(
                [_row("p1", snapshot_at=ts, market_value=10_000_000 + i * 250_000)]
            )
        with connect(store_dsn) as conn:
            rows = conn.execute(
                "SELECT snapshot_at, market_value FROM rehoboam.player_mv_history "
                "WHERE player_id = 'p1' ORDER BY snapshot_at"
            ).fetchall()
        assert [(r["snapshot_at"], r["market_value"]) for r in rows] == [
            (100.0, 10_000_000),
            (200.0, 10_250_000),
            (300.0, 10_500_000),
        ]
