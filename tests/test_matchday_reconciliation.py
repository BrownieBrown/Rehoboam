"""Tests for REH-20: matchday outcome reconciliation.

Covers the prediction snapshot path (`predicted_eps` table) and the
reconciliation step that pairs prior snapshots with finished matchday
performance (`mdst==2`) to populate `matchday_outcomes`.

Without these, `BidLearner.get_position_calibration_multiplier()` always
returns the uncalibrated default of 1.0 — the scorer self-calibration loop
shipped in PR #17 is dead.
"""

import time
from dataclasses import dataclass

import pytest

from rehoboam.bid_learner import BidLearner
from rehoboam.learning.tracker import LearningTracker

# ---------------------------------------------------------------------------
# Lightweight stand-ins for production objects. We don't pull in MarketPlayer
# or PlayerScore because both have many required fields irrelevant to this
# code path.
# ---------------------------------------------------------------------------


@dataclass
class FakePlayer:
    id: str
    last_name: str = "Tester"


@dataclass
class FakeScore:
    player_id: str
    expected_points: float
    position: str


@pytest.fixture
def learner(tmp_path):
    return BidLearner(db_path=tmp_path / "bid_learning.db")


@pytest.fixture
def tracker(learner):
    return LearningTracker(learner)


# ---------------------------------------------------------------------------
# BidLearner.snapshot_predictions / get_latest_prediction_before
# ---------------------------------------------------------------------------


class TestSnapshotPredictions:
    def test_round_trip_single_row(self, learner):
        ts = 1_700_000_000.0
        count = learner.snapshot_predictions(
            [
                {
                    "player_id": "p1",
                    "league_id": "L",
                    "predicted_at": ts,
                    "predicted_ep": 65.5,
                    "position": "MID",
                    "was_in_best_11": True,
                    "marginal_ep_gain": 4.2,
                }
            ]
        )
        assert count == 1

        snap = learner.get_latest_prediction_before("p1", ts + 1)
        assert snap is not None
        assert snap["predicted_ep"] == pytest.approx(65.5)
        assert snap["position"] == "MID"
        assert snap["was_in_best_11"] == 1

    def test_empty_input_is_noop(self, learner):
        assert learner.snapshot_predictions([]) == 0
        assert learner.get_latest_prediction_before("p1", time.time()) is None

    def test_picks_most_recent_prior_snapshot(self, learner):
        # Three snapshots: oldest, middle, newest.
        for ep, ts in [(40.0, 100), (50.0, 200), (60.0, 300)]:
            learner.snapshot_predictions(
                [
                    {
                        "player_id": "p1",
                        "league_id": "L",
                        "predicted_at": float(ts),
                        "predicted_ep": ep,
                        "position": "MID",
                        "was_in_best_11": False,
                    }
                ]
            )

        # Asking for the snapshot before t=250 should return ts=200 (middle).
        snap = learner.get_latest_prediction_before("p1", 250.0)
        assert snap is not None
        assert snap["predicted_ep"] == pytest.approx(50.0)

        # Asking for the snapshot before t=400 should return ts=300 (newest).
        snap = learner.get_latest_prediction_before("p1", 400.0)
        assert snap["predicted_ep"] == pytest.approx(60.0)

    def test_returns_none_when_no_prior_snapshot(self, learner):
        learner.snapshot_predictions(
            [
                {
                    "player_id": "p1",
                    "league_id": "L",
                    "predicted_at": 500.0,
                    "predicted_ep": 60.0,
                    "position": "MID",
                    "was_in_best_11": False,
                }
            ]
        )
        # Snapshot is AFTER the requested cutoff — nothing returned.
        assert learner.get_latest_prediction_before("p1", 100.0) is None

    def test_replace_on_same_pk(self, learner):
        ts = 1_700_000_000.0
        learner.snapshot_predictions(
            [
                {
                    "player_id": "p1",
                    "league_id": "L",
                    "predicted_at": ts,
                    "predicted_ep": 50.0,
                    "position": "MID",
                    "was_in_best_11": False,
                }
            ]
        )
        # Re-snapshot at the same ts with a different EP — primary key
        # (player_id, predicted_at) means the second write replaces the first.
        learner.snapshot_predictions(
            [
                {
                    "player_id": "p1",
                    "league_id": "L",
                    "predicted_at": ts,
                    "predicted_ep": 70.0,
                    "position": "MID",
                    "was_in_best_11": True,
                }
            ]
        )
        snap = learner.get_latest_prediction_before("p1", ts + 1)
        assert snap["predicted_ep"] == pytest.approx(70.0)
        assert snap["was_in_best_11"] == 1


