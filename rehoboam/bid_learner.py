"""Learn from auction outcomes to improve bidding strategy"""

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

            # Position-specific bidding statistics
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS position_bidding_stats (
                    position TEXT PRIMARY KEY,
                    avg_overbid_pct REAL,
                    recommended_overbid_pct REAL,
                    win_rate REAL,
                    avg_value_score REAL,
                    avg_profit_pct REAL,
                    total_auctions INTEGER DEFAULT 0,
                    successful_auctions INTEGER DEFAULT 0,
                    last_updated TEXT
                )
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

    def record_flip(self, outcome: FlipOutcome):
        """Record a completed flip for learning"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO flip_outcomes (
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

    def get_recommended_overbid(
        self,
        asking_price: int,
        value_score: float,
        market_value: int,
        predicted_future_value: int | None = None,
    ) -> dict[str, Any]:
        """
        Get recommended overbid based on learning from past auctions.
        NEVER recommends bidding above predicted future value.

        Args:
            asking_price: Player's asking price
            value_score: Our calculated value score (0-100)
            market_value: Player's market value
            predicted_future_value: Maximum we should pay (value ceiling)

        Returns:
            dict with recommended_overbid_pct, confidence, reason, max_bid
        """
        # Calculate value ceiling if not provided
        if predicted_future_value is None:
            # Estimate based on value score and market value
            # High value score = expect 10-20% growth
            growth_factor = 1.0 + (value_score / 1000)  # 60 score = 6% growth
            predicted_future_value = int(market_value * growth_factor)

        # Calculate absolute maximum overbid allowed by value ceiling
        if predicted_future_value <= asking_price:
            # Predicted value is below asking price - don't bid
            return {
                "recommended_overbid_pct": 0.0,
                "confidence": "high",
                "reason": f"Predicted value (€{predicted_future_value:,}) below asking price - SKIP",
                "based_on_auctions": 0,
                "max_bid": predicted_future_value,
            }

        max_overbid_amount = predicted_future_value - asking_price
        max_overbid_pct = (max_overbid_amount / asking_price) * 100
        with sqlite3.connect(self.db_path) as conn:
            # Get recent auction outcomes (extended to 90 days for more data)
            cursor = conn.execute(
                """
                SELECT our_overbid_pct, won, winning_overbid_pct
                FROM auction_outcomes
                WHERE timestamp > ?
                ORDER BY timestamp DESC
                LIMIT 100
            """,
                (datetime.now().timestamp() - (90 * 24 * 3600),),
            )  # Last 90 days (extended from 30)

            outcomes = cursor.fetchall()

            if not outcomes or len(outcomes) < 5:
                # Conservative default: 10%, but never exceed value ceiling
                # Increased from 8% as historical data shows low bids lose
                default_overbid = min(10.0, max_overbid_pct)
                return {
                    "recommended_overbid_pct": default_overbid,
                    "confidence": "low",
                    "reason": f"Insufficient data - using {default_overbid:.1f}% (max: {max_overbid_pct:.1f}%)",
                    "based_on_auctions": len(outcomes),
                    "max_bid": predicted_future_value,
                    "value_ceiling_applied": default_overbid < max_overbid_pct,
                }

            # Analyze patterns
            wins = [o for o in outcomes if o[1] == 1]
            losses = [o for o in outcomes if o[1] == 0]

            if not wins:
                # We never won - need to bid higher, but respect ceiling
                if losses:
                    avg_losing_overbid = sum(o[0] for o in losses) / len(losses)
                    recommended = min(avg_losing_overbid + 5.0, max_overbid_pct)
                    ceiling_applied = (avg_losing_overbid + 5.0) > max_overbid_pct
                    return {
                        "recommended_overbid_pct": recommended,
                        "confidence": "medium",
                        "reason": f"Lost all {len(losses)} recent auctions - bidding higher (max: {max_overbid_pct:.1f}%)",
                        "based_on_auctions": len(outcomes),
                        "max_bid": predicted_future_value,
                        "value_ceiling_applied": ceiling_applied,
                    }
                else:
                    recommended = min(10.0, max_overbid_pct)
                    return {
                        "recommended_overbid_pct": recommended,
                        "confidence": "low",
                        "reason": f"No wins yet - trying {recommended:.1f}% overbid (max: {max_overbid_pct:.1f}%)",
                        "based_on_auctions": len(outcomes),
                        "max_bid": predicted_future_value,
                        "value_ceiling_applied": recommended < 10.0,
                    }

            # Calculate winning overbid stats
            winning_overbids = [o[0] for o in wins]
            avg_winning_overbid = sum(winning_overbids) / len(winning_overbids)
            losing_overbids = [o[0] for o in losses]
            avg_losing_overbid = sum(losing_overbids) / len(losing_overbids) if losses else 0

            # Calculate win rate for adaptive learning
            win_rate = len(wins) / len(outcomes) * 100

            # If we have data about what others bid
            competitor_overbids = [o[2] for o in losses if o[2] is not None]

            if competitor_overbids:
                # We know what beats us
                avg_competitor_overbid = sum(competitor_overbids) / len(competitor_overbids)
                # Recommend above average competitor (more aggressive)
                recommended = avg_competitor_overbid + 3.0
            else:
                # Use our own winning pattern as base
                recommended = avg_winning_overbid

            # ADAPTIVE LEARNING: Adjust based on win rate
            # If win rate is too low, we need to bid more aggressively
            if win_rate < 30:
                # Losing too much - increase bid significantly
                # Add the gap between losing and winning bids
                bid_gap = max(avg_losing_overbid - avg_winning_overbid, 0) + 5.0
                recommended = max(recommended, avg_losing_overbid + bid_gap)
            elif win_rate < 50:
                # Below 50% - increase moderately
                recommended = max(recommended, avg_losing_overbid + 3.0)
            elif win_rate > 80:
                # Winning too much - might be overpaying, can reduce slightly
                recommended = recommended * 0.9

            # Adjust for value score (before applying ceiling)
            if value_score >= 80:
                # High value - bid aggressively
                recommended = max(recommended, 12.0)
            elif value_score >= 60:
                # Good value - ensure competitive
                recommended = max(recommended, 10.0)
            elif value_score < 50:
                # Low value - bid conservatively
                recommended = min(recommended, 8.0)

            # Cap at reasonable operational limits (increased max to 25%)
            recommended = max(8.0, min(recommended, 25.0))

            # CRITICAL: Apply value ceiling (never exceed predicted value)
            original_recommended = recommended
            recommended = min(recommended, max_overbid_pct)
            ceiling_applied = original_recommended > recommended

            win_rate = len(wins) / len(outcomes) * 100

            reason = f"Based on {len(outcomes)} auctions ({win_rate:.0f}% win rate)"
            if ceiling_applied:
                reason += f" | Value ceiling applied (max: {max_overbid_pct:.1f}%)"

            return {
                "recommended_overbid_pct": round(recommended, 1),
                "confidence": "high" if len(outcomes) >= 20 else "medium",
                "reason": reason,
                "based_on_auctions": len(outcomes),
                "win_rate": round(win_rate, 1),
                "avg_winning_overbid": round(avg_winning_overbid, 1) if wins else None,
                "avg_competitor_overbid": (
                    round(avg_competitor_overbid, 1) if competitor_overbids else None
                ),
                "max_bid": predicted_future_value,
                "value_ceiling_applied": ceiling_applied,
            }

    def get_statistics(self) -> dict[str, Any]:
        """Get overall learning statistics"""
        with sqlite3.connect(self.db_path) as conn:
            # Total auctions
            cursor = conn.execute("SELECT COUNT(*) FROM auction_outcomes")
            total = cursor.fetchone()[0]

            if total == 0:
                return {"total_auctions": 0, "wins": 0, "losses": 0, "win_rate": 0.0}

            # Wins/losses
            cursor = conn.execute("SELECT COUNT(*) FROM auction_outcomes WHERE won = 1")
            wins = cursor.fetchone()[0]

            cursor = conn.execute("SELECT COUNT(*) FROM auction_outcomes WHERE won = 0")
            losses = cursor.fetchone()[0]

            # Average overbid
            cursor = conn.execute("SELECT AVG(our_overbid_pct) FROM auction_outcomes WHERE won = 1")
            avg_winning_overbid = cursor.fetchone()[0] or 0

            cursor = conn.execute("SELECT AVG(our_overbid_pct) FROM auction_outcomes WHERE won = 0")
            avg_losing_overbid = cursor.fetchone()[0] or 0

            # Value score correlation
            cursor = conn.execute(
                """
                SELECT AVG(player_value_score) FROM auction_outcomes
                WHERE won = 1 AND player_value_score IS NOT NULL
            """
            )
            avg_value_score_wins = cursor.fetchone()[0] or 0

            cursor = conn.execute(
                """
                SELECT AVG(player_value_score) FROM auction_outcomes
                WHERE won = 0 AND player_value_score IS NOT NULL
            """
            )
            avg_value_score_losses = cursor.fetchone()[0] or 0

            return {
                "total_auctions": total,
                "wins": wins,
                "losses": losses,
                "win_rate": round((wins / total * 100) if total > 0 else 0, 1),
                "avg_winning_overbid": round(avg_winning_overbid, 1),
                "avg_losing_overbid": round(avg_losing_overbid, 1),
                "avg_value_score_wins": round(avg_value_score_wins, 1),
                "avg_value_score_losses": round(avg_value_score_losses, 1),
            }

    def analyze_competitor(self, user_id: str) -> dict[str, Any]:
        """Analyze a specific competitor's bidding patterns"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT COUNT(*), AVG(winning_overbid_pct), MIN(winning_overbid_pct), MAX(winning_overbid_pct)
                FROM auction_outcomes
                WHERE winner_user_id = ? AND winning_overbid_pct IS NOT NULL
            """,
                (user_id,),
            )

            result = cursor.fetchone()
            count, avg_overbid, min_overbid, max_overbid = result

            if count == 0 or count is None:
                return {
                    "user_id": user_id,
                    "times_beaten_us": 0,
                    "message": "No data on this competitor",
                }

            return {
                "user_id": user_id,
                "times_beaten_us": count,
                "avg_overbid": round(avg_overbid, 1) if avg_overbid else None,
                "min_overbid": round(min_overbid, 1) if min_overbid else None,
                "max_overbid": round(max_overbid, 1) if max_overbid else None,
                "message": (
                    f"This user typically overbids {avg_overbid:.1f}%"
                    if avg_overbid
                    else "Unknown pattern"
                ),
            }

    def track_outcome_validation(self, player_id: str, current_market_value: int) -> dict[str, Any]:
        """
        Track whether past auction outcomes were good predictions

        Args:
            player_id: Player to check
            current_market_value: Current market value of player

        Returns:
            dict with validation info (was winner right to pay that much?)
        """
        with sqlite3.connect(self.db_path) as conn:
            # Get our lost auctions for this player
            cursor = conn.execute(
                """
                SELECT our_bid, winning_bid, timestamp, asking_price, market_value
                FROM auction_outcomes
                WHERE player_id = ? AND won = 0 AND winning_bid IS NOT NULL
                ORDER BY timestamp DESC
                LIMIT 1
            """,
                (player_id,),
            )

            result = cursor.fetchone()

            if not result:
                return {
                    "player_id": player_id,
                    "has_data": False,
                    "message": "No past auction data for this player",
                }

            our_bid, winning_bid, timestamp, asking_price, old_market_value = result

            # Calculate value change since auction
            if old_market_value and old_market_value > 0:
                value_change_pct = (
                    (current_market_value - old_market_value) / old_market_value
                ) * 100
            else:
                value_change_pct = 0.0

            # Did winner overpay or get a good deal?
            winner_profit_pct = (
                ((current_market_value - winning_bid) / winning_bid) * 100 if winning_bid > 0 else 0
            )

            # We would have paid vs now
            our_profit_pct = (
                ((current_market_value - our_bid) / our_bid) * 100 if our_bid > 0 else 0
            )

            # Determine who was right
            if current_market_value >= winning_bid:
                assessment = "Winner got a good deal - our prediction was too conservative"
            elif current_market_value >= our_bid:
                assessment = "Both bids would have been profitable - we should have bid more"
            else:
                assessment = "Winner overpaid - we were right to skip"

            return {
                "player_id": player_id,
                "has_data": True,
                "our_bid": our_bid,
                "winning_bid": winning_bid,
                "auction_market_value": old_market_value,
                "current_market_value": current_market_value,
                "value_change_pct": round(value_change_pct, 1),
                "winner_profit_pct": round(winner_profit_pct, 1),
                "our_potential_profit_pct": round(our_profit_pct, 1),
                "assessment": assessment,
                "days_since_auction": int((datetime.now().timestamp() - timestamp) / (24 * 3600)),
            }

    def get_flip_statistics(self) -> dict[str, Any]:
        """Get statistics about completed flips for learning"""
        with sqlite3.connect(self.db_path) as conn:
            # Total flips
            cursor = conn.execute("SELECT COUNT(*) FROM flip_outcomes")
            total = cursor.fetchone()[0]

            if total == 0:
                return {
                    "total_flips": 0,
                    "profitable_flips": 0,
                    "unprofitable_flips": 0,
                    "success_rate": 0.0,
                }

            # Profitable vs unprofitable
            cursor = conn.execute("SELECT COUNT(*) FROM flip_outcomes WHERE profit > 0")
            profitable = cursor.fetchone()[0]

            cursor = conn.execute("SELECT COUNT(*) FROM flip_outcomes WHERE profit <= 0")
            unprofitable = cursor.fetchone()[0]

            # Average profit
            cursor = conn.execute("SELECT AVG(profit_pct) FROM flip_outcomes WHERE profit > 0")
            avg_profit_pct = cursor.fetchone()[0] or 0

            cursor = conn.execute("SELECT AVG(profit_pct) FROM flip_outcomes WHERE profit <= 0")
            avg_loss_pct = cursor.fetchone()[0] or 0

            # Average hold time
            cursor = conn.execute("SELECT AVG(hold_days) FROM flip_outcomes WHERE profit > 0")
            avg_hold_days_profit = cursor.fetchone()[0] or 0

            cursor = conn.execute("SELECT AVG(hold_days) FROM flip_outcomes WHERE profit <= 0")
            avg_hold_days_loss = cursor.fetchone()[0] or 0

            # Best flip
            cursor = conn.execute(
                """
                SELECT player_name, profit, profit_pct, hold_days
                FROM flip_outcomes
                ORDER BY profit_pct DESC
                LIMIT 1
            """
            )
            best_flip = cursor.fetchone()

            # Worst flip
            cursor = conn.execute(
                """
                SELECT player_name, profit, profit_pct, hold_days
                FROM flip_outcomes
                ORDER BY profit_pct ASC
                LIMIT 1
            """
            )
            worst_flip = cursor.fetchone()

            # Total profit
            cursor = conn.execute("SELECT SUM(profit) FROM flip_outcomes")
            total_profit = cursor.fetchone()[0] or 0

            return {
                "total_flips": total,
                "profitable_flips": profitable,
                "unprofitable_flips": unprofitable,
                "success_rate": round((profitable / total * 100) if total > 0 else 0, 1),
                "avg_profit_pct": round(avg_profit_pct, 1),
                "avg_loss_pct": round(avg_loss_pct, 1),
                "avg_hold_days_profit": round(avg_hold_days_profit, 1),
                "avg_hold_days_loss": round(avg_hold_days_loss, 1),
                "total_profit": total_profit,
                "best_flip": (
                    {
                        "player": best_flip[0],
                        "profit": best_flip[1],
                        "profit_pct": round(best_flip[2], 1),
                        "hold_days": best_flip[3],
                    }
                    if best_flip
                    else None
                ),
                "worst_flip": (
                    {
                        "player": worst_flip[0],
                        "profit": worst_flip[1],
                        "profit_pct": round(worst_flip[2], 1),
                        "hold_days": worst_flip[3],
                    }
                    if worst_flip
                    else None
                ),
            }

    def analyze_flip_patterns(self) -> dict[str, Any]:
        """Analyze patterns in flip outcomes to learn what works"""
        with sqlite3.connect(self.db_path) as conn:
            # Trend analysis
            cursor = conn.execute(
                """
                SELECT trend_at_buy, COUNT(*), AVG(profit_pct), SUM(CASE WHEN profit > 0 THEN 1 ELSE 0 END)
                FROM flip_outcomes
                WHERE trend_at_buy IS NOT NULL
                GROUP BY trend_at_buy
            """
            )

            trend_results = {}
            for trend, count, avg_profit, wins in cursor.fetchall():
                trend_results[trend] = {
                    "count": count,
                    "avg_profit_pct": round(avg_profit, 1) if avg_profit else 0,
                    "success_rate": round((wins / count * 100) if count > 0 else 0, 1),
                }

            # Position analysis
            cursor = conn.execute(
                """
                SELECT position, COUNT(*), AVG(profit_pct), SUM(CASE WHEN profit > 0 THEN 1 ELSE 0 END)
                FROM flip_outcomes
                WHERE position IS NOT NULL
                GROUP BY position
            """
            )

            position_results = {}
            for position, count, avg_profit, wins in cursor.fetchall():
                position_results[position] = {
                    "count": count,
                    "avg_profit_pct": round(avg_profit, 1) if avg_profit else 0,
                    "success_rate": round((wins / count * 100) if count > 0 else 0, 1),
                }

            # Hold time analysis (group by days)
            cursor = conn.execute(
                """
                SELECT
                    CASE
                        WHEN hold_days <= 1 THEN '0-1 days'
                        WHEN hold_days <= 3 THEN '2-3 days'
                        WHEN hold_days <= 7 THEN '4-7 days'
                        ELSE '8+ days'
                    END as hold_period,
                    COUNT(*),
                    AVG(profit_pct),
                    SUM(CASE WHEN profit > 0 THEN 1 ELSE 0 END)
                FROM flip_outcomes
                GROUP BY hold_period
            """
            )

            hold_time_results = {}
            for period, count, avg_profit, wins in cursor.fetchall():
                hold_time_results[period] = {
                    "count": count,
                    "avg_profit_pct": round(avg_profit, 1) if avg_profit else 0,
                    "success_rate": round((wins / count * 100) if count > 0 else 0, 1),
                }

            # Injury impact
            cursor = conn.execute(
                """
                SELECT was_injured, COUNT(*), AVG(profit_pct), SUM(CASE WHEN profit > 0 THEN 1 ELSE 0 END)
                FROM flip_outcomes
                GROUP BY was_injured
            """
            )

            injury_results = {}
            for was_injured, count, avg_profit, wins in cursor.fetchall():
                injury_results["injured" if was_injured else "healthy"] = {
                    "count": count,
                    "avg_profit_pct": round(avg_profit, 1) if avg_profit else 0,
                    "success_rate": round((wins / count * 100) if count > 0 else 0, 1),
                }

            return {
                "by_trend": trend_results,
                "by_position": position_results,
                "by_hold_time": hold_time_results,
                "by_injury_status": injury_results,
            }

    def get_learning_recommendations(self) -> list[str]:
        """Get actionable recommendations based on learning data"""
        recommendations = []

        patterns = self.analyze_flip_patterns()

        # Trend recommendations
        if "by_trend" in patterns and patterns["by_trend"]:
            trends = patterns["by_trend"]
            if "rising" in trends and "falling" in trends:
                rising_success = trends["rising"]["success_rate"]
                falling_success = trends["falling"]["success_rate"]

                if rising_success > falling_success + 20:
                    recommendations.append(
                        f"Focus on rising trend players (success rate: {rising_success}% vs {falling_success}% for falling)"
                    )
                elif falling_success > rising_success + 20:
                    recommendations.append(
                        f"Mean reversion strategy working well on falling players ({falling_success}% success rate)"
                    )

        # Position recommendations
        if "by_position" in patterns and patterns["by_position"]:
            positions = patterns["by_position"]
            sorted_positions = sorted(
                positions.items(), key=lambda x: x[1]["avg_profit_pct"], reverse=True
            )

            if sorted_positions:
                best_pos, best_stats = sorted_positions[0]
                if best_stats["count"] >= 3:  # Need at least 3 samples
                    recommendations.append(
                        f"Best position: {best_pos} ({best_stats['avg_profit_pct']}% avg profit, {best_stats['success_rate']}% success)"
                    )

        # Hold time recommendations
        if "by_hold_time" in patterns and patterns["by_hold_time"]:
            hold_times = patterns["by_hold_time"]
            sorted_holds = sorted(
                hold_times.items(), key=lambda x: x[1]["avg_profit_pct"], reverse=True
            )

            if sorted_holds:
                best_hold, best_stats = sorted_holds[0]
                if best_stats["count"] >= 3:
                    recommendations.append(
                        f"Optimal hold time: {best_hold} ({best_stats['avg_profit_pct']}% avg profit)"
                    )

        # Injury recommendations
        if "by_injury_status" in patterns and patterns["by_injury_status"]:
            injury = patterns["by_injury_status"]
            if "healthy" in injury and "injured" in injury:
                healthy_success = injury["healthy"]["success_rate"]
                injured_success = injury["injured"]["success_rate"]

                if healthy_success > injured_success + 15:
                    recommendations.append(
                        f"Avoid injured players (healthy success: {healthy_success}% vs injured: {injured_success}%)"
                    )

        if not recommendations:
            recommendations.append("Not enough data yet - keep trading to build learning database!")

        return recommendations

    def get_position_specific_overbid(
        self,
        position: str,
        asking_price: int,
        value_score: float,
        market_value: int,
        predicted_future_value: int | None = None,
    ) -> dict[str, Any]:
        """
        Get recommended overbid adjusted for position-specific patterns.

        Different positions have different competition levels:
        - Forward: High competition (premium strikers)
        - Midfielder: Medium-high competition
        - Defender: Medium competition
        - Goalkeeper: Low competition

        Args:
            position: Player position (Goalkeeper/Defender/Midfielder/Forward)
            asking_price: Player's asking price
            value_score: Our calculated value score
            market_value: Player's market value
            predicted_future_value: Maximum we should pay

        Returns:
            dict with recommended_overbid_pct and metadata
        """
        # Get base recommendation
        base_rec = self.get_recommended_overbid(
            asking_price, value_score, market_value, predicted_future_value
        )

        # Map position to short code
        position_map = {
            "Goalkeeper": "GK",
            "Defender": "DEF",
            "Midfielder": "MID",
            "Forward": "FWD",
        }
        position_code = position_map.get(position, position)

        # Get position-specific statistics
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT avg_overbid_pct, win_rate, total_auctions, recommended_overbid_pct
                FROM position_bidding_stats
                WHERE position = ?
                """,
                (position_code,),
            )
            result = cursor.fetchone()

        if not result or result[2] < 10:  # Less than 10 auctions for position
            # Not enough data - use position multipliers
            position_multipliers = {
                "FWD": 1.15,  # 15% more aggressive
                "MID": 1.08,  # 8% more aggressive
                "DEF": 1.0,  # Neutral
                "GK": 0.9,  # 10% less aggressive
            }

            multiplier = position_multipliers.get(position_code, 1.0)
            adjusted_overbid = base_rec["recommended_overbid_pct"] * multiplier

            base_rec["recommended_overbid_pct"] = round(adjusted_overbid, 1)
            base_rec["position_adjusted"] = True
            base_rec["reason"] += f" | Position: {position_code} (multiplier: {multiplier:.2f}x)"

            return base_rec

        avg_pos_overbid, pos_win_rate, total_auctions, recommended_pos_overbid = result

        # Adjust based on position win rate
        if pos_win_rate and pos_win_rate < 40:  # Winning <40% - bid higher
            adjustment = 1.2
            reason_adj = "low win rate"
        elif pos_win_rate and pos_win_rate > 60:  # Winning >60% - can bid less
            adjustment = 0.9
            reason_adj = "high win rate"
        else:
            adjustment = 1.0
            reason_adj = "balanced"

        adjusted_overbid = base_rec["recommended_overbid_pct"] * adjustment

        # Apply value ceiling if provided
        if predicted_future_value:
            max_overbid_pct = ((predicted_future_value - asking_price) / asking_price) * 100
            adjusted_overbid = min(adjusted_overbid, max_overbid_pct)

        base_rec["recommended_overbid_pct"] = round(adjusted_overbid, 1)
        base_rec["position_adjusted"] = True
        base_rec["position_win_rate"] = round(pos_win_rate, 1) if pos_win_rate else None
        base_rec["position_auctions"] = total_auctions
        base_rec["reason"] += f" | {position_code}: {reason_adj} ({total_auctions} auctions)"

        return base_rec

    def get_trend_aware_overbid(
        self,
        asking_price: int,
        value_score: float,
        market_value: int,
        trend: str | None,
        trend_change_pct: float | None,
        predicted_future_value: int | None = None,
    ) -> dict[str, Any]:
        """
        Adjust overbid based on player trend.

        Rising trends = more competition = bid higher
        Falling trends = less competition = bid lower

        Args:
            asking_price: Player's asking price
            value_score: Our calculated value score
            market_value: Player's market value
            trend: Trend direction ("rising"/"falling"/"stable")
            trend_change_pct: Trend change percentage
            predicted_future_value: Maximum we should pay

        Returns:
            dict with recommended_overbid_pct and metadata
        """
        base_rec = self.get_recommended_overbid(
            asking_price, value_score, market_value, predicted_future_value
        )

        if not trend or not trend_change_pct:
            return base_rec

        # Analyze historical trend performance from flip outcomes
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT trend_at_buy, COUNT(*), AVG(profit_pct)
                FROM flip_outcomes
                WHERE buy_date > ?
                GROUP BY trend_at_buy
                """,
                (datetime.now().timestamp() - (60 * 24 * 3600),),  # Last 60 days
            )
            trend_stats = {
                row[0]: {"count": row[1], "avg_profit": row[2]} for row in cursor.fetchall()
            }

        # Apply trend-based adjustment
        if trend == "rising" and trend_change_pct > 10:
            # Strong uptrend - expect competition
            multiplier = 1.15
            reason = " | Rising trend - expect competition"

            # Check if rising trends have been profitable historically
            if "rising" in trend_stats and trend_stats["rising"]["count"] >= 5:
                avg_profit = trend_stats["rising"]["avg_profit"]
                if avg_profit > 15:
                    multiplier = 1.20  # Very profitable - bid even more aggressively
                    reason = f" | Rising trend - historically {avg_profit:.0f}% profit"

        elif trend == "falling" and trend_change_pct < -10:
            # Falling - less competition
            multiplier = 0.85
            reason = " | Falling trend - less competition"

            # Check if falling trends have worked (mean reversion)
            if "falling" in trend_stats and trend_stats["falling"]["count"] >= 5:
                avg_profit = trend_stats["falling"]["avg_profit"]
                if avg_profit > 10:
                    multiplier = 0.90  # Mean reversion works - bid slightly more
                    reason = f" | Falling trend - mean reversion ({avg_profit:.0f}% profit)"
        else:
            multiplier = 1.0
            reason = ""

        adjusted = base_rec["recommended_overbid_pct"] * multiplier

        # Apply ceiling
        if predicted_future_value:
            max_overbid_pct = ((predicted_future_value - asking_price) / asking_price) * 100
            adjusted = min(adjusted, max_overbid_pct)

        base_rec["recommended_overbid_pct"] = round(adjusted, 1)
        base_rec["reason"] += reason
        base_rec["trend_adjusted"] = True
        base_rec["trend"] = trend
        base_rec["trend_change_pct"] = round(trend_change_pct, 1) if trend_change_pct else None

        return base_rec

    def update_position_statistics(self):
        """
        Update position-specific bidding statistics from auction history.

        Should be called periodically (daily) to keep stats fresh.
        """
        from datetime import datetime

        with sqlite3.connect(self.db_path) as conn:
            # For each position, calculate stats from flip outcomes
            for position in ["GK", "DEF", "MID", "FWD"]:
                # Get flip outcomes for this position
                cursor = conn.execute(
                    """
                    SELECT
                        COUNT(*),
                        AVG(CASE WHEN profit > 0 THEN 1.0 ELSE 0.0 END) * 100,
                        AVG(profit_pct)
                    FROM flip_outcomes
                    WHERE position = ?
                      AND buy_date > ?
                    """,
                    (position, datetime.now().timestamp() - (60 * 24 * 3600)),
                )

                result = cursor.fetchone()
                if result and result[0] > 0:
                    total_flips, success_rate, avg_profit_pct = result

                    # Get auction stats for this position (approximate from asking price ranges)
                    # Note: We don't directly track position in auction_outcomes, so this is an approximation
                    cursor = conn.execute(
                        """
                        SELECT
                            AVG(our_overbid_pct),
                            COUNT(*),
                            AVG(CASE WHEN won = 1 THEN 1.0 ELSE 0.0 END) * 100,
                            AVG(player_value_score)
                        FROM auction_outcomes
                        WHERE timestamp > ?
                        """,
                        (datetime.now().timestamp() - (60 * 24 * 3600),),
                    )

                    auction_result = cursor.fetchone()
                    if auction_result:
                        avg_overbid, total_auctions, win_rate, avg_value = auction_result

                        # Calculate recommended overbid (slightly above average)
                        recommended_overbid = avg_overbid * 1.05 if avg_overbid else 10.0

                        conn.execute(
                            """
                            INSERT OR REPLACE INTO position_bidding_stats
                            (position, avg_overbid_pct, recommended_overbid_pct, win_rate,
                             avg_value_score, avg_profit_pct, total_auctions, successful_auctions,
                             last_updated)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                position,
                                avg_overbid,
                                recommended_overbid,
                                win_rate,
                                avg_value,
                                avg_profit_pct,
                                int(total_auctions),
                                int(total_auctions * win_rate / 100) if win_rate else 0,
                                datetime.now().isoformat(),
                            ),
                        )
