"""The row builders are pure: Kickbase payload in, row dicts out."""

from __future__ import annotations

from datetime import date

from rehoboam.enrichment import rows


def test_match_history_rows_use_pt_for_team_and_skip_dayless_matches():
    perf = {
        "it": [
            {
                "ti": "2025/2026",
                "ph": [
                    {
                        "day": 1,
                        "p": 80,
                        "mp": "90'",
                        "t1": "3",
                        "t2": "4",
                        "pt": "4",
                        "st": 5,
                    },
                    {"p": 10},  # no day: cannot be placed on a timeline
                ],
            }
        ]
    }
    out = rows.match_history_rows("p1", "3", perf)
    assert out == [
        {
            "player_id": "p1",
            "season": "2025/2026",
            "day_number": 1,
            "match_date": None,
            "points": 80,
            "minutes": 90,
            "team_id": "4",
            "opponent_team_id": "3",
            "is_home": 0,
            "status": 5,
        }
    ]


def test_match_history_rows_fall_back_to_caller_team_when_pt_missing():
    perf = {"it": [{"ti": "s", "ph": [{"day": 2, "p": 0, "t1": "3", "t2": "4"}]}]}
    (row,) = rows.match_history_rows("p1", "3", perf)
    assert (row["team_id"], row["is_home"], row["opponent_team_id"]) == ("3", 1, "4")


def test_mv_series_rows_drop_sentinels_and_scale_days():
    out = rows.mv_series_rows("p1", {"it": [{"dt": 20000, "mv": 5}, {"dt": 20001, "mv": 0}]})
    assert out == [{"player_id": "p1", "snapshot_at": 20000 * 86400.0, "market_value": 5}]


def test_transfer_rows_skip_items_without_dt():
    hist = {
        "it": [
            {"u": "9", "unm": "X", "dt": "2026-08-01T10:00:00Z", "trp": 7, "t": 2},
            {"trp": 1},
        ]
    }
    (row,) = rows.transfer_rows("p1", hist)
    assert row["counterparty_id"] == "9" and row["price"] == 7 and row["transfer_type"] == 2
    assert isinstance(row["transfer_at"], float)


def test_status_row_reads_st_prob_mv_tid_and_the_last_mv_change():
    row = rows.status_row(
        "p1",
        date(2026, 9, 14),
        {
            "st": 0,
            "prob": 1,
            "mv": 1_000_000,
            "tid": 7,
            "tfhmvt": -25_000,
            "g": 0,
            "a": 0,
            "y": 0,
            "r": 0,
            "sec": 11508,
            "tp": 242,
            "ap": 121.0,
            "pim": "content/file/abc.png",
        },
        123.0,
    )
    assert row == {
        "player_id": "p1",
        "day": date(2026, 9, 14),
        "status": 0,
        "lineup_probability": 1,
        "market_value": 1_000_000,
        "mv_change": -25_000,
        "team_id": "7",
        "fetched_at": 123.0,
        "goals": 0,
        "assists": 0,
        "yellow_cards": 0,
        "red_cards": 0,
        "seconds_played": 11508,
        "season_points": 242,
        "season_average": 121.0,
        "image_source": "content/file/abc.png",
    }


def test_status_row_tolerates_missing_fields():
    row = rows.status_row("p1", date(2026, 9, 14), {}, 1.0)
    assert (
        row["status"],
        row["lineup_probability"],
        row["market_value"],
        row["mv_change"],
        row["team_id"],
        row["goals"],
        row["assists"],
        row["yellow_cards"],
        row["red_cards"],
        row["seconds_played"],
        row["season_points"],
        row["season_average"],
        row["image_source"],
    ) == (
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    )


def test_universe_rows_map_live_field_names():
    out = rows.universe_rows([{"pi": 5, "n": "Kane", "pos": 4, "tid": "2", "mv": 9, "ap": 1.5}, {}])
    assert out == [
        {
            "player_id": "5",
            "first_name": None,
            "last_name": "Kane",
            "position": "Forward",
            "team_id": "2",
            "market_value": 9,
            "average_points": 1.5,
        }
    ]
