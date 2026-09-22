"""Which players the bot sells to be back in the black before kickoff.

Marco's rule of 2026-09-22: the bot may sit in the red until gameday, so the
last sessions before kickoff must bring the wallet back to zero or better —
a negative budget at kickoff is zero points for the whole matchday. What to
sell is a weighing, not a single rule: bank profits first, and do not lock in
a transfer loss on a starter who is merely in a slump.

`plan_debt_recovery` is pure so every tier of that weighing can be pinned.
"""

from __future__ import annotations

from rehoboam.services.debt_recovery import DebtCandidate, plan_debt_recovery


def _c(
    pid: str,
    position: str = "Midfielder",
    mv: int = 10_000_000,
    buy: int | None = 8_000_000,
    ep: float = 50.0,
    starter: bool = False,
    trend: float | None = None,
) -> DebtCandidate:
    return DebtCandidate(
        player_id=pid,
        name=pid,
        position=position,
        market_value=mv,
        buy_price=buy,
        expected_points=ep,
        in_best_eleven=starter,
        trend_7d_pct=trend,
    )


def _counts(*cands: DebtCandidate) -> dict[str, int]:
    counts: dict[str, int] = {}
    for c in cands:
        counts[c.position] = counts.get(c.position, 0) + 1
    return counts


# A full squad of eleven so no position sits at its minimum unless a test
# says so: GK 1, DEF 4, MID 4, FW 2. Every one of them is the worst possible
# sacrifice (a starter, deep in the red, still falling) so the ordering tests
# below isolate the tier they name.
def _eleven(**overrides) -> list[DebtCandidate]:
    def worst(pid: str, position: str) -> DebtCandidate:
        return _c(pid, position, mv=10_000_000, buy=20_000_000, trend=-10.0, starter=True)

    base = [
        worst("gk", "Goalkeeper"),
        *[worst(f"d{i}", "Defender") for i in range(4)],
        *[worst(f"m{i}", "Midfielder") for i in range(4)],
        *[worst(f"f{i}", "Forward") for i in range(2)],
    ]
    return [overrides.get(c.player_id, c) for c in base]


class TestNothingToDo:
    def test_a_positive_wallet_sells_nobody(self):
        plan = plan_debt_recovery([_c("a")], shortfall=0, position_counts={"Midfielder": 1})
        assert plan.sells == []
        assert plan.covered is True
        assert plan.remaining == 0

    def test_a_negative_shortfall_is_treated_as_zero(self):
        plan = plan_debt_recovery([_c("a")], shortfall=-5, position_counts={"Midfielder": 1})
        assert plan.sells == []
        assert plan.covered is True


class TestTheOrderOfSacrifice:
    def test_profitable_bench_goes_before_profitable_starters(self):
        bench = _c("bench", mv=10_000_000, buy=8_000_000, starter=False)
        starter = _c("starter", mv=10_000_000, buy=5_000_000, ep=20.0, starter=True)
        squad = _eleven() + [bench, starter]
        plan = plan_debt_recovery(squad, shortfall=1, position_counts=_counts(*squad))
        assert [c.player_id for c in plan.sells] == ["bench"]

    def test_among_profitable_bench_the_biggest_profit_share_goes_first(self):
        small = _c("small", mv=10_000_000, buy=9_500_000)
        big = _c("big", mv=10_000_000, buy=5_000_000)
        squad = _eleven() + [small, big]
        plan = plan_debt_recovery(squad, shortfall=1, position_counts=_counts(*squad))
        assert [c.player_id for c in plan.sells] == ["big"]

    def test_a_profitable_starter_goes_before_a_loss_making_bench_player(self):
        """Profit outranks bench status: a loss is money gone for good, points
        lost to a sold starter are one matchday's."""
        bench_loss = _c("bench_loss", mv=8_000_000, buy=10_000_000, starter=False)
        starter_profit = _c("starter_profit", mv=8_000_000, buy=6_000_000, ep=40.0, starter=True)
        squad = _eleven() + [bench_loss, starter_profit]
        plan = plan_debt_recovery(squad, shortfall=1, position_counts=_counts(*squad))
        assert [c.player_id for c in plan.sells] == ["starter_profit"]

    def test_among_profitable_starters_the_fewest_points_go_first(self):
        squad = _eleven(
            m0=_c("m0", "Midfielder", mv=10_000_000, buy=8_000_000, ep=80.0, starter=True),
            m1=_c("m1", "Midfielder", mv=10_000_000, buy=8_000_000, ep=30.0, starter=True),
        )
        plan = plan_debt_recovery(squad, shortfall=1, position_counts=_counts(*squad))
        assert [c.player_id for c in plan.sells] == ["m1"]

    def test_a_loss_making_bench_player_goes_before_a_loss_making_starter(self):
        bench = _c("bench", mv=8_000_000, buy=10_000_000, starter=False)
        squad = _eleven(m0=_c("m0", "Midfielder", mv=8_000_000, buy=10_000_000, starter=True))
        squad = squad + [bench]
        plan = plan_debt_recovery(squad, shortfall=1, position_counts=_counts(*squad))
        assert [c.player_id for c in plan.sells] == ["bench"]

    def test_within_losses_a_slumping_player_goes_last(self):
        """Selling into a slump locks the loss in at its worst; a flat or
        rising price at a loss is the lesser evil."""
        slump = _c("slump", mv=8_000_000, buy=10_000_000, trend=-6.0)
        flat = _c("flat", mv=8_000_000, buy=10_000_000, trend=0.5)
        squad = _eleven() + [slump, flat]
        plan = plan_debt_recovery(squad, shortfall=1, position_counts=_counts(*squad))
        assert [c.player_id for c in plan.sells] == ["flat"]

    def test_within_losses_the_smaller_loss_share_goes_first(self):
        deep = _c("deep", mv=5_000_000, buy=10_000_000)
        shallow = _c("shallow", mv=9_500_000, buy=10_000_000)
        squad = _eleven() + [deep, shallow]
        plan = plan_debt_recovery(squad, shortfall=1, position_counts=_counts(*squad))
        assert [c.player_id for c in plan.sells] == ["shallow"]

    def test_an_unknown_cost_basis_counts_as_neither_profit_nor_loss(self):
        """No evidence of a loss: it sorts at the bottom of the profit tier,
        ahead of every known loss."""
        unknown = _c("unknown", mv=8_000_000, buy=None)
        profit = _c("profit", mv=8_000_000, buy=6_000_000)
        loss = _c("loss", mv=8_000_000, buy=10_000_000)
        squad = _eleven() + [unknown, profit, loss]
        plan = plan_debt_recovery(squad, shortfall=17_000_000, position_counts=_counts(*squad))
        assert [c.player_id for c in plan.sells] == ["profit", "unknown", "loss"]


