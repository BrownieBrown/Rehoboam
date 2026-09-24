"""`player_ranks`: where a player stands at his position (migration 026)."""

from __future__ import annotations

from rehoboam.store.league_store import LeagueStore

from .test_player_table import _seed


def test_ranks_by_points_per_appearance_among_players_who_played(store_dsn):
    # Seed: Alpha (MID, avg 50 over 2 apps), Beta (MID, avg 10 over 1 app),
    # Gamma (FW, no appearances).
    _seed(store_dsn)
    league = LeagueStore(dsn=store_dsn)

    ranks = league.position_ranks(["a", "b", "c"])

    assert ranks["a"]["avg_points_rank_pos"] == 1
    assert ranks["b"]["avg_points_rank_pos"] == 2
    assert ranks["c"]["avg_points_rank_pos"] is None, "never played: unknown, not last"
    assert ranks["c"]["mv_rank_pos"] == 1
    assert ranks["a"]["position_size"] == 2 and ranks["c"]["position_size"] == 1


def test_web_players_carries_the_two_ranks(store_dsn):
    _seed(store_dsn)
    with LeagueStore(dsn=store_dsn).connection() as conn:
        row = conn.execute(
            "SELECT avg_points_rank_pos, ep_rank_pos FROM rehoboam.web_players WHERE player_id = 'a'"
        ).fetchone()
    assert row["avg_points_rank_pos"] == 1
    assert row["ep_rank_pos"] in (1, 2)


def test_an_empty_lookup_needs_no_query(store_dsn):
    assert LeagueStore(dsn=store_dsn).position_ranks([]) == {}
