"""AWS Lambda handler for automated trading with Telegram notifications"""

import json
import os
import sys
import traceback
from pathlib import Path

# Add rehoboam to path
sys.path.insert(0, str(Path(__file__).parent))

# Lambda only allows writes to /tmp. Redirect 'logs' directory.
os.makedirs("/tmp/logs", exist_ok=True)
os.chdir("/tmp")


def download_database_from_s3():
    """Download learning database from S3 if it exists"""
    import boto3
    from botocore.exceptions import ClientError

    s3_bucket = os.getenv("S3_BUCKET")
    if not s3_bucket:
        print("No S3_BUCKET configured - skipping database download")
        return

    s3 = boto3.client("s3")
    db_path = "/tmp/bid_learning.db"

    try:
        print(f"Downloading database from s3://{s3_bucket}/bid_learning.db...")
        s3.download_file(s3_bucket, "bid_learning.db", db_path)
        print("Database downloaded successfully")
    except ClientError as e:
        if e.response["Error"]["Code"] == "404":
            print("No existing database in S3 - will create new one")
        else:
            print(f"Warning: Could not download database: {e}")


def upload_database_to_s3():
    """Upload learning database to S3 for persistence"""
    import boto3

    s3_bucket = os.getenv("S3_BUCKET")
    if not s3_bucket:
        print("No S3_BUCKET configured - skipping database upload")
        return

    s3 = boto3.client("s3")
    db_path = "/tmp/bid_learning.db"

    if not os.path.exists(db_path):
        print("No database to upload")
        return

    try:
        print(f"Uploading database to s3://{s3_bucket}/bid_learning.db...")
        s3.upload_file(db_path, s3_bucket, "bid_learning.db")
        print("Database uploaded successfully")
    except Exception as e:
        print(f"Warning: Could not upload database: {e}")


def send_telegram_digest(api, settings, league, profit_results, lineup_results, dry_run):
    """Send a Telegram daily digest with trading session results"""
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not telegram_token or not telegram_chat_id:
        print("No Telegram credentials configured - skipping notification")
        return

    from rehoboam.telegram_notifier import TelegramNotifier
    from rehoboam.trader import Trader

    notifier = TelegramNotifier(bot_token=telegram_token, chat_id=telegram_chat_id)

    try:
        # Use Trader.analyze_market() for full analysis (trends, risk, matchups)
        trader = Trader(api, settings)
        print("Running full market analysis for Telegram digest...")
        all_analyses = trader.analyze_market(league, calculate_risk=True)

        # Filter for BUY recommendations, sorted by score
        buy_analyses = [a for a in all_analyses if a.recommendation == "BUY"]
        buy_analyses.sort(key=lambda a: a.value_score, reverse=True)
        buy_analyses = buy_analyses[:5]

        # Compute smart bids for each buy recommendation
        for analysis in buy_analyses:
            try:
                bid_rec = trader.bidding.calculate_bid(
                    asking_price=analysis.current_price,
                    market_value=analysis.market_value,
                    value_score=analysis.value_score,
                    confidence=analysis.confidence,
                )
                if analysis.metadata is None:
                    analysis.metadata = {}
                analysis.metadata["smart_bid"] = bid_rec.recommended_bid
            except Exception:
                pass

        # Get sell candidates from squad analysis
        sell_candidates = []
        try:
            squad = api.get_squad(league)
            sell_candidates = trader.analyzer.rank_squad_for_selling(squad)
        except Exception:
            pass

        # Combine all trade results for the digest
        all_trade_results = list(profit_results) + list(lineup_results)

        # Format and send digest
        digest = notifier.format_daily_digest(
            buy_analyses=buy_analyses,
            sell_candidates=sell_candidates,
            league_name=league.name,
            trade_results=all_trade_results if all_trade_results else None,
        )

        # Add session results header
        mode_str = "DRY RUN" if dry_run else "LIVE"
        profit_count = len([r for r in profit_results if r.success])
        lineup_count = len([r for r in lineup_results if r.success])

        session_header = notifier._escape_markdown(
            f"[{mode_str}] Session: {profit_count} profit + {lineup_count} lineup trades"
        )
        digest = f"{session_header}\n\n{digest}"

        success = notifier.send_message(digest)
        if success:
            print("Telegram digest sent successfully")
        else:
            print("Warning: Failed to send Telegram digest")

    except Exception as e:
        print(f"Warning: Could not send Telegram digest: {e}")
        import traceback as tb

        tb.print_exc()
        # Try to send a simpler error notification
        try:
            notifier.send_error_notification(f"Digest failed: {e}")
        except Exception:
            pass


