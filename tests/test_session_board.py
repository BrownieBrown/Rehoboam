"""One message for what a session did with the wallet (spec §1).

The proposal board asked Marco to choose; this one tells him what happened.
Every offer the session placed, at what price and why; every candidate the
gate refused and for what reason; the budget before and after. No buttons —
there is nothing left to decide.
"""

from __future__ import annotations

import pytest

from rehoboam.notify.overview import OfferLine, render_session_board

BUDGET_BEFORE = 45_201_758


def _line(name, bid, ep, **kw):
    kw.setdefault("position", "Midfielder")
    kw.setdefault("club", "Bayern")
    kw.setdefault("market_value", int(bid / 1.2))
    return OfferLine(offer_id=name.lower(), name=name, bid=bid, ep=ep, marginal_gain=ep, **kw)


PAVLOVIC = _line("Pavlović", 32_608_485, 82.6, market_value=32_285_629, trend_7d_pct=1.9)
DEMAN = _line("Deman", 9_312_505, 50.8, position="Defender", club="Freiburg", fills_gap=True)
AOUCHICHE = _line(
    "Aouchiche",
    18_835_959,
    68.0,
    position="Forward",
    club="Bochum",
    outcome="refused",
    detail="overbid 40.0% exceeds the 35.0% ceiling for tier must_have",
)
NGUEFACK = _line(
    "Nguefack",
    1_269_172,
    40.0,
    outcome="failed",
    detail="Failed to make offer: 500 - UnderpayNotAllowed",
)

PLACED = [PAVLOVIC, DEMAN]
REFUSED = [AOUCHICHE, NGUEFACK]


def _render(placed=PLACED, refused=REFUSED):
    spend = sum(line.bid for line in placed)
    return render_session_board(
        squad_size=11,
        squad_cap=15,
        budget_before=BUDGET_BEFORE,
        budget_after=BUDGET_BEFORE - spend,
        placed=placed,
        refused=refused,
    )


class TestTheBoardReportsWhatHappened:
    def test_it_leads_with_the_budget_before_and_after(self):
        text = _render()

        assert text.splitlines()[0] == (
            f"SQUAD 11/15   BUDGET EUR {BUDGET_BEFORE:,} -> EUR {BUDGET_BEFORE - 41_920_990:,}"
        )

    def test_it_counts_and_names_every_offer_placed(self):
        text = _render()

        assert "OFFERS PLACED — 2" in text
        assert "Pavlović (MID, Bayern)  EUR 32,608,485" in text
        assert "Deman (DEF, Freiburg)  EUR 9,312,505" in text
        assert "total EUR 41,920,990" in text

    def test_it_counts_and_explains_every_refusal(self):
        text = _render()

        assert "REFUSED — 2" in text
        assert "Aouchiche (FWD, Bochum)  EUR 18,835,959" in text
        assert "! overbid 40.0% exceeds the 35.0% ceiling for tier must_have" in text
        assert "! Failed to make offer: 500 - UnderpayNotAllowed" in text

    def test_placed_come_before_refused(self):
        text = _render()

        assert text.index("OFFERS PLACED") < text.index("REFUSED")

    def test_it_asks_for_nothing(self):
        text = _render()

        assert "Approve" not in text
        assert "RECOMMENDED" not in text
        assert "awaiting" not in text.lower()

    def test_a_session_that_placed_nothing_says_so(self):
        text = _render(placed=[], refused=REFUSED)

        assert "OFFERS PLACED — none" in text
        assert "total EUR" not in text

    def test_a_session_with_no_refusals_has_no_refused_section(self):
        text = _render(placed=PLACED, refused=[])

        assert "REFUSED" not in text


class TestTheLineStillCarriesTheCase:
    def test_the_price_line_shows_the_overbid_and_the_trend(self):
        text = _render()

        assert "MV 32,285,629 · bid +1.0% · trend +1.9%/7d" in text

    def test_a_falling_market_value_is_still_flagged(self):
        falling = _line("Itten", 5_000_000, 40.0, trend_7d_pct=-27.0)

        assert "<-- FALLING" in _render(placed=[falling], refused=[])

    def test_a_gap_filler_is_still_labelled(self):
        assert "fills your DEF gap" in _render(placed=[DEMAN], refused=[])


class TestOfferLine:
    def test_the_default_outcome_is_placed(self):
        assert _line("X", 1_000_000, 10.0).outcome == "placed"

    @pytest.mark.parametrize("bid,mv,expected", [(1_300_000, 1_000_000, 30.0), (900_000, 0, 0.0)])
    def test_overbid_pct(self, bid, mv, expected):
        assert _line("X", bid, 10.0, market_value=mv).overbid_pct == pytest.approx(expected)

    def test_there_is_no_emergency_flag_any_more(self):
        """The emergency fill executes and never reaches the board (PR 1)."""
        import dataclasses

        assert "is_emergency" not in {f.name for f in dataclasses.fields(OfferLine)}
