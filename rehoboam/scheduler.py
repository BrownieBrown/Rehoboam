"""Automated trading scheduler - Runs trading sessions periodically"""

import logging
import signal
import time
from datetime import datetime, timedelta
from datetime import time as dt_time
from pathlib import Path

from .api import KickbaseAPI
from .auto_trader import AutoTrader
from .config import get_settings

# Set up logging
log_dir = Path.home() / ".rehoboam" / "logs"
log_dir.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(log_dir / f"auto_trader_{datetime.now().strftime('%Y%m%d')}.log"),
        logging.StreamHandler(),
    ],
)

logger = logging.getLogger(__name__)


class TradingScheduler:
    """Runs automated trading sessions on a schedule"""

    def __init__(
        self,
        interval_minutes: int = 120,  # Run every 2 hours by default
        trading_hours_start: int = 8,  # Start at 8 AM
        trading_hours_end: int = 22,  # End at 10 PM
        dry_run: bool = False,
        max_trades_per_session: int = 3,
        max_daily_spend: int = 50_000_000,
    ):
        """
        Args:
            interval_minutes: Minutes between trading sessions
            trading_hours_start: Hour to start trading (0-23)
            trading_hours_end: Hour to stop trading (0-23)
            dry_run: If True, simulate trades without executing
            max_trades_per_session: Max trades per run
            max_daily_spend: Max spend per day
        """
        self.interval_minutes = interval_minutes
        self.trading_hours_start = trading_hours_start
        self.trading_hours_end = trading_hours_end
        self.dry_run = dry_run
        self.max_trades_per_session = max_trades_per_session
        self.max_daily_spend = max_daily_spend

        self.running = False
        self.api: KickbaseAPI | None = None
        self.auto_trader: AutoTrader | None = None

        # Set up signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        logger.info(f"Received signal {signum}, shutting down...")
        self.running = False

    def _is_trading_hours(self) -> bool:
        """Check if current time is within trading hours"""
        now = datetime.now().time()
        start = dt_time(self.trading_hours_start, 0)
        end = dt_time(self.trading_hours_end, 0)

        return start <= now <= end

    def _initialize(self):
        """Initialize API and auto trader"""
        logger.info("Initializing trading bot...")

        settings = get_settings()
        self.api = KickbaseAPI(email=settings.kickbase_email, password=settings.kickbase_password)

        # Login
        logger.info("Logging in to KICKBASE...")
        self.api.login()
        logger.info("✓ Logged in successfully")

        # Initialize auto trader
        self.auto_trader = AutoTrader(
            api=self.api,
            settings=settings,
            max_trades_per_session=self.max_trades_per_session,
            max_daily_spend=self.max_daily_spend,
            dry_run=self.dry_run,
        )

        logger.info("✓ Initialization complete")

    def _run_trading_session(self):
        """Run a single trading session"""
        if not self.api or not self.auto_trader:
            logger.error("Bot not initialized")
            return

        try:
            # Get first league
            leagues = self.api.get_leagues()
            if not leagues:
                logger.warning("No leagues found")
                return

            league = leagues[0]
            logger.info(f"Running trading session for league: {league.name}")

            # Run full session
            session = self.auto_trader.run_full_session(league)

            # Log results
            logger.info("Session complete:")
            logger.info(
                f"  Profit trades: {len([r for r in session.profit_trades if r.success])}/{len(session.profit_trades)}"
            )
            logger.info(
                f"  Lineup trades: {len([r for r in session.lineup_trades if r.success])}/{len(session.lineup_trades)}"
            )
            logger.info(f"  Total spent: €{session.total_spent:,}")
            logger.info(f"  Total earned: €{session.total_earned:,}")
            logger.info(f"  Net change: €{session.net_change:,}")

            if session.errors:
                logger.warning(f"Errors occurred: {len(session.errors)}")
                for err in session.errors:
                    logger.error(f"  {err}")

        except Exception as e:
            logger.error(f"Trading session failed: {str(e)}", exc_info=True)

    def run(self):
        """Start the scheduler (runs indefinitely)"""
        logger.info("=" * 70)
        logger.info("Rehoboam Automated Trading Scheduler")
        logger.info("=" * 70)
        logger.info(f"Interval: Every {self.interval_minutes} minutes")
        logger.info(f"Trading hours: {self.trading_hours_start}:00 - {self.trading_hours_end}:00")
        logger.info(f"Max trades/session: {self.max_trades_per_session}")
        logger.info(f"Max daily spend: €{self.max_daily_spend:,}")
        if self.dry_run:
            logger.info("DRY RUN MODE - No trades will be executed")
        logger.info("=" * 70)

        # Initialize
        try:
            self._initialize()
        except Exception as e:
            logger.error(f"Initialization failed: {str(e)}", exc_info=True)
            return

        self.running = True
        next_run = datetime.now()

        logger.info("Scheduler started. Press Ctrl+C to stop.")

        while self.running:
            try:
                now = datetime.now()

                # Check if it's time to run
                if now >= next_run:
                    # Check trading hours
                    if self._is_trading_hours():
                        logger.info(f"\n{'-' * 70}")
                        logger.info(
                            f"Starting trading session at {now.strftime('%Y-%m-%d %H:%M:%S')}"
                        )
                        logger.info(f"{'-' * 70}")

                        self._run_trading_session()

                        # Schedule next run
                        next_run = now + timedelta(minutes=self.interval_minutes)
                        logger.info(
                            f"Next run scheduled for: {next_run.strftime('%Y-%m-%d %H:%M:%S')}"
                        )
                    else:
                        logger.info("Outside trading hours, waiting...")
                        # Check again in 30 minutes
                        next_run = now + timedelta(minutes=30)

                # Sleep for a bit
                time.sleep(60)  # Check every minute

            except KeyboardInterrupt:
                logger.info("Received keyboard interrupt, shutting down...")
                break
            except Exception as e:
                logger.error(f"Unexpected error: {str(e)}", exc_info=True)
                # Wait before retrying
                time.sleep(300)  # 5 minutes

        logger.info("Scheduler stopped")


def run_scheduler_cli():
    """CLI entry point for scheduler"""
    import argparse

    parser = argparse.ArgumentParser(description="Rehoboam Automated Trading Scheduler")
    parser.add_argument(
        "--interval", type=int, default=120, help="Minutes between sessions (default: 120)"
    )
    parser.add_argument("--start-hour", type=int, default=8, help="Trading start hour (default: 8)")
    parser.add_argument("--end-hour", type=int, default=22, help="Trading end hour (default: 22)")
    parser.add_argument(
        "--max-trades", type=int, default=3, help="Max trades per session (default: 3)"
    )
    parser.add_argument(
        "--max-daily-spend", type=int, default=50_000_000, help="Max daily spend (default: 50M)"
    )
    parser.add_argument("--dry-run", action="store_true", help="Simulate trades without executing")

    args = parser.parse_args()

    scheduler = TradingScheduler(
        interval_minutes=args.interval,
        trading_hours_start=args.start_hour,
        trading_hours_end=args.end_hour,
        dry_run=args.dry_run,
        max_trades_per_session=args.max_trades,
        max_daily_spend=args.max_daily_spend,
    )

    scheduler.run()


if __name__ == "__main__":
    run_scheduler_cli()