class TestHasMatchdayOutcome:
    def test_returns_false_when_empty(self, learner):
        assert learner.has_matchday_outcome("p1", "2026-04-15T15:30:00Z") is False

    def test_returns_true_after_record(self, learner):
        learner.record_matchday_outcome(
            player_id="p1",
            player_position="MID",
            matchday_date="2026-04-15T15:30:00Z",
            predicted_ep=60.0,
            actual_points=72.0,
        )
        assert learner.has_matchday_outcome("p1", "2026-04-15T15:30:00Z") is True
        # Different date → still false.
        assert learner.has_matchday_outcome("p1", "2026-04-22T15:30:00Z") is False


# ---------------------------------------------------------------------------
# LearningTracker.snapshot_predictions
# ---------------------------------------------------------------------------


class TestTrackerSnapshot:
    def test_writes_one_row_per_squad_score(self, tracker, learner):
        scores = [
            FakeScore(player_id="p1", expected_points=60.0, position="MID"),
            FakeScore(player_id="p2", expected_points=45.0, position="DEF"),
        ]
        n = tracker.snapshot_predictions(
            league_id="L",
            squad_scores=scores,
            best_11_ids={"p1"},
            predicted_at=1_000.0,
        )
        assert n == 2
        snap1 = learner.get_latest_prediction_before("p1", 2_000.0)
        snap2 = learner.get_latest_prediction_before("p2", 2_000.0)
        assert snap1["was_in_best_11"] == 1
        assert snap2["was_in_best_11"] == 0

    def test_empty_squad_scores_is_zero_rows(self, tracker):
        n = tracker.snapshot_predictions(
            league_id="L",
            squad_scores=[],
            best_11_ids=set(),
        )
        assert n == 0


# ---------------------------------------------------------------------------
# LearningTracker.reconcile_finished_matchdays
# ---------------------------------------------------------------------------


def _perf_with_matches(matches: list[dict]) -> dict:
    """Wrap a list of match dicts in the structure produced by
    `get_player_performance` — `it[0].ph` is the current season's history."""
    return {"it": [{"ti": "2026", "ph": matches}]}


