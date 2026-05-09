"""Learn from auction outcomes to improve bidding strategy"""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class AuctionOutcome:
    """Record of an auction result"""

    player_id: str
    player_name: str
    our_bid: int
    asking_price: int
    our_overbid_pct: float
    won: bool
    winning_bid: int | None = None
    winning_overbid_pct: float | None = None
    winner_user_id: str | None = None
    timestamp: float = None
    player_value_score: float | None = None
    market_value: int | None = None


def _opt_int(value: Any) -> int | None:
    """None-preserving int coercion. Used by writers that accept partial
    rows where any numeric column may legitimately be missing."""
    if value is None:
        return None
    return int(value)


@dataclass
class FlipOutcome:
    """Record of a completed flip (buy + sell)"""

    player_id: str
    player_name: str
    buy_price: int
    sell_price: int
    profit: int
    profit_pct: float
    hold_days: int
    buy_date: float
    sell_date: float
    trend_at_buy: str | None = None  # rising, falling, stable
    average_points: float | None = None
    position: str | None = None
    was_injured: bool = False


class BidLearner:
    """Learn from auction outcomes to improve bidding strategy"""

    def __init__(self, db_path: Path | None = None):
        if db_path is None:
            db_path = Path("logs") / "bid_learning.db"

        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Initialize database schema"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS auction_outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    player_id TEXT NOT NULL,
                    player_name TEXT NOT NULL,
                    our_bid INTEGER NOT NULL,
                    asking_price INTEGER NOT NULL,
                    our_overbid_pct REAL NOT NULL,
                    won INTEGER NOT NULL,
                    winning_bid INTEGER,
                    winning_overbid_pct REAL,
                    winner_user_id TEXT,
                    timestamp REAL NOT NULL,
                    player_value_score REAL,
                    market_value INTEGER
                )
            """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_player_id
                ON auction_outcomes(player_id)
            """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_timestamp
                ON auction_outcomes(timestamp)
            """
            )

            # Flip outcomes table for tracking buy+sell transactions
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS flip_outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    player_id TEXT NOT NULL,
                    player_name TEXT NOT NULL,
                    buy_price INTEGER NOT NULL,
                    sell_price INTEGER NOT NULL,
                    profit INTEGER NOT NULL,
                    profit_pct REAL NOT NULL,
                    hold_days INTEGER NOT NULL,
                    buy_date REAL NOT NULL,
                    sell_date REAL NOT NULL,
                    trend_at_buy TEXT,
                    average_points REAL,
                    position TEXT,
                    was_injured INTEGER NOT NULL DEFAULT 0
                )
            """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_flip_player_id
                ON flip_outcomes(player_id)
            """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_flip_buy_date
                ON flip_outcomes(buy_date)
            """
            )

            # REH-39: idempotency for the backfill-history CLI. Each real-world
            # flip is uniquely identified by (player_id, buy_date), so this
            # also catches accidental duplicate live writes as a side benefit.
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_flip_unique
                ON flip_outcomes(player_id, buy_date)
            """
            )

            # Matchday outcomes table for tracking EP accuracy
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS matchday_outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    player_id TEXT NOT NULL,
                    player_position TEXT NOT NULL,
                    matchday_date TEXT NOT NULL,
                    predicted_ep REAL NOT NULL,
                    actual_points REAL NOT NULL,
                    was_in_best_11 INTEGER DEFAULT 0,
                    opponent_strength TEXT,
                    purchase_price INTEGER,
                    marginal_ep_gain_at_purchase REAL,
                    timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(player_id, matchday_date)
                )
            """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_matchday_player
                ON matchday_outcomes(player_id)
            """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_matchday_position
                ON matchday_outcomes(player_position)
            """
            )

            # Operational state — bids placed but not yet resolved.
            # Replaces the legacy `pending_bids.json` file, which Azure
            # didn't sync between runs. One row per active auction; on
            # win/loss, the row is deleted and the outcome is appended
            # to `auction_outcomes` (which is the historical record).
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pending_bids (
                    player_id TEXT PRIMARY KEY,
                    player_name TEXT NOT NULL,
                    our_bid INTEGER NOT NULL,
                    asking_price INTEGER NOT NULL,
                    our_overbid_pct REAL NOT NULL,
                    timestamp REAL NOT NULL,
                    market_value INTEGER,
                    player_value_score REAL
                )
            """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_pending_bids_timestamp
                ON pending_bids(timestamp)
            """
            )

            # Sell-plan join table: when a bid wins, the listed players
            # are sold to recover budget. Normalized so we can answer
            # "which auctions freed which slots" without parsing JSON.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pending_bid_sell_plans (
                    pending_bid_player_id TEXT NOT NULL,
                    sell_player_id TEXT NOT NULL,
                    PRIMARY KEY (pending_bid_player_id, sell_player_id)
                )
            """
            )

            # Operational state — players we currently hold with their
            # cost basis. Replaces `tracked_purchases.json`. On sell,
            # the row is deleted and the closed flip is appended to
            # `flip_outcomes` (which is the historical record).
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tracked_purchases (
                    player_id TEXT PRIMARY KEY,
                    player_name TEXT NOT NULL,
                    buy_price INTEGER NOT NULL,
                    buy_date REAL NOT NULL,
                    source TEXT
                )
            """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tracked_purchases_buy_date
                ON tracked_purchases(buy_date)
            """
            )

            # Wash-trade guard — every sell is recorded here so the buy path
            # can refuse to re-bid on a player we just dumped. Without this
            # the same player can be sold and re-bought within hours,
            # paying the bid spread on both legs for no EP gain.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS recently_sold (
                    player_id TEXT PRIMARY KEY,
                    player_name TEXT NOT NULL,
                    sold_price INTEGER NOT NULL,
                    sold_at REAL NOT NULL,
                    reason TEXT
                )
            """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_recently_sold_sold_at
                ON recently_sold(sold_at)
            """
            )

            # Per-session EP prediction snapshots — required so post-matchday
            # reconciliation can pair "what we predicted before kickoff" with
            # "what actually happened" (matchday_outcomes). Without this
            # table the scorer-self-calibration loop in
            # get_position_calibration_multiplier has no input.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS predicted_eps (
                    player_id TEXT NOT NULL,
                    league_id TEXT NOT NULL,
                    predicted_at REAL NOT NULL,
                    predicted_ep REAL NOT NULL,
                    position TEXT NOT NULL,
                    was_in_best_11 INTEGER NOT NULL DEFAULT 0,
                    marginal_ep_gain REAL,
                    PRIMARY KEY (player_id, predicted_at)
                )
            """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_predicted_eps_player_at
                ON predicted_eps(player_id, predicted_at)
            """
            )

            # Per-session team-value snapshot — REH-23.
            # The bot fetches budget + team_value every run via get_team_info()
            # but throws the result away after logging it. Persisting it gives
            # us a longitudinal series for goal 3 (team value increases over
            # time) and feeds REH-37 (rank-trajectory regression).
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS team_value_history (
                    snapshot_at REAL PRIMARY KEY,
                    league_id TEXT NOT NULL,
                    team_value INTEGER NOT NULL,
                    budget INTEGER NOT NULL,
                    squad_size INTEGER NOT NULL
                )
            """
            )

            # Daily market-value snapshots for held players — REH-26.
            # The bot fetches /player/{id}/marketValue history every session
            # via TrendService (cached 24h) and consumes it transiently for
            # trend computation. The current MV plus 30-day peak/trough are
            # discarded after that, so we have no daily series to detect
            # slow drift on held positions (goal 2: loss avoidance).
            # Constrained to squad players to avoid blowing up DB size —
            # market players (~50/session) would multiply rows ~3x.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS player_mv_history (
                    player_id TEXT NOT NULL,
                    snapshot_at REAL NOT NULL,
                    market_value INTEGER NOT NULL,
                    peak_mv_30d INTEGER,
                    trough_mv_30d INTEGER,
                    PRIMARY KEY (player_id, snapshot_at)
                )
            """
            )
            # Explicit index matches the convention of every other time-series
            # table in this file. The composite PK already covers
            # (player_id, snapshot_at) lookups, but the named index makes
            # REH-33's "sold X% off peak" join obvious to readers.
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_mv_history_player
                ON player_mv_history(player_id, snapshot_at)
            """
            )

            # Matchday lineup actual results — REH-25.
            # One row per (league, matchday) capturing the lineup the bot
            # actually fielded that week and what it scored. Source: the
            # /users/{uid}/teamcenter?dayNumber=N endpoint, lp[] array.
            # Goal 4 (more points each week) is unmeasurable without this;
            # `matchday_outcomes` (REH-20) tracks per-player EP accuracy but
            # not total lineup output.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS matchday_lineup_results (
                    league_id TEXT NOT NULL,
                    day_number INTEGER NOT NULL,
                    matchday_date TEXT NOT NULL,
                    total_points INTEGER NOT NULL,
                    lineup_player_ids TEXT NOT NULL,
                    lineup_count INTEGER NOT NULL,
                    snapshot_at REAL NOT NULL,
                    PRIMARY KEY (league_id, day_number)
                )
            """
            )

            # League rank snapshot per session — REH-24.
            # The /ranking response already includes per-manager team_value
            # (`tv`), season points (`sp`), season placement (`spl`),
            # matchday points (`mdp`), matchday placement (`mdpl`) — all the
            # data goals 3, 4, 5 need. trader.py:179 was already calling this
            # for competitor_player_ids; we now persist what was discarded.
            # Composite PK lets a single session insert one row per manager.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS league_rank_history (
                    snapshot_at REAL NOT NULL,
                    league_id TEXT NOT NULL,
                    manager_id TEXT NOT NULL,
                    day_number INTEGER NOT NULL,
                    rank_overall INTEGER,
                    rank_matchday INTEGER,
                    total_points INTEGER,
                    matchday_points INTEGER,
                    team_value INTEGER,
                    is_self INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (snapshot_at, manager_id)
                )
            """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_league_rank_self_at
                ON league_rank_history(is_self, snapshot_at)
            """
            )

            # Per-manager transfer P&L snapshot — REH-38.
            # /managers/{mid}/dashboard returns `prft` (cumulative transfer
            # P&L) and `mdw` (matchday wins) — neither is in /ranking. We
            # snapshot both per session so we can plot trajectory and
            # benchmark the bot's flip P&L against leaguemates.
            # Composite PK matches league_rank_history for symmetry.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS manager_profile_history (
                    snapshot_at REAL NOT NULL,
                    league_id TEXT NOT NULL,
                    manager_id TEXT NOT NULL,
                    transfer_pnl INTEGER NOT NULL,
                    matchday_wins INTEGER,
                    is_self INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (snapshot_at, manager_id)
                )
            """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_manager_profile_self_at
                ON manager_profile_history(is_self, snapshot_at)
            """
            )

            # Per-manager transfer history — REH-38.
            # /managers/{mid}/transfer returns each completed buy/sell with
            # price + datetime. PK on (league, manager, dt, player) makes
            # re-imports idempotent: the same trade always collapses to one
            # row regardless of how many sessions see it.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS manager_transfers (
                    league_id TEXT NOT NULL,
                    manager_id TEXT NOT NULL,
                    transfer_dt TEXT NOT NULL,
                    player_id TEXT NOT NULL,
                    player_name TEXT NOT NULL,
                    transfer_type INTEGER,
                    transfer_price INTEGER,
                    PRIMARY KEY (league_id, manager_id, transfer_dt, player_id)
                )
            """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_manager_transfers_mgr_dt
                ON manager_transfers(manager_id, transfer_dt)
            """
            )

            # REH-22: drop legacy tables that no live code references.
            # `position_bidding_stats` was orphaned by REH-27 (writer + reader
            # methods deleted). The other three are stale residue from
            # deleted modules (factor_weight_learner, historical_tracker)
            # whose CREATE TABLE statements no longer exist in source. The
            # tables persist on Azure Blob Storage forever otherwise — once
            # SQLite creates a table, deleting the producing code doesn't
            # remove the table from the file. Idempotent: runs once on Azure,
            # becomes a no-op forever.
            for legacy_table in (
                "matchday_results",
                "recommendation_history",
                "factor_attribution",
                "position_bidding_stats",
            ):
                conn.execute(f"DROP TABLE IF EXISTS {legacy_table}")

            conn.commit()

    def record_outcome(self, outcome: AuctionOutcome):
        """Record an auction outcome for learning"""
        if outcome.timestamp is None:
            outcome.timestamp = datetime.now().timestamp()

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO auction_outcomes (
                    player_id, player_name, our_bid, asking_price, our_overbid_pct,
                    won, winning_bid, winning_overbid_pct, winner_user_id, timestamp,
                    player_value_score, market_value
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    outcome.player_id,
                    outcome.player_name,
                    outcome.our_bid,
                    outcome.asking_price,
                    outcome.our_overbid_pct,
                    1 if outcome.won else 0,
                    outcome.winning_bid,
                    outcome.winning_overbid_pct,
                    outcome.winner_user_id,
                    outcome.timestamp,
                    outcome.player_value_score,
                    outcome.market_value,
                ),
            )
            conn.commit()

    def record_flip(self, outcome: FlipOutcome) -> bool:
        """Record a completed flip for learning.

        Uses INSERT OR IGNORE so backfill reruns and accidental double-writes
        from the live trader collapse deterministically (idx_flip_unique on
        (player_id, buy_date)). Returns True if a row was actually inserted.
        """
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO flip_outcomes (
                    player_id, player_name, buy_price, sell_price, profit, profit_pct,
                    hold_days, buy_date, sell_date, trend_at_buy, average_points, position,
                    was_injured
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    outcome.player_id,
                    outcome.player_name,
                    outcome.buy_price,
                    outcome.sell_price,
                    outcome.profit,
                    outcome.profit_pct,
                    outcome.hold_days,
                    outcome.buy_date,
                    outcome.sell_date,
                    outcome.trend_at_buy,
                    outcome.average_points,
                    outcome.position,
                    1 if outcome.was_injured else 0,
                ),
            )
            conn.commit()
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Operational state: pending bids + tracked purchases
    #
    # These two table families used to live in JSON files (`pending_bids.json`,
    # `tracked_purchases.json`) which Azure wiped between runs. They live
    # here so they ride along with the existing `bid_learning.db` blob sync.
    # On lifecycle close (auction resolved / player sold) the row is deleted
    # and the historical outcome is appended to `auction_outcomes` /
    # `flip_outcomes` — those tables are the durable archive.
    # ------------------------------------------------------------------

    def add_pending_bid(
        self,
        *,
        player_id: str,
        player_name: str,
        our_bid: int,
        asking_price: int,
        our_overbid_pct: float,
        timestamp: float,
        market_value: int | None = None,
        player_value_score: float | None = None,
        sell_plan_player_ids: list[str] | None = None,
    ) -> None:
        """Record a freshly placed bid as pending (outcome TBD).

        Re-bidding on the same player overwrites the existing row — there's
        only ever one active auction per player from our side.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO pending_bids (
                    player_id, player_name, our_bid, asking_price,
                    our_overbid_pct, timestamp, market_value, player_value_score
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    player_id,
                    player_name,
                    our_bid,
                    asking_price,
                    our_overbid_pct,
                    timestamp,
                    market_value,
                    player_value_score,
                ),
            )
            # Replace sell-plan rows: an INSERT OR REPLACE on pending_bids
            # alone leaves stale join rows behind, so clear and reinsert.
            conn.execute(
                "DELETE FROM pending_bid_sell_plans WHERE pending_bid_player_id = ?",
                (player_id,),
            )
            if sell_plan_player_ids:
                conn.executemany(
                    """
                    INSERT INTO pending_bid_sell_plans (
                        pending_bid_player_id, sell_player_id
                    ) VALUES (?, ?)
                """,
                    [(player_id, sp) for sp in sell_plan_player_ids],
                )
            conn.commit()

    def get_pending_bids(self) -> list[dict[str, Any]]:
        """Return all pending bids, oldest first, with their sell plans inlined."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT player_id, player_name, our_bid, asking_price,
                       our_overbid_pct, timestamp, market_value, player_value_score
                FROM pending_bids
                ORDER BY timestamp ASC
            """
            ).fetchall()

            sell_plan_rows = conn.execute(
                """
                SELECT pending_bid_player_id, sell_player_id
                FROM pending_bid_sell_plans
            """
            ).fetchall()

        sell_plans: dict[str, list[str]] = {}
        for r in sell_plan_rows:
            sell_plans.setdefault(r["pending_bid_player_id"], []).append(r["sell_player_id"])

        return [
            {**dict(row), "sell_plan_player_ids": sell_plans.get(row["player_id"], [])}
            for row in rows
        ]

    def delete_pending_bid(self, player_id: str) -> None:
        """Remove the pending bid + its sell-plan rows. No-op if missing."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "DELETE FROM pending_bid_sell_plans WHERE pending_bid_player_id = ?",
                (player_id,),
            )
            conn.execute(
                "DELETE FROM pending_bids WHERE player_id = ?",
                (player_id,),
            )
            conn.commit()

    def add_tracked_purchase(
        self,
        *,
        player_id: str,
        player_name: str,
        buy_price: int,
        buy_date: float,
        source: str | None = None,
    ) -> None:
        """Record a player we now hold, with its cost basis.

        Re-buying overwrites the existing row — the latest cost basis
        wins so flip P&L always reflects the most recent purchase.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO tracked_purchases (
                    player_id, player_name, buy_price, buy_date, source
                )
                VALUES (?, ?, ?, ?, ?)
            """,
                (player_id, player_name, buy_price, buy_date, source),
            )
            conn.commit()

    def get_tracked_purchase(self, player_id: str) -> dict[str, Any] | None:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT player_id, player_name, buy_price, buy_date, source
                FROM tracked_purchases
                WHERE player_id = ?
            """,
                (player_id,),
            ).fetchone()
        return dict(row) if row else None

    def delete_tracked_purchase(self, player_id: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "DELETE FROM tracked_purchases WHERE player_id = ?",
                (player_id,),
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Wash-trade guard
    # ------------------------------------------------------------------

    def record_recent_sell(
        self,
        *,
        player_id: str,
        player_name: str,
        sold_price: int,
        sold_at: float,
        reason: str | None = None,
    ) -> None:
        """Remember that we just sold this player.

        Re-selling the same player overwrites the row — the latest sell
        timestamp is what the wash-trade check needs.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO recently_sold (
                    player_id, player_name, sold_price, sold_at, reason
                )
                VALUES (?, ?, ?, ?, ?)
            """,
                (player_id, player_name, sold_price, sold_at, reason),
            )
            conn.commit()

    def was_recently_sold(self, player_id: str, within_seconds: float) -> bool:
        """True iff we sold this player within the last *within_seconds*."""
        cutoff = datetime.now(tz=timezone.utc).timestamp() - within_seconds
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT sold_at FROM recently_sold WHERE player_id = ?",
                (player_id,),
            ).fetchone()
        return bool(row and row[0] >= cutoff)

    def get_recent_sell(self, player_id: str) -> dict[str, Any] | None:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT player_id, player_name, sold_price, sold_at, reason
                FROM recently_sold
                WHERE player_id = ?
            """,
                (player_id,),
            ).fetchone()
        return dict(row) if row else None

    def prune_recent_sells(self, older_than_seconds: float) -> int:
        """Drop wash-trade-guard rows older than the given age. Returns rows deleted."""
        cutoff = datetime.now(tz=timezone.utc).timestamp() - older_than_seconds
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("DELETE FROM recently_sold WHERE sold_at < ?", (cutoff,))
            conn.commit()
            return cur.rowcount

    def snapshot_predictions(self, rows: list[dict]) -> int:
        """Persist EP predictions for the current session.

        Each row should provide: player_id, league_id, predicted_at (unix
        seconds), predicted_ep, position, was_in_best_11, marginal_ep_gain.
        Reconciliation later joins these against actual matchday points to
        populate ``matchday_outcomes`` — without this snapshot, the scorer
        has nothing to self-calibrate against.

        Returns the number of rows inserted.
        """
        if not rows:
            return 0
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO predicted_eps (
                    player_id, league_id, predicted_at, predicted_ep,
                    position, was_in_best_11, marginal_ep_gain
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        r["player_id"],
                        r["league_id"],
                        r["predicted_at"],
                        r["predicted_ep"],
                        r["position"],
                        1 if r.get("was_in_best_11") else 0,
                        r.get("marginal_ep_gain"),
                    )
                    for r in rows
                ],
            )
            conn.commit()
        return len(rows)

    def get_latest_prediction_before(self, player_id: str, before_ts: float) -> dict | None:
        """Return the most recent prediction snapshot for *player_id* with
        ``predicted_at <= before_ts``, or None if none exists.

        Used by the matchday reconciliation path to find "what did we predict
        for this player before kickoff?".
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                """
                SELECT player_id, league_id, predicted_at, predicted_ep,
                       position, was_in_best_11, marginal_ep_gain
                FROM predicted_eps
                WHERE player_id = ? AND predicted_at <= ?
                ORDER BY predicted_at DESC
                LIMIT 1
                """,
                (player_id, before_ts),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def record_team_value_snapshot(
        self,
        league_id: str,
        team_value: int,
        budget: int,
        squad_size: int,
        snapshot_at: float | None = None,
    ) -> bool:
        """Persist one row of team_value_history for the current session.

        Returns True if a row was inserted, False if a row already existed at
        the same ``snapshot_at`` (theoretical collision when two sessions land
        in the same float second — the second is silently dropped via
        ``INSERT OR IGNORE``). Caller does not need to check the return value;
        it's exposed for tests.

        REH-23: feeds goal 3 (team value increases over time) and unblocks
        REH-37 (rank-trajectory regression).
        """
        ts = snapshot_at if snapshot_at is not None else datetime.now().timestamp()
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO team_value_history (
                    snapshot_at, league_id, team_value, budget, squad_size
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (ts, league_id, int(team_value), int(budget), int(squad_size)),
            )
            conn.commit()
            return cur.rowcount > 0

    def record_player_mv_snapshot(self, rows: list[dict]) -> int:
        """Bulk-persist one row per held player into ``player_mv_history``.

        Each row should provide: player_id, snapshot_at, market_value,
        peak_mv_30d, trough_mv_30d. Peak/trough are optional (the API may
        legitimately return an empty history for newly-listed players),
        coerced via ``_opt_int``.

        Uses ``INSERT OR IGNORE`` keyed on ``(player_id, snapshot_at)``;
        same-second retry on the same player is a no-op.

        REH-26: feeds goal 2 (loss avoidance) via daily drift detection
        and unblocks REH-33 (sell-timing peak-MV regret).
        """
        if not rows:
            return 0
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO player_mv_history (
                    player_id, snapshot_at, market_value,
                    peak_mv_30d, trough_mv_30d
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        r["player_id"],
                        r["snapshot_at"],
                        int(r["market_value"]),
                        _opt_int(r.get("peak_mv_30d")),
                        _opt_int(r.get("trough_mv_30d")),
                    )
                    for r in rows
                ],
            )
            conn.commit()
        return len(rows)

    def has_matchday_lineup_result(self, league_id: str, day_number: int) -> bool:
        """True if ``matchday_lineup_results`` already has a row for this
        (league_id, day_number). Used by the trader to skip the extra
        /teamcenter call once a matchday is captured."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                """
                SELECT 1 FROM matchday_lineup_results
                WHERE league_id = ? AND day_number = ?
                LIMIT 1
                """,
                (league_id, int(day_number)),
            )
            return cur.fetchone() is not None

    def record_matchday_lineup_result(
        self,
        league_id: str,
        day_number: int,
        matchday_date: str,
        total_points: int,
        lineup_player_ids: list[str],
        lineup_count: int,
        snapshot_at: float | None = None,
    ) -> bool:
        """Persist the bot's actual fielded lineup for one matchday.

        ``lineup_player_ids`` is stored as a JSON array in a TEXT column —
        analyses that need to join against player tables can json-decode.
        ``lineup_count`` (the API's ``clpc``) is normally 11; values < 11
        indicate empty slots and a -100 penalty applied by Kickbase.

        Uses ``INSERT OR IGNORE`` keyed on ``(league_id, day_number)`` so
        a session that runs twice on the same matchday is a no-op.

        Returns True on insert, False on collision (already recorded).
        """
        ts = snapshot_at if snapshot_at is not None else datetime.now().timestamp()
        ids_json = json.dumps([str(pid) for pid in lineup_player_ids])
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO matchday_lineup_results (
                    league_id, day_number, matchday_date, total_points,
                    lineup_player_ids, lineup_count, snapshot_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    league_id,
                    int(day_number),
                    matchday_date,
                    int(total_points),
                    ids_json,
                    int(lineup_count),
                    ts,
                ),
            )
            conn.commit()
            return cur.rowcount > 0

    def record_league_rank_snapshot(self, rows: list[dict]) -> int:
        """Bulk-persist one row per manager into ``league_rank_history``.

        Each row should provide:
            snapshot_at, league_id, manager_id, day_number, is_self
            rank_overall, rank_matchday, total_points, matchday_points,
            team_value
        Missing optional fields default to None — the schema accepts NULL on
        all the numeric columns so a partial response (e.g. mid-season the
        previous-week's matchday placement is None) doesn't blow up the row.

        Uses ``INSERT OR IGNORE`` so a same-second retry collapses
        deterministically rather than overwriting; PK is
        ``(snapshot_at, manager_id)``.

        REH-24: feeds goals 3 (team value growth across the league),
        4 (matchday points trajectory), and 5 (rank trajectory). Returns the
        number of rows attempted (NOT the number actually inserted, since
        sqlite3 doesn't expose per-row outcomes from executemany; tests
        verify behavior by reading back).
        """
        if not rows:
            return 0
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO league_rank_history (
                    snapshot_at, league_id, manager_id, day_number,
                    rank_overall, rank_matchday,
                    total_points, matchday_points,
                    team_value, is_self
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        r["snapshot_at"],
                        r["league_id"],
                        r["manager_id"],
                        int(r["day_number"]),
                        _opt_int(r.get("rank_overall")),
                        _opt_int(r.get("rank_matchday")),
                        _opt_int(r.get("total_points")),
                        _opt_int(r.get("matchday_points")),
                        _opt_int(r.get("team_value")),
                        1 if r.get("is_self") else 0,
                    )
                    for r in rows
                ],
            )
            conn.commit()
        return len(rows)

    def record_manager_profile_snapshot(self, rows: list[dict]) -> int:
        """Bulk-persist one row per manager into ``manager_profile_history``.

        Each row should provide:
            snapshot_at, league_id, manager_id, transfer_pnl, is_self
            matchday_wins (optional)

        ``transfer_pnl`` (the dashboard `prft` field) is required — it is
        the entire reason this snapshot exists. Missing values would defeat
        the purpose of the table, so callers must coerce or skip explicitly
        rather than passing None through.

        Uses ``INSERT OR IGNORE`` matching ``record_league_rank_snapshot``
        so a same-second retry collapses deterministically.

        REH-38: feeds goals 1 (flip revenue) and 2 (loss avoidance), and
        gives competitive intel on which leaguemates are bleeding transfer
        P&L. Returns the number of rows attempted.
        """
        if not rows:
            return 0
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO manager_profile_history (
                    snapshot_at, league_id, manager_id,
                    transfer_pnl, matchday_wins, is_self
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        r["snapshot_at"],
                        r["league_id"],
                        r["manager_id"],
                        int(r["transfer_pnl"]),
                        _opt_int(r.get("matchday_wins")),
                        1 if r.get("is_self") else 0,
                    )
                    for r in rows
                ],
            )
            conn.commit()
        return len(rows)

    def record_manager_transfers(self, rows: list[dict]) -> int:
        """Bulk-upsert per-trade rows into ``manager_transfers``.

        Each row should provide:
            league_id, manager_id, transfer_dt, player_id, player_name
            transfer_type (optional), transfer_price (optional)

        Uses ``INSERT OR IGNORE`` so re-importing the same transfer history
        page doesn't error or duplicate rows. The PK
        ``(league_id, manager_id, transfer_dt, player_id)`` collapses
        duplicates from overlapping pages during backfill.

        REH-38. Returns the number of rows attempted.
        """
        if not rows:
            return 0
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO manager_transfers (
                    league_id, manager_id, transfer_dt,
                    player_id, player_name,
                    transfer_type, transfer_price
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        r["league_id"],
                        r["manager_id"],
                        r["transfer_dt"],
                        r["player_id"],
                        r["player_name"],
                        _opt_int(r.get("transfer_type")),
                        _opt_int(r.get("transfer_price")),
                    )
                    for r in rows
                ],
            )
            conn.commit()
        return len(rows)

    def has_matchday_outcome(self, player_id: str, matchday_date: str) -> bool:
        """True if ``matchday_outcomes`` already has a row for this player+date."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                """
                SELECT 1 FROM matchday_outcomes
                WHERE player_id = ? AND matchday_date = ?
                LIMIT 1
                """,
                (player_id, matchday_date),
            )
            return cur.fetchone() is not None

    def record_matchday_outcome(
        self,
        player_id: str,
        player_position: str,
        matchday_date: str,
        predicted_ep: float,
        actual_points: float,
        was_in_best_11: bool = False,
        opponent_strength: str | None = None,
        purchase_price: int | None = None,
        marginal_ep_gain_at_purchase: float | None = None,
    ) -> None:
        """Record actual matchday points vs predicted EP.

        Uses INSERT OR REPLACE so duplicate (player_id, matchday_date) rows are
        silently overwritten rather than raising an error.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO matchday_outcomes (
                    player_id, player_position, matchday_date,
                    predicted_ep, actual_points, was_in_best_11,
                    opponent_strength, purchase_price, marginal_ep_gain_at_purchase
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    player_id,
                    player_position,
                    matchday_date,
                    predicted_ep,
                    actual_points,
                    1 if was_in_best_11 else 0,
                    opponent_strength,
                    purchase_price,
                    marginal_ep_gain_at_purchase,
                ),
            )
            conn.commit()

    # Half-life (days) for exponential decay of historical EP accuracy data.
    # Recent predictions count more — a 60-day half-life means 2-month-old
    # predictions are weighted half as much as today's, and 4-month-old ones
    # a quarter. Tuned for fantasy football's seasonal form cycles.
    EP_ACCURACY_HALF_LIFE_DAYS = 60.0

    # Minimum matchdays per position before the scorer calibration multiplier
    # is trusted. Below this we return 1.0 (uncalibrated) so new seasons don't
    # start with wildly swung scores from noisy small samples.
    POSITION_CALIBRATION_MIN_MATCHDAYS = 10

    def get_position_calibration_multiplier(self, position: str) -> float:
        """Time-decayed actual/predicted EP ratio for a position, [0.5, 1.5].

        Used by the scorer to correct systematic over/under-prediction at a
        position level. E.g. if defenders consistently score 20% more than
        we predict, this returns ~1.2 and the scorer boosts defender EPs.

        Wider clamp than :meth:`get_ep_accuracy_factor` (which only dampens
        bids) because here we want to raise scores when under-predicting, not
        just lower them. Reuses the 60-day half-life decay so recent
        matchdays dominate.

        Returns 1.0 (uncalibrated) when there are fewer than
        :attr:`POSITION_CALIBRATION_MIN_MATCHDAYS` records, or when total
        weighted predicted EP is zero.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT predicted_ep, actual_points, timestamp
                FROM matchday_outcomes
                WHERE player_position = ? AND predicted_ep > 0
                """,
                (position,),
            )
            rows = cursor.fetchall()

        if len(rows) < self.POSITION_CALIBRATION_MIN_MATCHDAYS:
            return 1.0

        now_ts = datetime.now(tz=timezone.utc).timestamp()
        half_life_seconds = self.EP_ACCURACY_HALF_LIFE_DAYS * 86_400

        weighted_actual = 0.0
        weighted_predicted = 0.0
        for predicted_ep, actual_points, ts in rows:
            age_seconds = self._age_seconds(ts, now_ts)
            weight = 0.5 ** (age_seconds / half_life_seconds)
            weighted_actual += weight * actual_points
            weighted_predicted += weight * predicted_ep

        if weighted_predicted <= 0:
            return 1.0

        raw_factor = weighted_actual / weighted_predicted
        return max(0.5, min(1.5, raw_factor))

    def get_ep_accuracy_factor(
        self,
        player_id: str | None = None,
        position: str | None = None,
        min_matchdays: int = 3,
    ) -> float:
        """Return EP accuracy multiplier clamped to [0.5, 1.0].

        Uses time-decayed weights — recent matchdays count more than old ones
        (60-day half-life). Tries player-specific accuracy first, falls back
        to position-level when the player has fewer than *min_matchdays*
        recorded games. Returns 1.0 when data is insufficient at both levels.
        """
        # 1. Try player-specific accuracy
        if player_id is not None:
            factor = self._decayed_accuracy(
                filter_col="player_id",
                filter_val=player_id,
                min_matchdays=min_matchdays,
            )
            if factor is not None:
                return factor

        # 2. Fall back to position-level accuracy
        if position is not None:
            factor = self._decayed_accuracy(
                filter_col="player_position",
                filter_val=position,
                min_matchdays=min_matchdays,
            )
            if factor is not None:
                return factor

        # 3. Insufficient data — return neutral multiplier
        return 1.0

    def _decayed_accuracy(
        self,
        filter_col: str,
        filter_val: str,
        min_matchdays: int,
    ) -> float | None:
        """Compute time-decayed EP accuracy for a filter (player_id or position).

        Weights each matchday by ``0.5 ** (age_days / half_life)`` and returns
        ``weighted_actual / weighted_predicted`` clamped to [0.5, 1.0]. Returns
        None when there are fewer than *min_matchdays* qualifying records.

        Only ``filter_col`` values listed above are accepted — the column name
        is interpolated directly into the SQL, so callers must not pass
        user-controlled input. The validation is a ValueError (not assert)
        so it survives Python's -O optimization mode.
        """
        if filter_col not in ("player_id", "player_position"):
            raise ValueError(f"invalid filter_col: {filter_col}")

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                f"""
                SELECT predicted_ep, actual_points, timestamp
                FROM matchday_outcomes
                WHERE {filter_col} = ? AND predicted_ep > 0
                """,
                (filter_val,),
            )
            rows = cursor.fetchall()

        if len(rows) < min_matchdays:
            return None

        now_ts = datetime.now(tz=timezone.utc).timestamp()
        half_life_seconds = self.EP_ACCURACY_HALF_LIFE_DAYS * 86_400

        weighted_actual = 0.0
        weighted_predicted = 0.0
        for predicted_ep, actual_points, ts in rows:
            age_seconds = self._age_seconds(ts, now_ts)
            weight = 0.5 ** (age_seconds / half_life_seconds)
            weighted_actual += weight * actual_points
            weighted_predicted += weight * predicted_ep

        if weighted_predicted <= 0:
            return None

        raw_factor = weighted_actual / weighted_predicted
        return max(0.5, min(1.0, raw_factor))

    @staticmethod
    def _age_seconds(timestamp_str: str | None, now_ts: float) -> float:
        """Age in seconds of a matchday_outcomes.timestamp value.

        SQLite's CURRENT_TIMESTAMP stores UTC as text ("YYYY-MM-DD HH:MM:SS").
        We parse it as UTC-aware so the arithmetic against *now_ts* (also UTC)
        is correct. Unparseable or missing timestamps return age=0 so they're
        treated as fresh rather than discarded.
        """
        if not timestamp_str:
            return 0.0
        try:
            dt = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            return max(0.0, now_ts - dt.timestamp())
        except (ValueError, TypeError):
            return 0.0

    def _get_won_player_outcome_quality(self) -> float:
        """How well did won-auction players perform on matchdays?

        Joins *auction_outcomes* (won=1) with *matchday_outcomes* and computes
        AVG(actual_points) / AVG(predicted_ep).  Returns a quality multiplier
        clamped to [0.5, 1.2]; defaults to 1.0 when data is insufficient.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT COUNT(*), AVG(mo.actual_points), AVG(mo.predicted_ep)
                FROM matchday_outcomes mo
                INNER JOIN auction_outcomes ao ON ao.player_id = mo.player_id
                WHERE ao.won = 1 AND mo.predicted_ep > 0
                """
            )
            row = cursor.fetchone()

        if not row:
            return 1.0

        count, avg_actual, avg_predicted = row
        if not count or count < 3 or not avg_predicted or avg_predicted <= 0:
            return 1.0

        raw_quality = avg_actual / avg_predicted
        return max(0.5, min(1.2, raw_quality))

    def get_ep_recommended_overbid(
        self,
        asking_price: int,
        marginal_ep_gain: float,
        market_value: int,
        budget_ceiling: int,
    ) -> dict[str, Any]:
        """EP-aware overbid recommendation.

        Uses historical win rate from *auction_outcomes* and the outcome quality
        from *_get_won_player_outcome_quality()* to calibrate aggressiveness.
        The *marginal_ep_gain* (expected points gained by buying this player vs
        the next-best alternative) sets a floor: higher EP gain justifies
        paying more.

        Returns a dict with ``recommended_overbid_pct`` (float) and ``reason``
        (str).  The overbid is constrained so the final bid never exceeds
        *budget_ceiling*.
        """
        # Maximum overbid allowed by budget
        if asking_price <= 0:
            return {"recommended_overbid_pct": 0.0, "reason": "Invalid asking price"}

        max_extra = budget_ceiling - asking_price
        if max_extra <= 0:
            return {
                "recommended_overbid_pct": 0.0,
                "reason": "Budget ceiling at or below asking price",
            }
        max_overbid_pct = (max_extra / asking_price) * 100.0

        # EP-gain-based minimum floor (each EP point of marginal gain is worth ~0.3% extra)
        ep_floor_pct = marginal_ep_gain * 0.3

        # Win-rate adjustment from historical auction data
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT COUNT(*), SUM(CASE WHEN won = 1 THEN 1 ELSE 0 END)
                FROM auction_outcomes
                WHERE timestamp > ?
                """,
                (datetime.now().timestamp() - (90 * 24 * 3600),),
            )
            row = cursor.fetchone()

        total_auctions, total_wins = row if row else (0, 0)
        total_auctions = total_auctions or 0
        total_wins = total_wins or 0

        if total_auctions >= 5:
            win_rate = total_wins / total_auctions
            if win_rate < 0.30:
                win_rate_adj = 1.20  # Losing too much — be more aggressive
                rate_reason = f"low win rate ({win_rate:.0%})"
            elif win_rate > 0.70:
                win_rate_adj = 0.90  # Winning easily — can dial back slightly
                rate_reason = f"high win rate ({win_rate:.0%})"
            else:
                win_rate_adj = 1.0
                rate_reason = f"balanced win rate ({win_rate:.0%})"
        else:
            win_rate_adj = 1.0
            rate_reason = "insufficient auction history"

        # Outcome quality adjustment
        outcome_quality = self._get_won_player_outcome_quality()

        # Base overbid: use ep floor as the starting point, then scale
        base_overbid = max(ep_floor_pct, 8.0)  # minimum sensible overbid
        recommended = base_overbid * win_rate_adj * outcome_quality

        # Cap to budget ceiling
        recommended = min(recommended, max_overbid_pct)
        recommended = max(0.0, recommended)

        reason = (
            f"EP-gain={marginal_ep_gain:.1f}pts → floor {ep_floor_pct:.1f}%"
            f" | {rate_reason}"
            f" | outcome quality {outcome_quality:.2f}"
        )

        return {
            "recommended_overbid_pct": round(recommended, 1),
            "reason": reason,
        }
