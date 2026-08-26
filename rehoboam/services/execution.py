"""Execution service — places bids and sells against the Kickbase API.

Owns the side-effecting half of the auto trading pipeline. The goal here is
that AutoTrader / SessionService just say "buy this player at this price"
and "sell this player instantly", and ExecutionService takes care of:

- Running `safety_gate.check_buy` before any buy reaches the API
- Dry-run short-circuiting (no real API call)
- Console feedback for each step
- Wrapping the API call in try/except and turning failures into a structured
  AutoTradeResult instead of raising
- Calling the LearningTracker hooks (record_bid_placed / record_flip_outcome)

Two public methods:

- `buy(league, player, price, reason)` — place an offer; on success the bid
  is recorded as pending in LearningTracker and resolved later.
- `instant_sell(league, player, reason)` — sell directly to Kickbase at
  ~95% market value. This frees the squad slot immediately, which is what
  trade pairs and profit-sell phases need. The "list on the transfer market"
  variant is intentionally absent — manager-to-manager trading is essentially
  dead in our league, so listings just sit there.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from rich.console import Console

from ..learning import LearningTracker
from .safety_gate import BuyGate

console = Console()
logger = logging.getLogger(__name__)


class BudgetSafetyError(Exception):
    """Raised when a buy would push budget < 0 within the kickoff lockout window.

    Defense-in-depth assertion. If this fires in prod, an upstream bug in
    MatchdayPhase / flip_budget logic let an unsafe candidate reach execution.
    """


LOCKOUT_DAYS = 1  # Within ~24h of kickoff: any trade that would go into debt is refused.


@dataclass
class AutoTradeResult:
    """Outcome of a single buy/sell attempt."""

    success: bool
    player_name: str
    action: str  # "BUY" | "SELL"
    price: int
    reason: str
    timestamp: float
    error: str | None = None


class ExecutionService:
    """Side-effecting trade execution against the Kickbase API."""

    def __init__(self, api, tracker: LearningTracker, dry_run: bool = False):
        self.api = api
        self.tracker = tracker
        self.dry_run = dry_run

    # ------------------------------------------------------------------
    # Public actions
    # ------------------------------------------------------------------

    def buy(
        self,
        league,
        player,
        price: int,
        reason: str,
        sell_plan_player_ids: list[str] | None = None,
        *,
        current_budget: int,
        days_until_match: int | None,
        gate: BuyGate,
    ) -> AutoTradeResult:
        """Place a buy offer at the given price.

        If the buy has a paired sell_plan (bench players to sell after winning
        the auction to recover budget), pass their IDs here. They'll be
        persisted alongside the pending bid and executed when resolve_auctions
        detects we won. This ensures buy-first-sell-after semantics: we never
        sell the old player before securing the new one.

        ``current_budget`` and ``days_until_match`` are required keyword args
        — they drive the kickoff-lockout safety guard (REH-11). Negative
        budget at kickoff = 0 points for the entire matchday, so within
        ``LOCKOUT_DAYS`` of kickoff any trade that would push budget < 0 is
        refused. Live mode raises ``BudgetSafetyError``; dry-run returns a
        failure ``AutoTradeResult``. Both modes log at ERROR.

        ``gate`` is required, not optional, and that is the point (REH-100).
        The gate previously guarded only the Telegram approval path, so the
        autonomous paths — trade pairs, profit flips, emergency squad fill —
        spent with nothing between the decision and the money. A default would
        let the next buy path added here inherit the same hole silently.
        """
        if (
            days_until_match is not None
            and days_until_match <= LOCKOUT_DAYS
            and (current_budget - price) < 0
        ):
            msg = (
                f"BLOCK: buy {player.first_name} {player.last_name} "
                f"€{price:,} would leave budget €{current_budget - price:,} "
                f"with {days_until_match}d to kickoff"
            )
            logger.error(msg)
            if self.dry_run:
                return AutoTradeResult(
                    success=False,
                    player_name=f"{player.first_name} {player.last_name}",
                    action="BUY",
                    price=price,
                    reason=reason,
                    timestamp=time.time(),
                    error=msg,
                )
            raise BudgetSafetyError(msg)

        # REH-100: the safety gate, on every buy rather than only the approved
        # one. It refuses with a result instead of raising, unlike the kickoff
        # guard above — that guard asserts an upstream bug and should be loud,
        # while a gate refusal is routine (a market value moves between sizing
        # and execution) and the callers already treat an unsuccessful buy as
        # "try the next candidate". Runs before the dry-run short-circuit so
        # `auto --dry-run` and `status` preview refusals rather than hiding them.
        verdict = gate.check(player_id=player.id, bid=price)
        if not verdict.ok:
            msg = "; ".join(verdict.reasons)
            logger.error(
                "GATE REFUSED buy player=%s price=%d: %s",
                player.id,
                price,
                msg,
            )
            console.print(
                f"[red]Gate refused {player.first_name} {player.last_name} "
                f"(€{price:,}): {msg}[/red]"
            )
            return AutoTradeResult(
                success=False,
                player_name=f"{player.first_name} {player.last_name}",
                action="BUY",
                price=price,
                reason=reason,
                timestamp=time.time(),
                error=f"safety gate refused: {msg}",
            )

        return self._do(
            action="BUY",
            player=player,
            price=price,
            reason=reason,
            announce=f"Buying {player.first_name} {player.last_name} for €{price:,}",
            success_msg=f"Buy order placed for {player.first_name} {player.last_name}",
            api_call=lambda: self.api.buy_player(league, player, price),
            on_success=lambda: self.tracker.record_bid_placed(
                player, price, sell_plan_player_ids=sell_plan_player_ids
            ),
        )

    def instant_sell(self, league, player, reason: str) -> AutoTradeResult:
        """Sell a player directly to Kickbase at ~95% market value.

        Used by trade pairs (slot must be free before placing the buy bid)
        and the profit-sell phase. Removes the player from the squad
        immediately — there's no "wait for buyer" step.
        """
        price = player.market_value
        return self._do(
            action="SELL",
            player=player,
            price=price,
            reason=reason,
            announce=(
                f"Instant-selling {player.first_name} {player.last_name}"
                f" to Kickbase for ~€{price:,}"
            ),
            success_msg=f"{player.first_name} {player.last_name} sold instantly to Kickbase",
            api_call=lambda: self.api.sell_player_instant(league=league, player=player),
            on_success=lambda: self.tracker.record_flip_outcome(player, price, reason=reason),
        )

    # ------------------------------------------------------------------
    # Internal scaffolding (dry-run, try/except, result building)
    # ------------------------------------------------------------------

    def _do(
        self,
        *,
        action: str,
        player,
        price: int,
        reason: str,
        announce: str,
        success_msg: str,
        api_call: Callable[[], object],
        on_success: Callable[[], None],
    ) -> AutoTradeResult:
        """Common buy/sell scaffolding.

        Steps:
        1. Print the announce line.
        2. If dry-run: return a successful result without touching the API.
        3. Otherwise: call api_call() inside try/except.
           On success: print success message, run on_success hook,
                       return success result.
           On failure: print red error, return failure result with error.
        """
        player_name = f"{player.first_name} {player.last_name}"
        console.print(f"\n[cyan]{announce}[/cyan]")

        if self.dry_run:
            console.print("[yellow]DRY RUN: Trade not executed[/yellow]")
            return AutoTradeResult(
                success=True,
                player_name=player_name,
                action=action,
                price=price,
                reason=reason,
                timestamp=time.time(),
            )

        try:
            api_call()
            console.print(f"[green]✓ {success_msg}[/green]")
            try:
                on_success()
            except Exception:
                pass  # Learning hook failures must never block trading
            return AutoTradeResult(
                success=True,
                player_name=player_name,
                action=action,
                price=price,
                reason=reason,
                timestamp=time.time(),
            )
        except Exception as e:
            error_msg = str(e)
            console.print(f"[red]✗ Failed to {action.lower()} {player_name}: {error_msg}[/red]")
            return AutoTradeResult(
                success=False,
                player_name=player_name,
                action=action,
                price=price,
                reason=reason,
                timestamp=time.time(),
                error=error_msg,
            )
