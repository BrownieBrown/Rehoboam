"""REH-75: name the ProfitTrader branch that would have accepted each buy.

`flip_outcomes.trend_at_buy` is NULL on all 151 rows -- declared in the schema,
never written -- so the branch cannot be looked up and has to be reconstructed
from market-value history at the buy instant.

Naming a branch means re-stating the ladder's conditions, and this repo's rule
is that nothing reimplements a heuristic. The reconciliation test below is what
makes that safe: the reconstruction supplies the LABEL, and the shipped
`ProfitTrader` remains the AUTHORITY on the accept/reject decision. If they
ever disagree, this fails.
"""

from __future__ import annotations

import pytest

from rehoboam.diagnostics.flip_branches import (
    ELIGIBLE_BRANCHES,
    label_for,
    profit_trader_accepts,
    reconstruct_branch,
)


def _trend(**kw) -> dict:
    base = {
        "has_data": True,
        "trend": "stable",
        "trend_pct": 0.0,
        "current_value": 1_000_000,
        "peak_value": 1_000_000,
        "is_dip_in_uptrend": False,
        "is_secular_decline": False,
        "is_recovery": False,
    }
    base.update(kw)
    return base


@pytest.mark.parametrize(
    ("trend", "average_points", "expected_branch"),
    [
        (_trend(), 10.0, "low_points"),
        (_trend(trend="rising", trend_pct=50.0), 90.0, "small_sample"),
        (_trend(trend="rising", trend_pct=12.0), 45.0, "rising"),
        (_trend(is_recovery=True), 35.0, "recovery"),
        (_trend(is_dip_in_uptrend=True), 35.0, "dip_in_uptrend"),
        (_trend(trend="stable"), 45.0, "stable"),
        (
            _trend(trend="falling", current_value=700_000, peak_value=1_000_000),
            45.0,
            "falling_mean_reversion",
        ),
        (
            _trend(trend="falling", is_secular_decline=True, current_value=700_000),
            45.0,
            "secular_decline",
        ),
        (
            _trend(trend="falling", current_value=950_000, peak_value=1_000_000),
            45.0,
            "shallow_dip",
        ),
        (_trend(trend="rising", trend_pct=2.0), 25.0, "no_pattern"),
    ],
)
def test_each_ladder_rung_is_named(trend, average_points, expected_branch):
    branch, _ = reconstruct_branch(trend, average_points)
    assert branch == expected_branch


def test_the_ladder_is_ordered_points_gate_before_pattern():
    """A player under the points floor is rejected for THAT reason even when a
    pattern would otherwise fire -- the order is what makes the label causal."""
    branch, _ = reconstruct_branch(_trend(trend="rising", trend_pct=12.0), 5.0)
    assert branch == "low_points"


def test_expected_appreciation_below_the_profit_floor_is_a_rejection():
    """`ProfitTrader` drops candidates whose expected appreciation is under
    `min_profit_pct`, so an eligible-looking pattern can still be rejected."""
    branch, appreciation = reconstruct_branch(_trend(trend="stable"), 45.0, min_profit_pct=20.0)
    assert appreciation == 8.0
    assert branch == "below_min_profit"


@pytest.mark.parametrize(
    ("trend", "average_points"),
    [
        (_trend(), 10.0),
        (_trend(trend="rising", trend_pct=12.0), 45.0),
        (_trend(is_dip_in_uptrend=True), 35.0),
        (_trend(trend="stable"), 45.0),
        (_trend(trend="falling", current_value=700_000, peak_value=1_000_000), 45.0),
        (_trend(trend="rising", trend_pct=2.0), 25.0),
        (_trend(has_data=False), 45.0),
    ],
)
def test_the_label_never_disagrees_with_the_shipped_verdict(trend, average_points):
    """The reconciliation gate. `label_for` reports an eligible branch if and
    only if the shipped `ProfitTrader` accepted the candidate -- when the
    ladder accepts but the post-ladder risk filter rejects, the label is
    `too_risky`, which is NOT an eligible branch. Eligibility is always the
    shipped verdict; the mirror only names the rung."""
    label = label_for(trend, average_points, market_value=1_000_000)
    assert (label in ELIGIBLE_BRANCHES) == profit_trader_accepts(
        trend, average_points, market_value=1_000_000
    )


def test_a_ladder_accepted_candidate_rejected_on_risk_is_labelled_too_risky(
    monkeypatch,
):
    """The risk filter is a rejection cause the ladder cannot see. Without its
    own label it would masquerade as an eligible branch in the per-branch
    table, overstating how much money each rung actually sourced."""
    import rehoboam.diagnostics.flip_branches as fb

    monkeypatch.setattr(fb, "profit_trader_accepts", lambda *a, **kw: False)
    assert fb.label_for(_trend(trend="rising", trend_pct=12.0), 45.0, 1_000_000) == "too_risky"
