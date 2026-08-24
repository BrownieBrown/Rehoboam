"""Rendering decisions into text a human can act on.

Pure functions. Every number here is one the bot computed and can show its
working for — the point of the message is that approving takes one tap because
the case is already complete.
"""

from __future__ import annotations


def render_proposal(
    *,
    player_name: str,
    club: str,
    bid: int,
    market_value: int,
    ep: float,
    displaced_name: str,
    displaced_ep: float,
    marginal_gain: float,
    budget_before: int,
    trend_7d_pct: float | None,
    risks: list[str],
) -> str:
    """The three questions Marco asked for, each as its own section."""
    overbid_str = f"+{(bid / market_value - 1.0) * 100.0:.1f}%" if market_value > 0 else "unknown"
    trend = f"{trend_7d_pct:+.1f}%/7d" if trend_7d_pct is not None else "unknown"
    budget_after = budget_before - bid

    lines = [
        f"BUY {player_name} — EUR {bid:,}",
        "",
        "WHY THIS PLAYER",
        f"  EP {ep:.1f}. {club}.",
        "",
        "WHY IT IMPROVES THE LINEUP",
        f"  Displaces {displaced_name} ({displaced_ep:.1f}) in the best eleven.",
        f"  Net gain {marginal_gain:+.1f} points per matchday.",
        "",
        "WHY THIS PRICE",
        f"  Market value EUR {market_value:,}; bid {overbid_str}. Trend {trend}.",
        f"  Budget EUR {budget_before:,} -> EUR {budget_after:,}.",
    ]
    if risks:
        lines += ["", "RISKS"] + [f"  {r}" for r in risks]
    return "\n".join(lines)


def render_daily_summary(
    *,
    outlook=None,
    squad_size: int,
    budget: int,
    pending: list[tuple[str, int]],
    executed: list[str],
    rejections: list[str],
    watch: list[str],
) -> str:
    """The once-a-day picture, trimmed to what actually needs a decision.

    Ordered by what the reader has to do about it: the fixture first because
    26/27 is head-to-head and one opponent is the whole target, then anything
    waiting on a tap, then risks, then what already happened. The old version
    led with a full market dump, which is the part nobody acts on.

    ``outlook`` is a ``h2h.MatchupOutlook`` or None when the fixture could not
    be read; the section is omitted rather than faked.
    """
    lines: list[str] = []

    if outlook is not None:
        m = outlook.matchup
        when = (m.starts_at or "")[:10]
        lines += [
            f"MATCHDAY {m.day} vs {m.opponent_name}   {when}",
            f"  projected  you {outlook.us.projected_points:>5.0f}"
            f"   {m.opponent_name} {outlook.them.projected_points:>5.0f}"
            f"   ({outlook.margin:+.0f}, {outlook.verdict})",
            "",
        ]

    lines += [f"SQUAD {squad_size}/15   BUDGET EUR {budget:,}", ""]

    lines.append(f"NEEDS YOU ({len(pending)})")
    if pending:
        for name, bid in pending:
            lines.append(f"  approve {name:<14} EUR {bid:>12,}")
    else:
        lines.append("  nothing awaiting approval")

    if watch:
        lines += ["", "WATCH"] + [f"  {w}" for w in watch]

    lines += ["", "DONE (24h)"] + ([f"  {e}" for e in executed] or ["  nothing"])

    if rejections:
        lines += ["", "BLOCKED OR FAILED"] + [f"  {r}" for r in rejections]

    return "\n".join(lines)
