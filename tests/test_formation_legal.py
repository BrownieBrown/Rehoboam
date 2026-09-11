"""Fieldability that knows Kickbase's legal formations (spec 2026-09-11 §5).

On 2026-09-11 the squad was 1 GK, 6 DEF, 3 MID, 1 FW. `can_fill_starting_eleven`
checked minimums only (GK 1, DEF 3, MID 2, FW 1) and said "fieldable". Only five
defenders can start in any formation, so the most that squad can field is ten,
and Kickbase answered every lineup call since 2026-09-09 with
`LineupNotEnoughPlayers`. The question is not "are there eleven bodies" but
"does some legal formation fit", and if not, "what is the cheapest way to make
one fit".
"""

from __future__ import annotations

from rehoboam.formation import (
    _POSITION_MAX_STARTERS,
    LEGAL_FORMATIONS,
    can_fill_starting_eleven,
    fieldability,
    fieldability_from_counts,
    is_legal_formation,
)
from rehoboam.kickbase_client import Player


def _p(pid: str, position: str) -> Player:
    return Player(
        id=pid,
        first_name="F",
        last_name=f"P{pid}",
        position=position,
        team_id="1",
        team_name="T",
        market_value=1_000_000,
        points=0,
        average_points=50.0,
    )


def _squad(gk: int, de: int, mi: int, fw: int) -> list[Player]:
    return (
        [_p(f"g{i}", "Goalkeeper") for i in range(gk)]
        + [_p(f"d{i}", "Defender") for i in range(de)]
        + [_p(f"m{i}", "Midfielder") for i in range(mi)]
        + [_p(f"f{i}", "Forward") for i in range(fw)]
    )


class TestLegalFormations:
    def test_every_formation_has_ten_outfield_players(self):
        for d, m, f in LEGAL_FORMATIONS:
            assert d + m + f == 10, (d, m, f)

    def test_the_conservative_eight(self):
        assert LEGAL_FORMATIONS == frozenset(
            {
                (3, 4, 3),
                (3, 5, 2),
                (4, 3, 3),
                (4, 4, 2),
                (4, 5, 1),
                (5, 2, 3),
                (5, 3, 2),
                (5, 4, 1),
            }
        )

    def test_ceilings_are_derived_from_the_set(self):
        assert _POSITION_MAX_STARTERS == {
            "Goalkeeper": 1,
            "Defender": 5,
            "Midfielder": 5,
            "Forward": 3,
        }


class TestFieldabilityFromCounts:
    def test_the_2026_09_11_squad_is_one_short_at_mid_or_fw(self):
        fb = fieldability_from_counts(
            {"Goalkeeper": 1, "Defender": 6, "Midfielder": 3, "Forward": 1}
        )
        assert fb.ok is False
        assert fb.purchases == 1
        assert fb.positions == frozenset({"Midfielder", "Forward"})
        assert "Defender 6 > 5" in fb.reason
        assert "10 of 11" in fb.reason

    def test_a_legal_eleven_needs_nothing(self):
        fb = fieldability_from_counts(
            {"Goalkeeper": 1, "Defender": 4, "Midfielder": 4, "Forward": 2}
        )
        assert fb.ok is True
        assert fb.purchases == 0
        assert fb.positions == frozenset()

    def test_seven_players_need_four(self):
        """The real 2026-08-31 shape: GK 1, DEF 4, MID 2, FW 0."""
        fb = fieldability_from_counts(
            {"Goalkeeper": 1, "Defender": 4, "Midfielder": 2, "Forward": 0}
        )
        assert fb.purchases == 4
        assert "Forward" in fb.positions and "Midfielder" in fb.positions

    def test_no_goalkeeper_is_one_purchase_at_goalkeeper(self):
        fb = fieldability_from_counts(
            {"Goalkeeper": 0, "Defender": 6, "Midfielder": 5, "Forward": 3}
        )
        assert fb.purchases == 1
        assert fb.positions == frozenset({"Goalkeeper"})
        assert "Goalkeeper: have 0, need 1" == fb.reason

    def test_empty_squad_needs_eleven(self):
        fb = fieldability_from_counts({})
        assert fb.purchases == 11

    def test_two_short_lists_every_position_some_minimal_plan_uses(self):
        """GK 1, DEF 6, MID 3, FW 0 (10 players): 5-3-2 needs two forwards,
        5-4-1 needs a midfielder and a forward. Both plans cost two, so both
        positions are acceptable buys."""
        fb = fieldability_from_counts(
            {"Goalkeeper": 1, "Defender": 6, "Midfielder": 3, "Forward": 0}
        )
        assert fb.purchases == 2
        assert fb.positions == frozenset({"Midfielder", "Forward"})


class TestWrappers:
    def test_can_fill_starting_eleven_keeps_its_dict_shape(self):
        result = can_fill_starting_eleven(_squad(1, 6, 3, 1))
        assert result["ok"] is False
        assert result["counts"]["Defender"] == 6
        assert "Defender 6 > 5" in result["reason"]

    def test_fieldability_counts_from_players(self):
        assert fieldability(_squad(1, 4, 4, 2)).ok is True

    def test_is_legal_formation_accepts_a_4_4_2(self):
        assert is_legal_formation(_squad(1, 4, 4, 2)) is True

    def test_is_legal_formation_rejects_ten_players(self):
        assert is_legal_formation(_squad(1, 5, 3, 1)) is False

    def test_is_legal_formation_rejects_an_eleven_outside_the_set(self):
        """Eleven players: length guard passes, set check must reject 3-3-4."""
        assert is_legal_formation(_squad(1, 3, 3, 4)) is False

    def test_is_legal_formation_rejects_an_eleven_with_two_goalkeepers(self):
        """Eleven players with two goalkeepers: GK != 1 guard must reject."""
        assert is_legal_formation(_squad(2, 4, 4, 1)) is False
