"""One message for the whole session, and a set that actually fits (REH-117).

Two complaints, one cause. Proposals were sent one message per player, so
nothing could show how they interact — and on 2026-08-31 Marco approved Nusa
and Ebnoutalib, which consumed the wallet, after which Avdullahu, Reis and a
second Avdullahu all failed on "budget would go negative". Four dead proposals
in two days, and no way to see it coming from six separate messages.

The other complaint was the content. A real message he received:

    BUY Edmond Tapsoba — EUR 41,702,302
    WHY THIS PLAYER
      EP 107.0. unknown club.
    WHY IT IMPROVES THE LINEUP
      Displaces the weakest starter (0.0) in the best eleven.

No position — he spotted "no striker" himself and the message never said
Tapsoba is a defender. "unknown club" on every proposal, because the market
payload carries `tid` and never `tn`. And with a short squad there is no
incumbent to displace, so the middle section printed a placeholder and 0.0.

`PlayerScore` already computes position, lineup probability, minutes trend,
average points and next opponent, and the renderer discarded all of it.

The selection rule is deliberately a walk, not a knapsack: the reader has to
be able to see why the list is ordered the way it is, and a set that skipped
the second line to afford the fourth would read as a bug.
"""

from __future__ import annotations

import pytest

from rehoboam.notify.overview import (
    ProposalLine,
    render_proposal_overview,
    split_by_budget,
)

BUDGET = 51_626_013


def _line(name, bid, ep, **kw):
    kw.setdefault("position", "Midfielder")
    kw.setdefault("club", "Bayern")
    kw.setdefault("market_value", int(bid / 1.3))
    return ProposalLine(proposal_id=name.lower(), name=name, bid=bid, ep=ep, marginal_gain=ep, **kw)


# The real 2026-09-01 board.
COUFAL = _line(
    "Coufal", 31_536_624, 96.0, position="Defender", club="Hoffenheim", is_emergency=True
)
CASTELLO = _line("Castello Jr.", 20_089_389, 74.0, is_emergency=True)
ANDRES = _line("Andrés", 5_968_130, 52.0, position="Forward", club="Mainz", fills_gap=True)
AVDULLAHU = _line("Avdullahu", 22_040_401, 72.0)
REIS = _line("Reis", 10_296_969, 61.0, position="Defender")

BOARD = [COUFAL, CASTELLO, ANDRES, AVDULLAHU, REIS]


class TestTheRecommendedSetFits:
    def test_it_never_exceeds_the_budget(self):
        """The bug in the mockup this was designed from: 57.6m against 51.6m."""
        recommended, _ = split_by_budget(BOARD, BUDGET)

        assert sum(line.bid for line in recommended) <= BUDGET

    def test_nothing_is_dropped_on_the_floor(self):
        """Every proposal appears somewhere — Marco asked for all of them."""
        recommended, alternatives = split_by_budget(BOARD, BUDGET)

        assert sorted(x.name for x in recommended + alternatives) == sorted(x.name for x in BOARD)

    def test_emergency_picks_come_first(self):
        """They carry the -100, so they outrank a better ordinary upgrade."""
        recommended, _ = split_by_budget(BOARD, BUDGET)

        assert recommended[0].name == "Coufal"
        assert {"Coufal", "Castello Jr."} <= {line.name for line in recommended}

    def test_an_unaffordable_line_does_not_block_a_cheaper_one_behind_it(self):
        """Walking past one that does not fit still fills the slot after it."""
        recommended, alternatives = split_by_budget(BOARD, BUDGET)

        # Coufal + Castello = 51,626,013 exactly; nothing else can fit.
        assert sum(line.bid for line in recommended) == 51_626_013
        assert {a.name for a in alternatives} == {"Andrés", "Avdullahu", "Reis"}

    def test_a_generous_budget_recommends_everything(self):
        recommended, alternatives = split_by_budget(BOARD, 500_000_000)

        assert len(recommended) == len(BOARD)
        assert alternatives == []

    def test_no_budget_recommends_nothing(self):
        recommended, alternatives = split_by_budget(BOARD, 0)

        assert recommended == []
        assert len(alternatives) == len(BOARD)


class TestTheMessageAnswersWhatWasMissing:
    def _render(self, budget=BUDGET):
        recommended, alternatives = split_by_budget(BOARD, budget)
        return render_proposal_overview(
            squad_size=9,
            squad_cap=15,
            budget=budget,
            recommended=recommended,
            alternatives=alternatives,
        )

    def test_it_names_the_position(self):
        """Marco spotted 'no striker' himself; the message never said."""
        assert "DEF" in self._render() or "Defender" in self._render()

    def test_it_names_the_club(self):
        assert "Hoffenheim" in self._render()

    def test_it_never_prints_unknown_club_when_the_club_is_known(self):
        assert "unknown club" not in self._render()

    def test_it_shows_every_proposal_in_one_message(self):
        text = self._render()

        for line in BOARD:
            assert line.name in text

    def test_it_shows_the_total_and_what_is_left(self):
        text = self._render()

        assert f"{51_626_013:,}" in text

    def test_it_separates_what_fits_from_what_does_not(self):
        text = self._render().lower()

        assert "recommend" in text
        assert "alternativ" in text or "needs a sell" in text

    def test_an_empty_slot_is_not_described_as_displacing_anyone(self):
        """The old message printed 'Displaces the weakest starter (0.0)'."""
        text = self._render()

        assert "0.0)" not in text
        assert "weakest starter" not in text


class TestTheRiskIsVisible:
    def test_a_falling_market_value_is_flagged(self):
        """Itten went out at -27.0%/7d with the number printed and no warning."""
        falling = _line("Itten", 6_174_557, 44.0, trend_7d_pct=-27.0)

        text = render_proposal_overview(
            squad_size=9, squad_cap=15, budget=BUDGET, recommended=[falling], alternatives=[]
        )

        assert "-27.0" in text
        assert "falling" in text.lower()

    def test_a_rising_market_value_is_not_flagged_as_a_risk(self):
        rising = _line("Rising", 6_174_557, 44.0, trend_7d_pct=12.0)

        text = render_proposal_overview(
            squad_size=9, squad_cap=15, budget=BUDGET, recommended=[rising], alternatives=[]
        )

        assert "falling" not in text.lower()


@pytest.mark.parametrize("budget", [0, 1, 5_968_130, 51_626_013, 999_999_999])
def test_the_recommended_set_always_fits(budget):
    recommended, _ = split_by_budget(BOARD, budget)

    assert sum(line.bid for line in recommended) <= budget


class TestAPositionGapRanksWithAnEmergency:
    """`emergency_basket._value` counts gap coverage at the slot penalty.

    A position below its formation minimum makes the eleven illegal rather
    than merely weaker, so the two orderings must agree — otherwise the
    message recommends a surplus midfielder over the only striker.
    """

    def test_the_only_forward_outranks_a_higher_ep_surplus_midfielder(self):
        gap = _line("Striker", 5_000_000, 52.0, position="Forward", fills_gap=True)
        surplus = _line("Surplus", 5_000_000, 72.0, position="Midfielder")

        recommended, _ = split_by_budget([surplus, gap], 5_000_000)

        assert [x.name for x in recommended] == ["Striker"]
