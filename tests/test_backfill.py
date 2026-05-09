"""Tests for rehoboam.backfill — REH-39 historical foundation-table backfill.

The KICKBASE client is mocked end-to-end so tests don't hit the network. The
``BidLearner`` is exercised against a fresh sqlite db in tmp_path, which
gives us real INSERT OR IGNORE semantics for the idempotency tests.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from rehoboam.backfill import (
    TRANSFER_BUY,
    TRANSFER_PAGE_SIZE,
    TRANSFER_SELL,
    _paginate_transfers,
    _pair_flips,
    run_backfill,
)
from rehoboam.bid_learner import BidLearner
from rehoboam.kickbase_client import League


def _t(pi: str, tty: int, dt: str, trp: int = 1_000_000, pn: str = "Player") -> dict[str, Any]:
    return {"pi": pi, "tty": tty, "dt": dt, "trp": trp, "pn": pn}


def _league(lid: str = "lid-1") -> League:
    return League(id=lid, name="Test League", creator_id="creator")


def _learner(tmp_path) -> BidLearner:
    return BidLearner(db_path=tmp_path / "bid_learning.db")


# --- _pair_flips ----------------------------------------------------------


def test_pair_flips_simple_one_to_one():
    transfers = [
        _t("p1", TRANSFER_BUY, "2026-04-01T10:00:00Z", trp=1_000_000),
        _t("p1", TRANSFER_SELL, "2026-04-05T10:00:00Z", trp=1_200_000),
    ]
    flips, unpaired, orphaned = _pair_flips(transfers)
    assert len(flips) == 1
    assert flips[0].player_id == "p1"
    assert flips[0].buy["trp"] == 1_000_000
    assert flips[0].sell["trp"] == 1_200_000
    assert (unpaired, orphaned) == (0, 0)


def test_pair_flips_unpaired_buy_still_in_squad():
    transfers = [_t("p1", TRANSFER_BUY, "2026-04-01T10:00:00Z")]
    flips, unpaired, orphaned = _pair_flips(transfers)
    assert flips == []
    assert unpaired == 1
    assert orphaned == 0


def test_pair_flips_orphaned_sell_logged_but_skipped():
    transfers = [_t("p1", TRANSFER_SELL, "2026-04-01T10:00:00Z")]
    flips, unpaired, orphaned = _pair_flips(transfers)
    assert flips == []
    assert unpaired == 0
    assert orphaned == 1


def test_pair_flips_rebuy_after_sell_creates_two_flips():
    transfers = [
        _t("p1", TRANSFER_BUY, "2026-01-01T10:00:00Z"),
        _t("p1", TRANSFER_SELL, "2026-01-15T10:00:00Z"),
        _t("p1", TRANSFER_BUY, "2026-03-01T10:00:00Z"),
        _t("p1", TRANSFER_SELL, "2026-03-15T10:00:00Z"),
    ]
    flips, unpaired, orphaned = _pair_flips(transfers)
    assert len(flips) == 2
    assert flips[0].buy["dt"] < flips[1].buy["dt"]
    assert (unpaired, orphaned) == (0, 0)


def test_pair_flips_handles_multiple_players_independently():
    transfers = [
        _t("p1", TRANSFER_BUY, "2026-04-01T10:00:00Z"),
        _t("p2", TRANSFER_BUY, "2026-04-02T10:00:00Z"),
        _t("p1", TRANSFER_SELL, "2026-04-05T10:00:00Z"),
        _t("p2", TRANSFER_SELL, "2026-04-06T10:00:00Z"),
    ]
    flips, _, _ = _pair_flips(transfers)
    assert len(flips) == 2
    assert {f.player_id for f in flips} == {"p1", "p2"}


def test_pair_flips_sorts_within_player_even_if_input_unsorted():
    """Input-order shouldn't matter — only per-player chronological order."""
    transfers = [
        _t("p1", TRANSFER_SELL, "2026-04-05T10:00:00Z", trp=1_200_000),
        _t("p1", TRANSFER_BUY, "2026-04-01T10:00:00Z", trp=1_000_000),
    ]
    flips, _, _ = _pair_flips(transfers)
    assert len(flips) == 1
    assert flips[0].buy["dt"] < flips[0].sell["dt"]


