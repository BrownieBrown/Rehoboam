"""Tests for rehoboam.scoring.v2.adapter — composing fitted models into PlayerScore."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from rehoboam.kickbase_client import MarketPlayer
from rehoboam.scoring.models import PlayerData
from rehoboam.scoring.v2.adapter import (
    compose_ep,
    has_top_flight_history,
    last_played_status,
    prev_status_from_history,
    score_player_v2,
)
from rehoboam.scoring.v2.availability import fit_availability
from rehoboam.scoring.v2.features import FeatureRow
from rehoboam.scoring.v2.rate import fit_rate


def _perf(matches: list[dict]) -> dict:
    return {"it": [{"ti": "2025/2026", "ph": matches}]}


def _player(pid: str = "1") -> MarketPlayer:
    return MarketPlayer(
        id=pid,
        first_name="Test",
        last_name="Player",
        position="Midfielder",
        team_id="2",
        team_name="T",
        market_value=1_000_000,
        price=1_000_000,
        points=0,
        average_points=0.0,
        status=0,
    )


def _data(pid: str = "1", performance: dict | None = None) -> PlayerData:
    return PlayerData(
        player=_player(pid),
        performance=performance,
        player_details=None,
        team_strength=None,
        opponent_strength=None,
        is_dgw=False,
    )


def _row(pid: str, prev: int | None, status: int, points: int) -> FeatureRow:
    return FeatureRow(
        player_id=pid,
        season="2024/2025",
        day_number=1,
        prev_status=prev,
        rolling_minutes_3=90.0,
        matches_seen=5,
        target_status=status,
        target_points=points,
    )


class TestPrevStatusFromHistory:
    def test_returns_the_latest_played_status(self):
        history = [
            ("2026-05-01T13:30:00Z", 5),
            ("2026-05-09T16:30:00Z", 4),
        ]
        assert prev_status_from_history(history) == 4

    def test_skips_unplayed_fixtures(self):
        history = [
            ("2026-05-01T13:30:00Z", 5),
            ("2026-08-29T13:30:00Z", 0),
            ("2026-09-05T13:30:00Z", None),
        ]
        assert prev_status_from_history(history) == 5

    def test_no_played_history_returns_none(self):
        assert prev_status_from_history([("2026-08-29T13:30:00Z", 0)]) is None

    def test_empty_history_returns_none(self):
        assert prev_status_from_history([]) is None


def test_last_played_status_reads_the_most_recent_played_match():
    perf = _perf(
        [
            {"day": 1, "st": 5, "p": 80, "mp": "90'"},
            {"day": 2, "st": 3, "p": 12, "mp": "20'"},
        ]
    )
    assert last_played_status(perf) == 3


def test_last_played_status_ignores_unplayed_fixtures():
    """status 0 means the fixture has not happened — it is not 'his last state'."""
    perf = _perf(
        [
            {"day": 1, "st": 5, "p": 80, "mp": "90'"},
            {"day": 2, "st": 0},
        ]
    )
    assert last_played_status(perf) == 5


def test_last_played_status_prefers_the_later_season_over_a_higher_day_number():
    """'Most recent played match' is across seasons, not within one.

    A day-1 match this season is more recent than a day-34 match last season.
    Pinned in both list orders so neither "first season wins" nor "last season
    wins" can pass by accident.
    """
    this_season = ("2025/2026", [{"day": 1, "st": 3, "p": 12, "mp": "20'"}])
    last_season = ("2024/2025", [{"day": 34, "st": 5, "p": 80, "mp": "90'"}])

    for ordering in ((this_season, last_season), (last_season, this_season)):
        perf = {"it": [{"ti": title, "ph": matches} for title, matches in ordering]}
        assert last_played_status(perf) == 3


def test_last_played_status_returns_none_without_history():
    assert last_played_status(None) is None
    assert last_played_status({"it": []}) is None
    assert last_played_status(_perf([])) is None


def test_compose_ep_is_the_probability_weighted_sum():
    rows = [_row("1", 5, 5, 90)] * 20
    av, rate = fit_availability(rows), fit_rate(rows, {"1": "Midfielder"})
    probs = av.predict(5)
    expected = sum(probs[s] * rate.predict("1", s, "Midfielder") for s in (1, 3, 4, 5))
    assert compose_ep("1", 5, "Midfielder", av, rate) == pytest.approx(expected)


def test_score_is_in_real_points_not_an_index():
    """A player who reliably starts and scores ~90 should score near 90, not 40."""
    perf = _perf([{"day": d, "st": 5, "p": 90, "mp": "90'"} for d in range(1, 11)])
    score = score_player_v2(_data(performance=perf))
    assert score.expected_points > 50.0, "real points, not a 0-100 index"


def test_v1_only_fields_are_zeroed_and_explained():
    """PlayerScore carries v1's decomposition; v2 has no counterpart for it."""
    perf = _perf([{"day": 1, "st": 5, "p": 80, "mp": "90'"}])
    score = score_player_v2(_data(performance=perf))
    assert score.base_points == 0.0
    assert score.consistency_bonus == 0.0
    assert score.lineup_bonus == 0.0
    assert score.fixture_bonus == 0.0
    assert score.form_bonus == 0.0
    assert score.minutes_bonus == 0.0
    assert any("availability" in n.lower() for n in score.notes)


