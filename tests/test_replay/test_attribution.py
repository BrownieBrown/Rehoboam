from rehoboam.replay.attribution import (
    LeagueStanding,
    attribution_rows,
    format_report,
    place_in_league,
)
from rehoboam.replay.engine import MatchdayOutcome, SeasonResult


def _outcome(day, pts, zeroed=False, penalty=0):
    return MatchdayOutcome(
        day_number=day,
        points_scored=pts,
        lineup_ids=[],
        penalty=penalty,
        budget_at_kickoff=0,
        zeroed=zeroed,
        squad_size=15,
        buys=0,
        sells=0,
    )


STANDINGS = [
    LeagueStanding(manager_id="a", name="Alice", total_points=37_857),
    LeagueStanding(manager_id="b", name="Bob", total_points=30_000),
    LeagueStanding(manager_id="c", name="Cara", total_points=26_170),
]


def test_place_in_league_top():
    assert place_in_league(40_000, STANDINGS) == 1


def test_place_in_league_middle():
    assert place_in_league(31_000, STANDINGS) == 2


def test_place_in_league_last():
    assert place_in_league(1_000, STANDINGS) == 4


def test_place_in_league_ties_lose_to_incumbent():
    """A tie does not overtake — the real manager keeps the higher place."""
    assert place_in_league(30_000, STANDINGS) == 3


def test_attribution_rows_report_penalties_avoided():
    result = SeasonResult(outcomes=[_outcome(1, 800), _outcome(2, 900)], total_points=1_700)
    rows = attribution_rows(result, actual_total=1_000, actual_per_matchday={1: 0, 2: 1_000})
    labels = {r[0] for r in rows}
    assert "Zero-point matchdays avoided" in labels
    assert any(r[2] == "exact" for r in rows)


def test_attribution_total_matches_simulated_minus_actual():
    result = SeasonResult(outcomes=[_outcome(1, 800), _outcome(2, 900)], total_points=1_700)
    rows = attribution_rows(result, actual_total=1_000, actual_per_matchday={1: 0, 2: 1_000})
    total_row = next(r for r in rows if r[0] == "TOTAL vs actual")
    assert total_row[1] == 700


def test_format_report_states_finishing_position_and_fidelity():
    result = SeasonResult(outcomes=[_outcome(1, 800)], total_points=800)
    text = format_report(
        result,
        actual_total=700,
        actual_per_matchday={1: 700},
        standings=STANDINGS,
        min_ep_gain=40.0,
    )
    assert "FINISHING POSITION" in text
    assert "Bid competition" in text  # fidelity caveat must be printed
