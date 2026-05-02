"""Learning tracker — persists bid/purchase/flip outcomes for the bid_learner.

Separates the operational state from AutoTrader so the trading code stays
focused on decisions and execution. Two slices of state are managed:

- pending bids — auctions placed but not yet known to be won/lost
- tracked purchases — players we currently hold, with their cost basis

Both used to live in JSON files (`pending_bids.json`,
`tracked_purchases.json`) under `logs/`. Azure didn't sync those, so
they were silently wiped between runs. Both now live in `bid_learning.db`
alongside `auction_outcomes` and `flip_outcomes` (which are the
historical archive — operational rows are deleted on lifecycle close).

On `__init__`, any leftover JSON files are imported into the DB and
renamed to `.bak` (one-time, idempotent — see `migration.py`).

On each `resolve_auctions()` call, pending bids are checked against the
current squad + active bids to determine won/lost; outcomes are pushed
into the BidLearner's history tables.
"""

import logging
import time
from datetime import datetime
from pathlib import Path

from ..bid_learner import AuctionOutcome, BidLearner, FlipOutcome
from .migration import migrate_json_state_if_needed

logger = logging.getLogger(__name__)

_LOG_DIR = Path("logs")
_PENDING_BIDS_JSON = _LOG_DIR / "pending_bids.json"
_TRACKED_PURCHASES_JSON = _LOG_DIR / "tracked_purchases.json"


