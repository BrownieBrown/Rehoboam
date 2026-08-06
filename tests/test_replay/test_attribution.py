from rehoboam.replay.attribution import (
    LeagueStanding,
    attribution_rows,
    format_report,
    place_in_league,
    trading_summary,
)
from rehoboam.replay.engine import FlipRecord, MatchdayOutcome, SeasonResult


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


def _result_with_flips() -> SeasonResult:
    return SeasonResult(
        flips=[
            FlipRecord("a", 10_000_000, 12_000_000, 0.0, 1.0),
            FlipRecord("b", 10_000_000, 7_000_000, 0.0, 1.0),
        ]
    )


def test_trading_summary_nets_wins_against_losses():
    assert trading_summary(_result_with_flips()) == (-1_000_000, 2, 1)


def test_the_report_keeps_cash_out_of_the_points_attribution():
    """Euros minus points is a category error. The Trading block must say so on
    its face, so no reader ever adds it to the attribution table."""
    report = format_report(
        _result_with_flips(),
        actual_total=0,
        actual_per_matchday={},
        standings=[],
        min_ep_gain=40.0,
        with_flips=True,
    )

    assert "does not enter the points attribution" in report


def test_the_report_prints_the_real_season_for_comparison():
    """A replay P&L with nothing to compare it against is uninterpretable."""
    report = format_report(
        _result_with_flips(),
        actual_total=0,
        actual_per_matchday={},
        standings=[],
        min_ep_gain=40.0,
        with_flips=True,
    )

    assert "55,256,064" in report
    assert "151" in report
