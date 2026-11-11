"""M0: the simulator reproduces history (spec v3 §8.3)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from rehoboam.sim import validate
from rehoboam.sim.history import BUY, SELL, SquadSnapshot, Transfer, WalletReading, in_update_window

T0 = datetime(2026, 10, 20, 7, 0, tzinfo=timezone.utc)  # 09:00 Berlin: value day 10-19
DAY = date(2026, 10, 19)


def _t(player, kind, at, price=1_000_000, manager="m"):
    return Transfer(manager_id=manager, player_id=player, at=at, kind=kind, price=price)


def test_replay_adds_buys_and_removes_sales_inside_the_window():
    transfers = [
        _t("b", BUY, T0 + timedelta(hours=1)),
        _t("a", SELL, T0 + timedelta(hours=2)),
        _t("c", BUY, T0 + timedelta(hours=5)),  # after the window
    ]

    held = validate.replay_holdings(frozenset({"a"}), transfers, T0, T0 + timedelta(hours=3))

    assert held == frozenset({"b"})


def test_a_manager_whose_transfers_explain_his_squads_passes():
    snaps = [
        SquadSnapshot("m", T0, {"a": 2_000_000}),
        SquadSnapshot("m", T0 + timedelta(hours=3), {"b": 3_000_000}),
    ]
    transfers = [_t("b", BUY, T0 + timedelta(hours=1)), _t("a", SELL, T0 + timedelta(hours=2))]
    paths = {"a": {DAY: 2_000_000}, "b": {DAY: 3_000_000}}

    check = validate.check_manager(snaps, transfers, paths)

    assert (check.squad_matches, check.priced, check.value_matches) == (1, 1, 1)
    assert check.passed


def test_a_missing_transfer_shows_as_a_squad_mismatch():
    snaps = [
        SquadSnapshot("m", T0, {"a": 1}),
        SquadSnapshot("m", T0 + timedelta(hours=3), {"b": 1}),
    ]

    check = validate.check_manager(snaps, [], {"a": {DAY: 1}, "b": {DAY: 1}})

    assert check.squad_matches == 0
    assert check.first_mismatch == T0 + timedelta(hours=3)
    assert not check.passed


def test_an_unpriced_player_leaves_the_snapshot_uncovered_not_wrong():
    snaps = [
        SquadSnapshot("m", T0, {"a": 1}),
        SquadSnapshot("m", T0 + timedelta(hours=3), {"a": 5}),
    ]

    check = validate.check_manager(snaps, [], {"a": {}})

    assert check.priced == 0
    assert check.passed


def test_a_same_second_race_in_the_wallet_is_not_unexplained():
    readings = [
        WalletReading(T0, 10_000_000),
        WalletReading(
            T0 + timedelta(hours=1), 10_000_000
        ),  # buy timestamped before, not yet deducted
        WalletReading(T0 + timedelta(hours=2), 4_000_000),
    ]
    buy = _t("x", BUY, T0 + timedelta(minutes=59), price=6_000_000)

    assert validate.check_wallet(readings, [buy]).passed


def test_a_missing_sale_in_the_wallet_is_unexplained():
    readings = [WalletReading(T0, 0), WalletReading(T0 + timedelta(hours=1), 5_000_000)]

    check = validate.check_wallet(readings, [])

    assert not check.passed
    assert check.unexplained == ((T0 + timedelta(hours=1), 5_000_000),)


def test_the_update_window_is_22_00_to_22_30_berlin():
    assert in_update_window(datetime(2026, 11, 11, 21, 10, tzinfo=timezone.utc))  # 22:10 CET
    assert not in_update_window(datetime(2026, 11, 11, 21, 30, tzinfo=timezone.utc))  # 22:30
    assert not in_update_window(datetime(2026, 11, 11, 20, 59, tzinfo=timezone.utc))  # 21:59