class LearningTracker:
    """Persists trade outcomes for the adaptive bidding feedback loop.

    All public methods silently swallow exceptions — learning side effects
    must never block the main trading loop. A failed write is not worse
    than skipped data.
    """

    def __init__(self, bid_learner: BidLearner):
        self.bid_learner = bid_learner

        # One-time migration from legacy JSON state files. Idempotent:
        # missing files are no-ops, and successful imports rename the
        # source to `.bak` so subsequent boots skip the work.
        try:
            migrate_json_state_if_needed(
                bid_learner,
                pending_bids_path=_PENDING_BIDS_JSON,
                tracked_purchases_path=_TRACKED_PURCHASES_JSON,
            )
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("State migration failed (continuing): %s", e)

    # ------------------------------------------------------------------
    # Bid placed → pending
    # ------------------------------------------------------------------

    def record_bid_placed(
        self,
        player,
        our_bid: int,
        sell_plan_player_ids: list[str] | None = None,
    ) -> None:
        """Record a freshly-placed bid as pending (outcome TBD).

        If the buy has a paired sell_plan (players to sell after winning to
        recover budget), persist their IDs here so resolve_auctions can
        execute the sells when we detect we won the auction.
        """
        try:
            asking_price = player.price
            overbid_pct = ((our_bid - asking_price) / asking_price * 100) if asking_price > 0 else 0

            self.bid_learner.add_pending_bid(
                player_id=player.id,
                player_name=f"{player.first_name} {player.last_name}",
                our_bid=our_bid,
                asking_price=asking_price,
                our_overbid_pct=overbid_pct,
                timestamp=time.time(),
                market_value=getattr(player, "market_value", 0),
                sell_plan_player_ids=sell_plan_player_ids,
            )
        except Exception as e:
            logger.warning("Failed to record bid placement: %s", e)

    # ------------------------------------------------------------------
    # Pending → resolved (won/lost)
    # ------------------------------------------------------------------

    def resolve_auctions(
        self,
        squad_ids: set[str],
        active_bid_ids: set[str],
    ) -> list[str]:
        """Reconcile pending bids against current squad + bids.

        Returns a list of player IDs that need to be sold (from sell plans
        attached to bids we won). The caller is responsible for executing
        those sells — this method only records outcomes and returns the IDs.

        - If a pending bid's player is now in the squad → we won.
          If the bid had sell_plan_player_ids, they're returned.
        - If a pending bid's player is not in squad AND not in active bids → lost
        - Otherwise → still pending (keep for next check)
        """
        sell_plan_ids: list[str] = []
        try:
            pending = self.bid_learner.get_pending_bids()
            if not pending:
                return sell_plan_ids

            for bid_data in pending:
                player_id = bid_data["player_id"]

                if player_id in squad_ids:
                    self._record_outcome(bid_data, won=True)
                    self._track_purchase(player_id, bid_data)
                    for sp_id in bid_data.get("sell_plan_player_ids", []):
                        if sp_id not in sell_plan_ids:
                            sell_plan_ids.append(sp_id)
                    self.bid_learner.delete_pending_bid(player_id)
                elif player_id not in active_bid_ids:
                    self._record_outcome(bid_data, won=False)
                    self.bid_learner.delete_pending_bid(player_id)
                # else: still pending — leave the row in place
        except Exception as e:
            logger.warning("Auction resolution failed: %s", e)
        return sell_plan_ids

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
        except Exception as e:
            logger.warning("Failed to record auction outcome: %s", e)

    # ------------------------------------------------------------------
    # Purchase tracking (for later flip profit calc)
    # ------------------------------------------------------------------

    def _track_purchase(self, player_id: str, bid_data: dict) -> None:
        """Record that we bought a player — used later to compute flip profit."""
        try:
            self.bid_learner.add_tracked_purchase(
                player_id=player_id,
                player_name=bid_data["player_name"],
                buy_price=bid_data["our_bid"],
                buy_date=bid_data["timestamp"],
                source="real",
            )
        except Exception as e:
            logger.warning("Failed to track purchase: %s", e)

    # ------------------------------------------------------------------
    # Flip outcome (bought + sold → profit recorded)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Matchday self-calibration (REH-20)
    #
    # `bid_learner.record_matchday_outcome` is the write side of the scorer
    # self-calibration loop wired up in PR #17 — `get_position_calibration_multiplier`
    # (read side) reads it back and corrects systematic per-position bias in
    # the EP scorer.  Until this method existed nothing called the writer, so
    # the read side has been returning the uncalibrated default of 1.0 since
    # PR #17 merged.
    # ------------------------------------------------------------------

    def snapshot_predictions(
        self,
        league_id: str,
        squad_scores: list,
        best_11_ids: set[str],
        predicted_at: float | None = None,
    ) -> int:
        """Persist this session's EP predictions for later reconciliation.

        ``squad_scores`` is the list of :class:`~rehoboam.scoring.models.PlayerScore`
        produced by the EP pipeline. We capture only what we need to evaluate
        post-matchday: predicted_ep, position, and whether the player was in
        the best-11 at prediction time.

        Returns the number of rows written.
        """
        ts = predicted_at if predicted_at is not None else time.time()
        rows = []
        for ps in squad_scores:
            try:
                rows.append(
                    {
                        "player_id": ps.player_id,
                        "league_id": league_id,
                        "predicted_at": ts,
                        "predicted_ep": ps.expected_points,
                        "position": ps.position,
                        "was_in_best_11": ps.player_id in best_11_ids,
                        "marginal_ep_gain": None,
                    }
                )
            except AttributeError:
                # Defensive: skip any score row missing expected fields rather
                # than blow up the session. Learning is best-effort.
                continue
        try:
            count = self.bid_learner.snapshot_predictions(rows)
            logger.info(
                "snapshot_predictions: persisted %d EP predictions for league=%s at ts=%s",
                count,
                league_id,
                int(ts),
            )
            return count
        except Exception as e:
            logger.warning("Failed to snapshot predictions: %s", e)
            return 0

    def reconcile_finished_matchdays(
        self,
        squad: list,
        performance_by_player_id: dict[str, dict],
    ) -> int:
        """Pair past actual points with our pre-kickoff predictions.

        Walks each squad player's ``performance['it'][0]['ph']`` (current-season
        match history). For every match with ``mdst == 2`` (finished) that we
        haven't already recorded into ``matchday_outcomes``, looks up the
        snapshot prediction we made before that match's ``md`` timestamp and
        writes a paired row.

        Skips:
          - matches without ``mdst == 2`` (still upcoming/in-progress)
          - matches missing ``md`` or ``p`` (defensive)
          - matchdays we already recorded (idempotent)
          - players for whom we have no prior prediction snapshot

        Returns the number of new ``matchday_outcomes`` rows written.
        """
        written = 0
        for player in squad:
            perf = performance_by_player_id.get(player.id)
            if not perf:
                continue
            seasons = perf.get("it") or []
            if not seasons:
                continue
            seasons_sorted = sorted(seasons, key=lambda s: s.get("ti", ""), reverse=True)
            current_season = seasons_sorted[0]
            matches = current_season.get("ph") or []

            for match in matches:
                if match.get("mdst") != 2:
                    continue  # skip upcoming / in-progress
                md_str = match.get("md")
                actual_pts = match.get("p")
                if not md_str or actual_pts is None:
                    continue
                if self.bid_learner.has_matchday_outcome(player.id, md_str):
                    continue

                try:
                    md_dt = datetime.fromisoformat(md_str.replace("Z", "+00:00"))
                    md_ts = md_dt.timestamp()
                except (ValueError, AttributeError):
                    continue

                snapshot = self.bid_learner.get_latest_prediction_before(player.id, md_ts)
                if snapshot is None:
                    logger.debug(
                        "reconcile: no prior prediction for %s before %s — skip",
                        player.id,
                        md_str,
                    )
                    continue

                try:
                    self.bid_learner.record_matchday_outcome(
                        player_id=player.id,
                        player_position=snapshot["position"],
                        matchday_date=md_str,
                        predicted_ep=snapshot["predicted_ep"],
                        actual_points=float(actual_pts),
                        was_in_best_11=bool(snapshot.get("was_in_best_11")),
                    )
                    written += 1
                    logger.info(
                        "reconcile: %s @ %s — predicted=%.1f actual=%s",
                        getattr(player, "last_name", player.id),
                        md_str,
                        snapshot["predicted_ep"],
                        actual_pts,
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to record matchday outcome for %s @ %s: %s",
                        player.id,
                        md_str,
                        e,
                    )
        if written:
            logger.info("reconcile_finished_matchdays: wrote %d new outcome row(s)", written)
        else:
            logger.debug("reconcile_finished_matchdays: no new outcomes to record")
        return written

    def record_flip_outcome(self, player, sell_price: int, reason: str | None = None) -> None:
        """Compute and record a flip profit outcome for a sold player.

        Always records into ``recently_sold`` (the wash-trade guard) even when
        we have no purchase record — selling a player we never tracked still
        means we shouldn't re-bid on them within the guard window.
        """
        sell_date = time.time()
        player_name = f"{player.first_name} {player.last_name}"

        try:
            self.bid_learner.record_recent_sell(
                player_id=player.id,
                player_name=player_name,
                sold_price=sell_price,
                sold_at=sell_date,
                reason=reason,
            )
        except Exception as e:
            logger.warning("Failed to record recent sell: %s", e)

        try:
            purchase = self.bid_learner.get_tracked_purchase(player.id)
            if purchase is None:
                return

            buy_price = purchase["buy_price"]
            buy_date = purchase["buy_date"]

            profit = sell_price - buy_price
            profit_pct = (profit / buy_price * 100) if buy_price > 0 else 0
            hold_days = int((sell_date - buy_date) / (24 * 3600))

            outcome = FlipOutcome(
                player_id=player.id,
                player_name=player_name,
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
            self.bid_learner.delete_tracked_purchase(player.id)
        except Exception as e:
            logger.warning("Failed to record flip outcome: %s", e)