def test_player_with_no_history_still_scores():
    """A new signing must not crash — the model falls back to its prior."""
    score = score_player_v2(_data(pid="unknown-player", performance=None))
    assert score.expected_points >= 0.0
    assert score.player_id == "unknown-player"


def test_score_carries_identity_fields_decisions_depend_on():
    perf = _perf([{"day": 1, "st": 5, "p": 80, "mp": "90'"}])
    score = score_player_v2(_data(performance=perf))
    assert score.player_id == "1"
    assert score.position == "Midfielder"
    assert score.market_value == 1_000_000


def test_dgw_multiplies_the_composed_score():
    perf = _perf([{"day": 1, "st": 5, "p": 80, "mp": "90'"}])
    single = score_player_v2(_data(performance=perf))
    dgw_data = _data(performance=perf)
    dgw_data.is_dgw = True
    doubled = score_player_v2(dgw_data)
    assert doubled.expected_points > single.expected_points
    assert doubled.is_dgw is True


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class TestPrevStatusRecency:
    def test_stale_status_is_discarded(self):
        """The live Pavlovic case: an unused-sub appearance from 3 months ago."""
        now = datetime(2026, 8, 22, tzinfo=timezone.utc)
        history = [("2026-05-16T13:30:00Z", 4)]
        assert prev_status_from_history(history, now=now, max_age_days=60.0) is None

    def test_recent_status_is_kept(self):
        now = datetime(2026, 8, 22, tzinfo=timezone.utc)
        history = [(_iso(now - timedelta(days=7)), 4)]
        assert prev_status_from_history(history, now=now, max_age_days=60.0) == 4

    def test_no_bound_keeps_current_behaviour(self):
        now = datetime(2026, 8, 22, tzinfo=timezone.utc)
        history = [("2026-05-16T13:30:00Z", 4)]
        assert prev_status_from_history(history, now=now) == 4

    def test_unparseable_date_is_treated_as_stale(self):
        """Fail closed: an unknown date cannot be shown to be recent."""
        now = datetime(2026, 8, 22, tzinfo=timezone.utc)
        assert prev_status_from_history([("not-a-date", 5)], now=now, max_age_days=60.0) is None

    def test_missing_date_is_treated_as_stale(self):
        now = datetime(2026, 8, 22, tzinfo=timezone.utc)
        assert prev_status_from_history([(None, 5)], now=now, max_age_days=60.0) is None


class TestTopFlightHistory:
    def test_player_with_average_points_has_top_flight_history(self):
        assert has_top_flight_history({"ap": 119, "tp": 2844}) is True

    def test_missing_ap_means_no_top_flight_history(self):
        """The live Elversberg case: full 2. Bundesliga record, no `ap` field."""
        assert has_top_flight_history({"fn": "Maximilian", "ln": "Rohr"}) is False

    def test_zero_ap_means_no_top_flight_history(self):
        assert has_top_flight_history({"ap": 0, "tp": 0}) is False

    def test_missing_details_fails_open(self):
        """None means "unknown", not "no top-flight history" — a transient
        get_player_details failure must not withhold a fitted quality coefficient."""
        assert has_top_flight_history(None) is True


