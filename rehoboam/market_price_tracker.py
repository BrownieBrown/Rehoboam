"""Track market listing price adjustments and learn patterns"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from rich.console import Console
from rich.table import Table

console = Console()


@dataclass
class PriceSnapshot:
    """A snapshot of a player's asking price on the market"""

    id: int | None
    player_id: str
    player_name: str
    asking_price: int
    market_value: int
    days_listed: int
    timestamp: str
    hour: int  # Hour of day when snapshot taken


@dataclass
class PriceAdjustment:
    """A detected price adjustment event"""

    id: int | None
    player_id: str
    player_name: str
    old_price: int
    new_price: int
    price_change: int
    price_change_pct: float
    days_listed: int
    market_value: int
    detected_at: str
    adjustment_type: str  # "decrease", "increase", "unchanged"


@dataclass
class PriceAdjustmentPattern:
    """Learned pattern about price adjustments"""

    days_listed: int
    avg_adjustment_pct: float
    median_adjustment_pct: float
    sample_count: int
    decrease_probability: float  # Probability of price decrease
    typical_decrease_pct: float  # Average decrease when it happens


class MarketPriceTracker:
    """Track market listing prices and learn adjustment patterns"""

    def __init__(self, db_path: Path | None = None):
        if db_path is None:
            db_path = Path("logs") / "market_prices.db"

        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Initialize database schema"""
        with sqlite3.connect(self.db_path) as conn:
            # Price snapshots table
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS price_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    player_id TEXT NOT NULL,
                    player_name TEXT NOT NULL,
                    asking_price INTEGER NOT NULL,
                    market_value INTEGER NOT NULL,
                    days_listed INTEGER NOT NULL,
                    timestamp TEXT NOT NULL,
                    hour INTEGER NOT NULL
                )
            """
            )

            # Price adjustments table
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS price_adjustments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    player_id TEXT NOT NULL,
                    player_name TEXT NOT NULL,
                    old_price INTEGER NOT NULL,
                    new_price INTEGER NOT NULL,
                    price_change INTEGER NOT NULL,
                    price_change_pct REAL NOT NULL,
                    days_listed INTEGER NOT NULL,
                    market_value INTEGER NOT NULL,
                    detected_at TEXT NOT NULL,
                    adjustment_type TEXT NOT NULL
                )
            """
            )

            # Indexes for efficient queries
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_snapshots_player
                ON price_snapshots(player_id, timestamp DESC)
            """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_adjustments_days
                ON price_adjustments(days_listed)
            """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp
                ON price_snapshots(timestamp DESC)
            """
            )

            conn.commit()

    def record_market_snapshot(self, market_players: list) -> int:
        """
        Record current asking prices for all market players

        Args:
            market_players: List of MarketPlayer objects

        Returns:
            Number of snapshots recorded
        """
        now = datetime.now()
        timestamp = now.isoformat()
        hour = now.hour

        snapshots = []
        for player in market_players:
            # Calculate days listed
            days_listed = 0
            if hasattr(player, "listed_at") and player.listed_at:
                try:
                    listed_dt = datetime.fromisoformat(player.listed_at.replace("Z", "+00:00"))
                    days_listed = (datetime.now(listed_dt.tzinfo) - listed_dt).days
                except Exception:
                    pass

            snapshots.append(
                (
                    player.id,
                    f"{player.first_name} {player.last_name}",
                    player.price,
                    player.market_value,
                    days_listed,
                    timestamp,
                    hour,
                )
            )

        # Insert snapshots
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                """
                INSERT INTO price_snapshots
                (player_id, player_name, asking_price, market_value, days_listed, timestamp, hour)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                snapshots,
            )
            conn.commit()

        return len(snapshots)

    def detect_price_adjustments(self, market_players: list) -> list[PriceAdjustment]:
        """
        Detect price changes by comparing current prices to last snapshot

        Args:
            market_players: List of MarketPlayer objects

        Returns:
            List of detected price adjustments
        """
        adjustments = []
        now = datetime.now()

        with sqlite3.connect(self.db_path) as conn:
            for player in market_players:
                # Get most recent snapshot for this player
                cursor = conn.execute(
                    """
                    SELECT asking_price, days_listed, timestamp
                    FROM price_snapshots
                    WHERE player_id = ?
                    ORDER BY timestamp DESC
                    LIMIT 1
                """,
                    (player.id,),
                )

                row = cursor.fetchone()
                if not row:
                    continue  # No previous snapshot

                old_price, old_days_listed, old_timestamp = row
                current_price = player.price

                # Check if price changed
                if current_price != old_price:
                    price_change = current_price - old_price
                    price_change_pct = (price_change / old_price * 100) if old_price > 0 else 0

                    # Calculate current days listed
                    days_listed = 0
                    if hasattr(player, "listed_at") and player.listed_at:
                        try:
                            listed_dt = datetime.fromisoformat(
                                player.listed_at.replace("Z", "+00:00")
                            )
                            days_listed = (datetime.now(listed_dt.tzinfo) - listed_dt).days
                        except Exception:
                            pass

                    # Determine adjustment type
                    if price_change < 0:
                        adj_type = "decrease"
                    elif price_change > 0:
                        adj_type = "increase"
                    else:
                        adj_type = "unchanged"

                    adjustment = PriceAdjustment(
                        id=None,
                        player_id=player.id,
                        player_name=f"{player.first_name} {player.last_name}",
                        old_price=old_price,
                        new_price=current_price,
                        price_change=price_change,
                        price_change_pct=price_change_pct,
                        days_listed=days_listed,
                        market_value=player.market_value,
                        detected_at=now.isoformat(),
                        adjustment_type=adj_type,
                    )

                    adjustments.append(adjustment)

        # Record adjustments to database
        if adjustments:
            self._record_adjustments(adjustments)

        return adjustments

    def _record_adjustments(self, adjustments: list[PriceAdjustment]):
        """Record price adjustments to database"""
        records = [
            (
                adj.player_id,
                adj.player_name,
                adj.old_price,
                adj.new_price,
                adj.price_change,
                adj.price_change_pct,
                adj.days_listed,
                adj.market_value,
                adj.detected_at,
                adj.adjustment_type,
            )
            for adj in adjustments
        ]

        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                """
                INSERT INTO price_adjustments
                (player_id, player_name, old_price, new_price, price_change,
                 price_change_pct, days_listed, market_value, detected_at, adjustment_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                records,
            )
            conn.commit()

    def learn_adjustment_patterns(self) -> list[PriceAdjustmentPattern]:
        """
        Learn patterns about price adjustments from historical data

        Returns:
            List of patterns grouped by days_listed
        """
        patterns = []

        with sqlite3.connect(self.db_path) as conn:
            # Group by days_listed and calculate statistics
            cursor = conn.execute(
                """
                SELECT
                    days_listed,
                    AVG(price_change_pct) as avg_change_pct,
                    COUNT(*) as sample_count,
                    SUM(CASE WHEN adjustment_type = 'decrease' THEN 1 ELSE 0 END) as decrease_count,
                    AVG(CASE WHEN adjustment_type = 'decrease' THEN price_change_pct ELSE NULL END) as avg_decrease_pct
                FROM price_adjustments
                WHERE days_listed >= 0 AND days_listed <= 14
                GROUP BY days_listed
                HAVING sample_count >= 3
                ORDER BY days_listed
            """
            )

            for row in cursor:
                days_listed, avg_change, sample_count, decrease_count, avg_decrease = row

                decrease_probability = (decrease_count / sample_count) if sample_count > 0 else 0.0

                # Get median (approximate using percentile)
                median_cursor = conn.execute(
                    """
                    SELECT price_change_pct
                    FROM price_adjustments
                    WHERE days_listed = ?
                    ORDER BY price_change_pct
                    LIMIT 1 OFFSET (SELECT COUNT(*)/2 FROM price_adjustments WHERE days_listed = ?)
                """,
                    (days_listed, days_listed),
                )
                median_row = median_cursor.fetchone()
                median_change = median_row[0] if median_row else avg_change

                pattern = PriceAdjustmentPattern(
                    days_listed=int(days_listed),
                    avg_adjustment_pct=float(avg_change) if avg_change else 0.0,
                    median_adjustment_pct=float(median_change) if median_change else 0.0,
                    sample_count=int(sample_count),
                    decrease_probability=float(decrease_probability),
                    typical_decrease_pct=float(avg_decrease) if avg_decrease else 0.0,
                )

                patterns.append(pattern)

        return patterns

    def predict_price_adjustment(self, player, days_listed: int) -> dict[str, float | None] | None:
        """
        Predict likely price adjustment based on learned patterns

        Args:
            player: MarketPlayer object
            days_listed: Days player has been on market

        Returns:
            Dict with prediction or None if insufficient data
        """
        patterns = self.learn_adjustment_patterns()

        # Find pattern for this days_listed value
        matching_pattern = None
        for pattern in patterns:
            if pattern.days_listed == days_listed:
                matching_pattern = pattern
                break

        if not matching_pattern:
            # Try nearest neighbor
            if patterns:
                closest = min(patterns, key=lambda p: abs(p.days_listed - days_listed))
                if abs(closest.days_listed - days_listed) <= 2:
                    matching_pattern = closest

        if not matching_pattern or matching_pattern.sample_count < 5:
            return None

        # Calculate prediction
        expected_change_pct = matching_pattern.avg_adjustment_pct
        decrease_probability = matching_pattern.decrease_probability

        # Estimate new price
        current_price = player.price
        predicted_price = int(current_price * (1 + expected_change_pct / 100))

        return {
            "expected_change_pct": expected_change_pct,
            "predicted_price": predicted_price,
            "decrease_probability": decrease_probability,
            "confidence": min(matching_pattern.sample_count / 20, 1.0),  # Max at 20 samples
            "based_on_samples": matching_pattern.sample_count,
        }

    def display_adjustment_patterns(self):
        """Display learned price adjustment patterns"""
        patterns = self.learn_adjustment_patterns()

        if not patterns:
            console.print("[yellow]No price adjustment patterns learned yet[/yellow]")
            console.print(
                "[dim]Price patterns will be learned as the bot tracks market prices[/dim]"
            )
            return

        console.print("\n[bold cyan]📊 Learned Price Adjustment Patterns[/bold cyan]")
        console.print("[dim]How asking prices change based on days listed on market[/dim]\n")

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Days Listed", justify="center", style="cyan")
        table.add_column("Avg Change", justify="right", style="yellow")
        table.add_column("Median Change", justify="right", style="yellow")
        table.add_column("Drop Prob.", justify="right", style="red")
        table.add_column("Typical Drop", justify="right", style="red")
        table.add_column("Samples", justify="right", style="dim")

        for pattern in patterns:
            # Color code changes
            avg_color = "red" if pattern.avg_adjustment_pct < 0 else "green"
            median_color = "red" if pattern.median_adjustment_pct < 0 else "green"

            table.add_row(
                f"{pattern.days_listed}d",
                f"[{avg_color}]{pattern.avg_adjustment_pct:+.1f}%[/{avg_color}]",
                f"[{median_color}]{pattern.median_adjustment_pct:+.1f}%[/{median_color}]",
                f"{pattern.decrease_probability*100:.0f}%",
                f"{pattern.typical_decrease_pct:.1f}%",
                str(pattern.sample_count),
            )

        console.print(table)

        # Add insights
        console.print("\n[bold]💡 Insights:[/bold]")

        # Find highest risk days
        high_risk = [p for p in patterns if p.decrease_probability > 0.6]
        if high_risk:
            days = ", ".join([f"{p.days_listed}d" for p in high_risk])
            console.print(f"  • High price drop risk at: {days}")

        # Find safe days
        safe_days = [p for p in patterns if p.decrease_probability < 0.3]
        if safe_days:
            days = ", ".join([f"{p.days_listed}d" for p in safe_days[:3]])
            console.print(f"  • Price typically stable at: {days}")

        console.print()

    def get_recent_adjustments(self, hours: int = 24) -> list[PriceAdjustment]:
        """Get price adjustments from the last N hours"""
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()

        adjustments = []
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT
                    id, player_id, player_name, old_price, new_price,
                    price_change, price_change_pct, days_listed, market_value,
                    detected_at, adjustment_type
                FROM price_adjustments
                WHERE detected_at >= ?
                ORDER BY detected_at DESC
            """,
                (cutoff,),
            )

            for row in cursor:
                adjustment = PriceAdjustment(
                    id=row[0],
                    player_id=row[1],
                    player_name=row[2],
                    old_price=row[3],
                    new_price=row[4],
                    price_change=row[5],
                    price_change_pct=row[6],
                    days_listed=row[7],
                    market_value=row[8],
                    detected_at=row[9],
                    adjustment_type=row[10],
                )
                adjustments.append(adjustment)

        return adjustments

    def display_recent_adjustments(self, hours: int = 24):
        """Display recent price adjustments"""
        adjustments = self.get_recent_adjustments(hours)

        if not adjustments:
            console.print(f"[yellow]No price adjustments detected in last {hours} hours[/yellow]")
            return

        console.print(f"\n[bold cyan]📉 Recent Price Adjustments (Last {hours}h)[/bold cyan]\n")

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Player", style="cyan")
        table.add_column("Old Price", justify="right", style="dim")
        table.add_column("New Price", justify="right", style="yellow")
        table.add_column("Change", justify="right")
        table.add_column("Days Listed", justify="center", style="blue")
        table.add_column("Time", style="dim")

        for adj in adjustments:
            # Color code change
            if adj.adjustment_type == "decrease":
                change_color = "red"
            elif adj.adjustment_type == "increase":
                change_color = "green"
            else:
                change_color = "yellow"

            # Format timestamp
            try:
                dt = datetime.fromisoformat(adj.detected_at)
                time_str = dt.strftime("%H:%M")
            except Exception:
                time_str = adj.detected_at

            table.add_row(
                adj.player_name,
                f"€{adj.old_price:,}",
                f"€{adj.new_price:,}",
                f"[{change_color}]{adj.price_change_pct:+.1f}%[/{change_color}]",
                f"{adj.days_listed}d",
                time_str,
            )

        console.print(table)
        console.print()