def lambda_handler(event, context):
    """
    Lambda handler that runs the trading bot

    Triggered by EventBridge on a schedule:
    - Morning: 10:30 AM CET (after market values update)
    - Evening: 6:00 PM CET (check for evening opportunities)
    """
    from rehoboam.api import KickbaseAPI
    from rehoboam.auto_trader import AutoTrader
    from rehoboam.bid_learner import BidLearner
    from rehoboam.config import get_settings

    print("Starting Rehoboam trading session...")

    try:
        # Download learning database from S3
        download_database_from_s3()

        # Get settings from environment variables
        settings = get_settings()

        # Initialize API
        print("Logging in to Kickbase...")
        api = KickbaseAPI(settings.kickbase_email, settings.kickbase_password)
        api.login()
        print(f"Logged in as {api.user.name}")

        # Get league
        leagues = api.get_leagues()
        if not leagues:
            return {"statusCode": 400, "body": json.dumps("No leagues found")}

        # Use specified league
        league_index = int(os.getenv("LEAGUE_INDEX", "0"))
        league = leagues[league_index]
        print(f"Trading in league: {league.name}")

        # Initialize bid learner with Lambda-compatible path
        os.makedirs("/tmp/logs", exist_ok=True)
        learner = BidLearner(db_path=Path("/tmp/bid_learning.db"))

        # Initialize auto trader
        dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
        trader = AutoTrader(
            api=api,
            settings=settings,
            max_trades_per_session=5,
            max_daily_spend=50_000_000,
            dry_run=dry_run,
        )

        print("\nAuto-Trading: Profit Opportunities")
        profit_results = trader.run_profit_trading_session(league)
        print(f"Executed {len(profit_results)} profit trades")

        print("\nAuto-Trading: Lineup Improvements")
        lineup_results = trader.run_lineup_improvement_session(league)
        print(f"Executed {len(lineup_results)} lineup trades")

        # Upload learning database to S3 for next run
        upload_database_to_s3()

        # Send Telegram daily digest
        send_telegram_digest(api, settings, league, profit_results, lineup_results, dry_run)

        # Get learning statistics
        stats = learner.get_statistics()

        result = {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": "Trading session completed successfully",
                    "league": league.name,
                    "dry_run": dry_run,
                    "profit_trades": len(profit_results),
                    "lineup_trades": len(lineup_results),
                    "learning_stats": {
                        "total_auctions": stats["total_auctions"],
                        "win_rate": stats["win_rate"],
                    },
                }
            ),
        }

        print(
            f"\nSession complete! {len(profit_results)} profit + {len(lineup_results)} lineup trades"
        )
        return result

    except Exception as e:
        error_msg = f"Error: {e!s}"
        print(f"FAILED: {error_msg}")
        traceback.print_exc()

        # Try to send error notification via Telegram
        telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
        telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        if telegram_token and telegram_chat_id:
            try:
                from rehoboam.telegram_notifier import TelegramNotifier

                notifier = TelegramNotifier(bot_token=telegram_token, chat_id=telegram_chat_id)
                notifier.send_error_notification(str(e))
            except Exception:
                pass

        return {
            "statusCode": 500,
            "body": json.dumps({"message": "Trading session failed", "error": str(e)}),
        }
