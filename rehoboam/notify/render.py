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
    lineup: list[tuple[str, float, str | None]],
    squad_size: int,
    budget: int,
    market: list[tuple[str, int, float]],
    pending: list[tuple[str, int]],
    executed: list[str],
    rejections: list[str],
) -> str:
    """The once-a-day picture: lineup, market, what happened, what was blocked.

    ``market`` is (name, market_value, average_points) — not a 7-day trend.
    A real trend needs one `TrendService` call per market player (~30 extra
    API calls for an email); average points is what `MarketPlayer` already
    carries.

    Proposal volume is reported explicitly. If approvals start arriving daily
    rather than weekly, that is the signal the approval gate has become the
    daily loop it was meant to replace.
    """
    lines = [
        f"SQUAD {squad_size}/15   BUDGET EUR {budget:,}",
        "",
        "LINEUP",
    ]
    for name, ep, flag in lineup:
        note = f"  [{flag}]" if flag else ""
        lines.append(f"  {name:<16} {ep:>6.1f}{note}")

    lines += ["", "MARKET"]
    for name, mv, avg_pts in market:
        lines.append(f"  {name:<16} EUR {mv:>12,}  {avg_pts:>6.1f} avg pts")

    lines += ["", f"PENDING PROPOSALS ({len(pending)})"]
    for name, bid in pending:
        lines.append(f"  {name:<16} EUR {bid:>12,}  awaiting approval")

    lines += ["", "EXECUTED (24h)"] + ([f"  {e}" for e in executed] or ["  nothing"])

    if rejections:
        lines += ["", "BLOCKED OR FAILED"] + [f"  {r}" for r in rejections]

    return "\n".join(lines)
