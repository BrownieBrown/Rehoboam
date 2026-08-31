"""Filling the eleven is worth more than winning any one auction (REH-113).

`_run_emergency_squad_fill` walked its ranked list greedily and stopped when
the money ran out. On 2026-08-31, four slots short with EUR 55,485,928, it
bought three and left a slot empty — a standing -100 — because the bids it
walked past were inflated:

    Nadir        ask  2,571,571   bid  2,957,306  (+15%)   EP 35.0
    Ebnoutalib   ask 12,712,298   bid 15,252,298  (+20%)   EP 61.2   fills gap
    El-Faouzi    ask 14,778,365   bid 19,208,365  (+30%)   EP 94.5
    Avdullahu    ask 16,904,943   bid 21,974,943  (+30%)   EP 70.5
    Nusa         ask 26,183,029   bid 34,033,029  (+30%)   EP 86.1

Four cheapest ASKS total 46,967,177 and fit. Four cheapest BIDS total
56,654,430 and do not, by 1,168,502. The overbid buys a better chance at one
auction; the slot it costs is a certain -100.

So the basket is chosen on ask price — the most slots that fit — and the
leftover is then spent as overbid on the players most worth winning. Choosing
by `total_ep + 100 * count` makes that automatic rather than a rule of thumb:
cardinality dominates exactly while EP spreads stay under the penalty, and
stops dominating if one ever exceeds it.
"""

from __future__ import annotations

import pytest

from rehoboam.services.emergency_basket import (
    EMPTY_SLOT_PENALTY,
    EmergencyCandidate,
    select_emergency_basket,
)

BUDGET = 55_485_928

# The real 2026-08-31 board, minus the two players we already had bids on.
NADIR = EmergencyCandidate("15478", "Nadir", ask=2_571_571, max_bid=2_957_306, ep=35.0)
EBNOUTALIB = EmergencyCandidate(
    "10004", "Ebnoutalib", ask=12_712_298, max_bid=15_252_298, ep=61.2, fills_gap=True
)
EL_FAOUZI = EmergencyCandidate("2967", "El-Faouzi", ask=14_778_365, max_bid=19_208_365, ep=94.5)
AVDULLAHU = EmergencyCandidate("11162", "Avdullahu", ask=16_904_943, max_bid=21_974_943, ep=70.5)
NUSA = EmergencyCandidate("9507", "Nusa", ask=26_183_029, max_bid=34_033_029, ep=86.1)
BAUMANN = EmergencyCandidate("524", "Baumann", ask=17_811_538, max_bid=19_236_461, ep=20.5)

BOARD = [NADIR, EBNOUTALIB, EL_FAOUZI, AVDULLAHU, NUSA, BAUMANN]


class TestTheRealAugust31Board:
    def test_it_fills_all_four_slots(self):
        picks = select_emergency_basket(BOARD, slots_short=4, budget=BUDGET)

        assert len(picks) == 4, [p.candidate.name for p in picks]

    def test_it_stays_inside_the_budget(self):
        picks = select_emergency_basket(BOARD, slots_short=4, budget=BUDGET)

        assert sum(p.bid for p in picks) <= BUDGET

    def test_it_covers_the_striker_gap(self):
        picks = select_emergency_basket(BOARD, slots_short=4, budget=BUDGET)

        assert any(p.candidate.fills_gap for p in picks)

    def test_it_beats_what_the_greedy_walk_bought(self):
        """Greedy took Ebnoutalib + El-Faouzi + Nadir: 3 slots, EP 190.7."""
        picks = select_emergency_basket(BOARD, slots_short=4, budget=BUDGET)
        greedy_value = 190.7 + EMPTY_SLOT_PENALTY * 3
        value = sum(p.candidate.ep for p in picks) + EMPTY_SLOT_PENALTY * len(picks)

        assert value > greedy_value


class TestTheObjective:
    def test_cardinality_beats_a_single_better_player(self):
        """Two modest bodies (+200 penalty avoided) beat one strong one."""
        star = EmergencyCandidate("s", "Star", ask=90, max_bid=100, ep=95.0)
        a = EmergencyCandidate("a", "A", ask=50, max_bid=50, ep=10.0)
        b = EmergencyCandidate("b", "B", ask=50, max_bid=50, ep=10.0)

        picks = select_emergency_basket([star, a, b], slots_short=2, budget=100)

        assert sorted(p.candidate.id for p in picks) == ["a", "b"]

    def test_an_ep_gap_wider_than_the_penalty_wins(self):
        """The penalty is 100, so a 250-point player outweighs a second body."""
        star = EmergencyCandidate("s", "Star", ask=90, max_bid=90, ep=250.0)
        a = EmergencyCandidate("a", "A", ask=50, max_bid=50, ep=10.0)
        b = EmergencyCandidate("b", "B", ask=50, max_bid=50, ep=10.0)

        picks = select_emergency_basket([star, a, b], slots_short=2, budget=100)

        assert [p.candidate.id for p in picks] == ["s"]

    def test_it_never_buys_more_than_the_slots_that_are_short(self):
        picks = select_emergency_basket(BOARD, slots_short=1, budget=BUDGET)

        assert len(picks) == 1

    def test_an_unaffordable_board_returns_nothing(self):
        pricey = EmergencyCandidate("x", "X", ask=10_000, max_bid=10_000, ep=50.0)

        assert select_emergency_basket([pricey], slots_short=3, budget=500) == []

    def test_an_empty_board_returns_nothing(self):
        assert select_emergency_basket([], slots_short=4, budget=BUDGET) == []


