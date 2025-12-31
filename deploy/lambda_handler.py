"""AWS Lambda handler for automated trading"""

import json
import os
import sys
from pathlib import Path

# Add rehoboam to path
sys.path.insert(0, str(Path(__file__).parent))


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
        print("✓ Database downloaded successfully")
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
        print("✓ Database uploaded successfully")
    except Exception as e:
        print(f"Warning: Could not upload database: {e}")


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

    print("🤖 Starting Rehoboam trading session...")

    try:
        # Download learning database from S3
        download_database_from_s3()

        # Get settings from environment variables
        settings = get_settings()

        # Initialize API
        print("Logging in to Kickbase...")
        api = KickbaseAPI(settings.kickbase_email, settings.kickbase_password)
        api.login()
        print(f"✓ Logged in as {api.user.name}")

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
        dry_run = os.getenv("DRY_RUN", "false").lower() == "true"
        trader = AutoTrader(
            api=api,
            settings=settings,
            max_trades_per_session=5,
            max_daily_spend=50_000_000,
            dry_run=dry_run,
            learner=learner,
        )

        print("\n🤖 Auto-Trading: Profit Opportunities")
        # Run profit trading session
        profit_results = trader.run_profit_trading_session(league)
        print(f"✓ Executed {len(profit_results)} profit trades")

        print("\n🤖 Auto-Trading: Lineup Improvements")
        # Run lineup improvement session
        lineup_results = trader.run_lineup_improvement_session(league)
        print(f"✓ Executed {len(lineup_results)} lineup trades")

        # Upload learning database to S3 for next run
        upload_database_to_s3()

        # Get learning statistics
        stats = learner.get_statistics()

        result = {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": "Trading session completed successfully",
                    "league": league.name,
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
            f"\n✅ Session complete! {len(profit_results)} profit + {len(lineup_results)} lineup trades"
        )
        return result

    except Exception as e:
        error_msg = f"Error: {str(e)}"
        print(f"❌ {error_msg}")
        import traceback

        traceback.print_exc()

        return {
            "statusCode": 500,
            "body": json.dumps({"message": "Trading session failed", "error": str(e)}),
        }
