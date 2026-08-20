"""REH-75: what the report must say, including what it must refuse to say."""

from __future__ import annotations

import pytest

from rehoboam.diagnostics.flip_diagnosis import (
    Decomposition,
    DiagnosisResult,
    RoundTrip,
    TripRow,
)
from rehoboam.diagnostics.flip_report import POSITIVE_WINNER_NOTE, SUPERSEDED_NOTE, format_report

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
        by_horizon={
            h: Decomposition(selection=10 * h, exit_timing=-50, entry_premium=100_000)
            for h in (14, 21, 30, 45, 60)
        },
        at_hold=None,
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
        by_horizon={h: d for h, d in result.rows[0].by_horizon.items() if h != 30},
        at_hold=result.rows[0].at_hold,
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
        by_horizon={},
        at_hold=None,
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
        by_horizon={
            h: Decomposition(selection=0, exit_timing=0, entry_premium=0)
            for h in (14, 21, 30, 45, 60)
        },
        at_hold=None,
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


def _positive_winner_result():
    """A population whose dominant term GAINED money -- the shape of the real
    2025/26 data, where exit timing wins the rule at +EUR177.7M."""
    trip = RoundTrip(
        trip_id=9,
        player_id="p9",
        player_name="PositiveExit",
        buy_price=1_000_000,
        sell_price=1_500_000,
        buy_date=DAY0,
        sell_date=DAY0 + 30 * 86400,
        hold_days=30,
    )
    row = TripRow(
        trip=trip,
        mv_buy=1_000_000,
        branch="rising",
        by_horizon={
            h: Decomposition(selection=-100, exit_timing=500_100, entry_premium=0)
            for h in (14, 21, 30, 45, 60)
        },
        at_hold=None,
        peak_during_hold=1_500_000,
        is_floor_trip=False,
    )
    return DiagnosisResult(
        rows=[row],
        horizons=(14, 21, 30, 45, 60),
        censored=dict.fromkeys((14, 21, 30, 45, 60), 0),
    )


def test_a_positive_winning_term_is_flagged_as_positive():
    """The rule ranks by MAGNITUDE of a signed contribution, so it can name the
    term that helped. The results document spends two pages on that; someone
    who only runs the tool must not be handed the verdict bare."""
    report = format_report(_positive_winner_result())
    assert "dominant mechanism = exit_timing" in report
    assert POSITIVE_WINNER_NOTE in report


def test_a_negative_winning_term_carries_no_such_note():
    """The note must not become decoration printed on every run -- it means
    something specific, and a term that genuinely lost money gets no caveat."""
    report = format_report(_result())
    assert "dominant mechanism = entry_premium" in report
    assert POSITIVE_WINNER_NOTE not in report


def test_the_report_prints_its_own_closure_evidence():
    """Total is the identity's output; sum(realised) is the ground truth it
    must equal. A re-runner without the source databases can only check the two
    against each other if the artifact carries both. `_positive_winner_result`
    is used because its terms genuinely cancel to the realised P&L."""
    report = format_report(_positive_winner_result())
    assert "Ground truth: sum(realised) over the 1 scored round trips" in report
    assert "EUR +500,000" in report
    assert "the identity closes with no residual" in report


def test_a_broken_identity_is_announced_rather_than_printed_quietly():
    """`_result()`'s synthetic terms do NOT cancel to its realised P&L, which
    is exactly the condition the closure check exists to catch. Without it the
    Total column looks entirely plausible."""
    report = format_report(_result())
    assert "IDENTITY DOES NOT CLOSE" in report


def test_the_scored_count_is_printed_beside_the_population_count():
    """Every table below the header is n=scored, while the header names the
    population. Printing only one of the two is how a reader ends up dividing
    a scored total by the population count."""
    report = format_report(_result())
    assert "1 completed ROUND TRIPS" in report
    assert "1 scored below" in report


# REH-75's published population totals (results doc section 3) and its hold
# view (section 4). Both close to the same realised P&L, -55,256,064, because
# the decomposition is an identity -- which is why one synthetic trip can
# carry them.
REH75_HEADLINE = {
    14: Decomposition(selection=-64_936_734, exit_timing=+126_081_998, entry_premium=116_401_328),
    21: Decomposition(selection=-115_271_263, exit_timing=+176_416_527, entry_premium=116_401_328),
    30: Decomposition(selection=-116_527_447, exit_timing=+177_672_711, entry_premium=116_401_328),
    45: Decomposition(selection=-141_559_888, exit_timing=+202_705_152, entry_premium=116_401_328),
    60: Decomposition(selection=-164_802_412, exit_timing=+225_947_676, entry_premium=116_401_328),
}
HOLD_TOTALS = Decomposition(
    selection=+43_371_202, exit_timing=+17_774_062, entry_premium=116_401_328
)


