"""The basket is scored by the slots it actually closes (spec 2026-09-11 §5).

On 2026-09-07 the squad was 1 GK, 6 DEF, 3 MID, 1 FW: ten can start, one slot
short, and only a midfielder or forward can close it. The basket objective
counted bodies — `+100 x players_bought` — so with EUR 2,334,394 to spend it
bought Stergiou, a seventh defender, for 2,294,821 and the lineup stayed at
ten. With `gap_after` the objective counts closed slots, and a player who
closes none is not an emergency buy at all.
"""

from __future__ import annotations

from rehoboam.formation import fieldability_from_counts
from rehoboam.services.emergency_basket import (
    EmergencyCandidate,
    select_emergency_basket,
)

SQUAD_2026_09_11 = {"Goalkeeper": 1, "Defender": 6, "Midfielder": 3, "Forward": 1}


def _gap_after_for(counts: dict[str, int]):
    def gap_after(positions):
        after = dict(counts)
        for pos in positions:
            after[pos] = after.get(pos, 0) + 1
        return fieldability_from_counts(after).purchases

    return gap_after


GUTIERREZ = EmergencyCandidate(
    "3759",
    "Gutiérrez",
    ask=22_308_405,
    max_bid=28_418_405,
    ep=63.5,
    position="Defender",
)
STERGIOU = EmergencyCandidate(
    "7257", "Stergiou", ask=2_054_821, max_bid=2_294_821, ep=3.9, position="Defender"
)
REGEER = EmergencyCandidate(
    "17288", "Regeer", ask=6_239_298, max_bid=8_079_298, ep=68.4, position="Midfielder"
)
MOFFI = EmergencyCandidate(
    "17203", "Moffi", ask=4_398_390, max_bid=5_497_987, ep=62.4, position="Forward"
)
BOARD = [GUTIERREZ, STERGIOU, REGEER, MOFFI]


class TestOneShortWithSaturatedDefence:
    def test_buys_the_midfielder_that_closes_the_slot(self):
        picks = select_emergency_basket(
            BOARD,
            slots_short=1,
            budget=12_929_567,
            gap_after=_gap_after_for(SQUAD_2026_09_11),
        )
        assert [p.candidate.id for p in picks] == [REGEER.id]

    def test_a_seventh_defender_is_not_an_emergency_buy(self):
        """The 2026-09-07 session: only Stergiou is affordable. He closes
        nothing, so the answer is 'nothing', not 'Stergiou'."""
        picks = select_emergency_basket(
            BOARD,
            slots_short=1,
            budget=2_334_394,
            gap_after=_gap_after_for(SQUAD_2026_09_11),
        )
        assert picks == []

    def test_a_cheaper_forward_wins_when_the_midfielder_is_unaffordable(self):
        picks = select_emergency_basket(
            BOARD,
            slots_short=1,
            budget=5_000_000,
            gap_after=_gap_after_for(SQUAD_2026_09_11),
        )
        assert [p.candidate.id for p in picks] == [MOFFI.id]


class TestTwoShort:
    """GK 1, DEF 6, MID 3, FW 0: two purchases. MID+MID leaves 5-4-1 a forward
    short; MID+FW or FW+FW closes both. Counting bodies would pick the two
    midfielders (EP 70 + 65) and leave a slot empty."""

    COUNTS = {"Goalkeeper": 1, "Defender": 6, "Midfielder": 3, "Forward": 0}
    M1 = EmergencyCandidate(
        "m1", "M1", ask=1_000_000, max_bid=1_100_000, ep=70.0, position="Midfielder"
    )
    M2 = EmergencyCandidate(
        "m2", "M2", ask=1_000_000, max_bid=1_100_000, ep=65.0, position="Midfielder"
    )
    F1 = EmergencyCandidate(
        "f1", "F1", ask=1_000_000, max_bid=1_100_000, ep=40.0, position="Forward"
    )
    F2 = EmergencyCandidate(
        "f2", "F2", ask=1_000_000, max_bid=1_100_000, ep=35.0, position="Forward"
    )

    def test_closes_both_slots_rather_than_maximising_ep(self):
        picks = select_emergency_basket(
            [self.M1, self.M2, self.F1, self.F2],
            slots_short=2,
            budget=10_000_000,
            gap_after=_gap_after_for(self.COUNTS),
        )
        assert {p.candidate.id for p in picks} == {"m1", "f1"}

    def test_with_money_for_one_it_still_closes_one(self):
        picks = select_emergency_basket(
            [self.M1, self.M2, self.F1, self.F2],
            slots_short=2,
            budget=1_500_000,
            gap_after=_gap_after_for(self.COUNTS),
        )
        assert [p.candidate.id for p in picks] == ["m1"]


class TestGreedyPathAlsoRespectsTheGap:
    def test_a_large_pool_never_picks_a_saturated_position(self):
        pool = [
            EmergencyCandidate(
                f"d{i}",
                f"D{i}",
                ask=1_000_000,
                max_bid=1_000_000,
                ep=90.0,
                position="Defender",
            )
            for i in range(20)
        ] + [
            EmergencyCandidate(
                "m",
                "M",
                ask=1_000_000,
                max_bid=1_000_000,
                ep=20.0,
                position="Midfielder",
            )
        ]
        picks = select_emergency_basket(
            pool,
            slots_short=1,
            budget=50_000_000,
            gap_after=_gap_after_for(SQUAD_2026_09_11),
        )
        assert [p.candidate.id for p in picks] == ["m"]


class TestTheBasketIsTheClosersAndNothingElse:
    """A purchase reduces the shortfall by at most one, so the smallest basket
    with the most closed slots IS the set of closers.

    Ranking closed slots and then expected points let a 90-EP seventh defender
    ride along with the midfielder that actually closed the slot: the bundle
    scored the same `closed` as the closer alone and more EP, so it won, and
    the wallet paid EUR 20,000,000 for a body that can never start.
    """

    COUNTS = {"Goalkeeper": 1, "Defender": 6, "Midfielder": 3, "Forward": 0}
    DEAD_WEIGHT = EmergencyCandidate(
        "d", "D", ask=20_000_000, max_bid=20_000_000, ep=90.0, position="Defender"
    )
    CLOSER = EmergencyCandidate(
        "m", "M", ask=1_000_000, max_bid=1_000_000, ep=70.0, position="Midfielder"
    )

    def test_exact_path_drops_the_dead_weight(self):
        picks = select_emergency_basket(
            [self.DEAD_WEIGHT, self.CLOSER],
            slots_short=2,
            budget=50_000_000,
            gap_after=_gap_after_for(self.COUNTS),
        )
        assert [p.candidate.id for p in picks] == ["m"]

    def test_greedy_path_drops_the_dead_weight(self):
        """Twenty-one candidates: above `_EXACT_ENUMERATION_LIMIT`, so the
        greedy walk answers instead. It must reach the same basket."""
        pool = [
            EmergencyCandidate(
                f"d{i}",
                f"D{i}",
                ask=20_000_000,
                max_bid=20_000_000,
                ep=90.0,
                position="Defender",
            )
            for i in range(20)
        ] + [self.CLOSER]
        picks = select_emergency_basket(
            pool,
            slots_short=2,
            budget=50_000_000,
            gap_after=_gap_after_for(self.COUNTS),
        )
        assert [p.candidate.id for p in picks] == ["m"]


def test_without_gap_after_the_old_objective_is_untouched():
    """`tests/test_emergency_basket.py` pins that behaviour in full; this is
    the one-line reminder that the default path did not move."""
    picks = select_emergency_basket(BOARD, slots_short=1, budget=12_929_567)
    assert len(picks) == 1
