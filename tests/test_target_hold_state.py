"""Target availability is a computed, logged state — not an accident.

The bot holds slots when nothing worth having is listed. That is only
defensible if it can say how many targets exist and where they are.
"""

from types import SimpleNamespace

from rehoboam.auto_trader import _target_availability


def _rec(pid, ep):
    return SimpleNamespace(
        player=SimpleNamespace(id=pid, last_name=pid),
        score=SimpleNamespace(expected_points=ep),
    )


class TestTargetAvailability:
    def test_counts_listed_targets_above_the_bar(self):
        state = _target_availability(
            [_rec("a", 120.0), _rec("b", 50.0)], competitor_ids=set(), bar=100.0
        )
        assert state["listed"] == 1

    def test_targets_held_by_opponents_are_counted_separately(self):
        state = _target_availability([_rec("a", 120.0)], competitor_ids={"a"}, bar=100.0)
        assert state["listed"] == 0
        assert state["owned_by_opponents"] == 1

    def test_no_bar_means_every_recommendation_is_a_target(self):
        state = _target_availability(
            [_rec("a", 120.0), _rec("b", 50.0)], competitor_ids=set(), bar=0.0
        )
        assert state["listed"] == 2
