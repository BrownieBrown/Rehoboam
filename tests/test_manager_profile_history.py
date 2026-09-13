"""Tests for REH-38: manager_profile_history + manager_transfers persistence.

`/ranking` exposes per-manager `tv`, `sp`, `mdp`, but NOT cumulative transfer
P&L (`prft`). REH-38 adds two writers — `record_manager_profile_snapshot`
captures the dashboard's `prft` + `mdw` per session; `record_manager_transfers`
ingests the per-trade history from /managers/{mid}/transfer.
"""

from rehoboam.store import connect


def _profile_row(
    manager_id: str,
    *,
    snapshot_at: float = 1_000.0,
    league_id: str = "L1",
    transfer_pnl: int = -34_812_837,
    matchday_wins: int | None = 0,
    is_self: bool = False,
) -> dict:
    return {
        "snapshot_at": snapshot_at,
        "league_id": league_id,
        "manager_id": manager_id,
        "transfer_pnl": transfer_pnl,
        "matchday_wins": matchday_wins,
        "is_self": is_self,
    }


def _transfer_row(
    *,
    manager_id: str = "m1",
    league_id: str = "L1",
    transfer_dt: str = "2026-05-04T09:57:07Z",
    player_id: str = "10049",
    player_name: str = "Behrens",
    transfer_type: int | None = 1,
    transfer_price: int | None = 1_926_310,
) -> dict:
    return {
        "league_id": league_id,
        "manager_id": manager_id,
        "transfer_dt": transfer_dt,
        "player_id": player_id,
        "player_name": player_name,
        "transfer_type": transfer_type,
        "transfer_price": transfer_price,
    }


class TestRecordManagerProfileSnapshot:
    def test_round_trip_one_row(self, learner, store_dsn):
        n = learner.record_manager_profile_snapshot([_profile_row("m1", is_self=True)])
        assert n == 1

        with connect(store_dsn) as conn:
            row = conn.execute(
                "SELECT manager_id, transfer_pnl, matchday_wins, is_self "
                "FROM rehoboam.manager_profile_history"
            ).fetchone()
        assert (
            row["manager_id"],
            row["transfer_pnl"],
            row["matchday_wins"],
            row["is_self"],
        ) == ("m1", -34_812_837, 0, 1)

    def test_bulk_insert_multiple_managers(self, learner, store_dsn):
        rows = [
            _profile_row("m1", transfer_pnl=-34_812_837, is_self=True),
            _profile_row("m2", transfer_pnl=-48_555_897),
            _profile_row("m3", transfer_pnl=12_500_000),
        ]
        learner.record_manager_profile_snapshot(rows)

        with connect(store_dsn) as conn:
            data = conn.execute(
                "SELECT manager_id, transfer_pnl FROM rehoboam.manager_profile_history "
                "ORDER BY transfer_pnl"
            ).fetchall()
        assert [(r["manager_id"], r["transfer_pnl"]) for r in data] == [
            ("m2", -48_555_897),
            ("m1", -34_812_837),
            ("m3", 12_500_000),
        ]

    def test_empty_input_is_noop(self, learner):
        assert learner.record_manager_profile_snapshot([]) == 0

    def test_collision_at_same_pk_is_silently_dropped(self, learner, store_dsn):
        # PK (snapshot_at, manager_id) — same-second retry preserves the first.
        learner.record_manager_profile_snapshot(
            [_profile_row("m1", snapshot_at=42.0, transfer_pnl=-1)]
        )
        learner.record_manager_profile_snapshot(
            [_profile_row("m1", snapshot_at=42.0, transfer_pnl=-999)]
        )
        with connect(store_dsn) as conn:
            rows = conn.execute(
                "SELECT manager_id, transfer_pnl FROM rehoboam.manager_profile_history"
            ).fetchall()
        assert [(r["manager_id"], r["transfer_pnl"]) for r in rows] == [("m1", -1)]

    def test_matchday_wins_accepts_none(self, learner, store_dsn):
        # Pre-season the dashboard may legitimately omit `mdw`.
        learner.record_manager_profile_snapshot([_profile_row("m1", matchday_wins=None)])
        with connect(store_dsn) as conn:
            row = conn.execute(
                "SELECT matchday_wins FROM rehoboam.manager_profile_history"
            ).fetchone()
        assert row["matchday_wins"] is None

    def test_is_self_flag_round_trips_as_int(self, learner, store_dsn):
        learner.record_manager_profile_snapshot(
            [_profile_row("m1", is_self=True), _profile_row("m2", is_self=False)]
        )
        with connect(store_dsn) as conn:
            self_row = conn.execute(
                "SELECT manager_id FROM rehoboam.manager_profile_history WHERE is_self=1"
            ).fetchone()
        assert self_row["manager_id"] == "m1"

    def test_trajectory_query_for_self(self, learner, store_dsn):
        # The canonical post-deploy verification query: read self's prft trend.
        learner.record_manager_profile_snapshot(
            [
                _profile_row("m1", snapshot_at=100.0, transfer_pnl=-31_764_953, is_self=True),
                _profile_row("m1", snapshot_at=200.0, transfer_pnl=-34_812_837, is_self=True),
            ]
        )
        with connect(store_dsn) as conn:
            latest = conn.execute(
                "SELECT manager_id, transfer_pnl FROM rehoboam.manager_profile_history "
                "WHERE is_self=1 ORDER BY snapshot_at DESC LIMIT 1"
            ).fetchone()
        assert (latest["manager_id"], latest["transfer_pnl"]) == ("m1", -34_812_837)


