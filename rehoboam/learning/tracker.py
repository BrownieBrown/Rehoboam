"""Learning tracker — persists bid/purchase/flip outcomes for the bid_learner.

Separates the file I/O and outcome-reconciliation logic from AutoTrader so the
trading code stays focused on decisions and execution.

Two JSON files are maintained under `logs/`:

- `pending_bids.json` — bids we placed but don't yet know if we won
- `tracked_purchases.json` — players we bought (for later flip profit calc)

On each `resolve_auctions()` call, pending bids are checked against the current
squad + active bids to determine won/lost, and outcomes are pushed into the
BidLearner (which is the real storage layer).
"""

import json
import time
from pathlib import Path
from typing import Any

from ..bid_learner import AuctionOutcome, BidLearner, FlipOutcome

_LOG_DIR = Path("logs")
_PENDING_BIDS = _LOG_DIR / "pending_bids.json"
_TRACKED_PURCHASES = _LOG_DIR / "tracked_purchases.json"


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


class LearningTracker:
    """Persists trade outcomes for the adaptive bidding feedback loop.

    All methods silently swallow exceptions — learning side effects must never
    block the main trading loop. A failed write is not worse than skipped data.
    """

    def __init__(self, bid_learner: BidLearner):
        self.bid_learner = bid_learner

    # ------------------------------------------------------------------
    # Bid placed → pending
    # ------------------------------------------------------------------

    def record_bid_placed(self, player, our_bid: int) -> None:
        """Record a freshly-placed bid as pending (outcome TBD)."""
        try:
            asking_price = player.price
            overbid_pct = ((our_bid - asking_price) / asking_price * 100) if asking_price > 0 else 0

            entry = {
                "player_id": player.id,
                "player_name": f"{player.first_name} {player.last_name}",
                "our_bid": our_bid,
                "asking_price": asking_price,
                "our_overbid_pct": overbid_pct,
                "timestamp": time.time(),
                "market_value": getattr(player, "market_value", 0),
            }

            pending = _load_json(_PENDING_BIDS, [])
            pending.append(entry)
            _save_json(_PENDING_BIDS, pending)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Pending → resolved (won/lost)
    # ------------------------------------------------------------------

    def resolve_auctions(self, squad_ids: set[str], active_bid_ids: set[str]) -> None:
        """Reconcile pending bids against current squad + bids.

        - If a pending bid's player is now in the squad → we won
        - If a pending bid's player is not in squad AND not in active bids → we lost
        - Otherwise → still pending (keep for next check)
        """
        try:
            pending = _load_json(_PENDING_BIDS, [])
            if not pending:
                return

            still_pending = []
            for bid_data in pending:
                player_id = bid_data["player_id"]

                if player_id in squad_ids:
                    # Won — record the outcome AND track the purchase for flip calc
                    self._record_outcome(bid_data, won=True)
                    self._track_purchase(player_id, bid_data)
                elif player_id not in active_bid_ids:
                    # Lost — record the outcome and drop
                    self._record_outcome(bid_data, won=False)
                else:
                    still_pending.append(bid_data)

            _save_json(_PENDING_BIDS, still_pending)
        except Exception:
            pass

    def _record_outcome(self, bid_data: dict, won: bool) -> None:
        try:
            outcome = AuctionOutcome(
                player_id=bid_data["player_id"],
                player_name=bid_data["player_name"],
                our_bid=bid_data["our_bid"],
                asking_price=bid_data["asking_price"],
                our_overbid_pct=bid_data["our_overbid_pct"],
                won=won,
                timestamp=bid_data["timestamp"],
                player_value_score=bid_data.get("player_value_score"),
                market_value=bid_data.get("market_value"),
            )
            self.bid_learner.record_outcome(outcome)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Purchase tracking (for later flip profit calc)
    # ------------------------------------------------------------------

    def _track_purchase(self, player_id: str, bid_data: dict) -> None:
        """Record that we bought a player — used later to compute flip profit."""
        try:
            purchases = _load_json(_TRACKED_PURCHASES, {})
            purchases[player_id] = {
                "player_name": bid_data["player_name"],
                "buy_price": bid_data["our_bid"],
                "buy_date": bid_data["timestamp"],
            }
            _save_json(_TRACKED_PURCHASES, purchases)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Flip outcome (bought + sold → profit recorded)
    # ------------------------------------------------------------------

    def record_flip_outcome(self, player, sell_price: int) -> None:
        """Compute and record a flip profit outcome for a sold player.

        Silently skips if we have no record of buying this player.
        """
        try:
            purchases = _load_json(_TRACKED_PURCHASES, {})
            if player.id not in purchases:
                return

            purchase = purchases[player.id]
            buy_price = purchase["buy_price"]
            buy_date = purchase["buy_date"]
            sell_date = time.time()

            profit = sell_price - buy_price
            profit_pct = (profit / buy_price * 100) if buy_price > 0 else 0
            hold_days = int((sell_date - buy_date) / (24 * 3600))

            outcome = FlipOutcome(
                player_id=player.id,
                player_name=f"{player.first_name} {player.last_name}",
                buy_price=buy_price,
                sell_price=sell_price,
                profit=profit,
                profit_pct=profit_pct,
                hold_days=hold_days,
                buy_date=buy_date,
                sell_date=sell_date,
                average_points=getattr(player, "average_points", None),
                position=getattr(player, "position", None),
                was_injured=(player.status != 0) if hasattr(player, "status") else False,
            )
            self.bid_learner.record_flip(outcome)

            # Drop from tracked purchases now that flip is closed
            del purchases[player.id]
            _save_json(_TRACKED_PURCHASES, purchases)
        except Exception:
            pass
