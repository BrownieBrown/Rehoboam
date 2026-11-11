"""The M1 hurdle's verdict arithmetic (spec v3 §4.2)."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from rehoboam.sim import drift_report
from rehoboam.sim.auction import NO_RIVAL, UNKNOWN, Rival
from rehoboam.sim.drift_report import ListingDrift

AT = datetime(2026, 10, 1, tzinfo=timezone.utc)


def _d(predicted: float, realised: float, premium=0.06, rival: Rival = NO_RIVAL) -> ListingDrift:
    return ListingDrift(
        "p",
        AT,
        band=1,
        predicted=predicted,
        realised=realised,
        band_premium=premium,
        rival=rival,
        lag=1,
    )


def _report(drifts):
    return drift_report.hurdle(drifts, fit_cutoff=date(2026, 7, 1), samples=1)


def test_the_hurdle_passes_when_the_top_decile_beats_the_premium():
    drifts = [_d(0.20, 0.10)] + [_d(0.0, 0.0)] * 9  # top decile = the one 0.20 prediction

    report = _report(drifts)

    assert report.top == 1
    assert report.top_median_excess == pytest.approx(1.10 / 1.06 - 1)
    assert report.passed is True


def test_the_hurdle_fails_when_the_top_decile_does_not_cover_the_premium():
    drifts = [_d(0.20, 0.05)] + [_d(0.0, 0.0)] * 9

    assert _report(drifts).passed is False


def test_deciles_are_ranked_by_prediction():
    drifts = [_d(i / 100, i / 100) for i in range(20)]

    report = _report(drifts)

    assert [row.decile for row in report.deciles] == list(range(1, 11))
    assert report.deciles[0].mean_predicted > report.deciles[-1].mean_predicted


def test_fixed_premium_bids_resolve_against_the_rivals():
    top = [
        _d(0.30, 0.10, rival=Rival(premium=0.01)),  # beaten by +2%, not by 0%
        _d(0.29, 0.10, rival=NO_RIVAL),  # any bid at value wins
        _d(0.28, 0.10, rival=UNKNOWN),  # our own real buy
    ]
    drifts = top + [_d(0.0, 0.0)] * 27

    bids = {row.premium: row for row in _report(drifts).bids}

    assert (bids[0.0].won, bids[0.0].unknown) == (1, 1)
    assert (bids[0.02].won, bids[0.02].unknown) == (2, 1)
    assert bids[0.02].median_return == pytest.approx(1.10 / 1.02 - 1)


def test_band_premiums_are_medians_per_band():
    premiums = [(3_000_000, 0.10), (3_000_000, 0.20), (3_000_000, 0.30), (20_000_000, 0.08)]

    assert drift_report.band_premiums(premiums) == {0: pytest.approx(0.20), 2: pytest.approx(0.08)}