# --- _paginate_transfers --------------------------------------------------


def _client_with_pages(pages: list[list[dict[str, Any]]]) -> MagicMock:
    """Return a fake KickbaseV4Client whose paginated calls walk ``pages``."""
    c = MagicMock()
    c.get_manager_transfer_history.side_effect = [{"it": p} for p in pages]
    return c


def test_paginate_transfers_walks_full_pages_until_short():
    full = [_t(f"p{i}", TRANSFER_BUY, "2026-04-01T10:00:00Z") for i in range(TRANSFER_PAGE_SIZE)]
    short = [_t("plast", TRANSFER_BUY, "2026-04-01T10:00:00Z")]
    client = _client_with_pages([full, short])

    items, pages = _paginate_transfers(client, "lid", "mid")

    assert pages == 2
    assert len(items) == TRANSFER_PAGE_SIZE + 1


def test_paginate_transfers_stops_on_empty_page():
    full = [_t(f"p{i}", TRANSFER_BUY, "2026-04-01T10:00:00Z") for i in range(TRANSFER_PAGE_SIZE)]
    client = _client_with_pages([full, []])

    items, pages = _paginate_transfers(client, "lid", "mid")

    assert pages == 2
    assert len(items) == TRANSFER_PAGE_SIZE


def test_paginate_transfers_passes_cursor_via_start_param():
    client = _client_with_pages([[]])
    _paginate_transfers(client, "lid", "mid")
    client.get_manager_transfer_history.assert_called_with("lid", "mid", start=0)


# --- run_backfill (integration with real BidLearner) ----------------------


def _baseline_client(transfers: list[dict[str, Any]], current_day: int = 0) -> MagicMock:
    c = MagicMock()
    # First call: current ranking (provides `day`). Subsequent: per-day rankings.
    c.get_league_ranking.return_value = {"day": current_day, "us": []}
    c.get_manager_transfer_history.side_effect = [{"it": transfers}, {"it": []}]
    c.get_user_teamcenter.return_value = {"lp": []}
    return c


def test_run_backfill_writes_flip_outcomes(tmp_path):
    transfers = [
        _t("p1", TRANSFER_BUY, "2026-04-01T10:00:00Z", trp=1_000_000, pn="P1"),
        _t("p1", TRANSFER_SELL, "2026-04-05T10:00:00Z", trp=1_200_000, pn="P1"),
        _t("p2", TRANSFER_BUY, "2026-04-02T10:00:00Z", trp=2_000_000, pn="P2"),
        _t("p2", TRANSFER_SELL, "2026-04-08T10:00:00Z", trp=1_800_000, pn="P2"),
    ]
    learner = _learner(tmp_path)
    client = _baseline_client(transfers, current_day=0)

    stats = run_backfill(client, _league(), "uid", "mid", learner, dry_run=False)

    assert stats.flip_outcomes_inserted == 2
    assert stats.flip_outcomes_skipped_duplicate == 0


def test_run_backfill_is_idempotent_on_rerun(tmp_path):
    transfers = [
        _t("p1", TRANSFER_BUY, "2026-04-01T10:00:00Z", trp=1_000_000),
        _t("p1", TRANSFER_SELL, "2026-04-05T10:00:00Z", trp=1_200_000),
    ]
    learner = _learner(tmp_path)

    client1 = _baseline_client(transfers, current_day=0)
    s1 = run_backfill(client1, _league(), "uid", "mid", learner, dry_run=False)
    assert s1.flip_outcomes_inserted == 1

    client2 = _baseline_client(transfers, current_day=0)
    s2 = run_backfill(client2, _league(), "uid", "mid", learner, dry_run=False)
    assert s2.flip_outcomes_inserted == 0
    assert s2.flip_outcomes_skipped_duplicate == 1


