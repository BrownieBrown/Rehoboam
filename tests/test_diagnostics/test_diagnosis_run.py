"""REH-75: population aggregates, and the rule that reads them.

The dominance rule is pre-registered in the design doc and fixed BEFORE any
real number is produced. These tests pin it so it cannot drift toward whatever
the data happens to say.
"""

from __future__ import annotations

from rehoboam.diagnostics.flip_diagnosis import (
    Decomposition,
    DiagnosisResult,
    RoundTrip,
    TripRow,
    dominant_mechanism,
    temporal_split,
    totals_by_branch,
    totals_by_horizon,
)

DAY0 = 1_700_000_000.0


def _row(
    *,
    branch="rising",
    buy_date=DAY0,
    selection=0,
    exit_timing=0,
    entry_premium=0,
    floor=False,
):
    trip = RoundTrip(
        trip_id=1,
        player_id="p1",
        player_name="Tester",
        buy_price=1_000_000,
        sell_price=1_000_000,
        buy_date=buy_date,
        sell_date=buy_date + 30 * 86400,
        hold_days=30,
    )
    return TripRow(
        trip=trip,
        mv_buy=1_000_000,
        branch=branch,
        expected_appreciation=10.0,
        by_horizon={
            30: Decomposition(
                selection=selection,
                exit_timing=exit_timing,
                entry_premium=entry_premium,
            )
        },
        peak_during_hold=1_000_000,
        is_floor_trip=floor,
    )


def test_horizon_totals_sum_the_terms_componentwise():
    result = DiagnosisResult(
        rows=[
            _row(selection=100, exit_timing=-30, entry_premium=20),
            _row(selection=50),
        ],
        horizons=(30,),
        censored={30: 0},
    )
    assert totals_by_horizon(result)[30] == Decomposition(
        selection=150, exit_timing=-30, entry_premium=20
    )


def test_floor_trips_are_excluded_from_the_headline_totals():
    """EUR 500k floor round trips have MV pinned at the floor; including them
    dilutes every term with structural zeros. They are reported separately."""
    result = DiagnosisResult(
        rows=[_row(selection=100), _row(selection=999, floor=True)],
        horizons=(30,),
        censored={30: 0},
    )
    assert totals_by_horizon(result)[30].selection == 100


def test_branch_totals_are_keyed_by_reconstructed_branch():
    result = DiagnosisResult(
        rows=[_row(branch="rising", selection=10), _row(branch="stable", selection=7)],
        horizons=(30,),
        censored={30: 0},
    )
    totals = totals_by_branch(result, horizon=30)
    assert totals["rising"].selection == 10
    assert totals["stable"].selection == 7


def test_temporal_split_partitions_on_the_buy_date():
    result = DiagnosisResult(
        rows=[
            _row(buy_date=DAY0 - 86400, selection=5),
            _row(buy_date=DAY0 + 86400, selection=-9),
        ],
        horizons=(30,),
        censored={30: 0},
    )
    split = temporal_split(result, horizon=30, boundary=DAY0)
    assert split["before"].selection == 5
    assert split["after"].selection == -9


def test_the_dominant_mechanism_is_the_largest_signed_magnitude():
    """Entry premium enters negated, exactly as in the identity."""
    assert (
        dominant_mechanism(Decomposition(selection=-1_000, exit_timing=-100, entry_premium=100))
        == "selection"
    )
    assert (
        dominant_mechanism(Decomposition(selection=-100, exit_timing=-200, entry_premium=5_000))
        == "entry_premium"
    )


def test_a_near_tie_reports_no_single_dominant_mechanism():
    """The 20% band exists so a photo-finish is not narrated as a winner."""
    assert (
        dominant_mechanism(Decomposition(selection=-1_000, exit_timing=-950, entry_premium=0))
        == "no single dominant mechanism"
    )


def test_a_clear_win_outside_the_band_is_named():
    assert (
        dominant_mechanism(Decomposition(selection=-1_000, exit_timing=-500, entry_premium=0))
        == "selection"
    )
