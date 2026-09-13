"""Learn from auction outcomes to improve bidding strategy"""

import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


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


def _opt_float(value: Any) -> float | None:
    """None-preserving float coercion, matching `_opt_int`'s contract."""
    if value is None:
        return None
    return float(value)


def _iso_to_epoch(value: Any) -> float | None:
    """Parse Kickbase's ISO-8601 transfer timestamps to epoch seconds.

    The feed sends `2026-05-05T10:00:00Z`; `fromisoformat` rejects the literal
    `Z` before Python 3.11, so it is normalised. Returns None on anything
    unparseable rather than raising -- this feeds a best-effort learning join,
    and a malformed row should be skipped, not crash a session.
    """
    if not value:
        return None
    try:
        text = str(value).strip().replace("Z", "+00:00")
        return datetime.fromisoformat(text).timestamp()
    except (TypeError, ValueError):
        return None


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
    # REH-104 entry context. Reconstructed from `player_mv_history` rather than
    # captured at buy time, so closed flips can be backfilled and the entry rule
    # becomes measurable on history. See `learning/entry_context.py`.
    trend_pct_at_buy: float | None = None
    mv_at_buy: int | None = None
    pct_below_peak_30d_at_buy: float | None = None


# --- EP overbid recommender (REH-89) --------------------------------------
#
# Minimum auctions inside the window before the learner is allowed to move a
# bid at all. Below this there is nothing measured, so it must defer to the
# bid stack rather than substitute an assumption for evidence.
MIN_AUCTIONS_FOR_LEARNED_OVERBID = 5

# How far back auction outcomes stay relevant. Note the off-season is longer
# than this, so at season start the window is legitimately empty -- which is
# precisely when the bot spends most.
AUCTION_HISTORY_WINDOW_DAYS = 90

# Overbid percentage contributed per point of marginal EP gain.
#
# NOT re-derived for the real-points scale, and deliberately so: doing that
# honestly needs auction outcomes measured against v2 gains, and there are
# currently zero inside the window. It is named and greppable here so the
# re-derivation is a one-line change once REH-68's replay can measure it.
#
# Scale history: on the 0-100 index a gain had to exceed ~27 to beat the
# MIN_LEARNED_OVERBID_PCT floor below, so this was a rarely-triggered
# backstop. On real points (p50 gain 43.1) it binds every time, which is how
# a backstop became the primary driver of bid size. It now only applies when
# there is auction evidence, which bounds the damage until it is re-derived.
EP_FLOOR_PCT_PER_POINT = 0.3

# Smallest overbid the learner will ever recommend once it does speak.
MIN_LEARNED_OVERBID_PCT = 8.0


