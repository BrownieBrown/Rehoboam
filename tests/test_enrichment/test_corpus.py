"""Tests for rehoboam.enrichment.corpus — the v2 training corpus store."""

from __future__ import annotations

import sqlite3

from rehoboam.enrichment.corpus import TrainingCorpus


def _perf(season: str, matches: list[dict]) -> dict:
    return {"it": [{"ti": season, "ph": matches}]}


def test_upsert_players_is_idempotent(tmp_path):
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    players = [
        {
            "player_id": "1",
            "last_name": "Musiala",
            "position": "Midfielder",
            "team_id": "2",
            "market_value": 30_000_000,
            "average_points": 120.0,
        }
    ]

    assert corpus.upsert_players(players) == 1
    assert corpus.upsert_players(players) == 1
    assert corpus.players_needing_fetch("performance") == ["1"]


def test_record_match_history_parses_minutes_and_home_away(tmp_path):
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    corpus.upsert_players([{"player_id": "1", "team_id": "3"}])

    perf = _perf(
        "2025/2026",
        [
            {
                "day": 21,
                "p": 17,
                "mp": "1'",
                "md": "2026-02-07T14:30:00Z",
                "t1": "11",
                "t2": "3",
            },
            {
                "day": 22,
                "p": 72,
                "mp": "90+5'",
                "md": "2026-02-13T19:30:00Z",
                "t1": "3",
                "t2": "18",
            },
        ],
    )
    assert corpus.record_match_history("1", "3", perf) == 2

    rows = corpus.matches_for_player("1")
    assert [r["day_number"] for r in rows] == [21, 22]
    assert rows[0]["minutes"] == 1
    assert rows[1]["minutes"] == 95  # stoppage time summed
    assert rows[0]["is_home"] == 0  # team 3 is t2 -> away
    assert rows[1]["is_home"] == 1  # team 3 is t1 -> home
    assert rows[0]["opponent_team_id"] == "11"
    assert rows[1]["opponent_team_id"] == "18"


def test_record_match_history_populates_status_from_st(tmp_path):
    """The availability label (REH-52): ``st`` on the payload becomes the
    ``status`` column, not a column literally named ``st`` -- that name is
    already taken by the unrelated live injury-status field elsewhere in the
    codebase (kickbase_client.py, matchup_analyzer.py, scorer.py)."""
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    corpus.upsert_players([{"player_id": "1", "team_id": "3"}])

    perf = _perf(
        "2025/2026",
        [
            {"day": 1, "p": 85, "mp": "90'", "t1": "3", "t2": "4", "st": 5},  # started
            {"day": 2, "p": 18, "mp": "23'", "t1": "3", "t2": "5", "st": 3},  # came on
            {"day": 3, "p": 1, "mp": "0'", "t1": "3", "t2": "6", "st": 4},  # unused sub
            {"day": 4, "p": 0, "mp": "0'", "t1": "3", "t2": "7", "st": 1},  # not in squad
            {"day": 5, "p": 0, "mp": "0'", "t1": "3", "t2": "8", "st": 0},  # unplayed fixture
        ],
    )
    assert corpus.record_match_history("1", "3", perf) == 5

    rows = {r["day_number"]: r for r in corpus.matches_for_player("1")}
    assert rows[1]["status"] == 5
    assert rows[2]["status"] == 3
    assert rows[3]["status"] == 4
    assert rows[4]["status"] == 1
    assert rows[5]["status"] == 0  # 0 is a real value, not a stand-in for NULL


def test_record_match_history_leaves_status_null_when_st_absent(tmp_path):
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    corpus.upsert_players([{"player_id": "1", "team_id": "3"}])

    perf = _perf("2025/2026", [{"day": 1, "p": 50, "mp": "90'", "t1": "3", "t2": "4"}])
    corpus.record_match_history("1", "3", perf)

    rows = corpus.matches_for_player("1")
    assert rows[0]["status"] is None


