"""Tests for rehoboam.scoring.v2.coefficients — persistence round-trip."""

from __future__ import annotations

import pytest

from rehoboam.scoring.v2.availability import fit_availability
from rehoboam.scoring.v2.coefficients import load_coefficients, save_coefficients
from rehoboam.scoring.v2.features import FeatureRow
from rehoboam.scoring.v2.rate import fit_rate


def _row(pid: str, prev: int | None, status: int, points: int) -> FeatureRow:
    return FeatureRow(
        player_id=pid,
        season="2024/2025",
        day_number=1,
        prev_status=prev,
        rolling_minutes_3=90.0,
        matches_seen=5,
        target_status=status,
        target_points=points,
    )


def test_round_trip_preserves_predictions(tmp_path):
    rows = [_row("a", 5, 5, 80)] * 20 + [_row("b", 5, 3, 20)] * 20
    availability = fit_availability(rows)
    rate = fit_rate(rows, {"a": "Forward", "b": "Midfielder"})

    path = tmp_path / "coefficients.json"
    save_coefficients(availability, rate, {"trained_on": "2024/2025"}, path)
    loaded_av, loaded_rate, meta = load_coefficients(path)

    assert loaded_av.predict(5) == availability.predict(5)
    assert loaded_rate.predict("a", 5, "Forward") == pytest.approx(rate.predict("a", 5, "Forward"))
    assert meta["trained_on"] == "2024/2025"


def test_saved_file_is_human_readable_json(tmp_path):
    """Coefficients are committed to the repo — a diff must be reviewable."""
    rows = [_row("a", 5, 5, 80)] * 10
    path = tmp_path / "coefficients.json"
    save_coefficients(fit_availability(rows), fit_rate(rows, {"a": "Forward"}), {}, path)

    text = path.read_text()
    assert "\n" in text, "must be pretty-printed, not one line"
    assert '"availability"' in text
    assert '"rate"' in text


def test_missing_file_raises_a_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_coefficients(tmp_path / "nope.json")


def test_committed_coefficients_load_and_look_right():
    """Smoke test on the shipped artifact.

    The round-trip tests above only ever touch a tmp_path file — the actual
    committed rehoboam/scoring/v2/coefficients.json that ships to the Azure
    Function is never loaded by any test. Load it via the default path (no
    explicit path argument) and sanity-check known fitted values, loosely,
    so this also catches accidental exclusion from packaging.
    """
    availability, rate, meta = load_coefficients()

    assert availability.predict(5)[5] == pytest.approx(0.837, abs=0.02)
    assert rate.base_rate[5] == pytest.approx(91.1, abs=1.5)
    assert len(rate.quality) == 480
    assert meta["train_max_season"] == "2025/2026"
