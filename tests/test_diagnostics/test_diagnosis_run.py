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
    agreement_label,
    dominant_loss_mechanisms,
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
        by_horizon={
            30: Decomposition(
                selection=selection,
                exit_timing=exit_timing,
                entry_premium=entry_premium,
            )
        },
        at_hold=None,
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


# --- REH-78: the re-registered rule -------------------------------------

# The population totals REH-75 published (results doc section 3), pre-REH-77.
# Entry premium is stored UNNEGATED on Decomposition, so +116_401_328 here is
# a signed contribution of -116_401_328 -- which is what makes it eligible.
REH75_TOTALS = {
    14: Decomposition(selection=-64_936_734, exit_timing=+126_081_998, entry_premium=116_401_328),
    21: Decomposition(selection=-115_271_263, exit_timing=+176_416_527, entry_premium=116_401_328),
    30: Decomposition(selection=-116_527_447, exit_timing=+177_672_711, entry_premium=116_401_328),
    45: Decomposition(selection=-141_559_888, exit_timing=+202_705_152, entry_premium=116_401_328),
    60: Decomposition(selection=-164_802_412, exit_timing=+225_947_676, entry_premium=116_401_328),
}


def test_a_positive_term_is_never_eligible_however_large():
    """The defect REH-78 exists to fix: exit timing is +EUR177.7M at H=30 and
    was named the dominant mechanism OF A LOSS. A gain cannot cause a loss."""
    assert "exit_timing" not in dominant_loss_mechanisms(REH75_TOTALS[30])


def test_the_five_published_horizons_return_the_pre_registered_verdicts():
    """Pinned to numbers that existed before this code did (design doc,
    'What the rule returns, stated before it is run')."""
    assert dominant_loss_mechanisms(REH75_TOTALS[14]) == ("entry_premium",)
    assert dominant_loss_mechanisms(REH75_TOTALS[21]) == ("entry_premium", "selection")
    assert dominant_loss_mechanisms(REH75_TOTALS[30]) == ("selection", "entry_premium")
    assert dominant_loss_mechanisms(REH75_TOTALS[45]) == ("selection", "entry_premium")
    assert dominant_loss_mechanisms(REH75_TOTALS[60]) == ("selection",)


def test_the_old_rule_still_returns_its_degenerate_answer():
    """Kept callable on purpose: it makes the re-run a controlled comparison
    (same data, two rules) instead of a claim about deleted code."""
    assert dominant_mechanism(REH75_TOTALS[30]) == "exit_timing"


def test_no_negative_term_means_no_loss_to_explain():
    assert (
        dominant_loss_mechanisms(Decomposition(selection=100, exit_timing=50, entry_premium=0))
        == ()
    )


def test_exactly_zero_is_not_negative_and_not_eligible():
    verdict = dominant_loss_mechanisms(
        Decomposition(selection=-1_000, exit_timing=0, entry_premium=0)
    )
    assert verdict == ("selection",)


def test_a_gap_at_exactly_the_band_is_co_dominant():
    """`<=`, matching the old rule's arithmetic: 800 is exactly 20% below 1000."""
    verdict = dominant_loss_mechanisms(
        Decomposition(selection=-1_000, exit_timing=-800, entry_premium=0)
    )
    assert verdict == ("selection", "exit_timing")


def test_a_gap_outside_the_band_names_one_term():
    verdict = dominant_loss_mechanisms(
        Decomposition(selection=-1_000, exit_timing=-799, entry_premium=0)
    )
    assert verdict == ("selection",)


def test_verdicts_are_ordered_by_magnitude_descending():
    verdict = dominant_loss_mechanisms(
        Decomposition(selection=-900, exit_timing=-1_000, entry_premium=0)
    )
    assert verdict == ("exit_timing", "selection")


def test_agreement_labels():
    assert agreement_label(("selection",), ("selection",)) == "identical"
    assert agreement_label(("selection", "entry_premium"), ("entry_premium",)) == "overlapping"
    assert agreement_label(("selection",), ("entry_premium",)) == "disjoint"
    assert agreement_label((), ()) == "identical"
    assert agreement_label((), ("entry_premium",)) == "disjoint"


def test_agreement_label_compares_sets_not_tuples():
    """The two verdicts are computed over different populations (H=30 sweep
    totals vs. hold-instant totals), so a shared set of terms is not
    guaranteed to share an ordering. Tuple equality would wrongly print
    "overlapping" here instead of "identical"."""
    assert (
        agreement_label(("selection", "entry_premium"), ("entry_premium", "selection"))
        == "identical"
    )