class TestNonTopFlightUsesPositionPrior:
    def test_fitted_quality_is_withheld_without_top_flight_history(self):
        """A 2. Bundesliga record must not buy a confident Bundesliga rate."""
        from rehoboam.scoring.models import PlayerData

        player = _player("3284")  # Rohr, present in the fitted quality table
        matches = [
            {"day": d, "st": 5, "p": 100, "md": "2026-05-16T13:30:00Z"} for d in range(1, 35)
        ]
        with_history = PlayerData(
            player=player,
            performance=_perf(matches),
            player_details={"ap": 74, "tp": 1251},
            team_strength=None,
            opponent_strength=None,
            is_dgw=False,
        )
        without_history = PlayerData(
            player=player,
            performance=_perf(matches),
            player_details={"fn": "Maximilian", "ln": "Rohr"},
            team_strength=None,
            opponent_strength=None,
            is_dgw=False,
        )

        scored_with = score_player_v2(with_history)
        scored_without = score_player_v2(without_history)

        assert scored_without.expected_points < scored_with.expected_points
        assert scored_without.data_quality.grade != "A"
        assert any("position prior" in n for n in scored_without.notes)

    def test_missing_player_details_keeps_fitted_quality(self):
        """The transient-lookup-failure case (REH fail-open fix): a player who IS
        in the fitted quality table, scored with player_details=None (e.g. a
        get_player_details HTTP blip mid-session — see Trader._fetch_player_data),
        must keep his fitted quality — same EP as when details are present —
        not fall to the position prior. Fails without the fail-open fix."""
        player = _player("3284")  # Rohr, present in the fitted quality table
        matches = [
            {"day": d, "st": 5, "p": 100, "md": "2026-05-16T13:30:00Z"} for d in range(1, 35)
        ]
        with_history = PlayerData(
            player=player,
            performance=_perf(matches),
            player_details={"ap": 74, "tp": 1251},
            team_strength=None,
            opponent_strength=None,
            is_dgw=False,
        )
        unknown_details = PlayerData(
            player=player,
            performance=_perf(matches),
            player_details=None,
            team_strength=None,
            opponent_strength=None,
            is_dgw=False,
        )

        scored_with = score_player_v2(with_history)
        scored_unknown = score_player_v2(unknown_details)

        assert scored_unknown.expected_points == scored_with.expected_points
        assert scored_unknown.data_quality.grade == "A"
        assert not any("position prior" in n for n in scored_unknown.notes)


class TestLiveInjuryStatusReachesTheScore:
    """The serving-time override must actually reach EP, not just exist.

    On 2026-08-22 Hoeler carried status 256 (long-term injury), scored 47.2 EP,
    and was named in the bot's starting eleven. Nothing in scoring/v2 read the
    flag. These pin that it is now read.
    """

    @staticmethod
    def _data(status_code):
        from rehoboam.scoring.models import PlayerData

        matches = [{"day": d, "st": 5, "p": 90, "md": "2026-05-16T13:30:00Z"} for d in range(1, 35)]
        return PlayerData(
            player=_player("1856"),
            performance=_perf(matches),
            player_details={"ap": 83, "tp": 2000, "st": status_code},
            team_strength=None,
            opponent_strength=None,
            is_dgw=False,
        )

    def test_long_term_injury_collapses_expected_points(self):
        healthy = score_player_v2(self._data(0))
        injured = score_player_v2(self._data(256))

        assert healthy.expected_points > 10.0, "baseline must be non-trivial"
        assert (
            injured.expected_points < 1.0
        ), f"a long-term-injured player must not score {injured.expected_points}"

    def test_short_term_injury_collapses_expected_points(self):
        assert score_player_v2(self._data(4)).expected_points < 1.0

    def test_uncertain_reduces_but_does_not_erase(self):
        """Fuehrich's case: status 2 is a haircut, not a verdict."""
        healthy = score_player_v2(self._data(0))
        uncertain = score_player_v2(self._data(2))

        assert 0.0 < uncertain.expected_points < healthy.expected_points

    def test_missing_status_scores_as_healthy(self):
        from rehoboam.scoring.models import PlayerData

        healthy = score_player_v2(self._data(0))
        no_details = PlayerData(
            player=_player("1856"),
            performance=self._data(0).performance,
            player_details=None,
            team_strength=None,
            opponent_strength=None,
            is_dgw=False,
        )
        assert score_player_v2(no_details).expected_points == pytest.approx(healthy.expected_points)
