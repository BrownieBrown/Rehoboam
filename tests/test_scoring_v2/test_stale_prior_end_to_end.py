"""`score_player_v2` end to end with a stale availability record (REH-98).

The live reproduction: on 2026-08-25, three days before MD1, nine market
candidates all scored P(start)=56% because the last Bundesliga matchday was
2026-05-16 -- 101 days back, past `max_status_age_days=60`. EP became a pure
points-when-playing ranking, and Can Uzun (15 of 34 starts) outranked Nathaniel
Brown (30 of 34) while contributing 18% fewer points per matchday.

These tests use the real fitted coefficients, so they assert the behaviour the
production scorer actually has.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from rehoboam.kickbase_client import MarketPlayer
from rehoboam.scoring.models import PlayerData
from rehoboam.scoring.v2.adapter import score_player_v2

STALE_MATCH_DATE = "2026-05-16T13:30:00Z"
NOW = datetime(2026, 8, 25, tzinfo=timezone.utc)


def _perf(statuses: list[int], match_date: str = STALE_MATCH_DATE) -> dict:
    return {
        "it": [
            {
                "ti": "2025/2026",
                "n": "Bundesliga",
                "ph": [{"day": i + 1, "st": st, "md": match_date} for i, st in enumerate(statuses)],
            }
        ]
    }


def _data(performance: dict | None, pid: str = "6081") -> PlayerData:
    return PlayerData(
        player=MarketPlayer(
            id=pid,
            first_name="Test",
            last_name="Player",
            position="Midfielder",
            team_id="4",
            team_name="Frankfurt",
            market_value=28_288_063,
            price=28_288_063,
            points=2232,
            average_points=106.0,
            status=0,
        ),
        performance=performance,
        player_details=None,
        team_strength=None,
        opponent_strength=None,
        is_dgw=False,
    )


# The two shapes that motivated the ticket, as real status sequences.
UZUN = [5] * 15 + [3] * 6 + [4] * 11 + [1] * 2  # 21 of 34
BROWN = [5] * 29 + [3] * 4 + [4] * 1  # 33 of 34


def _score(statuses: list[int], **kwargs):
    return score_player_v2(_data(_perf(statuses)), max_status_age_days=60.0, now=NOW, **kwargs)


class TestTheRankingInversionIsCorrected:
    def test_a_frequently_absent_player_scores_below_an_ever_present(self):
        """The headline defect: EP had these two the wrong way round."""
        assert _score(UZUN).expected_points < _score(BROWN).expected_points

    def test_the_ever_present_is_unchanged_from_the_marginal_prior(self):
        """Downward-only: Brown is under-rated, not inflated. REH-53 owns that."""
        no_record = score_player_v2(_data(None), max_status_age_days=60.0, now=NOW)
        assert _score(BROWN).expected_points == pytest.approx(no_record.expected_points, rel=1e-9)

    def test_the_absent_player_actually_loses_points(self):
        no_record = score_player_v2(_data(None), max_status_age_days=60.0, now=NOW)
        assert _score(UZUN).expected_points < no_record.expected_points


class TestTheNoteReportsTheCorrectedProbability:
    def test_p_start_in_the_note_reflects_the_reduction(self):
        """`availability_probs` is shared, so the note cannot disagree with EP."""
        note = _score(UZUN).notes[0]
        assert "P(start)=56%" not in note

    def test_an_ever_present_still_reports_the_marginal(self):
        assert "P(start)=56%" in _score(BROWN).notes[0]


class TestFreshHistoryIsUnaffected:
    def test_an_in_season_status_still_drives_availability(self):
        """With a recent match the prev_status path wins and this is inert."""
        recent = _perf(UZUN, match_date="2026-08-22T13:30:00Z")
        fresh = score_player_v2(_data(recent), max_status_age_days=60.0, now=NOW)
        stale = _score(UZUN)
        assert fresh.expected_points != pytest.approx(stale.expected_points)


class TestNoHistoryIsNotPenalised:
    def test_a_cold_start_player_keeps_the_marginal_prior(self):
        """Onyedika: transferred in with no Kickbase history. REH-41 owns him."""
        empty = score_player_v2(_data({"it": []}), max_status_age_days=60.0, now=NOW)
        no_record = score_player_v2(_data(None), max_status_age_days=60.0, now=NOW)
        assert empty.expected_points == pytest.approx(no_record.expected_points)
