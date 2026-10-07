"""The season a moment belongs to, in Kickbase's ``"2026/2027"`` form.

Kickbase's season turns over in the summer; July 1st is the boundary the
fixtures table already uses. Berlin time, because that is the league's clock.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")


def season_label(now: datetime) -> str:
    local = now.astimezone(BERLIN) if now.tzinfo is not None else now.replace(tzinfo=BERLIN)
    start = local.year if local.month >= 7 else local.year - 1
    return f"{start}/{start + 1}"
