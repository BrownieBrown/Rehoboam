"""One message for a whole session's proposals, budget-aware (REH-117).

Proposals used to go out one Telegram message per player. Nothing could then
show how they interact, and they compete for one wallet: on 2026-08-31 Marco
approved Nusa and Ebnoutalib, which consumed the budget, and Avdullahu, Reis
and a second Avdullahu all failed on "budget would go negative".

So the session sends one message: the set that fits, then everything else.

The split is a WALK, not a knapsack. A knapsack would fit marginally more
money, and would sometimes skip the second line to afford the fourth — which,
in a list a human reads top to bottom, reads as a bug rather than as
optimisation. The order has to be explicable: slot-filling emergencies first
because they carry the -100, then by expected points, take what fits.

Pure, so the message can be asserted directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Below this weekly market-value move a proposal is called out as falling.
#: Itten went out at -27.0%/7d with the number printed and nothing flagged.
FALLING_TREND_PCT = -10.0

_POSITION_ABBR = {
    "Goalkeeper": "GK",
    "Defender": "DEF",
    "Midfielder": "MID",
    "Forward": "FWD",
}


@dataclass(frozen=True)
class ProposalLine:
    """One proposal, with everything the message needs to justify it.

    The pipeline already computes all of this and the old renderer discarded
    it — `PlayerScore` carries position, lineup probability, minutes trend,
    average points and next opponent, and every proposal still said
    "unknown club" with no position at all.
    """

    proposal_id: str
    name: str
    bid: int
    ep: float
    marginal_gain: float
    position: str = ""
    club: str = ""
    market_value: int = 0
    is_emergency: bool = False
    fills_gap: bool = False
    trend_7d_pct: float | None = None
    season_avg: float | None = None
    lineup_probability: int | None = None
    minutes_trend: str | None = None
    next_opponent: str | None = None
    is_dgw: bool = False
    squad_at_position: int | None = None
    position_minimum: int | None = None
    risks: tuple[str, ...] = field(default_factory=tuple)

    @property
    def overbid_pct(self) -> float:
        if self.market_value <= 0:
            return 0.0
        return (self.bid / self.market_value - 1.0) * 100.0

    @property
    def is_falling(self) -> bool:
        return self.trend_7d_pct is not None and self.trend_7d_pct <= FALLING_TREND_PCT

    @property
    def pos_short(self) -> str:
        return _POSITION_ABBR.get(self.position, self.position[:3].upper() or "???")


def split_by_budget(
    lines: list[ProposalLine], budget: int
) -> tuple[list[ProposalLine], list[ProposalLine]]:
    """Split proposals into (fits now, needs money first).

    Ordered by what costs points to leave undone, then by expected points. An
    unfilled lineup slot is -100 every matchday, and a position below its
    formation minimum makes the eleven illegal rather than merely weaker — the
    same argument `emergency_basket._value` makes when it counts gap coverage
    at the slot penalty. The two orderings have to agree, or the message
    recommends a surplus midfielder over the only available striker.

    A line that does not fit is moved aside and the walk continues, so one
    expensive pick does not strand the cheaper ones behind it.
    """
    ordered = sorted(lines, key=lambda x: (not (x.is_emergency or x.fills_gap), -x.ep, x.bid))

    recommended: list[ProposalLine] = []
    alternatives: list[ProposalLine] = []
    remaining = int(budget)
    for line in ordered:
        if line.bid <= remaining:
            recommended.append(line)
            remaining -= line.bid
        else:
            alternatives.append(line)
    return recommended, alternatives


def _availability(line: ProposalLine) -> str:
    bits: list[str] = []
    if line.lineup_probability is not None:
        bits.append(
            {1: "starter", 2: "likely starter", 3: "rotation"}.get(
                line.lineup_probability, "bench risk"
            )
        )
    if line.minutes_trend:
        bits.append(f"minutes {line.minutes_trend}")
    if line.is_dgw:
        bits.append("DOUBLE gameweek")
    return " · ".join(bits)


def _line_block(line: ProposalLine) -> list[str]:
    """One proposal, four short lines. Position and club lead — they were the
    two facts the old message never carried."""
    club = line.club or "unknown club"
    head = f"  {line.name} ({line.pos_short}, {club})  EUR {line.bid:,}"

    form: list[str] = []
    if line.season_avg is not None:
        form.append(f"avg {line.season_avg:.0f}/game")
    form.append(f"EP {line.ep:.0f}")
    if line.next_opponent:
        form.append(f"next {line.next_opponent}")

    price = f"MV {line.market_value:,} · bid {line.overbid_pct:+.1f}%"
    if line.trend_7d_pct is not None:
        price += f" · trend {line.trend_7d_pct:+.1f}%/7d"
        if line.is_falling:
            price += "  <-- FALLING"

    out = [head, f"      {' · '.join(form)}", f"      {price}"]

    availability = _availability(line)
    if availability:
        out.append(f"      {availability}")

    if line.is_emergency:
        # NOT "displaces the weakest starter (0.0)" — with a short squad there
        # is no incumbent, and the old message printed a placeholder and a zero.
        out.append("      fills an empty lineup slot (worth +100)")
    elif line.fills_gap and line.position:
        out.append(f"      fills your {line.pos_short} gap")
    elif line.squad_at_position is not None and line.position_minimum is not None:
        out.append(
            f"      you have {line.squad_at_position} {line.pos_short} "
            f"(min {line.position_minimum})"
        )

    for risk in line.risks:
        out.append(f"      ! {risk}")
    return out


def render_proposal_overview(
    *,
    squad_size: int,
    squad_cap: int,
    budget: int,
    recommended: list[ProposalLine],
    alternatives: list[ProposalLine],
) -> str:
    """The session's whole board, in one message.

    `recommended` is expected to come from `split_by_budget` and to fit inside
    `budget`; the total is printed either way so a caller that builds its own
    set cannot quietly present an unaffordable one as affordable.
    """
    total = sum(line.bid for line in recommended)
    lines = [
        f"SQUAD {squad_size}/{squad_cap}   BUDGET EUR {budget:,}",
        "",
    ]

    if recommended:
        lines.append(f"RECOMMENDED — {len(recommended)} of {len(recommended) + len(alternatives)}")
        for line in recommended:
            lines += _line_block(line)
            lines.append("")
        lines.append(f"  total EUR {total:,}   leaves EUR {budget - total:,}")
        if total > budget:
            lines.append(f"  WARNING over budget by EUR {total - budget:,}")
    else:
        lines.append("RECOMMENDED — none fit the current budget")

    if alternatives:
        lines += ["", "ALTERNATIVES — need a sell first"]
        for line in alternatives:
            lines += _line_block(line)
            lines.append("")

    return "\n".join(lines).rstrip()
