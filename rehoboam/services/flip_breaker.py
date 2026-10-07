"""A drawdown breaker for flips (2026-10-07).

Five flips closed between 10-04 and 10-06, every one at a loss, -4.8m in
three days, and the bot kept opening more. Below a realised loss over the
last days the window closes until the window rolls on. Pure.
"""

from __future__ import annotations


def breaker_reason(realised_eur: int | None, *, loss_limit_eur: int, days: int) -> str | None:
    if realised_eur is None:
        return None
    if realised_eur <= -int(loss_limit_eur):
        return (
            f"flips lost EUR {-int(realised_eur):,} over the last {days} days "
            f"(limit EUR {int(loss_limit_eur):,})"
        )
    return None
