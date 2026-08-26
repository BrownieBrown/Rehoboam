"""Hard limits applied to anything that executes, autonomous or approved.

This is the last thing between a decision and real money. It is a pure
function so it can be exhaustively tested, and it collects ALL failing
reasons rather than short-circuiting on the first — a caller reporting to
Telegram should be able to say everything that is wrong at once.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from rehoboam.config import MAX_PLAYERS_PER_CLUB
from rehoboam.services.bid_ceiling import FALLBACK_TIER, BidCeilingPolicy, Tier


def club_counts(holdings: Iterable[object]) -> dict[str, int]:
    """How many players we hold per club, counting open bids as already held.

    Kickbase counts a pending offer toward the squad, so a bid that would be
    our fourth player from a club is illegal the moment it is placed, not when
    it resolves. Pass squad + open bids together.

    Lives here rather than at each call site because the approval handler and
    the autonomous paths must count the same way — a gate that disagrees with
    itself about the club limit is worse than no gate.
    """
    counts: dict[str, int] = {}
    for held in holdings:
        cid = str(getattr(held, "team_id", "") or "")
        if cid:
            counts[cid] = counts.get(cid, 0) + 1
    return counts


@dataclass(frozen=True)
class GateResult:
    """Outcome of a gate check. ``reasons`` is empty when ``ok`` is True."""

    ok: bool
    reasons: list[str] = field(default_factory=list)


def check_buy(
    *,
    player_id: str,
    bid: int,
    market_value: int,
    current_budget: int,
    free_slots: int,
    known_player_ids: Iterable[str],
    tier: Tier | str | None,
    ceiling_policy: BidCeilingPolicy,
    club_id: str | None = None,
    squad_club_counts: Mapping[str, int] | None = None,
    max_players_per_club: int = MAX_PLAYERS_PER_CLUB,
) -> GateResult:
    """Is this buy allowed to execute?

    ``known_player_ids`` is the set of ids from the data this decision was
    made against. An id outside it never reaches ``api.buy_player``: a forged
    webhook callback, or a stale proposal naming a player who has since left
    the market, must not be able to spend money.
    """
    reasons: list[str] = []

    if player_id not in set(known_player_ids):
        reasons.append(f"unknown player id {player_id!r} — not in the current market data")

    if bid <= 0:
        reasons.append(f"invalid bid: EUR {bid:,} (must be positive)")

    if current_budget - bid < 0:
        reasons.append(
            f"budget would go negative: EUR {current_budget:,} - EUR {bid:,} "
            f"= EUR {current_budget - bid:,} (zero points for the matchday)"
        )

    if market_value <= 0:
        reasons.append(f"invalid market value: EUR {market_value:,} (must be positive)")
    else:
        # The ceiling is computed here rather than accepted as a number, so the
        # last check before real money never trusts a caller's arithmetic. It
        # is the same `max_allowed_bid` the bidder sized against, which is what
        # stops a proposal being offered and then refused (REH-99).
        cap = ceiling_policy.max_bid(market_value, tier)
        if bid > cap:
            over = (bid / market_value - 1.0) * 100.0
            allowed = (cap / market_value - 1.0) * 100.0
            reasons.append(
                f"overbid {over:.1f}% exceeds the {allowed:.1f}% ceiling for tier "
                f"{Tier(tier).value if tier else FALLBACK_TIER.value} "
                f"(bid EUR {bid:,} vs max EUR {cap:,} on market value EUR {market_value:,})"
            )

    if free_slots <= 0:
        reasons.append("no free squad slot")

    # League rule: at most three players from any one club. Breaking it makes
    # the squad illegal, so this refuses rather than warns. Unknown club or
    # unknown counts cannot be checked, and are not treated as a violation —
    # the caller is responsible for supplying them.
    if club_id is not None and squad_club_counts is not None:
        held = int(squad_club_counts.get(str(club_id), 0))
        if held >= max_players_per_club:
            reasons.append(
                f"club limit: already hold {held} player(s) from club {club_id} "
                f"(max {max_players_per_club})"
            )

    return GateResult(ok=not reasons, reasons=reasons)


@dataclass(frozen=True)
class BuyGate:
    """The gate's inputs that a call site already knows before it spends.

    `check_buy` stays a flat pure function — it is exhaustively tested that
    way, and the approval handler builds its arguments one at a time from live
    API state. The autonomous paths don't have that luxury: they decide inside
    a loop, and a seven-argument call repeated at each site is exactly how one
    of them ends up forgotten, which is the defect REH-100 describes.

    So this carries the per-player half of the check as one value, built fresh
    for each candidate immediately before it executes.

    `spendable_budget` is the money **this path is permitted to commit**, which
    is not always the wallet balance. `check_buy` refuses a bid that takes the
    budget below zero, and for a single approved buy that is exactly right. The
    autonomous pipeline is different by design: `_compute_flip_budget` hands the
    aggressive phase `current_budget + max_debt`, because buying a good player
    mid-week and selling to recover before kickoff is the strategy, not a bug
    (`test_negative_budget_allowed_far_from_kickoff` pins it). Passing the raw
    balance here would have quietly deleted that. The kickoff-lockout guard in
    `ExecutionService.buy` is what makes the balance itself non-negative when it
    actually matters — at kickoff, where negative means zero points all matchday.
    """

    market_value: int
    spendable_budget: int
    known_player_ids: Iterable[str]
    free_slots: int
    tier: Tier | str | None
    ceiling_policy: BidCeilingPolicy
    club_id: str | None = None
    squad_club_counts: Mapping[str, int] | None = None

    def check(self, *, player_id: str, bid: int) -> GateResult:
        """Run the gate for this player at this price."""
        return check_buy(
            player_id=player_id,
            bid=bid,
            market_value=self.market_value,
            current_budget=self.spendable_budget,
            free_slots=self.free_slots,
            known_player_ids=self.known_player_ids,
            tier=self.tier,
            ceiling_policy=self.ceiling_policy,
            club_id=self.club_id,
            squad_club_counts=self.squad_club_counts,
        )
