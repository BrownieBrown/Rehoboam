"""When a marked flip is sold (2026-10-07): at the turn.

Measured on 48 momentum runs of six or more nights (status readings,
2026-09-17 to 10-06): selling the morning after the first night the value
did not rise captured 82% of the peak gain on average; after a run of
eight-plus nights ends, the next three nights cost a median -13.7%. A
target sells too early while the run continues (league resales held 8-30
days after a rising-night entry: median +10.9%, 68% wins); a stop-loss
from cost sells far too late (a +20% run gives back 35 points before -15%
fires). So the rule is: hold for the run, sell at the turn. The stop-loss
stays as the floor; the target and stop-loss rules apply unchanged when
the last nightly move is unknown. Pure, so every branch is pinned.
"""

from __future__ import annotations


def flip_exit_reason(
    *,
    profit_pct: float,
    last_move_pct: float | None,
    target_pct: float,
    max_loss_pct: float,
    exit_on_turn: bool = True,
) -> str | None:
    """The reason to sell a marked flip now, or None to hold."""
    if profit_pct <= max_loss_pct:
        return f"Flip stop-loss ({max_loss_pct:.0f}%): {profit_pct:+.1f}%"
    if exit_on_turn and last_move_pct is not None:
        if last_move_pct <= 0:
            return (
                f"Flip exit at the turn: last night {last_move_pct:+.1f}%, "
                f"{profit_pct:+.1f}% vs cost"
            )
        return None  # still rising: hold for the run, whatever the target says
    if profit_pct >= target_pct:
        return f"Flip target ({target_pct:.0f}%) hit: {profit_pct:+.1f}%"
    return None


_RULE_PREFIXES = (
    ("Flip exit at the turn", "turn"),
    ("Flip stop-loss", "stop_loss"),
    ("Flip target", "target"),
    ("Deferred sell plan", "sell_plan"),
    ("Debt recovery", "debt_recovery"),
    ("Outside the top", "rank"),
    ("Out for weeks", "rank"),
)


def exit_rule_tag(reason: str | None) -> str | None:
    """The short tag of the rule behind a sale reason, for `flip_outcomes.exit_rule`."""
    if reason is None:
        return None
    for prefix, tag in _RULE_PREFIXES:
        if reason.startswith(prefix):
            return tag
    return "other"
