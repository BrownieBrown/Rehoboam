"""Rules I1-I7 (spec Sec 3): facts in, failures out, nothing else."""

from __future__ import annotations

import pytest

from rehoboam.services.integrity import (
    INGEST_MAX_AGE_S,
    KICKOFF_LINEUP_WINDOW_S,
    check_integrity,
    i3_budget_covered,
)
from rehoboam.services.session_facts import SessionFacts

NOW = 1_000_000.0


def _facts(**over) -> SessionFacts:
    base = {
        "session_id": "s",
        "app": "function",
        "mode": "lineup_only",
        "started_at": NOW - 10,
        "phase": "moderate",
        "next_kickoff": NOW + 3 * 86_400,
        "next_kickoff_source": "schedule",
        "squad_gk": 1,
        "squad_def": 4,
        "squad_mid": 4,
        "squad_fw": 2,
        "fieldable_count": 11,
        "legal_formation": "4-4-2",
        "budget": 5_000_000,
        "sellable_value": 150_000_000,
        "open_offers_total": 0,
        "open_offers_manual": 0,
        "cost_basis_missing": 0,
        "predictions_written": 15,
        "lineup_result": "set",
        "errors": 0,
    }
    base.update(over)
    return SessionFacts(**base)


def _rules(failures):
    return [f.rule for f in failures]


def test_all_rules_pass_on_a_healthy_session():
    assert check_integrity(_facts(), now=NOW, last_ingest_completed_at=NOW - 3600) == []


def test_i1_unknown_kickoff():
    assert _rules(
        check_integrity(
            _facts(next_kickoff=None, next_kickoff_source="none"),
            now=NOW,
            last_ingest_completed_at=NOW,
        )
    ) == ["I1"]


@pytest.mark.parametrize("over", [{"fieldable_count": 10}, {"legal_formation": None}])
def test_i2_unfieldable_or_no_legal_formation(over):
    assert "I2" in _rules(check_integrity(_facts(**over), now=NOW, last_ingest_completed_at=NOW))


def test_i3_deficit_covered_by_sellable_value_passes():
    f = _facts(budget=-2_000_000, open_offers_total=1_000_000, sellable_value=10_000_000)
    assert "I3" not in _rules(check_integrity(f, now=NOW, last_ingest_completed_at=NOW))


def test_i3_deficit_not_covered_fails():
    f = _facts(budget=-2_000_000, open_offers_total=1_000_000, sellable_value=2_000_000)
    assert "I3" in _rules(check_integrity(f, now=NOW, last_ingest_completed_at=NOW))


def test_i3_helper_reports_the_deficit():
    ok, detail = i3_budget_covered(budget=-500, open_offers_total=500, sellable_value=0)
    assert ok is False and "1,000" in detail


def test_i4_cost_basis_missing():
    assert "I4" in _rules(
        check_integrity(_facts(cost_basis_missing=2), now=NOW, last_ingest_completed_at=NOW)
    )


def test_i5_no_predictions():
    assert "I5" in _rules(
        check_integrity(_facts(predictions_written=0), now=NOW, last_ingest_completed_at=NOW)
    )


def test_i6_lineup_failed_inside_48h_fails_but_outside_passes():
    inside = _facts(next_kickoff=NOW + KICKOFF_LINEUP_WINDOW_S - 1, lineup_result="illegal")
    outside = _facts(next_kickoff=NOW + KICKOFF_LINEUP_WINDOW_S + 1, lineup_result="illegal")
    assert "I6" in _rules(check_integrity(inside, now=NOW, last_ingest_completed_at=NOW))
    assert "I6" not in _rules(check_integrity(outside, now=NOW, last_ingest_completed_at=NOW))


def test_i6_dry_run_lineup_counts_as_success():
    f = _facts(next_kickoff=NOW + 3600, lineup_result="dry_run", dry_run=True)
    assert "I6" not in _rules(check_integrity(f, now=NOW, last_ingest_completed_at=NOW))


def test_i7_stale_or_missing_ingest():
    stale = check_integrity(_facts(), now=NOW, last_ingest_completed_at=NOW - INGEST_MAX_AGE_S - 1)
    missing = check_integrity(_facts(), now=NOW, last_ingest_completed_at=None)
    assert _rules(stale) == ["I7"] and _rules(missing) == ["I7"]


def test_unknown_facts_do_not_fail_rules_that_need_them():
    """A run that died before it knew its squad reports I1/I5/I6 as applicable, not I2/I4."""
    f = _facts(
        fieldable_count=None,
        legal_formation=None,
        cost_basis_missing=None,
        predictions_written=None,
    )
    rules = _rules(check_integrity(f, now=NOW, last_ingest_completed_at=NOW))
    assert "I2" not in rules and "I4" not in rules and "I5" in rules
