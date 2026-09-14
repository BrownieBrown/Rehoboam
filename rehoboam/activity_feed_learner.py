"""Learn from league activity feed to improve bidding strategy"""

from datetime import datetime, timedelta, timezone
from typing import Any

from rich.console import Console

console = Console()


class ActivityFeedLearner:
    """Learn from all league transfer activities, not just our own bids"""

    def __init__(self, dsn: str | None = None):
        """`dsn` overrides DATABASE_URL — tests pass a fresh database.

        Resolution is lazy, matching `BidLearner`: a session that never
        processes a feed never needs the store.
        """
        self.dsn = dsn

    def connection(self):
        """A connection with dict rows; one transaction per `with` block."""
        from rehoboam.store import connect

        return connect(self.dsn)

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

        with self.connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO rehoboam.league_transfers (
                    activity_id, player_id, player_name, buyer_name, seller_name,
                    transfer_price, transfer_type, market_value_at_time, overbid_pct,
                    timestamp, processed_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (activity_id) DO NOTHING
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
            return "new" if cur.rowcount else "duplicate"

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

        with self.connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO rehoboam.market_value_snapshots (
                    activity_id, player_id, player_name, market_value,
                    timestamp, processed_at
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (activity_id) DO NOTHING
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
            return "new" if cur.rowcount else "duplicate"

    def get_competitive_bidding_stats(self, player_id: str | None = None) -> dict[str, Any]:
        """
        Analyze what prices win in this league

        Args:
            player_id: Optional player ID to get player-specific stats

        Returns:
            dict with competitive bidding statistics
        """
        with self.connection() as conn:
            if player_id:
                # Player-specific stats
                row = conn.execute(
                    """
                    SELECT AVG(transfer_price)::float8 AS avg_price,
                           MIN(transfer_price) AS min_price,
                           MAX(transfer_price) AS max_price,
                           COUNT(*) AS n
                    FROM rehoboam.league_transfers
                    WHERE player_id = %s AND transfer_type = 1
                    """,
                    (player_id,),
                ).fetchone()
            else:
                # Overall league stats
                row = conn.execute(
                    """
                    SELECT AVG(transfer_price)::float8 AS avg_price,
                           MIN(transfer_price) AS min_price,
                           MAX(transfer_price) AS max_price,
                           COUNT(*) AS n
                    FROM rehoboam.league_transfers
                    WHERE transfer_type = 1
                    """
                ).fetchone()

            # Get most active buyers
            buyer_rows = conn.execute(
                """
                SELECT buyer_name,
                       COUNT(*) AS purchases,
                       AVG(transfer_price)::float8 AS avg_price
                FROM rehoboam.league_transfers
                WHERE buyer_name IS NOT NULL AND transfer_type = 1
                GROUP BY buyer_name
                ORDER BY COUNT(*) DESC
                LIMIT 5
                """
            ).fetchall()

            top_buyers = [
                {
                    "name": r["buyer_name"],
                    "purchases": r["purchases"],
                    "avg_price": int(r["avg_price"]) if r["avg_price"] else 0,
                }
                for r in buyer_rows
            ]

            return {
                "total_transfers": row["n"] if row["n"] else 0,
                "avg_transfer_price": int(row["avg_price"]) if row["avg_price"] else 0,
                "min_transfer_price": int(row["min_price"]) if row["min_price"] else 0,
                "max_transfer_price": int(row["max_price"]) if row["max_price"] else 0,
                "top_buyers": top_buyers,
            }

    def get_player_demand_score(self, player_id: str) -> float:
        """
        Calculate how in-demand a player is based on transfer activity

        Returns:
            Score 0-100, higher = more competitive
        """
        cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS n FROM rehoboam.league_transfers
                WHERE player_id = %s AND transfer_type = 1
                AND timestamp > %s
                """,
                (player_id, cutoff),
            ).fetchone()

            recent_buys = row["n"]

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
        with self.connection() as conn:
            # Get competitor's purchase history
            row = conn.execute(
                """
                SELECT COUNT(*) AS n,
                       AVG(transfer_price)::float8 AS avg_price,
                       MIN(transfer_price) AS min_price,
                       MAX(transfer_price) AS max_price
                FROM rehoboam.league_transfers
                WHERE buyer_name = %s AND transfer_type = 1
                """,
                (competitor_name,),
            ).fetchone()

            count = row["n"]
            avg_price = row["avg_price"]
            min_price = row["min_price"]
            max_price = row["max_price"]

            if not count:
                return {
                    "name": competitor_name,
                    "purchases": 0,
                    "message": "No purchase data for this competitor",
                }

            # Get recent activity (last 7 days)
            cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=7)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            recent_row = conn.execute(
                """
                SELECT COUNT(*) AS n
                FROM rehoboam.league_transfers
                WHERE buyer_name = %s AND transfer_type = 1
                AND timestamp > %s
                """,
                (competitor_name, cutoff),
            ).fetchone()
            recent_purchases = recent_row["n"]

            # Get their most expensive purchases
            expensive_rows = conn.execute(
                """
                SELECT player_name, transfer_price
                FROM rehoboam.league_transfers
                WHERE buyer_name = %s AND transfer_type = 1
                ORDER BY transfer_price DESC
                LIMIT 3
                """,
                (competitor_name,),
            ).fetchall()

            expensive_buys = [
                {"player": r["player_name"], "price": r["transfer_price"]} for r in expensive_rows
            ]

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

    def has_aggressive_competitors(self, threat_score_threshold: float = 100.0) -> bool:
        """Return True if the league has at least one high-threat competitor.

        Threat score combines purchase frequency and average spend — a manager
        with ``threat_score > 100`` is actively buying and paying premium prices.
        Used by bidding logic to tighten the skip criteria on contested
        mid-tier auctions (we won't outbid a whale for a solid-but-not-essential
        upgrade).
        """
        for competitor in self.get_top_competitors(limit=5):
            if competitor.get("threat_score", 0) > threat_score_threshold:
                return True
        return False

    def get_top_competitors(self, limit: int = 5) -> list[dict[str, Any]]:
        """
        Get the most active/aggressive competitors

        Returns:
            List of competitor stats
        """
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT buyer_name,
                       COUNT(*) AS purchases,
                       AVG(transfer_price)::float8 AS avg_price,
                       MAX(transfer_price) AS max_price
                FROM rehoboam.league_transfers
                WHERE buyer_name IS NOT NULL AND transfer_type = 1
                GROUP BY buyer_name
                ORDER BY COUNT(*) DESC, AVG(transfer_price) DESC
                LIMIT %s
                """,
                (limit,),
            ).fetchall()

            competitors = []
            for r in rows:
                purchases = r["purchases"]
                avg_price = r["avg_price"]
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
                        "name": r["buyer_name"],
                        "purchases": purchases,
                        "avg_price": int(avg_price) if avg_price else 0,
                        "max_price": int(r["max_price"]) if r["max_price"] else 0,
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
