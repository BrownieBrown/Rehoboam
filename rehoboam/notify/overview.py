"""One message for what a session did with the wallet (spec §1).

Proposals used to go out and wait for a tap: 13 approvals, 2 acquisitions,
the rest poached while the message sat there (2026-08-29 to 2026-09-02). The
session now places its offers itself, behind the safety gate, and this is the
record: which offers went out, at what price and why; which candidates the
gate refused and for what reason; the budget before and after. No buttons —
there is nothing left to decide.

Pure, so the message can be asserted directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Below this weekly market-value move a line is called out as falling.
#: Itten went out at -27.0%/7d with the number printed and nothing flagged.
FALLING_TREND_PCT = -10.0

_POSITION_ABBR = {
    "Goalkeeper": "GK",
    "Defender": "DEF",
    "Midfielder": "MID",
    "Forward": "FWD",
}


@dataclass(frozen=True)
class OfferLine:
    """One attempted buy, with everything the message needs to justify it.

    The pipeline already computes all of this — `PlayerScore` carries
    position, lineup probability, minutes trend, average points and next
    opponent — and the first renderer discarded it and printed "unknown club".
    """

    offer_id: str
    name: str
    bid: int
    ep: float
    marginal_gain: float
    #: "placed" — the offer is live. "refused" — the safety gate said no.
    #: "failed" — Kickbase or the network said no. `detail` carries the why.
    outcome: str = "placed"
    detail: str = ""
    position: str = ""
    club: str = ""
    market_value: int = 0
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


def _availability(line: OfferLine) -> str:
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


def _line_block(line: OfferLine) -> list[str]:
    """One offer, a few short lines. Position and club lead."""
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

    if line.fills_gap and line.position:
        out.append(f"      fills your {line.pos_short} gap")
    elif line.squad_at_position is not None and line.position_minimum is not None:
        out.append(
            f"      you have {line.squad_at_position} {line.pos_short} "
            f"(min {line.position_minimum})"
        )

    for risk in line.risks:
        out.append(f"      ! {risk}")
    return out


def render_session_board(
    *,
    squad_size: int,
    squad_cap: int,
    budget_before: int,
    budget_after: int,
    open_offers_before: int = 0,
    placed: list[OfferLine],
    refused: list[OfferLine],
) -> str:
    """What this session did with the wallet, in one message.

    `placed` are live offers; `refused` are the gate's and Kickbase's refusals,
    each with its reason on its own line so a ceiling that keeps firing is
    visible without a log query.

    `budget_after` is what this session's own offers leave behind, not what
    Kickbase will report — Kickbase does not deduct open offers from the
    budget it hands back, so `open_offers_before` (money already committed
    before this session touched anything) gets its own line rather than
    being folded silently into the header's arithmetic.
    """
    lines = [
        f"SQUAD {squad_size}/{squad_cap}   BUDGET EUR {budget_before:,} -> "
        f"EUR {budget_after:,} after this session's offers",
    ]
    if open_offers_before > 0:
        lines.append(
            f"OPEN OFFERS BEFORE THIS SESSION EUR {open_offers_before:,} "
            "(not deducted by Kickbase)"
        )
    lines.append("")

    if placed:
        lines.append(f"OFFERS PLACED — {len(placed)}")
        for line in placed:
            lines += _line_block(line)
            lines.append("")
        lines.append(f"  total EUR {sum(line.bid for line in placed):,}")
    else:
        lines.append("OFFERS PLACED — none")

    if refused:
        lines += ["", f"REFUSED — {len(refused)}"]
        for line in refused:
            lines += _line_block(line)
            if line.detail:
                lines.append(f"      ! {line.detail}")
            lines.append("")

    return "\n".join(lines).rstrip()