class TestReconcileFinishedMatchdays:
    def test_records_finished_match_with_prior_snapshot(self, tracker, learner):
        # Snapshot a prediction BEFORE the matchday's `md` timestamp.
        # Match `md` = 2026-04-15T15:30:00Z = unix 1776264600
        match_ts = 1_776_264_600
        snapshot_ts = match_ts - 3600  # 1h before kickoff
        tracker.snapshot_predictions(
            league_id="L",
            squad_scores=[FakeScore(player_id="p1", expected_points=60.0, position="MID")],
            best_11_ids={"p1"},
            predicted_at=float(snapshot_ts),
        )

        squad = [FakePlayer(id="p1")]
        perf = {
            "p1": _perf_with_matches(
                [{"mi": "1", "p": 72, "md": "2026-04-15T15:30:00Z", "mdst": 2}]
            )
        }
        n = tracker.reconcile_finished_matchdays(squad, perf)
        assert n == 1
        assert learner.has_matchday_outcome("p1", "2026-04-15T15:30:00Z")

    def test_skips_unfinished_match(self, tracker, learner):
        match_ts = 1_776_264_600
        tracker.snapshot_predictions(
            league_id="L",
            squad_scores=[FakeScore(player_id="p1", expected_points=60.0, position="MID")],
            best_11_ids=set(),
            predicted_at=float(match_ts - 3600),
        )

        # mdst=0 = upcoming, mdst=1 = in progress; only mdst=2 should record.
        perf = {
            "p1": _perf_with_matches(
                [
                    {"mi": "1", "p": 0, "md": "2026-04-15T15:30:00Z", "mdst": 0},
                    {"mi": "2", "p": 50, "md": "2026-04-22T15:30:00Z", "mdst": 1},
                ]
            )
        }
        n = tracker.reconcile_finished_matchdays([FakePlayer(id="p1")], perf)
        assert n == 0

    def test_idempotent_on_second_call(self, tracker, learner):
        match_ts = 1_776_264_600
        tracker.snapshot_predictions(
            league_id="L",
            squad_scores=[FakeScore(player_id="p1", expected_points=60.0, position="MID")],
            best_11_ids=set(),
            predicted_at=float(match_ts - 3600),
        )
        squad = [FakePlayer(id="p1")]
        perf = {
            "p1": _perf_with_matches(
                [{"mi": "1", "p": 72, "md": "2026-04-15T15:30:00Z", "mdst": 2}]
            )
        }
        first = tracker.reconcile_finished_matchdays(squad, perf)
        second = tracker.reconcile_finished_matchdays(squad, perf)
        assert first == 1
        # Second pass sees the existing row via has_matchday_outcome and skips.
        assert second == 0

    def test_skips_player_without_prior_snapshot(self, tracker, learner):
        # No snapshot persisted before reconciliation → cannot pair predicted
        # with actual; row must NOT be written.
        squad = [FakePlayer(id="p1")]
        perf = {
            "p1": _perf_with_matches(
                [{"mi": "1", "p": 72, "md": "2026-04-15T15:30:00Z", "mdst": 2}]
            )
        }
        n = tracker.reconcile_finished_matchdays(squad, perf)
        assert n == 0
        assert not learner.has_matchday_outcome("p1", "2026-04-15T15:30:00Z")

    def test_skips_match_when_only_snapshot_is_after_kickoff(self, tracker, learner):
        # The only snapshot we have was taken AFTER the matchday — that's
        # a leak (post-kickoff prediction shouldn't count as "predicted").
        # `get_latest_prediction_before` filters this out.
        match_ts = 1_776_264_600
        tracker.snapshot_predictions(
            league_id="L",
            squad_scores=[FakeScore(player_id="p1", expected_points=60.0, position="MID")],
            best_11_ids=set(),
            predicted_at=float(match_ts + 3600),  # 1h AFTER kickoff
        )
        squad = [FakePlayer(id="p1")]
        perf = {
            "p1": _perf_with_matches(
                [{"mi": "1", "p": 72, "md": "2026-04-15T15:30:00Z", "mdst": 2}]
            )
        }
        n = tracker.reconcile_finished_matchdays(squad, perf)
        assert n == 0

    def test_handles_missing_performance(self, tracker, learner):
        # Player in squad but no perf data at all (e.g. fetch failed).
        squad = [FakePlayer(id="p1"), FakePlayer(id="p2")]
        perf = {}  # nothing
        n = tracker.reconcile_finished_matchdays(squad, perf)
        assert n == 0

    def test_handles_malformed_md_string(self, tracker, learner):
        # Snapshot exists, but `md` field on the match is unparseable —
        # reconcile should swallow and continue without raising.
        tracker.snapshot_predictions(
            league_id="L",
            squad_scores=[FakeScore(player_id="p1", expected_points=60.0, position="MID")],
            best_11_ids=set(),
            predicted_at=1_000.0,
        )
        squad = [FakePlayer(id="p1")]
        perf = {"p1": _perf_with_matches([{"mi": "1", "p": 72, "md": "not-a-date", "mdst": 2}])}
        n = tracker.reconcile_finished_matchdays(squad, perf)
        assert n == 0
