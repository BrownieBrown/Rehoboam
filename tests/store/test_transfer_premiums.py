"""Migration 024: every buyer's premium over the market value in force, and
what the buy was worth afterwards -- from what the store already holds."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from rehoboam.store import connect


def _epoch_day(day: str) -> float:
    return datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp()


def _seed(dsn):
    with connect(dsn) as conn:
        conn.execute(
            "insert into rehoboam.managers (manager_id, league_id, name, is_self, updated_at) "
            "values ('me', 'L', 'Brownie', true, 0), ('r1', 'L', 'Rival', false, 0)"
        )
        transfer = (
            "insert into rehoboam.manager_transfers (league_id, manager_id, transfer_dt, "
            "player_id, player_name, transfer_type, transfer_price) values (%s, %s, %s, %s, %s, %s, %s)"
        )
        for row in (
            # Rival buys p1 at 10:00 Berlin on the 8th: priced against the 7th's value.
            ("L", "r1", "2026-09-08T08:00:00Z", "p1", "Morning", 1, 11_000_000),
            # Rival buys p1 again at 22:30 Berlin on the 8th: the 8th's value is in force.
            ("L", "r1", "2026-09-08T20:30:00Z", "p1", "Evening", 1, 11_000_000),
            # ...and sells him twelve days later.
            ("L", "r1", "2026-09-20T08:00:00Z", "p1", "Sold", 2, 13_000_000),
            # We buy a player with no market-value series at all.
            ("L", "me", "2026-09-10T08:00:00Z", "p2", "Unknown", 1, 4_000_000),
            # A buy last March belongs to last season, and is never sold.
            ("L", "me", "2026-03-01T12:00:00Z", "p3", "Spring", 1, 6_000_000),
            # A sale is not a buy.
            ("L", "r1", "2026-09-11T08:00:00Z", "p9", "Nope", 2, 1_000_000),
        ):
            conn.execute(transfer, row)
        series = "insert into rehoboam.mv_series (player_id, snapshot_at, market_value) values (%s, %s, %s)"
        conn.execute(series, ("p1", _epoch_day("2026-09-07"), 10_000_000))
        conn.execute(series, ("p1", _epoch_day("2026-09-08"), 12_000_000))
        # A buy at 13:00 Berlin on 1 March is priced against 28 February's value.
        conn.execute(series, ("p3", _epoch_day("2026-02-28"), 5_000_000))
        match = (
            "insert into rehoboam.player_match_history (player_id, season, day_number, match_date, "
            "points, minutes, status) values ('p1', '2026/2027', %s, %s, %s, 90, 5)"
        )
        # One match before the buy, six after it in the past, one in the future.
        conn.execute(match, (1, "2026-09-01T18:30:00Z", 999))
        for day, (date, points) in enumerate(
            (
                ("2026-09-09T18:30:00Z", 10),
                ("2026-09-10T18:30:00Z", 20),
                ("2026-09-11T18:30:00Z", 30),
                ("2026-09-12T18:30:00Z", 40),
                ("2026-09-13T18:30:00Z", 50),
                ("2026-09-14T18:30:00Z", 60),
                ("2099-01-01T18:30:00Z", 0),
            ),
            start=2,
        ):
            conn.execute(match, (day, date, points))


def _rows(dsn, view, where="true", order="transferred_at"):
    with connect(dsn) as conn:
        return [
            dict(r)
            for r in conn.execute(
                f"select * from rehoboam.{view} where {where} order by {order}"  # noqa: S608
            )
        ]


class TestTransferPremiums:
    def test_only_buys_appear(self, store_dsn):
        _seed(store_dsn)
        names = [r["player_name"] for r in _rows(store_dsn, "transfer_premiums")]
        assert "Nope" not in names
        assert "Sold" not in names
        assert len(names) == 4

    def test_a_morning_buy_is_priced_against_yesterdays_value(self, store_dsn):
        _seed(store_dsn)
        row = _rows(store_dsn, "transfer_premiums", "player_name = 'Morning'")[0]
        assert str(row["mv_day"]) == "2026-09-07"
        assert row["mv_at_transfer"] == 10_000_000
        assert float(row["premium_pct"]) == pytest.approx(10.0)

    def test_a_buy_after_the_update_is_priced_against_todays_value(self, store_dsn):
        _seed(store_dsn)
        row = _rows(store_dsn, "transfer_premiums", "player_name = 'Evening'")[0]
        assert str(row["mv_day"]) == "2026-09-08"
        assert row["mv_at_transfer"] == 12_000_000
        assert float(row["premium_pct"]) == pytest.approx(-8.33, abs=0.01)

    def test_no_series_means_no_premium_not_no_row(self, store_dsn):
        _seed(store_dsn)
        row = _rows(store_dsn, "transfer_premiums", "player_name = 'Unknown'")[0]
        assert row["mv_at_transfer"] is None
        assert row["premium_pct"] is None
        assert row["is_self"] is True
        assert row["manager_name"] == "Brownie"

    def test_the_season_turns_in_july(self, store_dsn):
        _seed(store_dsn)
        by_name = {r["player_name"]: r["season"] for r in _rows(store_dsn, "transfer_premiums")}
        assert by_name["Spring"] == "2025/2026"
        assert by_name["Morning"] == "2026/2027"


class TestTransferOutcomes:
    def test_the_next_sale_by_the_same_manager_is_the_resale(self, store_dsn):
        _seed(store_dsn)
        row = _rows(store_dsn, "transfer_outcomes", "player_name = 'Morning'")[0]
        assert row["sell_price"] == 13_000_000
        assert row["realised_profit"] == 2_000_000
        assert float(row["days_held"]) == pytest.approx(12.0)

    def test_a_player_never_sold_has_no_resale(self, store_dsn):
        _seed(store_dsn)
        row = _rows(store_dsn, "transfer_outcomes", "player_name = 'Spring'")[0]
        assert row["sold_at"] is None
        assert row["realised_profit"] is None
        assert row["days_held"] is None

    def test_points_count_the_five_played_matches_after_the_buy(self, store_dsn):
        _seed(store_dsn)
        row = _rows(store_dsn, "transfer_outcomes", "player_name = 'Morning'")[0]
        assert row["matches_after"] == 5
        assert row["points_after"] == 10 + 20 + 30 + 40 + 50
        assert float(row["avg_points_after"]) == pytest.approx(30.0)

    def test_a_player_without_matches_has_zero_not_null_matches(self, store_dsn):
        _seed(store_dsn)
        row = _rows(store_dsn, "transfer_outcomes", "player_name = 'Unknown'")[0]
        assert row["matches_after"] == 0
        assert row["points_after"] is None
