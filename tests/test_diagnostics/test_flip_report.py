"""REH-75: what the report must say, including what it must refuse to say."""

from __future__ import annotations

import pytest

from rehoboam.diagnostics.flip_diagnosis import (
    Decomposition,
    DiagnosisResult,
    RoundTrip,
    TripRow,
)
from rehoboam.diagnostics.flip_report import format_report

DAY0 = 1_700_000_000.0


def _result():
    trip = RoundTrip(
        trip_id=1,
        player_id="p1",
        player_name="Tester",
        buy_price=1_100_000,
        sell_price=1_000_000,
        buy_date=DAY0,
        sell_date=DAY0 + 30 * 86400,
        hold_days=30,
    )
    row = TripRow(
        trip=trip,
        mv_buy=1_000_000,
        branch="rising",
        expected_appreciation=10.0,
        by_horizon={
            h: Decomposition(selection=10 * h, exit_timing=-50, entry_premium=100_000)
            for h in (14, 21, 30, 45, 60)
        },
        peak_during_hold=1_200_000,
        is_floor_trip=False,
    )
    return DiagnosisResult(
        rows=[row],
        horizons=(14, 21, 30, 45, 60),
        censored=dict.fromkeys((14, 21, 30, 45, 60), 0),
    )


def test_every_horizon_appears_in_the_sweep():
    report = format_report(_result())
    for h in (14, 21, 30, 45, 60):
        assert f"{h}d" in report


def test_a_horizon_sweep_missing_the_headline_horizon_fails_clearly():
    """The headline line and the per-branch table are both anchored at
    HEADLINE_HORIZON (30d) -- a result swept without it has nothing for
    either to show. This must raise a clear error, not a bare KeyError deep
    inside the function."""
    result = _result()
    trimmed_row = TripRow(
        trip=result.rows[0].trip,
        mv_buy=result.rows[0].mv_buy,
        branch=result.rows[0].branch,
        expected_appreciation=result.rows[0].expected_appreciation,
        by_horizon={h: d for h, d in result.rows[0].by_horizon.items() if h != 30},
        peak_during_hold=result.rows[0].peak_during_hold,
        is_floor_trip=result.rows[0].is_floor_trip,
    )
    no_headline = DiagnosisResult(
        rows=[trimmed_row],
        horizons=(14, 21, 45, 60),
        censored=dict.fromkeys((14, 21, 45, 60), 0),
    )
    with pytest.raises(ValueError, match="HEADLINE_HORIZON"):
        format_report(no_headline)


LABEL_SEMANTICS = (
    "Branch labels mean flip-eligible at buy time. They do not mean the flip "
    "path bought the player — provenance is unrecorded before 2026-01-03."
)


def test_the_report_states_the_label_semantics():
    """Per-branch numbers without this wording invite exactly the causal
    reading the data cannot support. Asserted verbatim: a substring check
    would pass on a sentence softened into meaning something else."""
    assert LABEL_SEMANTICS in format_report(_result())


def test_the_report_names_the_population_correctly():
    """It is round trips, not flips. The wrong noun here is what sent REH-71
    to a withdrawn conclusion."""
    report = format_report(_result())
    assert "round trip" in report.lower()


def test_censored_counts_are_shown_even_when_zero():
    """A silent absence of censoring is indistinguishable from unhandled
    censoring; the report says which."""
    assert "censored" in format_report(_result()).lower()


def test_rows_with_no_market_value_at_buy_are_counted_even_when_zero():
    """A row with no `mv_buy` at all is fully censored at every horizon --
    indistinguishable, in the Censored column alone, from a row that merely
    missed one horizon's snapshot. The report must say how many rows are in
    the first, more severe, category, even when that count is zero."""
    assert "Rows with no market value at buy: 0" in format_report(_result())


def test_rows_with_no_market_value_at_buy_are_counted_when_present():
    trip = RoundTrip(
        trip_id=3,
        player_id="p3",
        player_name="NoMarketValue",
        buy_price=700_000,
        sell_price=650_000,
        buy_date=DAY0,
        sell_date=DAY0 + 30 * 86400,
        hold_days=30,
    )
    no_mv_row = TripRow(
        trip=trip,
        mv_buy=None,
        branch="no_trend_data",
        expected_appreciation=0.0,
        by_horizon={},
        peak_during_hold=None,
        is_floor_trip=False,
    )
    result = _result()
    result = DiagnosisResult(
        rows=[*result.rows, no_mv_row],
        horizons=result.horizons,
        censored=result.censored,
    )
    assert "Rows with no market value at buy: 1" in format_report(result)


ZERO_DIVERGENCE_SENTENCE = (
    "Mirror divergence: 0 rows (expected — the branch reconstruction "
    "agrees with the shipped ProfitTrader ladder on every labelled row)."
)


def test_mirror_divergence_is_stated_as_zero_when_absent():
    """Zero is the expected case, and the report must say so explicitly so its
    absence reads as checked-and-clean rather than unchecked. Asserted
    exactly: a loose 'does 0 appear somewhere near the words' check would
    also pass on a report saying the opposite, e.g. "10 / 151 rows"."""
    assert ZERO_DIVERGENCE_SENTENCE in format_report(_result())


def test_mirror_divergence_is_surfaced_prominently_when_present():
    """A non-zero count is a defect signal in the label reconstruction, not a
    market outcome, and must not be discoverable only via the per-branch
    table -- it must appear ahead of it."""
    trip = RoundTrip(
        trip_id=2,
        player_id="p2",
        player_name="Divergent",
        buy_price=1_000_000,
        sell_price=1_100_000,
        buy_date=DAY0,
        sell_date=DAY0 + 30 * 86400,
        hold_days=30,
    )
    divergent_row = TripRow(
        trip=trip,
        mv_buy=1_000_000,
        branch="mirror_divergence",
        expected_appreciation=0.0,
        by_horizon={
            h: Decomposition(selection=0, exit_timing=0, entry_premium=0)
            for h in (14, 21, 30, 45, 60)
        },
        peak_during_hold=1_100_000,
        is_floor_trip=False,
    )
    result = _result()
    result = DiagnosisResult(
        rows=[*result.rows, divergent_row],
        horizons=result.horizons,
        censored=result.censored,
    )
    report = format_report(result)
    lowered = report.lower()
    assert "mirror divergence" in lowered
    divergence_pos = lowered.index("mirror divergence")
    branch_table_pos = lowered.index("per-branch")
    assert divergence_pos < branch_table_pos
