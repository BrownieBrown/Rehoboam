"""Learn from league activity feed to improve bidding strategy"""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console

console = Console()


class ActivityFeedLearner:
    """Learn from all league transfer activities, not just our own bids"""

    def __init__(self, db_path: Path | None = None):
        if db_path is None:
            # Share the same database as bid_learner
            db_path = Path("logs") / "bid_learning.db"

        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Initialize database schema for activity feed learning"""
        with sqlite3.connect(self.db_path) as conn:
            # Table for all league transfers (from activity feed)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS league_transfers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    activity_id TEXT UNIQUE NOT NULL,
                    player_id TEXT NOT NULL,
                    player_name TEXT NOT NULL,
                    buyer_name TEXT,
                    seller_name TEXT,
                    transfer_price INTEGER NOT NULL,
                    transfer_type INTEGER NOT NULL,
                    market_value_at_time INTEGER,
                    overbid_pct REAL,
                    timestamp TEXT NOT NULL,
                    processed_at REAL NOT NULL
                )
            """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_league_transfers_player
                ON league_transfers(player_id)
            """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_league_transfers_timestamp
                ON league_transfers(timestamp)
            """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_league_transfers_buyer
                ON league_transfers(buyer_name)
            """
            )

            # Table for market value snapshots (from Type 3 activities)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS market_value_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    activity_id TEXT UNIQUE NOT NULL,
                    player_id TEXT NOT NULL,
                    player_name TEXT NOT NULL,
                    market_value INTEGER NOT NULL,
                    timestamp TEXT NOT NULL,
                    processed_at REAL NOT NULL
                )
            """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_mv_snapshots_player
                ON market_value_snapshots(player_id)
            """
            )

            conn.commit()

    def process_activity_feed(
        self, activities_response: dict[str, Any], api_client=None
    ) -> dict[str, int]:
        """
        Process activity feed and extract learning data

        Args:
            activities_response: Response from get_activities_feed()
            api_client: Optional API client to fetch additional data

        Returns:
            dict with counts of processed activities
        """
        activities = activities_response.get("af", [])

        stats = {
            "transfers_new": 0,
            "transfers_duplicate": 0,
            "market_values_new": 0,
            "market_values_duplicate": 0,
        }

        for activity in activities:
            activity_id = activity.get("i")
            activity_type = activity.get("t")
            data = activity.get("data", {})
            timestamp = activity.get("dt")

            if activity_type == 15:
                # Transfer activity
                result = self._process_transfer(
                    activity_id=activity_id,
                    data=data,
                    timestamp=timestamp,
                    api_client=api_client,
                )
                if result == "new":
                    stats["transfers_new"] += 1
                else:
                    stats["transfers_duplicate"] += 1

            elif activity_type == 3:
                # Market value change
                result = self._process_market_value(
                    activity_id=activity_id, data=data, timestamp=timestamp
                )
                if result == "new":
                    stats["market_values_new"] += 1
                else:
                    stats["market_values_duplicate"] += 1

        return stats

    def _process_transfer(
        self, activity_id: str, data: dict, timestamp: str, api_client=None
    ) -> str:
        """
        Process a transfer activity (Type 15)

        Returns:
            "new" if inserted, "duplicate" if already exists
        """
        player_id = data.get("pi")
        player_name = data.get("pn", "Unknown")
        buyer_name = data.get("byr")
        seller_name = data.get("slr")
        transfer_price = data.get("trp", 0)
        transfer_type = data.get("t", 0)  # 1=buy, 2=sell

        if not player_id or transfer_price == 0:
            return "duplicate"  # Invalid data

        # Try to get market value at time of transfer (if we have it)
        market_value_at_time = None
        overbid_pct = None

        # Check if we already have this activity
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT id FROM league_transfers WHERE activity_id = ?", (activity_id,)
            )
            if cursor.fetchone():
                return "duplicate"

            # Insert the transfer
            conn.execute(
                """
                INSERT INTO league_transfers (
                    activity_id, player_id, player_name, buyer_name, seller_name,
                    transfer_price, transfer_type, market_value_at_time, overbid_pct,
                    timestamp, processed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    activity_id,
                    player_id,
                    player_name,
                    buyer_name,
                    seller_name,
                    transfer_price,
                    transfer_type,
                    market_value_at_time,
                    overbid_pct,
                    timestamp,
                    datetime.now().timestamp(),
                ),
            )
            conn.commit()

        return "new"

    def _process_market_value(self, activity_id: str, data: dict, timestamp: str) -> str:
        """
        Process a market value change activity (Type 3)

        Returns:
            "new" if inserted, "duplicate" if already exists
        """
        player_id = data.get("pi")
        player_name = f"{data.get('fn', '')} {data.get('ln', '')}".strip()
        market_value = data.get("mv", 0)

        if not player_id or market_value == 0:
            return "duplicate"

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT id FROM market_value_snapshots WHERE activity_id = ?", (activity_id,)
            )
            if cursor.fetchone():
                return "duplicate"

            conn.execute(
                """
                INSERT INTO market_value_snapshots (
                    activity_id, player_id, player_name, market_value,
                    timestamp, processed_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (
                    activity_id,
                    player_id,
                    player_name,
                    market_value,
                    timestamp,
                    datetime.now().timestamp(),
                ),
            )
            conn.commit()

        return "new"

    def get_competitive_bidding_stats(self, player_id: str | None = None) -> dict[str, Any]:
        """
        Analyze what prices win in this league

        Args:
            player_id: Optional player ID to get player-specific stats

        Returns:
            dict with competitive bidding statistics
        """
        with sqlite3.connect(self.db_path) as conn:
            if player_id:
                # Player-specific stats
                cursor = conn.execute(
                    """
                    SELECT AVG(transfer_price), MIN(transfer_price), MAX(transfer_price), COUNT(*)
                    FROM league_transfers
                    WHERE player_id = ? AND transfer_type = 1
                """,
                    (player_id,),
                )
            else:
                # Overall league stats
                cursor = conn.execute(
                    """
                    SELECT AVG(transfer_price), MIN(transfer_price), MAX(transfer_price), COUNT(*)
                    FROM league_transfers
                    WHERE transfer_type = 1
                """
                )

            result = cursor.fetchone()
            avg_price, min_price, max_price, count = result

            # Get most active buyers
            cursor = conn.execute(
                """
                SELECT buyer_name, COUNT(*), AVG(transfer_price)
                FROM league_transfers
                WHERE buyer_name IS NOT NULL AND transfer_type = 1
                GROUP BY buyer_name
                ORDER BY COUNT(*) DESC
                LIMIT 5
            """
            )

            top_buyers = []
            for buyer, buy_count, avg_spend in cursor.fetchall():
                top_buyers.append(
                    {
                        "name": buyer,
                        "purchases": buy_count,
                        "avg_price": int(avg_spend) if avg_spend else 0,
                    }
                )

            return {
                "total_transfers": count if count else 0,
                "avg_transfer_price": int(avg_price) if avg_price else 0,
                "min_transfer_price": int(min_price) if min_price else 0,
                "max_transfer_price": int(max_price) if max_price else 0,
                "top_buyers": top_buyers,
            }

    def get_player_demand_score(self, player_id: str) -> float:
        """
        Calculate how in-demand a player is based on transfer activity

        Returns:
            Score 0-100, higher = more competitive
        """
        with sqlite3.connect(self.db_path) as conn:
            # Count recent transfers of this player
            cursor = conn.execute(
                """
                SELECT COUNT(*) FROM league_transfers
                WHERE player_id = ? AND transfer_type = 1
                AND datetime(timestamp) > datetime('now', '-30 days')
            """,
                (player_id,),
            )

            recent_buys = cursor.fetchone()[0]

            # More buys = higher demand = need to bid more aggressively
            # 0 buys = 50 (neutral)
            # 1 buy = 60
            # 2+ buys = 75+
            if recent_buys == 0:
                return 50.0
            elif recent_buys == 1:
                return 60.0
            elif recent_buys == 2:
                return 75.0
            else:
                return min(95.0, 75.0 + (recent_buys - 2) * 10)

    def get_competitor_analysis(self, competitor_name: str) -> dict[str, Any]:
        """
        Analyze a specific competitor's bidding behavior

        Args:
            competitor_name: Name of the competitor to analyze

        Returns:
            dict with competitor stats
        """
        with sqlite3.connect(self.db_path) as conn:
            # Get competitor's purchase history
            cursor = conn.execute(
                """
                SELECT COUNT(*), AVG(transfer_price), MIN(transfer_price), MAX(transfer_price)
                FROM league_transfers
                WHERE buyer_name = ? AND transfer_type = 1
            """,
                (competitor_name,),
            )

            result = cursor.fetchone()
            count, avg_price, min_price, max_price = result

            if not count or count == 0:
                return {
                    "name": competitor_name,
                    "purchases": 0,
                    "message": "No purchase data for this competitor",
                }

            # Get recent activity (last 7 days)
            cursor = conn.execute(
                """
                SELECT COUNT(*)
                FROM league_transfers
                WHERE buyer_name = ? AND transfer_type = 1
                AND datetime(timestamp) > datetime('now', '-7 days')
            """,
                (competitor_name,),
            )
            recent_purchases = cursor.fetchone()[0]

            # Get their most expensive purchases
            cursor = conn.execute(
                """
                SELECT player_name, transfer_price
                FROM league_transfers
                WHERE buyer_name = ? AND transfer_type = 1
                ORDER BY transfer_price DESC
                LIMIT 3
            """,
                (competitor_name,),
            )

            expensive_buys = []
            for player_name, price in cursor.fetchall():
                expensive_buys.append({"player": player_name, "price": price})

            # Determine aggression level
            if avg_price > 15_000_000:
                aggression = "Very Aggressive"
            elif avg_price > 10_000_000:
                aggression = "Aggressive"
            elif avg_price > 5_000_000:
                aggression = "Moderate"
            else:
                aggression = "Conservative"

            return {
                "name": competitor_name,
                "purchases": count,
                "avg_price": int(avg_price) if avg_price else 0,
                "min_price": int(min_price) if min_price else 0,
                "max_price": int(max_price) if max_price else 0,
                "recent_purchases": recent_purchases,
                "expensive_buys": expensive_buys,
                "aggression_level": aggression,
            }

    def get_top_competitors(self, limit: int = 5) -> list[dict[str, Any]]:
        """
        Get the most active/aggressive competitors

        Returns:
            List of competitor stats
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT buyer_name, COUNT(*), AVG(transfer_price), MAX(transfer_price)
                FROM league_transfers
                WHERE buyer_name IS NOT NULL AND transfer_type = 1
                GROUP BY buyer_name
                ORDER BY COUNT(*) DESC, AVG(transfer_price) DESC
                LIMIT ?
            """,
                (limit,),
            )

            competitors = []
            for name, purchases, avg_price, max_price in cursor.fetchall():
                # Determine threat level
                threat_score = (purchases * 10) + (avg_price / 1_000_000)

                if threat_score > 100:
                    threat_level = "🔥 HIGH THREAT"
                elif threat_score > 50:
                    threat_level = "⚠️ MEDIUM THREAT"
                else:
                    threat_level = "✓ LOW THREAT"

                competitors.append(
                    {
                        "name": name,
                        "purchases": purchases,
                        "avg_price": int(avg_price) if avg_price else 0,
                        "max_price": int(max_price) if max_price else 0,
                        "threat_level": threat_level,
                        "threat_score": threat_score,
                    }
                )

            return competitors

    def display_league_stats(self):
        """Display learning statistics from activity feed"""
        stats = self.get_competitive_bidding_stats()

        console.print("\n[bold cyan]📊 League Transfer Activity (from Activity Feed)[/bold cyan]\n")

        console.print(f"Total transfers recorded: {stats['total_transfers']}")

        if stats["total_transfers"] > 0:
            console.print(f"Average transfer price: [cyan]€{stats['avg_transfer_price']:,}[/cyan]")
            console.print(
                f"Price range: €{stats['min_transfer_price']:,} - €{stats['max_transfer_price']:,}"
            )

            if stats["top_buyers"]:
                console.print("\n[bold]Most Active Buyers:[/bold]")
                for buyer in stats["top_buyers"]:
                    console.print(
                        f"  • {buyer['name']}: {buyer['purchases']} purchases (avg: €{buyer['avg_price']:,})"
                    )

        console.print()

    def display_competitor_analysis(self):
        """Display detailed competitor threat analysis"""
        competitors = self.get_top_competitors(limit=5)

        if not competitors:
            console.print("[yellow]No competitor data available yet[/yellow]")
            return

        console.print("\n[bold red]⚔️  COMPETITOR THREAT ANALYSIS[/bold red]\n")

        for comp in competitors:
            console.print(f"{comp['threat_level']} {comp['name']}")
            console.print(f"  Purchases: {comp['purchases']}")
            console.print(f"  Avg price: €{comp['avg_price']:,}")
            console.print(f"  Max price: €{comp['max_price']:,}")
            console.print()

        # Detailed analysis of top threat
        if competitors:
            top_threat = competitors[0]
            console.print(f"[bold]🎯 Top Competitor: {top_threat['name']}[/bold]")
            analysis = self.get_competitor_analysis(top_threat["name"])

            if analysis.get("expensive_buys"):
                console.print("\n[bold]Their Expensive Purchases:[/bold]")
                for buy in analysis["expensive_buys"]:
                    console.print(f"  • {buy['player']}: €{buy['price']:,}")

            console.print(
                f"\n[bold]Strategy:[/bold] This competitor is {analysis['aggression_level']}"
            )
            if analysis["recent_purchases"] > 0:
                console.print(
                    f"[yellow]⚠️ Active recently: {analysis['recent_purchases']} purchases in last 7 days[/yellow]"
                )

        console.print()
