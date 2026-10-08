"""Persistence for fitted v2 scorer coefficients.

Coefficients live in a committed JSON file rather than a database because they
ship to the Azure Function with the code, and because a pretty-printed diff makes
a refit reviewable — you can see what moved.
"""

from __future__ import annotations

import json
from pathlib import Path

from rehoboam.scoring.v2.availability import AvailabilityModel
from rehoboam.scoring.v2.lineup_prob import LineupProbModel
from rehoboam.scoring.v2.rate import RateModel

COEFFICIENTS_PATH = Path(__file__).parent / "coefficients.json"


def save_coefficients(
    availability: AvailabilityModel,
    rate: RateModel,
    meta: dict,
    path: Path = COEFFICIENTS_PATH,
) -> None:
    """Write fitted models to disk, pretty-printed for reviewable diffs.

    A lineup model already in the file (`save_lineup_prob`) is carried over:
    it is fitted from a different source on a different cadence, and a refit
    of availability and rate must not silently drop it.
    """
    payload = {
        "meta": meta,
        "availability": availability.to_dict(),
        "rate": rate.to_dict(),
    }
    existing = _read(path)
    if existing.get("lineup_prob"):
        payload["lineup_prob"] = existing["lineup_prob"]
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _read(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def load_lineup_prob(path: Path = COEFFICIENTS_PATH) -> LineupProbModel | None:
    """The fitted P(status | lineup code), or None until `fit-lineup-prob` has run.

    None is the "feature off" state: every caller then scores exactly as it did
    before the code existed.
    """
    data = _read(path).get("lineup_prob")
    return LineupProbModel.from_dict(data) if data else None


def save_lineup_prob(model: LineupProbModel, path: Path = COEFFICIENTS_PATH) -> None:
    """Add or replace the lineup model, leaving availability and rate untouched."""
    payload = _read(path)
    payload["lineup_prob"] = model.to_dict()
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def load_coefficients(
    path: Path = COEFFICIENTS_PATH,
) -> tuple[AvailabilityModel, RateModel, dict]:
    """Load fitted models. Raises FileNotFoundError if never fitted."""
    if not path.exists():
        raise FileNotFoundError(
            f"No fitted coefficients at {path}. Run `rehoboam fit-scorer` first."
        )
    payload = json.loads(path.read_text())
    return (
        AvailabilityModel.from_dict(payload["availability"]),
        RateModel.from_dict(payload["rate"]),
        payload.get("meta", {}),
    )