def test_record_match_history_uses_pt_for_transferred_player(tmp_path):
    """A player's team changes over a career, but the caller only ever
    passes the team_id it currently holds. Old matches must still resolve
    is_home/opponent against the team the player was *actually on at the
    time* (the ``pt`` field on each match), not today's team_id -- otherwise
    every match before the player's most recent transfer is mislabelled and
    ``opponent_team_id`` ends up recording the player's own historical team.

    The caller here passes team_id="7" (today's team), matching how
    ``sweep.py`` builds ``team_by_id`` from the *live* universe snapshot.
    Two of the four matches below are from when the player was still on
    team "3" -- before the transfer.
    """
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    corpus.upsert_players([{"player_id": "1", "team_id": "7"}])

    perf = _perf(
        "2024/2025",
        [
            # Pre-transfer: player was on team "3".
            {"day": 5, "p": 10, "mp": "90'", "t1": "3", "t2": "9", "pt": "3"},
            {"day": 6, "p": 20, "mp": "90'", "t1": "20", "t2": "3", "pt": "3"},
            # Post-transfer: player is now on team "7".
            {"day": 25, "p": 30, "mp": "90'", "t1": "7", "t2": "12", "pt": "7"},
            {"day": 26, "p": 40, "mp": "90'", "t1": "4", "t2": "7", "pt": "7"},
        ],
    )
    assert corpus.record_match_history("1", "7", perf) == 4

    rows = {r["day_number"]: r for r in corpus.matches_for_player("1")}

    # Pre-transfer matches: home/away and opponent follow team "3" (pt),
    # never team "7" (the passed-in, merely-current team_id).
    assert rows[5]["is_home"] == 1  # pt "3" == t1 "3"
    assert rows[5]["opponent_team_id"] == "9"
    assert rows[6]["is_home"] == 0  # pt "3" == t2 "3"
    assert rows[6]["opponent_team_id"] == "20"

    # Post-transfer matches: pt now agrees with the passed team_id.
    assert rows[25]["is_home"] == 1
    assert rows[25]["opponent_team_id"] == "12"
    assert rows[26]["is_home"] == 0
    assert rows[26]["opponent_team_id"] == "4"


def test_record_match_history_falls_back_to_passed_team_when_pt_missing(tmp_path):
    """Older/edge-case responses may omit ``pt`` entirely -- fall back to the
    caller-supplied team_id rather than losing the row's home/away label."""
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    corpus.upsert_players([{"player_id": "1", "team_id": "3"}])

    perf = _perf("2025/2026", [{"day": 1, "p": 10, "mp": "90'", "t1": "3", "t2": "9"}])
    corpus.record_match_history("1", "3", perf)

    rows = corpus.matches_for_player("1")
    assert rows[0]["is_home"] == 1
    assert rows[0]["opponent_team_id"] == "9"


def test_record_match_history_is_idempotent(tmp_path):
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    perf = _perf("2025/2026", [{"day": 1, "p": 50, "mp": "90'", "t1": "3", "t2": "4"}])

    corpus.record_match_history("1", "3", perf)
    corpus.record_match_history("1", "3", perf)
    assert len(corpus.matches_for_player("1")) == 1


def test_matches_without_day_number_are_skipped(tmp_path):
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    perf = _perf("2025/2026", [{"p": 50, "mp": "90'"}, {"day": 2, "p": 60, "mp": "90'"}])
    assert corpus.record_match_history("1", None, perf) == 1


def test_record_mv_series_drops_sentinel_rows(tmp_path):
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    history = {
        "it": [
            {"dt": 20000, "mv": 5_000_000},
            {"dt": 20001, "mv": 0},  # sentinel for newly-listed
            {"dt": 20002, "mv": 5_100_000},
        ]
    }
    assert corpus.record_mv_series("1", history) == 2


