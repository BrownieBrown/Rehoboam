"""Extracting a player's own availability record from `performance` (REH-98).

`apply_stale_history_prior` needs the share of matchdays a player was actually
on the pitch. This is where that comes from: the same `performance` dict the
scorer already fetches, so it costs no extra HTTP traffic.

Policy, decided 2026-08-25:

- **Most recent qualifying season only.** A blend of the last two smears
  exactly the signal this exists to read -- a player whose role is changing.
  Can Uzun finished 2025/26 with four consecutive starts after half a season
  out of the squad; a two-season average would hide that in both directions.
- **2. Bundesliga counts.** REH-90 establishes that 2.BL *scoring rate* must
  not be read as Bundesliga rate, but availability is a different quantity:
  "started 31 of 34" is evidence of durability and a secure role, and that
  travels across divisions in a way that points-per-match does not.

The denominator is matchdays *recorded*, not appearances. A matchday spent as
an unused sub (status 4) or out of the squad (status 1) is precisely the
evidence being captured -- counting only appearances would make every player
look perfectly available.
"""

import pytest

from rehoboam.scoring.v2.adapter import recent_played_share


def _season(title: str, statuses: list[int], competition: str = "Bundesliga") -> dict:
    return {
        "ti": title,
        "n": competition,
        "ph": [{"day": i + 1, "st": st} for i, st in enumerate(statuses)],
    }


def _perf(*seasons: dict) -> dict:
    """`it` is ordered oldest-first, as the API returns it."""
    return {"it": list(seasons)}


class TestTheDenominatorCountsMatchdaysNotAppearances:
    def test_uzun_is_read_as_21_of_34(self):
        """15 starts, 6 sub appearances, 13 matchdays not on the pitch."""
        statuses = [5] * 15 + [3] * 6 + [4] * 11 + [1] * 2
        assert recent_played_share(_perf(_season("2025/2026", statuses))) == (21, 34)

    def test_an_ever_present_is_read_as_all_of_them(self):
        assert recent_played_share(_perf(_season("2025/2026", [5] * 34))) == (34, 34)

    def test_unused_subs_stay_in_the_denominator(self):
        """The whole point: a benched matchday is evidence, not an absence."""
        played, matchdays = recent_played_share(_perf(_season("2025/2026", [5] * 10 + [4] * 24)))
        assert (played, matchdays) == (10, 34)


class TestUnplayedFixturesAreExcluded:
    def test_status_zero_does_not_count_against_a_player(self):
        """Pre-season: the 2026/27 fixtures exist but have not been played."""
        statuses = [5] * 10 + [0] * 24
        assert recent_played_share(_perf(_season("2025/2026", statuses))) == (10, 10)

    def test_a_not_yet_started_season_is_skipped_for_the_last_real_one(self):
        """The live pre-season case that motivated the ticket."""
        result = recent_played_share(
            _perf(
                _season("2025/2026", [5] * 30 + [4] * 4),
                _season("2026/2027", [0] * 34),
            )
        )
        assert result == (30, 34)


class TestSeasonSelection:
    def test_the_most_recent_qualifying_season_wins(self):
        result = recent_played_share(
            _perf(
                _season("2024/2025", [5] * 34),
                _season("2025/2026", [4] * 34),
            )
        )
        assert result == (0, 34), "the older ever-present season must not win"

    def test_second_bundesliga_counts_as_availability_evidence(self):
        result = recent_played_share(
            _perf(_season("2025/2026", [5] * 30 + [4] * 4, competition="2. Bundesliga"))
        )
        assert result == (30, 34)

    def test_a_season_shorter_than_the_minimum_is_skipped(self):
        """A three-match cameo is not a role; fall through to a real season."""
        result = recent_played_share(
            _perf(
                _season("2024/2025", [5] * 20 + [4] * 14),
                _season("2025/2026", [3] * 3),
            )
        )
        assert result == (20, 34)


class TestNoEvidence:
    def test_missing_performance_returns_none(self):
        assert recent_played_share(None) is None

    def test_empty_performance_returns_none(self):
        assert recent_played_share({}) is None

    def test_a_player_with_no_seasons_returns_none(self):
        """Onyedika: transferred in, zero Kickbase history. REH-41 owns him."""
        assert recent_played_share(_perf()) is None

    def test_a_player_with_only_unplayed_fixtures_returns_none(self):
        assert recent_played_share(_perf(_season("2026/2027", [0] * 34))) is None


class TestMalformedData:
    @pytest.mark.parametrize("season", [{"ti": "2025/2026"}, {"ti": "x", "ph": None}])
    def test_a_season_without_matches_is_skipped(self, season):
        assert recent_played_share(_perf(season)) is None

    def test_a_match_without_a_status_is_ignored(self):
        perf = {"it": [{"ti": "2025/2026", "ph": [{"day": i} for i in range(34)]}]}
        assert recent_played_share(perf) is None