def test_run_backfill_dry_run_writes_nothing(tmp_path):
    transfers = [
        _t("p1", TRANSFER_BUY, "2026-04-01T10:00:00Z", trp=1_000_000),
        _t("p1", TRANSFER_SELL, "2026-04-05T10:00:00Z", trp=1_200_000),
    ]
    learner = _learner(tmp_path)
    client = _baseline_client(transfers, current_day=0)

    stats = run_backfill(client, _league(), "uid", "mid", learner, dry_run=True)

    # Stats reflect what WOULD have been written
    assert stats.flip_outcomes_inserted == 1
    # But the DB is empty
    import sqlite3

    with sqlite3.connect(learner.db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM flip_outcomes").fetchone()[0]
    assert count == 0


def test_run_backfill_writes_lineup_and_rank_per_matchday(tmp_path):
    """Phase 2/3: when current_day>0 and teamcenter returns a real lineup,
    we get one matchday_lineup_results row and N league_rank_history rows."""
    learner = _learner(tmp_path)

    teamcenter_for_md1 = {
        "lp": [
            {"i": "10", "p": 80, "md": "2025-08-23T13:30:00Z"},
            {"i": "20", "p": 65, "md": "2025-08-23T13:30:00Z"},
            {"i": "30", "p": 90, "md": "2025-08-23T13:30:00Z"},
        ]
    }
    ranking_for_md1 = {
        "us": [
            {"i": "uid", "spl": 1, "mdpl": 1, "sp": 235, "mdp": 235, "tv": 100_000_000},
            {"i": "other", "spl": 2, "mdpl": 2, "sp": 200, "mdp": 200, "tv": 95_000_000},
        ]
    }

    c = MagicMock()
    c.get_league_ranking.side_effect = [
        {"day": 1, "us": []},  # initial probe
        ranking_for_md1,  # per-matchday call
    ]
    c.get_manager_transfer_history.side_effect = [{"it": []}]
    c.get_user_teamcenter.return_value = teamcenter_for_md1

    stats = run_backfill(c, _league(), "uid", "mid", learner, dry_run=False)

    assert stats.matchdays_processed == 1
    assert stats.matchday_lineup_results_inserted == 1
    assert stats.league_rank_history_inserted == 2

    import sqlite3

    with sqlite3.connect(learner.db_path) as conn:
        lineup_row = conn.execute(
            "SELECT total_points, lineup_count, matchday_date FROM matchday_lineup_results"
        ).fetchone()
        assert lineup_row[0] == 80 + 65 + 90
        assert lineup_row[1] == 3
        assert lineup_row[2] == "2025-08-23T13:30:00Z"

        self_rank = conn.execute(
            "SELECT rank_overall, total_points, is_self FROM league_rank_history "
            "WHERE manager_id = 'uid'"
        ).fetchone()
        assert self_rank == (1, 235, 1)


def test_run_backfill_skips_matchday_with_no_lineup(tmp_path):
    """Pre-league-join matchdays return empty lp; should be counted but not
    written, and the ranking call for that matchday should NOT fire."""
    learner = _learner(tmp_path)
    c = MagicMock()
    c.get_league_ranking.side_effect = [{"day": 1, "us": []}]
    c.get_manager_transfer_history.side_effect = [{"it": []}]
    c.get_user_teamcenter.return_value = {"lp": []}

    stats = run_backfill(c, _league(), "uid", "mid", learner, dry_run=False)

    assert stats.matchdays_skipped_no_lineup == 1
    assert stats.matchday_lineup_results_inserted == 0
    # Only the initial /ranking probe call, no per-day call
    assert c.get_league_ranking.call_count == 1