def test_record_mv_series_is_idempotent(tmp_path):
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    history = {"it": [{"dt": 20000, "mv": 5_000_000}]}

    corpus.record_mv_series("1", history)
    corpus.record_mv_series("1", history)

    with sqlite3.connect(corpus.db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM mv_series").fetchone()[0]
    assert count == 1


def test_mark_fetched_removes_player_from_pending(tmp_path):
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    corpus.upsert_players([{"player_id": "1"}, {"player_id": "2"}])

    corpus.mark_fetched("1", performance=True)
    assert corpus.players_needing_fetch("performance") == ["2"]
    # marking performance must not satisfy the mv sweep
    assert set(corpus.players_needing_fetch("mv")) == {"1", "2"}


def test_clear_performance_fetched_resets_all_players_by_default(tmp_path):
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    corpus.upsert_players([{"player_id": "1"}, {"player_id": "2"}])
    corpus.mark_fetched("1", performance=True, mv=True)
    corpus.mark_fetched("2", performance=True)

    cleared = corpus.clear_performance_fetched()

    assert cleared == 2
    assert set(corpus.players_needing_fetch("performance")) == {"1", "2"}
    # MV resumability is untouched.
    assert corpus.players_needing_fetch("mv") == ["2"]


def test_clear_performance_fetched_can_be_scoped_to_specific_ids(tmp_path):
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    corpus.upsert_players([{"player_id": "1"}, {"player_id": "2"}])
    corpus.mark_fetched("1", performance=True)
    corpus.mark_fetched("2", performance=True)

    cleared = corpus.clear_performance_fetched(["1"])

    assert cleared == 1
    assert corpus.players_needing_fetch("performance") == ["1"]


def test_clear_performance_fetched_is_a_noop_when_nothing_was_fetched(tmp_path):
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    corpus.upsert_players([{"player_id": "1"}])
    assert corpus.clear_performance_fetched() == 0


def test_ensure_players_inserts_stub_rows_for_new_ids(tmp_path):
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")

    added = corpus.ensure_players(["10", "20"])

    assert added == 2
    assert set(corpus.players_needing_fetch("performance")) == {"10", "20"}
    with sqlite3.connect(corpus.db_path) as conn:
        row = conn.execute(
            "SELECT position, last_name FROM player_universe WHERE player_id = '10'"
        ).fetchone()
    assert row == (None, None)


def test_ensure_players_never_overwrites_an_existing_row(tmp_path):
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    corpus.upsert_players([{"player_id": "1", "last_name": "Musiala", "position": "Midfielder"}])

    added = corpus.ensure_players(["1", "2"])

    assert added == 1  # only "2" is new
    with sqlite3.connect(corpus.db_path) as conn:
        row = conn.execute(
            "SELECT position, last_name FROM player_universe WHERE player_id = '1'"
        ).fetchone()
    assert row == ("Midfielder", "Musiala")


def test_ensure_players_is_idempotent(tmp_path):
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")

    corpus.ensure_players(["1"])
    added_again = corpus.ensure_players(["1"])

    assert added_again == 0


def test_players_missing_position_includes_stubs_and_unknown_ids(tmp_path):
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    corpus.ensure_players(["1"])  # stub, position NULL

    missing = corpus.players_missing_position(["1", "2"])  # "2" isn't even a row yet

    assert missing == ["1", "2"]


def test_players_missing_position_excludes_players_with_a_real_position(tmp_path):
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    corpus.upsert_players([{"player_id": "1", "position": "Defender"}])
    corpus.ensure_players(["2"])

    missing = corpus.players_missing_position(["1", "2"])

    assert missing == ["2"]


def test_positions_for_returns_only_resolved_positions(tmp_path):
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    corpus.upsert_players([{"player_id": "1", "position": "Defender"}])
    corpus.ensure_players(["2"])  # stub, position NULL

    positions = corpus.positions_for(["1", "2", "3"])  # "3" isn't a row at all

    assert positions == {"1": "Defender"}


def test_positions_for_empty_input_returns_empty_dict(tmp_path):
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")
    assert corpus.positions_for([]) == {}


def test_fresh_db_gets_status_column_from_create_table(tmp_path):
    """A brand-new DB must have ``status`` from the CREATE TABLE statement
    itself, with no migration step needed."""
    corpus = TrainingCorpus(db_path=tmp_path / "corpus.db")

    with sqlite3.connect(corpus.db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(player_match_history)")}
    assert "status" in columns


def test_existing_db_without_status_column_is_migrated_preserving_rows(tmp_path):
    """The one most likely to be got wrong (REH-52): opening a pre-existing
    corpus DB -- created before ``status`` existed -- must add the column via
    ALTER TABLE and must not touch, reorder, or drop a single one of its
    existing rows."""
    db_path = tmp_path / "corpus.db"

    # Build a DB using the *pre-status* player_match_history shape directly,
    # bypassing TrainingCorpus entirely so this genuinely models an old file
    # on disk rather than something the current code already knows how to
    # produce.
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE player_universe (
                player_id      TEXT PRIMARY KEY,
                first_name     TEXT,
                last_name      TEXT,
                position       TEXT,
                team_id        TEXT,
                market_value   INTEGER,
                average_points REAL
            );
            CREATE TABLE player_match_history (
                player_id        TEXT NOT NULL,
                season           TEXT NOT NULL,
                day_number       INTEGER NOT NULL,
                match_date       TEXT,
                points           INTEGER NOT NULL,
                minutes          INTEGER NOT NULL,
                team_id          TEXT,
                opponent_team_id TEXT,
                is_home          INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (player_id, season, day_number)
            );
            CREATE TABLE mv_series (
                player_id    TEXT NOT NULL,
                snapshot_at  REAL NOT NULL,
                market_value INTEGER NOT NULL,
                PRIMARY KEY (player_id, snapshot_at)
            );
            CREATE TABLE sweep_progress (
                player_id             TEXT PRIMARY KEY,
                performance_fetched_at REAL,
                mv_fetched_at          REAL
            );
            """
        )
        conn.execute(
            "INSERT INTO player_match_history "
            "(player_id, season, day_number, match_date, points, minutes, "
            "team_id, opponent_team_id, is_home) VALUES "
            "('1', '2025/2026', 3, '2026-02-01T00:00:00Z', 42, 90, '3', '4', 1)"
        )
        conn.commit()

    with sqlite3.connect(db_path) as conn:
        pre_columns = {row[1] for row in conn.execute("PRAGMA table_info(player_match_history)")}
        pre_count = conn.execute("SELECT COUNT(*) FROM player_match_history").fetchone()[0]
    assert "status" not in pre_columns
    assert pre_count == 1

    # Opening this pre-existing file through TrainingCorpus must migrate it
    # in place.
    corpus = TrainingCorpus(db_path=db_path)

    with sqlite3.connect(db_path) as conn:
        post_columns = {row[1] for row in conn.execute("PRAGMA table_info(player_match_history)")}
    assert "status" in post_columns

    rows = corpus.matches_for_player("1")
    assert len(rows) == 1
    row = rows[0]
    assert row["season"] == "2025/2026"
    assert row["day_number"] == 3
    assert row["match_date"] == "2026-02-01T00:00:00Z"
    assert row["points"] == 42
    assert row["minutes"] == 90
    assert row["team_id"] == "3"
    assert row["opponent_team_id"] == "4"
    assert row["is_home"] == 1
    assert row["status"] is None  # pre-existing row has nothing to backfill from

    # And the migration itself must be idempotent -- reopening again (the
    # normal case on every subsequent run) must not error or duplicate work.
    corpus_again = TrainingCorpus(db_path=db_path)
    with sqlite3.connect(db_path) as conn:
        columns_again = {row[1] for row in conn.execute("PRAGMA table_info(player_match_history)")}
        count_again = conn.execute("SELECT COUNT(*) FROM player_match_history").fetchone()[0]
    assert columns_again == post_columns
    assert count_again == 1
    assert corpus_again.matches_for_player("1")[0]["points"] == 42
