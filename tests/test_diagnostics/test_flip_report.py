"""REH-75: what the report must say, including what it must refuse to say."""

from __future__ import annotations

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


def test_mirror_divergence_is_stated_as_zero_when_absent():
    """Zero is the expected case, and the report must say so explicitly so its
    absence reads as checked-and-clean rather than unchecked."""
    report = format_report(_result())
    assert "mirror divergence" in report.lower()
    assert "0" in report.lower().split("mirror divergence")[1][:20]


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
