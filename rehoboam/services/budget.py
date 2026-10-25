"""How much a trading phase may commit: the wallet plus the debt allowance.

One rule, one module. Until 2026-10-13 the allowance lived only in
`auto_trader` (the `BuyGate`'s `spendable_budget`) while the recommender in
`Trader.get_ep_recommendations` judged affordability against the raw wallet.
With the wallet at -10.0m and a 30.8m allowance, every buy therefore needed a
sell plan covering the price PLUS the whole debt, and with eight of ten
players rank-held no plan was viable: a ten-man squad placed nothing twice
(session 4acbbbdf98a1, 2026-10-12 20:00 UTC) although Marco's rule is "it
can go into minus until gameday, always". The two guards now read the same
number.

Pure: no API, no clock, no settings object -- callers pass the numbers in.
"""

from __future__ import annotations

from dataclasses import dataclass

from .execution import LOCKOUT_DAYS


def max_debt(*, team_value: int, budget: int, max_debt_pct: float, worth_pct: float) -> int:
    """How far below zero a phase may take the wallet.

    Marco's allowance is ``max_debt_pct`` of team value (60%). Kickbase's is
    ``worth_pct`` (33%) of total worth — team value plus budget — and it
    refuses the offer that would breach it (`err 5050
    ThirtyThreePercentRuleExceeded`: nine of the bot's offers between
    2026-09-22 and 10-04). The allowance stops where Kickbase stops.
    """
    tv = int(team_value or 0)
    if tv <= 0:
        return 0
    own = int(tv * (float(max_debt_pct) / 100.0))
    worth = tv + int(budget or 0)
    kickbase = int(max(0, worth) * (float(worth_pct) / 100.0))
    return max(0, min(own, kickbase))


def phase_allowance(
    phase: str,
    current_budget: int,
    pending_bid_total: int,
    max_debt: int,
    *,
    schedule_known: bool = True,
) -> int:
    """Free budget for the session's buy paths, by matchday phase.

    Shared by session-context build and the trade-phase refresh so both
    call sites agree on the formula after sells/bid-cancels mutate the
    inputs mid-session.

    Moderate (2-4 days out) used to allow no new debt, on the premise that
    nothing would recover it in time. Since 2026-09-22 the locked window
    sells the wallet back to zero itself (`_run_debt_recovery`), so the
    allowance is the same in every trading phase: Marco's rule is "it can go
    into minus until gameday, always".

    ``schedule_known`` is False when neither the schedule nor `/myeleven`
    gave a kickoff. Then the phase is a fallback, not a measurement, and the
    recovery — gated on the day count — could never fire before a kickoff
    the bot cannot see; so no NEW debt, exactly as the old moderate rule.
    """
    if phase == "locked":
        return 0
    if not schedule_known:
        return current_budget - pending_bid_total
    return current_budget + max_debt - pending_bid_total


@dataclass(frozen=True)
class BuyingPower:
    """What the recommender may plan with, derived from one day count.

    ``gross`` is the wallet plus whatever debt the day count permits; it is
    what the bidder's ceiling starts from (pacing nets the open offers off
    it, as it always has). ``spendable`` is ``gross`` net of open offers: the
    number a buy is affordable against, and the number the `BuyGate` reads
    as ``spendable_budget`` on the same day.
    """

    wallet: int
    max_debt: int
    pending: int
    gross: int
    spendable: int

    @property
    def debt_allowed(self) -> bool:
        return self.gross > self.wallet


def buying_power(
    *,
    wallet: int,
    team_value: int,
    pending_bid_total: int,
    days_until_match: int | None,
    max_debt_pct: float,
    worth_pct: float,
    lockout_days: int = LOCKOUT_DAYS,
) -> BuyingPower:
    """The recommender's allowance for a given day count.

    Debt is permitted only while the kickoff is further away than the
    execution service's lockout (`LOCKOUT_DAYS`): inside it every buy that
    would leave the wallet negative is refused at execution anyway, and the
    debt recovery is selling, not buying. An unknown day count permits no
    new debt either, for the reason `phase_allowance` gives. Never more
    permissive than the gate: the REH-110 fallback phase may grant the gate
    the allowance on a small day count, and this stays at the wallet.
    """
    wallet_i = int(wallet or 0)
    pending_i = int(pending_bid_total or 0)
    debt_window_open = days_until_match is not None and int(days_until_match) > int(lockout_days)
    allowance = (
        max_debt(
            team_value=team_value, budget=wallet_i, max_debt_pct=max_debt_pct, worth_pct=worth_pct
        )
        if debt_window_open
        else 0
    )
    gross = wallet_i + allowance
    return BuyingPower(
        wallet=wallet_i,
        max_debt=allowance,
        pending=pending_i,
        gross=gross,
        spendable=gross - pending_i,
    )


def forced_sell_due(days_until_match: int | None) -> bool:
    """Whether step 5's forced bench sale may repay a negative wallet.

    Only when the schedule is unknown. With a known day count the debt is
    allowed until the locked window and `_run_debt_recovery` repays it there
    (it runs whenever the day count is within `LOCKOUT_DAYS`, before step 5
    is ever reached), so an earlier forced sale only realises losses: on
    Saturday 2026-10-17, six days out, it sold Maloney and Hack, the latter
    the morning he arrived. An unknown schedule hides the locked window, and
    a negative wallet at an unseen kickoff is zero points for the matchday.
    """
    return days_until_match is None
