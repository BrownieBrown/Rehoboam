"""The proposal message.

Marco asked for three things explicitly: why this is a buy, why it improves the
lineup, and why this price. Each gets its own asserted section — a renderer
that silently drops one is the failure mode that matters.
"""

from rehoboam.notify.render import render_proposal


def _render(**over):
    base = {
        "player_name": "Aleksandar Pavlović",
        "club": "Bayern",
        "bid": 32_608_485,
        "market_value": 32_285_629,
        "ep": 82.6,
        "displaced_name": "Klaas",
        "displaced_ep": 25.5,
        "marginal_gain": 57.2,
        "budget_before": 95_317_114,
        "trend_7d_pct": 1.9,
        "risks": ["Availability is the 59% generic prior — no recent match evidence."],
    }
    base.update(over)
    return render_proposal(**base)


class TestTheThreeQuestions:
    def test_it_says_why_this_player(self):
        out = _render()
        assert "82.6" in out and "Bayern" in out

    def test_it_says_why_it_improves_the_lineup(self):
        out = _render()
        assert "Klaas" in out
        assert "25.5" in out
        assert "57.2" in out

    def test_it_says_why_this_price(self):
        out = _render()
        assert "32,285,629" in out
        assert "32,608,485" in out
        assert "1.0%" in out


class TestBudget:
    def test_it_shows_the_budget_after_the_trade(self):
        assert "62,708,629" in _render()


class TestRisks:
    def test_risks_are_shown(self):
        assert "generic prior" in _render()

    def test_no_risk_section_when_there_are_none(self):
        assert "RISKS" not in _render(risks=[])


class TestNoCrashOnEdges:
    def test_zero_market_value_does_not_divide_by_zero(self):
        out = _render(market_value=0)
        assert isinstance(out, str) and out