class TestItStopsWhenCovered:
    def test_it_sells_only_as_much_as_the_shortfall_needs(self):
        a = _c("a", mv=6_000_000, buy=4_000_000)
        b = _c("b", mv=6_000_000, buy=5_000_000)
        c = _c("c", mv=6_000_000, buy=5_500_000)
        squad = _eleven() + [a, b, c]
        plan = plan_debt_recovery(squad, shortfall=7_000_000, position_counts=_counts(*squad))
        assert [x.player_id for x in plan.sells] == ["a", "b"]
        assert plan.recovered == 12_000_000
        assert plan.remaining == 0
        assert plan.covered is True

    def test_it_reports_what_it_could_not_cover(self):
        a = _c("a", mv=6_000_000, buy=4_000_000)
        squad = _eleven() + [a]
        plan = plan_debt_recovery(squad, shortfall=200_000_000, position_counts=_counts(*squad))
        assert plan.covered is False
        assert plan.remaining > 0
        assert plan.recovered == sum(c.market_value for c in plan.sells)


class TestPositionMinimumsGoLast:
    def test_the_last_goalkeeper_is_not_sold_while_anyone_else_covers(self):
        """One GK is the formation minimum; selling him is an empty slot at
        -100 on top of whatever the debt would have cost — so he goes last."""
        squad = _eleven(gk=_c("gk", "Goalkeeper", mv=50_000_000, buy=1_000_000, starter=True))
        plan = plan_debt_recovery(squad, shortfall=1, position_counts=_counts(*squad))
        assert "gk" not in [c.player_id for c in plan.sells]
        assert plan.below_minimum == []

    def test_the_minimum_is_tracked_as_sells_are_planned(self):
        """Four defenders, minimum three: the plan may sell one, never two."""
        squad = _eleven(
            **{
                f"d{i}": _c(f"d{i}", "Defender", mv=1_000_000, buy=500_000, starter=True)
                for i in range(4)
            }
        )
        # Every other position is at a loss so the defenders are preferred.
        squad = [
            (
                c
                if c.position == "Defender"
                else _c(c.player_id, c.position, buy=c.market_value * 2, starter=True)
            )
            for c in squad
        ]
        # 30m is covered by the first pass: one defender (1m), then the
        # loss-making others above their minimums (two midfielders and a
        # forward, 10m each) — never a second defender while anyone is left.
        plan = plan_debt_recovery(squad, shortfall=30_000_000, position_counts=_counts(*squad))
        assert sum(1 for c in plan.sells if c.position == "Defender") == 1
        assert plan.below_minimum == []

    def test_a_protected_player_is_sold_when_nothing_else_covers(self):
        """Marco, 2026-09-22: the bot may trade below eleven if it fills the
        slots. An unfilled slot is -100; a negative wallet at kickoff is zero
        for the whole matchday. The fill runs right after and buys it back."""
        squad = [_c("gk", "Goalkeeper", mv=50_000_000, buy=1_000_000, starter=True)]
        plan = plan_debt_recovery(squad, shortfall=1_000_000, position_counts=_counts(*squad))
        assert [c.player_id for c in plan.sells] == ["gk"]
        assert [c.player_id for c in plan.below_minimum] == ["gk"]
        assert plan.covered is True

    def test_the_last_resort_keeps_the_same_order(self):
        """Among players at their minimum, profit still beats a slump loss."""
        squad = [
            _c("gk_loss", "Goalkeeper", mv=10_000_000, buy=20_000_000, trend=-5.0, starter=True),
            _c("fw_profit", "Forward", mv=10_000_000, buy=5_000_000, starter=True),
        ]
        plan = plan_debt_recovery(squad, shortfall=1, position_counts=_counts(*squad))
        assert [c.player_id for c in plan.sells] == ["fw_profit"]

    def test_it_stops_at_an_empty_squad_and_reports_the_rest(self):
        squad = [_c("gk", "Goalkeeper", mv=1_000_000, buy=1_000_000, starter=True)]
        plan = plan_debt_recovery(squad, shortfall=5_000_000, position_counts=_counts(*squad))
        assert [c.player_id for c in plan.sells] == ["gk"]
        assert plan.covered is False
        assert plan.remaining == 4_000_000
