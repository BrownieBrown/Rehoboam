"""Tests for rehoboam.scoring.v2.features — leak-free feature construction."""

from __future__ import annotations

import pytest

from rehoboam.scoring.v2.features import MatchRow, build_feature_rows


def _m(day: int, status: int | None, points: int, minutes: int) -> MatchRow:
    return MatchRow(
        player_id="1",
        season="2024/2025",
        day_number=day,
        status=status,
        points=points,
        minutes=minutes,
    )


def test_first_match_has_no_previous_status():
    rows = build_feature_rows([_m(1, 5, 80, 90)])
    assert len(rows) == 1
    assert rows[0].prev_status is None
    assert rows[0].matches_seen == 0
    assert rows[0].rolling_minutes_3 == 0.0


def test_prev_status_is_the_immediately_preceding_match():
    rows = build_feature_rows([_m(1, 5, 80, 90), _m(2, 3, 10, 20), _m(3, 4, 0, 0)])
    assert [r.prev_status for r in rows] == [None, 5, 3]


def test_rolling_minutes_uses_only_prior_matches():
    """The row for day 4 must not see day 4's own minutes."""
    rows = build_feature_rows(
        [_m(1, 5, 80, 90), _m(2, 5, 70, 90), _m(3, 5, 60, 90), _m(4, 4, 0, 0)]
    )
    # day 4 sees days 1-3 only: (90+90+90)/3
    assert rows[3].rolling_minutes_3 == pytest.approx(90.0)
    # day 2 sees day 1 only
    assert rows[1].rolling_minutes_3 == pytest.approx(90.0)


def test_rolling_minutes_window_is_capped_at_three():
    rows = build_feature_rows(
        [
            _m(1, 5, 0, 0),
            _m(2, 5, 0, 90),
            _m(3, 5, 0, 90),
            _m(4, 5, 0, 90),
            _m(5, 5, 0, 0),
        ]
    )
    # day 5 sees days 2,3,4 — NOT day 1's zero
    assert rows[4].rolling_minutes_3 == pytest.approx(90.0)


def test_matches_seen_counts_prior_rows():
    rows = build_feature_rows([_m(1, 5, 0, 90), _m(2, 5, 0, 90), _m(3, 5, 0, 90)])
    assert [r.matches_seen for r in rows] == [0, 1, 2]


def test_target_carries_this_row_own_outcome():
    rows = build_feature_rows([_m(1, 5, 80, 90), _m(2, 3, 12, 20)])
    assert [(r.target_status, r.target_points) for r in rows] == [(5, 80), (3, 12)]


def test_rows_are_ordered_by_day_regardless_of_input_order():
    rows = build_feature_rows([_m(3, 5, 0, 90), _m(1, 5, 0, 90), _m(2, 5, 0, 90)])
    assert [r.day_number for r in rows] == [1, 2, 3]


def test_season_boundary_resets_history():
    """A new season starts fresh — last May's form is not last week's form."""
    a = MatchRow("1", "2023/2024", 34, 5, 80, 90)
    b = MatchRow("1", "2024/2025", 1, 5, 70, 90)
    rows = build_feature_rows([a, b])
    assert rows[1].prev_status is None
    assert rows[1].matches_seen == 0


def test_unplayed_fixtures_are_excluded():
    """status 0/None means the match has not been played — it is not a training row."""
    rows = build_feature_rows([_m(1, 5, 80, 90), _m(2, 0, 0, 0), _m(3, None, 0, 0)])
    assert [r.day_number for r in rows] == [1]


def test_empty_input_returns_empty():
    assert build_feature_rows([]) == []


def test_mixed_players_raise_instead_of_silently_interleaving():
    """Sorting is by (season, day_number) only — mixed input would otherwise
    silently hand one player's rolling history to another player's row.
    """
    a1 = MatchRow("A", "2024/2025", 1, 5, 80, 90)
    b1 = MatchRow("B", "2024/2025", 1, 1, 0, 0)
    a2 = MatchRow("A", "2024/2025", 2, 5, 70, 90)

    with pytest.raises(ValueError, match="one player"):
        build_feature_rows([a1, b1, a2])