class TestRecordManagerTransfers:
    def test_round_trip_one_row(self, learner, store_dsn):
        n = learner.record_manager_transfers([_transfer_row()])
        assert n == 1

        with connect(store_dsn) as conn:
            row = conn.execute(
                "SELECT league_id, manager_id, transfer_dt, player_id, "
                "player_name, transfer_type, transfer_price FROM rehoboam.manager_transfers"
            ).fetchone()
        assert (
            row["league_id"],
            row["manager_id"],
            row["transfer_dt"],
            row["player_id"],
            row["player_name"],
            row["transfer_type"],
            row["transfer_price"],
        ) == (
            "L1",
            "m1",
            "2026-05-04T09:57:07Z",
            "10049",
            "Behrens",
            1,
            1_926_310,
        )

    def test_empty_input_is_noop(self, learner):
        assert learner.record_manager_transfers([]) == 0

    def test_collision_on_pk_is_silently_dropped(self, learner, store_dsn):
        # PK (league_id, manager_id, transfer_dt, player_id) — re-importing
        # an overlapping page during backfill must NOT error or duplicate.
        original = _transfer_row(transfer_price=1_926_310)
        rewritten = _transfer_row(transfer_price=999_999_999)
        learner.record_manager_transfers([original])
        learner.record_manager_transfers([rewritten])

        with connect(store_dsn) as conn:
            rows = conn.execute(
                "SELECT player_id, transfer_price FROM rehoboam.manager_transfers"
            ).fetchall()
        assert [(r["player_id"], r["transfer_price"]) for r in rows] == [("10049", 1_926_310)]

    def test_distinct_dts_for_same_player_create_separate_rows(self, learner, store_dsn):
        # The same player can be bought and sold multiple times — each
        # transfer is a distinct event keyed on transfer_dt.
        learner.record_manager_transfers(
            [
                _transfer_row(transfer_dt="2026-04-01T10:00:00Z", transfer_type=1),
                _transfer_row(transfer_dt="2026-04-15T20:00:00Z", transfer_type=2),
            ]
        )
        with connect(store_dsn) as conn:
            rows = conn.execute(
                "SELECT transfer_dt, transfer_type FROM rehoboam.manager_transfers "
                "ORDER BY transfer_dt"
            ).fetchall()
        assert [(r["transfer_dt"], r["transfer_type"]) for r in rows] == [
            ("2026-04-01T10:00:00Z", 1),
            ("2026-04-15T20:00:00Z", 2),
        ]

    def test_optional_numeric_fields_accept_none(self, learner, store_dsn):
        learner.record_manager_transfers([_transfer_row(transfer_type=None, transfer_price=None)])
        with connect(store_dsn) as conn:
            row = conn.execute(
                "SELECT transfer_type, transfer_price FROM rehoboam.manager_transfers"
            ).fetchone()
        assert (row["transfer_type"], row["transfer_price"]) == (None, None)

    def test_bulk_insert_across_managers(self, learner, store_dsn):
        learner.record_manager_transfers(
            [
                _transfer_row(manager_id="m1", player_id="p1"),
                _transfer_row(manager_id="m2", player_id="p1"),
                _transfer_row(manager_id="m1", player_id="p2"),
            ]
        )
        with connect(store_dsn) as conn:
            count = conn.execute("SELECT COUNT(*) AS n FROM rehoboam.manager_transfers").fetchone()[
                "n"
            ]
        assert count == 3
