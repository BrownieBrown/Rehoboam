import sqlite3

import pytest

from rehoboam.enrichment.corpus import TrainingCorpus
from rehoboam.replay.state import ReplayPlayer, ReplayState, initial_state

ASSIGNED_AT = 1_754_661_947.0  # 2025-08-08T14:05:47Z


def _player(pid, pos="Forward", team="40"):
    return ReplayPlayer(id=pid, position=pos, team_id=team)


def test_buy_debits_budget_and_adds_player():
    s = ReplayState(budget=10_000_000, squad={})
    s.buy(_player("1"), 4_000_000)
    assert s.budget == 6_000_000
    assert s.player_ids == ["1"]


def test_sell_credits_budget_and_removes_player():
    s = ReplayState(budget=0, squad={"1": _player("1")})
    s.sell("1", 3_000_000)
    assert s.budget == 3_000_000
    assert s.player_ids == []


def test_sell_unknown_player_raises():
    s = ReplayState(budget=0, squad={})
    with pytest.raises(KeyError):
        s.sell("nope", 1)


def test_budget_may_go_negative_on_buy():
    s = ReplayState(budget=1_000_000, squad={})
    s.buy(_player("1"), 5_000_000)
    assert s.budget == -4_000_000


def test_team_counts_groups_by_team():
    s = ReplayState(
        budget=0,
        squad={
            "1": _player("1", team="40"),
            "2": _player("2", team="40"),
            "3": _player("3", team="7"),
        },
    )
    assert s.team_counts() == {"40": 2, "7": 1}


def test_initial_state_reconstructs_the_assigned_squad(tmp_path):
    c = TrainingCorpus(tmp_path / "c.db")
    with sqlite3.connect(c.db_path) as conn:
        conn.executemany(
            "INSERT INTO player_transfers (player_id, transfer_at, price, transfer_type,"
            " counterparty_id, counterparty_name) VALUES (?,?,?,?,?,?)",
            [
                ("1", ASSIGNED_AT, 0, 0, "3616202", "Brownie"),
                ("2", ASSIGNED_AT, 0, 0, "3616202", "Brownie"),
                ("3", ASSIGNED_AT, 0, 0, "9999", "Someone"),  # another manager
                (
                    "4",
                    ASSIGNED_AT - 400 * 86400,
                    0,
                    0,
                    "3616202",
                    "Brownie",
                ),  # prior season
            ],
        )
        conn.executemany(
            "INSERT INTO player_universe (player_id, position, team_id) VALUES (?,?,?)",
            [("1", "Forward", "40"), ("2", "Defender", "7")],
        )
    state = initial_state(
        c, manager_id="3616202", assigned_on=ASSIGNED_AT, starting_budget=80_000_000
    )
    assert sorted(state.player_ids) == ["1", "2"]
    assert state.budget == 80_000_000
    assert state.squad["1"].position == "Forward"
    assert state.squad["1"].team_id == "40"


def test_initial_state_ignores_prior_season_assignments(tmp_path):
    c = TrainingCorpus(tmp_path / "d.db")
    with sqlite3.connect(c.db_path) as conn:
        conn.execute(
            "INSERT INTO player_transfers (player_id, transfer_at, price, transfer_type,"
            " counterparty_id, counterparty_name) VALUES (?,?,?,?,?,?)",
            ("old", ASSIGNED_AT - 365 * 86400, 0, 0, "3616202", "Brownie"),
        )
    state = initial_state(
        c, manager_id="3616202", assigned_on=ASSIGNED_AT, starting_budget=80_000_000
    )
    assert state.player_ids == []


def test_initial_state_ignores_a_superseded_earlier_draw(tmp_path):
    """A re-rolled league has two assignment batches; only the later is real.

    Our real league drew a 14-player squad on 2025-08-07 20:40:08 and re-drew
    12 players on 2025-08-08 14:05:48. A window wide enough to span both
    produces a 25-player phantom squad that never existed.
    """
    c = TrainingCorpus(tmp_path / "rerolled.db")
    earlier = ASSIGNED_AT - 62_499  # ~17.4h before, matching the real re-roll gap
    with sqlite3.connect(c.db_path) as conn:
        conn.executemany(
            "INSERT INTO player_transfers (player_id, transfer_at, price, transfer_type,"
            " counterparty_id, counterparty_name) VALUES (?,?,?,?,?,?)",
            [
                ("stale1", earlier, 0, 0, "3616202", "Brownie"),
                ("stale2", earlier, 0, 0, "3616202", "Brownie"),
                ("real1", ASSIGNED_AT, 0, 0, "3616202", "Brownie"),
            ],
        )
        conn.executemany(
            "INSERT INTO player_universe (player_id, position, team_id) VALUES (?,?,?)",
            [("stale1", "Forward", "1"), ("stale2", "Defender", "2"), ("real1", "Midfielder", "3")],
        )
    state = initial_state(
        c, manager_id="3616202", assigned_on=ASSIGNED_AT, starting_budget=80_000_000
    )
    assert state.player_ids == ["real1"]