class TestTheLeftoverBecomesOverbid:
    def test_no_bid_exceeds_the_paced_maximum(self):
        picks = select_emergency_basket(BOARD, slots_short=4, budget=BUDGET)

        for p in picks:
            assert p.bid <= p.candidate.max_bid
            assert p.bid >= p.candidate.ask

    def test_spare_budget_is_spent_on_overbid_not_left_idle(self):
        """Bidding every pick at bare ask would forfeit winnable auctions."""
        picks = select_emergency_basket(BOARD, slots_short=4, budget=BUDGET)

        assert sum(p.bid for p in picks) > sum(p.candidate.ask for p in picks)

    def test_a_rich_budget_pays_every_paced_bid_in_full(self):
        picks = select_emergency_basket(BOARD, slots_short=4, budget=500_000_000)

        for p in picks:
            assert p.bid == p.candidate.max_bid


@pytest.mark.parametrize("slots", [1, 2, 3, 4, 5])
def test_the_basket_is_always_affordable_and_within_slots(slots):
    picks = select_emergency_basket(BOARD, slots_short=slots, budget=BUDGET)

    assert len(picks) <= slots
    assert sum(p.bid for p in picks) <= BUDGET
    assert len({p.candidate.id for p in picks}) == len(picks)


class TestAPositionGapOutranksRawEP:
    """A position below its formation minimum makes the eleven ILLEGAL.

    Caught by `test_prioritises_gap_positions_over_raw_ep` when gap coverage
    was only a tie-break on value: a surplus defender with 25 EP outranked the
    only forward with 12, and the squad reached eleven bodies still fielding
    ten. Pinned here so the objective, not just the wiring, holds the line.
    """

    def test_the_only_forward_beats_a_better_surplus_defender(self):
        defender = EmergencyCandidate(
            "def_top", "DefTop", ask=1_000_000, max_bid=1_000_000, ep=25.0, position="Defender"
        )
        forward = EmergencyCandidate(
            "fwd_gap",
            "FwdGap",
            ask=1_000_000,
            max_bid=1_000_000,
            ep=12.0,
            fills_gap=True,
            position="Forward",
        )

        picks = select_emergency_basket([defender, forward], slots_short=1, budget=5_000_000)

        assert [p.candidate.id for p in picks] == ["fwd_gap"]

    def test_two_forwards_do_not_double_count_one_gap(self):
        """The gap bonus is per POSITION covered, not per gap-filling player."""
        fw1 = EmergencyCandidate(
            "f1", "F1", ask=10, max_bid=10, ep=1.0, fills_gap=True, position="Forward"
        )
        fw2 = EmergencyCandidate(
            "f2", "F2", ask=10, max_bid=10, ep=1.0, fills_gap=True, position="Forward"
        )
        gk = EmergencyCandidate(
            "g1", "G1", ask=10, max_bid=10, ep=1.0, fills_gap=True, position="Goalkeeper"
        )

        picks = select_emergency_basket([fw1, fw2, gk], slots_short=2, budget=20)

        covered = {p.candidate.position for p in picks}
        assert covered == {"Forward", "Goalkeeper"}, "should spread across gaps, not stack one"


class TestTheOverbidFollowsTheSameObjective:
    """Leftover must be spent by the value the selector actually used.

    Observed on the 2026-08-31 board: Ebnoutalib, the only forward and the
    single player making the eleven legal, was bid at bare ask while a surplus
    midfielder took the overbid. Losing that auction leaves an illegal
    formation, so it is the one most worth winning — `_value` counts the gap
    at 100 and the distribution has to agree, or the two disagree about what
    the basket is for.
    """

    def test_the_gap_filler_is_funded_before_a_higher_ep_surplus_player(self):
        gap = EmergencyCandidate(
            "gap", "Gap", ask=10_000, max_bid=14_000, ep=61.2, fills_gap=True, position="Forward"
        )
        surplus = EmergencyCandidate(
            "sur", "Surplus", ask=10_000, max_bid=14_000, ep=94.5, position="Midfielder"
        )
        # Room for exactly one of the two overbids.
        budget = 10_000 + 10_000 + 4_000

        picks = {p.candidate.id: p.bid for p in select_emergency_basket([gap, surplus], 2, budget)}

        assert picks["gap"] == 14_000, "the only gap filler must get the overbid first"
        assert picks["sur"] == 10_000
