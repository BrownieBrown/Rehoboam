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
    reconstruct_branch,
    shipped_opportunity,
)

MARKET_VALUE = 1_000_000


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
    branch, _ = reconstruct_branch(trend, average_points, market_value=MARKET_VALUE)
    assert branch == expected_branch


def test_the_ladder_is_ordered_points_gate_before_pattern():
    """A player under the points floor is rejected for THAT reason even when a
    pattern would otherwise fire -- the order is what makes the label causal."""
    branch, _ = reconstruct_branch(
        _trend(trend="rising", trend_pct=12.0), 5.0, market_value=MARKET_VALUE
    )
    assert branch == "low_points"


def test_expected_appreciation_below_the_profit_floor_is_a_rejection():
    """`ProfitTrader` drops candidates whose expected appreciation is under
    `min_profit_pct`, so an eligible-looking pattern can still be rejected."""
    branch, appreciation = reconstruct_branch(
        _trend(trend="stable"), 45.0, market_value=MARKET_VALUE, min_profit_pct=20.0
    )
    assert appreciation == 8.0
    assert branch == "below_min_profit"


def test_current_value_defaults_to_market_value_not_zero():
    """A trend dict without `current_value` must default to `market_value`,
    matching `profit_trader.py:114` -- defaulting to 0 instead fabricates a
    -100% below-peak reading and wrongly fires `falling_mean_reversion` for
    what is actually a shallow (0%) dip."""
    trend = {
        "has_data": True,
        "trend": "falling",
        "trend_pct": -5,
        "peak_value": 1_000_000,
    }
    branch, _ = reconstruct_branch(trend, 45.0, market_value=1_000_000)
    assert branch == "shallow_dip"
    assert shipped_opportunity(trend, 45.0, 1_000_000) is None


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
def test_the_mirror_never_diverges_from_the_shipped_ladder(trend, average_points):
    """The reconciliation gate, asserted directly against `reconstruct_branch`
    and `shipped_opportunity` -- NOT through `label_for`, whose own use of
    `profit_trader_accepts` would make the assertion true by construction.

    Eligible: the shipped trader must also accept, AND at the SAME expected
    appreciation -- the values (20/12/10/8/15) fingerprint the rung, so this
    pins rung identity, not merely eligibility. Rejected: the shipped trader
    must also reject."""
    branch, appreciation = reconstruct_branch(trend, average_points, market_value=MARKET_VALUE)
    shipped = shipped_opportunity(trend, average_points, MARKET_VALUE)
    if branch in ELIGIBLE_BRANCHES:
        assert shipped is not None, f"mirror accepted via {branch}, shipped ProfitTrader rejected"
        assert appreciation == shipped.expected_appreciation
    else:
        assert shipped is None, f"mirror rejected as {branch}, shipped ProfitTrader accepted"


def test_a_ladder_accepted_candidate_rejected_on_risk_is_labelled_too_risky(
    monkeypatch,
):
    """A genuine risk-filter rejection: the shipped trader rejects at the live
    risk threshold but accepts once risk is disabled -- exactly what
    distinguishes a real risk rejection from a mirror/ladder disagreement."""
    import rehoboam.diagnostics.flip_branches as fb

    def fake_accepts(trend, average_points, market_value, *, max_risk_score=fb.FLIP_MAX_RISK_SCORE):
        return max_risk_score == fb.RISK_DISABLED

    monkeypatch.setattr(fb, "profit_trader_accepts", fake_accepts)
    assert fb.label_for(_trend(trend="rising", trend_pct=12.0), 45.0, 1_000_000) == "too_risky"


def test_a_ladder_shipped_disagreement_is_labelled_mirror_divergence(monkeypatch):
    """When the shipped trader rejects even with risk disabled, the rejection
    was never about risk -- the mirror and the shipped ladder have diverged.
    That is a defect signal, never `too_risky`, and never a market outcome."""
    import rehoboam.diagnostics.flip_branches as fb

    monkeypatch.setattr(fb, "profit_trader_accepts", lambda *a, **kw: False)
    assert (
        fb.label_for(_trend(trend="rising", trend_pct=12.0), 45.0, 1_000_000) == "mirror_divergence"
    )
