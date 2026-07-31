"""Persistence for fitted v2 scorer coefficients.

Coefficients live in a committed JSON file rather than a database because they
ship to the Azure Function with the code, and because a pretty-printed diff makes
a refit reviewable — you can see what moved.
"""

from __future__ import annotations

import json
from pathlib import Path

from rehoboam.scoring.v2.availability import AvailabilityModel
from rehoboam.scoring.v2.rate import RateModel

COEFFICIENTS_PATH = Path(__file__).parent / "coefficients.json"


def save_coefficients(
    availability: AvailabilityModel,
    rate: RateModel,
    meta: dict,
    path: Path = COEFFICIENTS_PATH,
) -> None:
    """Write fitted models to disk, pretty-printed for reviewable diffs."""
    payload = {
        "meta": meta,
        "availability": availability.to_dict(),
        "rate": rate.to_dict(),
    }
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