def _published_result(by_horizon=None, at_hold=None, realised=-55_256_064):
    """One synthetic row carrying the published totals, so the report's verdict
    lines are pinned to numbers that predate this code. `format_report` checks
    that every horizon Total equals the realised P&L of the rows behind it, so
    buy/sell prices are chosen to satisfy that identity."""
    trip = RoundTrip(
        trip_id=1,
        player_id="p1",
        player_name="Tester",
        buy_price=100_000_000,
        sell_price=100_000_000 + realised,
        buy_date=DAY0,
        sell_date=DAY0 + 40 * 86400,
        hold_days=40,
    )
    row = TripRow(
        trip=trip,
        mv_buy=100_000_000,
        branch="rising",
        by_horizon=REH75_HEADLINE if by_horizon is None else by_horizon,
        peak_during_hold=None,
        is_floor_trip=False,
        at_hold=HOLD_TOTALS if at_hold is None else at_hold,
    )
    horizons = tuple(row.by_horizon)
    return DiagnosisResult(
        rows=[row],
        horizons=horizons,
        censored=dict.fromkeys(horizons, 0),
        hold_censored=0,
    )


def test_the_report_prints_the_registered_verdict_and_marks_the_old_one_superseded():
    text = format_report(_published_result())
    assert "Registered verdict at H=30d (REH-78): selection + entry premium (co-dominant)" in text
    assert SUPERSEDED_NOTE in text
    # The old rule's line survives verbatim beside it -- that is what makes the
    # re-run a controlled comparison rather than a claim about deleted code.
    assert "dominant mechanism = exit_timing" in text


def test_the_report_prints_a_verdict_for_every_horizon():
    """Anchored on the full rendered line (`f"{f'{h}d':<9}{verdict}"`), not on
    substrings: the horizon sweep table above this block also contains every
    "{h}d" substring, and the "Registered verdict" line above it satisfies
    the 14d/30d/45d/60d verdict substrings on its own -- only the 21d verdict
    text is unique to this block. A bare substring check would therefore not
    catch a bug that swapped the 14d and 60d rows, or that rendered the H=30
    verdict on the 45d row."""
    text = format_report(_published_result())
    assert "Dominance by horizon (REH-78 rule)" in text
    for horizon, verdict in (
        (14, "entry premium"),
        (21, "entry premium + selection (co-dominant)"),
        (30, "selection + entry premium (co-dominant)"),
        (45, "selection + entry premium (co-dominant)"),
        (60, "selection"),
    ):
        assert f"{f'{horizon}d':<9}{verdict}" in text


def test_the_report_prints_the_hold_view_with_its_agreement_label():
    text = format_report(_published_result())
    assert "Supplementary — the identity at each trip's realised hold" in text
    assert "NOT the registered instrument" in text
    # Registered verdict is {selection, entry premium}; the hold view has one
    # eligible term, entry premium. They share a term without being equal.
    assert "Agreement with the registered verdict: overlapping" in text


def test_the_hold_block_prints_its_contributing_n():
    """`hold_censored` only increments when mv_buy resolved and mv_sell did
    not -- a row that never got mv_buy at all is invisible to that counter,
    so "Censored: 0" alone cannot be trusted to mean every scored row fed the
    totals. `_published_result`'s one row has an `at_hold` value, so n must
    read 1, matching its single scored trip."""
    text = format_report(_published_result())
    assert "  n: 1 scored round trips contribute to the totals below" in text


def test_a_population_that_lost_nothing_is_rendered_as_no_loss_to_explain():
    """The rule's one silence. It must not surface as an empty list."""
    all_gains = dict.fromkeys(
        (14, 21, 30, 45, 60),
        Decomposition(selection=100, exit_timing=50, entry_premium=0),
    )
    text = format_report(
        _published_result(
            by_horizon=all_gains,
            at_hold=Decomposition(selection=100, exit_timing=50, entry_premium=0),
            realised=150,
        )
    )
    assert "Registered verdict at H=30d (REH-78): no loss to explain" in text


def test_the_horizon_sweep_table_header_is_unchanged():
    """REH-78 design section 5 predicts the sweep cannot move, and that
    prediction is tested by diffing this table against REH-75's appendix --
    which only works while the columns stay exactly as they were. A verdict
    column added here would break the diff for a formatting reason and make the
    prediction untestable."""
    text = format_report(_published_result())
    assert (
        "Horizon           Selection              Exit     Entry premium"
        "             Total      n  Censored"
    ) in text