class BidLearner:
    """Learn from auction outcomes to improve bidding strategy"""

    def __init__(self, dsn: str | None = None):
        """`dsn` overrides DATABASE_URL — tests pass a fresh database.

        Resolution is lazy: a session that never records anything never needs
        the store, and a missing DATABASE_URL surfaces at the first write as
        StoreUnconfigured — or, for the paths that matter, at startup through
        `store.ensure_ready()`.
        """
        self.dsn = dsn

    def connection(self):
        """A connection with dict rows; one transaction per `with` block."""
        from rehoboam.store import connect

        return connect(self.dsn)

    def record_outcome(self, outcome: AuctionOutcome):
        """Record an auction outcome for learning"""
        if outcome.timestamp is None:
            outcome.timestamp = datetime.now().timestamp()

        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO rehoboam.auction_outcomes (
                    player_id, player_name, our_bid, asking_price, our_overbid_pct,
                    won, winning_bid, winning_overbid_pct, winner_user_id, timestamp,
                    player_value_score, market_value
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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

    def record_flip(self, outcome: FlipOutcome) -> bool:
        """Record a completed flip for learning.

        Uses ON CONFLICT DO NOTHING so backfill reruns and accidental
        double-writes from the live trader collapse deterministically
        (idx_flip_unique on (player_id, buy_date)). Returns True if a row was
        actually inserted.
        """
        with self.connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO rehoboam.flip_outcomes (
                    player_id, player_name, buy_price, sell_price, profit, profit_pct,
                    hold_days, buy_date, sell_date, trend_at_buy, average_points, position,
                    was_injured, trend_pct_at_buy, mv_at_buy, pct_below_peak_30d_at_buy
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (player_id, buy_date) DO NOTHING
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
                    outcome.trend_pct_at_buy,
                    outcome.mv_at_buy,
                    outcome.pct_below_peak_30d_at_buy,
                ),
            )
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Operational state: pending bids + tracked purchases
    #
    # These two table families hold in-flight state, not history. On
    # lifecycle close (auction resolved / player sold) the row is deleted
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
        tier: str | None = None,
    ) -> None:
        """Record a freshly placed bid as pending (outcome TBD).

        Re-bidding on the same player overwrites the existing row — there's
        only ever one active auction per player from our side.
        """
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO rehoboam.pending_bids (
                    player_id, player_name, our_bid, asking_price,
                    our_overbid_pct, timestamp, market_value, player_value_score, tier
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (player_id) DO UPDATE SET
                    player_name = excluded.player_name,
                    our_bid = excluded.our_bid,
                    asking_price = excluded.asking_price,
                    our_overbid_pct = excluded.our_overbid_pct,
                    timestamp = excluded.timestamp,
                    market_value = excluded.market_value,
                    player_value_score = excluded.player_value_score,
                    tier = excluded.tier
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
                    tier,
                ),
            )
            # Sell-plan rows are replaced wholesale: an upsert on pending_bids
            # alone would leave stale join rows behind.
            conn.execute(
                "DELETE FROM rehoboam.pending_bid_sell_plans WHERE pending_bid_player_id = %s",
                (player_id,),
            )
            if sell_plan_player_ids:
                with conn.cursor() as cur:
                    cur.executemany(
                        "INSERT INTO rehoboam.pending_bid_sell_plans "
                        "(pending_bid_player_id, sell_player_id) VALUES (%s, %s)",
                        [(player_id, sp) for sp in sell_plan_player_ids],
                    )

    def get_pending_bids(self) -> list[dict[str, Any]]:
        """Return all pending bids, oldest first, with their sell plans inlined."""
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT player_id, player_name, our_bid, asking_price,
                       our_overbid_pct, timestamp, market_value, player_value_score,
                       tier
                FROM rehoboam.pending_bids
                ORDER BY timestamp ASC
            """
            ).fetchall()

            sell_plan_rows = conn.execute(
                """
                SELECT pending_bid_player_id, sell_player_id
                FROM rehoboam.pending_bid_sell_plans
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
        with self.connection() as conn:
            conn.execute(
                "DELETE FROM rehoboam.pending_bid_sell_plans WHERE pending_bid_player_id = %s",
                (player_id,),
            )
            conn.execute(
                "DELETE FROM rehoboam.pending_bids WHERE player_id = %s",
                (player_id,),
            )

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
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO rehoboam.tracked_purchases (
                    player_id, player_name, buy_price, buy_date, source
                )
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (player_id) DO UPDATE SET
                    player_name = excluded.player_name,
                    buy_price = excluded.buy_price,
                    buy_date = excluded.buy_date,
                    source = excluded.source
                """,
                (player_id, player_name, buy_price, buy_date, source),
            )

    def get_tracked_purchase(self, player_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT player_id, player_name, buy_price, buy_date, source
                FROM rehoboam.tracked_purchases
                WHERE player_id = %s
            """,
                (player_id,),
            ).fetchone()
        return dict(row) if row else None

    def delete_tracked_purchase(self, player_id: str) -> None:
        with self.connection() as conn:
            conn.execute(
                "DELETE FROM rehoboam.tracked_purchases WHERE player_id = %s",
                (player_id,),
            )

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
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO rehoboam.recently_sold (
                    player_id, player_name, sold_price, sold_at, reason
                )
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (player_id) DO UPDATE SET
                    player_name = excluded.player_name,
                    sold_price = excluded.sold_price,
                    sold_at = excluded.sold_at,
                    reason = excluded.reason
                """,
                (player_id, player_name, sold_price, sold_at, reason),
            )

    def was_recently_sold(self, player_id: str, within_seconds: float) -> bool:
        """True iff we sold this player within the last *within_seconds*."""
        cutoff = datetime.now(tz=timezone.utc).timestamp() - within_seconds
        with self.connection() as conn:
            row = conn.execute(
                "SELECT sold_at FROM rehoboam.recently_sold WHERE player_id = %s",
                (player_id,),
            ).fetchone()
        return bool(row and row["sold_at"] >= cutoff)

    def get_recent_sell(self, player_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT player_id, player_name, sold_price, sold_at, reason
                FROM rehoboam.recently_sold
                WHERE player_id = %s
            """,
                (player_id,),
            ).fetchone()
        return dict(row) if row else None

    def prune_recent_sells(self, older_than_seconds: float) -> int:
        """Drop wash-trade-guard rows older than the given age. Returns rows deleted."""
        cutoff = datetime.now(tz=timezone.utc).timestamp() - older_than_seconds
        with self.connection() as conn:
            cur = conn.execute("DELETE FROM rehoboam.recently_sold WHERE sold_at < %s", (cutoff,))
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

    def mv_history_for(self, player_id: str) -> list[tuple[float, int]]:
        """Every recorded market value for one player, as (epoch, value).

        Feeds `learning.entry_context.entry_context`. Returns a plain list of
        tuples rather than rows so the reconstruction stays pure and testable
        without a database.
        """
        with sqlite3.connect(self.db_path) as conn:
            return [
                (float(ts), int(mv))
                for ts, mv in conn.execute(
                    "SELECT snapshot_at, market_value FROM player_mv_history "
                    "WHERE player_id = ? ORDER BY snapshot_at",
                    (str(player_id),),
                )
            ]

    def backfill_flip_entry_context(self) -> int:
        """Fill entry context on closed flips that have none. Returns rows written.

        Idempotent by construction: only rows where `mv_at_buy IS NULL` are
        touched, so a rerun cannot overwrite a value captured live, and a flip
        with no usable market-value history is left NULL rather than faked —
        an unknown entry must stay distinguishable from a real zero reading
        ("bought exactly at the 30-day peak").
        """
        from rehoboam.learning.entry_context import entry_context

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            pending = conn.execute(
                "SELECT id, player_id, buy_date FROM flip_outcomes WHERE mv_at_buy IS NULL"
            ).fetchall()

            written = 0
            for row in pending:
                ctx = entry_context(self.mv_history_for(row["player_id"]), row["buy_date"])
                if ctx.mv_at_buy is None:
                    continue
                conn.execute(
                    "UPDATE flip_outcomes SET trend_at_buy = COALESCE(trend_at_buy, ?), "
                    "trend_pct_at_buy = ?, mv_at_buy = ?, pct_below_peak_30d_at_buy = ? "
                    "WHERE id = ?",
                    (
                        ctx.trend_at_buy,
                        ctx.trend_pct_at_buy,
                        ctx.mv_at_buy,
                        ctx.pct_below_peak_30d_at_buy,
                        row["id"],
                    ),
                )
                written += 1
            conn.commit()
            return written

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

    def record_buy_decision(
        self,
        *,
        player_id: str,
        player_name: str | None,
        decision: str,
        reason: str,
        marginal_ep_gain: float | None = None,
        asking_price: int | None = None,
        market_value: int | None = None,
        budget_ceiling: int | None = None,
        timestamp: float | None = None,
    ) -> None:
        """Record a buy candidate the bot evaluated, and what it decided.

        REH-86. Until this existed the learning tables recorded only what the
        bot DID, never what it considered and rejected -- so "the bot bought
        nothing for three weeks" could not be distinguished from "the bot saw
        nothing worth buying", and neither from "it wanted a player and could
        not afford him". That last case is the one REH-85 turns on: a EUR 21.3m
        listing bid at 1.0% over asking is a budget ceiling talking, not a
        judgement about the player.

        `reason` is a short stable slug (``below_ep_threshold``,
        ``unaffordable``, ``contested_skip``, ``squad_full``) so the column can
        be grouped without parsing prose.
        """
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO rehoboam.buy_decisions (
                    timestamp, player_id, player_name, decision, reason,
                    marginal_ep_gain, asking_price, market_value, budget_ceiling
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    float(
                        timestamp
                        if timestamp is not None
                        else datetime.now(timezone.utc).timestamp()
                    ),
                    str(player_id),
                    player_name,
                    str(decision),
                    str(reason),
                    _opt_float(marginal_ep_gain),
                    _opt_int(asking_price),
                    _opt_int(market_value),
                    _opt_int(budget_ceiling),
                ),
            )

    def resolve_auction_winners(self, *, window_days: float = 3.0) -> int:
        """Fill `winning_bid` / `winner_user_id` on auctions we lost.

        REH-86. `_record_outcome` marks a bid lost by ELIMINATION -- the player
        is neither in our squad nor in our active bids -- and never asks who
        actually took him. So all 26 rows recorded in 2025/26 carry a NULL
        winning bid, and "how far over asking must we go to win a EUR 20m
        player?" is unanswerable.

        The answer is already arriving: every session ingests the league
        transfer feed into `manager_transfers` with price and buyer. This joins
        the two on `player_id` within `window_days` of the auction.

        Deliberately conservative. A transfer outside the window is not
        attributed, because a wrong `winning_bid` would corrupt the exact
        distribution this exists to measure -- and an unfilled row is honestly
        unknown, where a wrong one is silently misleading. Returns the number
        of rows filled.
        """
        filled = 0
        window = float(window_days) * 86400.0
        with sqlite3.connect(self.db_path) as conn:
            pending = conn.execute(
                """
                SELECT id, player_id, timestamp, our_bid, asking_price, player_name
                FROM auction_outcomes
                WHERE won = 0 AND winning_bid IS NULL
                """
            ).fetchall()
            for row_id, player_id, ts, _our_bid, asking, player_name in pending:
                if ts is None:
                    continue
                best = None
                for mgr, price, dt_str in conn.execute(
                    """
                    SELECT manager_id, transfer_price, transfer_dt
                    FROM manager_transfers
                    WHERE player_id = ? AND transfer_price IS NOT NULL
                    """,
                    (str(player_id),),
                ):
                    epoch = _iso_to_epoch(dt_str)
                    if epoch is None or abs(epoch - float(ts)) > window:
                        continue
                    gap = abs(epoch - float(ts))
                    if best is None or gap < best[0]:
                        best = (gap, mgr, price)
                if best is None:
                    continue
                _, winner, price = best
                overbid = None
                if asking:
                    overbid = (price - asking) / asking * 100.0
                conn.execute(
                    """
                    UPDATE auction_outcomes
                    SET winning_bid = ?, winner_user_id = ?, winning_overbid_pct = ?
                    WHERE id = ?
                    """,
                    (int(price), str(winner), overbid, row_id),
                )
                filled += 1

                # REH-102: losing while holding the best price is not a loss,
                # it is a missing offer. Three August 2026 auctions went that
                # way — Rohr twice and Gyamerah, all Kickbase-listed, where the
                # highest bid should win — and nobody noticed for a week
                # because `won` is a boolean and cannot say "we were ahead".
                # This is the first instant the anomaly is knowable, so it is
                # raised here rather than left for someone to query.
                if _our_bid is not None and int(price) < int(_our_bid):
                    logger.warning(
                        "auction anomaly: we were HIGH BIDDER and still lost %s — "
                        "ours EUR %s, winner paid EUR %s (margin EUR %s). "
                        "An offer that loses to a lower price was not present at "
                        "resolution; see REH-102.",
                        player_name,
                        f"{int(_our_bid):,}",
                        f"{int(price):,}",
                        f"{int(_our_bid) - int(price):,}",
                    )
            conn.commit()
        return filled

    def high_bidder_losses(self) -> list[dict[str, Any]]:
        """Auctions lost despite our bid exceeding what the winner paid.

        REH-102. On a Kickbase-listed player the highest bid wins, so these
        cannot be explained by being outbid — the offer was not there when the
        listing resolved. Reported worst-margin-first, because the largest
        unexplained loss is the one worth chasing.

        Only rows with an attributed `winning_bid` are considered. An
        unattributed loss is honestly unknown, and guessing would turn the one
        signal that distinguishes this failure from ordinary competition into
        noise.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT player_id, player_name, our_bid, winning_bid, timestamp,
                       our_bid - winning_bid AS margin
                FROM auction_outcomes
                WHERE won = 0
                  AND winning_bid IS NOT NULL
                  AND winning_bid > 0
                  AND our_bid > winning_bid
                ORDER BY margin DESC
                """
            ).fetchall()
        return [dict(r) for r in rows]

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

    def get_last_purchase(self, manager_id: str, player_id: str) -> dict[str, Any] | None:
        """Our most recent purchase of a player, if we still hold that purchase.

        REH-103. `manager_transfers` mirrors the league activity feed, so it
        records purchases the bidding path never saw — which is the only reason
        Raum's EUR 40.7m cost basis is recoverable at all.

        A buy followed by a SELL is deliberately reported as no basis rather
        than as the old price. Da Costa was bought 2026-05-03 and sold
        2026-05-11; the player of that name in the squad today is a different,
        untracked holding, and attaching the May figure to it would drive
        profit-and-loss decisions off a basis that belongs to a position we no
        longer own. Returns None in that case so the caller reports a gap
        instead of acting on a stale number.

        `transfer_dt` is stored as an ISO-8601 string, so the ordering and the
        after-the-sale comparison are both plain lexicographic.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            buy = conn.execute(
                """
                SELECT transfer_price, transfer_dt
                FROM manager_transfers
                WHERE manager_id = ? AND player_id = ? AND transfer_type = 1
                  AND transfer_price IS NOT NULL
                ORDER BY transfer_dt DESC
                LIMIT 1
                """,
                (str(manager_id), str(player_id)),
            ).fetchone()
            if buy is None:
                return None

            sold_since = conn.execute(
                """
                SELECT 1
                FROM manager_transfers
                WHERE manager_id = ? AND player_id = ? AND transfer_type = 2
                  AND transfer_dt > ?
                LIMIT 1
                """,
                (str(manager_id), str(player_id), buy["transfer_dt"]),
            ).fetchone()

        if sold_since is not None:
            return None
        return {"price": int(buy["transfer_price"]), "transfer_dt": buy["transfer_dt"]}

    def get_last_purchase_price(self, manager_id: str, player_id: str) -> int | None:
        """Price of the purchase we still hold, or None if there is no basis."""
        purchase = self.get_last_purchase(manager_id, player_id)
        return purchase["price"] if purchase else None

    def recent_buy_prices(self, window_days: int) -> list[int]:
        """Purchase prices across the league in the trailing window, in euros.

        The population behind REH-85's reserve: what one further move actually
        costs here. `transfer_type = 1` is a buy and `2` is a sale — verified
        against manager 3616202, whose type-1 total of EUR 69,831,333 and
        type-2 total of EUR 139,638,056 match REH-72 §5.

        Prices are returned as magnitudes because the transfer feed signs a
        purchase negative in some payloads and a reserve is a size, not a
        direction. `transfer_dt` is ISO-8601, so the cutoff compares
        lexicographically without parsing.
        """
        cutoff = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - window_days * 86400))
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT ABS(transfer_price)
                FROM manager_transfers
                WHERE transfer_type = 1
                  AND transfer_price IS NOT NULL
                  AND transfer_dt >= ?
                """,
                (cutoff,),
            ).fetchall()
        return [int(r[0]) for r in rows]

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

        # Win-rate adjustment from historical auction data
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT COUNT(*), SUM(CASE WHEN won = 1 THEN 1 ELSE 0 END)
                FROM auction_outcomes
                WHERE timestamp > ?
                """,
                (datetime.now().timestamp() - (AUCTION_HISTORY_WINDOW_DAYS * 24 * 3600),),
            )
            row = cursor.fetchone()

        total_auctions, total_wins = row if row else (0, 0)
        total_auctions = total_auctions or 0
        total_wins = total_wins or 0

        # No evidence, no opinion. The caller treats any positive value as
        # authoritative and lets it replace the whole bid stack -- including
        # the reduction applied when a player's market value is falling. An
        # EP-derived guess is not a good enough reason to overrule that, so
        # say nothing and let the stack stand (REH-89).
        if total_auctions < MIN_AUCTIONS_FOR_LEARNED_OVERBID:
            return {
                "recommended_overbid_pct": 0.0,
                "reason": (
                    f"only {total_auctions} auction(s) in the last "
                    f"{AUCTION_HISTORY_WINDOW_DAYS}d "
                    f"(need {MIN_AUCTIONS_FOR_LEARNED_OVERBID}) — "
                    f"deferring to the bid stack"
                ),
            }

        # EP-gain-based minimum floor.
        ep_floor_pct = marginal_ep_gain * EP_FLOOR_PCT_PER_POINT

        # Past the early return there is always enough history to divide by.
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

        # Outcome quality adjustment
        outcome_quality = self._get_won_player_outcome_quality()

        # Base overbid: use ep floor as the starting point, then scale
        base_overbid = max(ep_floor_pct, MIN_LEARNED_OVERBID_PCT)
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

    def record_proposal(
        self,
        *,
        proposal_id: str,
        player_id: str,
        player_name: str,
        bid: int,
        market_value: int,
        message: str,
        tier: str | None = None,
        auto_approve_at: float | None = None,
        batch_id: str | None = None,
    ) -> None:
        """Persist a proposal awaiting approval.

        ``tier`` is the marginal-EP band the bid was sized under. Approval
        needs it to recompute the ceiling against the live market value
        (REH-99); None falls back to the tightest tier.
        """
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO rehoboam.trade_proposals "
                "(proposal_id, player_id, player_name, bid, market_value, message, "
                " status, created_at, tier, auto_approve_at, batch_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s, %s, %s, %s) "
                "ON CONFLICT (proposal_id) DO UPDATE SET "
                "player_id = excluded.player_id, "
                "player_name = excluded.player_name, "
                "bid = excluded.bid, "
                "market_value = excluded.market_value, "
                "message = excluded.message, "
                "status = excluded.status, "
                "created_at = excluded.created_at, "
                "tier = excluded.tier, "
                "auto_approve_at = excluded.auto_approve_at, "
                "batch_id = excluded.batch_id",
                (
                    proposal_id,
                    player_id,
                    player_name,
                    int(bid),
                    int(market_value),
                    message,
                    datetime.now().timestamp(),
                    tier,
                    auto_approve_at,
                    batch_id,
                ),
            )

    def pending_in_batch(self, batch_id: str) -> list[dict]:
        """Still-pending proposals from one session's recommended set.

        Oldest first, which is the order the overview presented them in — the
        set was chosen to fit the budget as a whole, so executing it in a
        different order can strand the tail on "budget would go negative".

        `status = 'pending'` keeps anything already tapped, rejected or
        auto-approved out, which is what makes a second tap on "Approve all"
        buy nothing more.
        """
        if not batch_id:
            return []
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM rehoboam.trade_proposals
                WHERE batch_id = %s AND status = 'pending'
                ORDER BY created_at ASC, proposal_id ASC
                """,
                (batch_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def due_auto_approvals(self, *, now: float) -> list[dict]:
        """Pending proposals whose auto-approve deadline has passed.

        Only emergency fills carry a deadline, so an ordinary upgrade never
        appears here however long it waits. `status = 'pending'` keeps a
        rejected or already-executed row out — the deadline is a backstop for
        silence, not an override of a decision Marco has already made.
        """
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM rehoboam.trade_proposals
                WHERE status = 'pending'
                  AND auto_approve_at IS NOT NULL
                  AND auto_approve_at <= %s
                ORDER BY auto_approve_at ASC
                """,
                (float(now),),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_proposal(self, proposal_id: str) -> dict | None:
        """One proposal by id, or None."""
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM rehoboam.trade_proposals WHERE proposal_id = %s", (proposal_id,)
            ).fetchone()
        return dict(row) if row else None

    def mark_proposal(self, proposal_id: str, status: str) -> bool:
        """Move a proposal out of 'pending'. Returns False if it already left.

        The WHERE clause is the idempotency guarantee: Telegram retries
        callbacks, and a second tap must not buy the player twice.
        """
        with self.connection() as conn:
            cur = conn.execute(
                "UPDATE rehoboam.trade_proposals SET status = %s "
                "WHERE proposal_id = %s AND status = 'pending'",
                (status, proposal_id),
            )
            return cur.rowcount > 0

    def set_proposal_status(self, proposal_id: str, status: str) -> None:
        """Set a status unconditionally.

        Distinct from ``mark_proposal``, which only moves a row OUT of
        'pending' and is the replay lock. Once a callback has claimed a
        proposal it owns it, and the follow-up transitions to 'executed' or
        'failed' must not be blocked by that guard.
        """
        with self.connection() as conn:
            conn.execute(
                "UPDATE rehoboam.trade_proposals SET status = %s WHERE proposal_id = %s",
                (status, proposal_id),
            )

    def record_forced_sale(
        self,
        *,
        matchday: int,
        place: int,
        player_id: str,
        player_name: str,
        pool: str,
        reason: str,
        executed: bool,
    ) -> bool:
        """Log a Top-5 forced sale. False if this matchday was already settled.

        The matchday is the primary key, so a second call for the same one is
        refused rather than selling a second player — the rule takes exactly
        one player per matchday, and a re-run of the session must not compound
        it.
        """
        with self.connection() as conn:
            cur = conn.execute(
                "INSERT INTO rehoboam.forced_sales "
                "(matchday, place, player_id, player_name, pool, reason, executed, settled_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT DO NOTHING",
                (
                    int(matchday),
                    int(place),
                    str(player_id),
                    player_name,
                    pool,
                    reason,
                    1 if executed else 0,
                    time.time(),
                ),
            )
            return cur.rowcount > 0

    def forced_sale_settled(self, matchday: int) -> bool:
        """Has the Top-5 obligation for this matchday already been discharged?"""
        with self.connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM rehoboam.forced_sales WHERE matchday = %s", (int(matchday),)
            ).fetchone()
        return row is not None

    def proposals_for_player(self, player_id: str) -> list[dict]:
        """Every proposal ever made for this player, newest first."""
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM rehoboam.trade_proposals WHERE player_id = %s "
                "ORDER BY created_at DESC",
                (str(player_id),),
            ).fetchall()
        return [dict(r) for r in rows]

    def proposals_since(self, since_ts: float) -> list[dict]:
        """Every proposal created at or after ``since_ts``, oldest first.

        The daily summary uses this to report what happened to proposals —
        without it an approved purchase and a gate-refused one both vanish,
        since the session's own results only cover autonomous trades.
        """
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM rehoboam.trade_proposals WHERE created_at >= %s "
                "ORDER BY created_at",
                (float(since_ts),),
            ).fetchall()
        return [dict(r) for r in rows]

    def pending_proposals(self) -> list[dict]:
        """All proposals still awaiting a decision, oldest first."""
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM rehoboam.trade_proposals WHERE status = 'pending' "
                "ORDER BY created_at"
            ).fetchall()
        return [dict(r) for r in rows]
