"""AWS Lambda handler for automated Rehoboam trading bot

This handler runs the auto-trader with full competitive intelligence on a schedule via EventBridge.
Designed to work within AWS Lambda's constraints (15-minute timeout, /tmp storage).

✅ UPDATED: Now includes activity feed learning and competitive intelligence!
"""

import json
import logging
import os
from pathlib import Path

# Configure logging for CloudWatch
logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    """
    Lambda handler for scheduled trading bot execution with competitive intelligence

    Environment Variables Required:
        KICKBASE_EMAIL: Your Kickbase account email
        KICKBASE_PASSWORD: Your Kickbase account password
        DRY_RUN: Set to 'false' to enable real trading (default: 'true')
        MIN_VALUE_SCORE_TO_BUY: Minimum score for buy recommendations (default: 50.0)
        MAX_PLAYER_COST: Maximum spend on single player (default: 5000000)
        RESERVE_BUDGET: Budget to keep in reserve (default: 1000000)
        MIN_SELL_PROFIT_PCT: Minimum profit % to sell (default: 5.0)
        MAX_LOSS_PCT: Maximum loss % to accept (default: -3.0)

    Returns:
        dict: Execution summary with trades made and analysis results
    """
    logger.info("Starting Rehoboam trading bot Lambda execution with competitive intelligence")

    try:
        # Import here to minimize cold start impact
        from rehoboam.api import KickbaseAPI
        from rehoboam.auto_trader import AutoTrader
        from rehoboam.config import Settings

        # Configure paths for Lambda environment
        # Lambda has writable /tmp directory (512MB-10GB, ephemeral)
        tmp_dir = Path("/tmp/rehoboam")
        tmp_dir.mkdir(parents=True, exist_ok=True)

        # Set database path for Lambda
        os.environ["REHOBOAM_DB_PATH"] = str(tmp_dir)

        logger.info("Initializing trading components with competitive intelligence")

        # Use environment variables for configuration
        settings = Settings(
            kickbase_email=os.getenv("KICKBASE_EMAIL"),
            kickbase_password=os.getenv("KICKBASE_PASSWORD"),
            dry_run=os.getenv("DRY_RUN", "true").lower() == "true",
            min_value_score_to_buy=float(os.getenv("MIN_VALUE_SCORE_TO_BUY", "50.0")),
            max_player_cost=int(os.getenv("MAX_PLAYER_COST", "5000000")),
            reserve_budget=int(os.getenv("RESERVE_BUDGET", "1000000")),
            min_sell_profit_pct=float(os.getenv("MIN_SELL_PROFIT_PCT", "5.0")),
            max_loss_pct=float(os.getenv("MAX_LOSS_PCT", "-3.0")),
        )

        if not settings.kickbase_email or not settings.kickbase_password:
            logger.error("Missing credentials in environment variables")
            return {
                "statusCode": 400,
                "body": json.dumps(
                    {
                        "error": "KICKBASE_EMAIL and KICKBASE_PASSWORD environment variables are required"
                    }
                ),
            }

        # Initialize API client
        api = KickbaseAPI(settings.kickbase_email, settings.kickbase_password)
        logger.info(f"Logging in as {settings.kickbase_email}")
        api.login()

        # Get leagues
        leagues = api.get_leagues()
        if not leagues:
            logger.error("No leagues found")
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "No leagues found for this account"}),
            }

        # Use first league (or could iterate through all)
        league = leagues[0]
        logger.info(f"Processing league: {league.name}")

        # Initialize AutoTrader with competitive intelligence
        # This includes:
        # - Activity feed learning (learns from all league transfers)
        # - Competitor analysis (tracks Eduard, Chris, etc.)
        # - Smart bidding (adjusts for league competitiveness)
        # - Bid learning (learns from your win/loss patterns)
        auto_trader = AutoTrader(
            api=api,
            settings=settings,
            max_trades_per_session=5,  # Safety limit
            max_daily_spend=50_000_000,  # €50M max per day
            dry_run=settings.dry_run,
        )

        logger.info(f"Auto-trading mode: {'DRY RUN' if settings.dry_run else 'LIVE TRADING'}")

        # Run full trading session
        # This will:
        # 1. Sync activity feed for competitive intelligence
        # 2. Check resolved auctions for learning
        # 3. Run squad optimization (sell excess players)
        # 4. Execute profit trading opportunities
        # 5. Execute lineup improvement trades
        session_result = auto_trader.run_full_session(league)

        # Prepare summary
        profit_trades = [
            {
                "action": t.action,
                "player": t.player_name,
                "price": t.price,
                "reason": t.reason,
                "success": t.success,
            }
            for t in session_result.profit_trades
        ]

        lineup_trades = [
            {
                "action": t.action,
                "player": t.player_name,
                "price": t.price,
                "reason": t.reason,
                "success": t.success,
            }
            for t in session_result.lineup_trades
        ]

        summary = {
            "league": league.name,
            "dry_run": settings.dry_run,
            "duration_seconds": round(session_result.end_time - session_result.start_time, 1),
            "profit_trades": profit_trades,
            "lineup_trades": lineup_trades,
            "total_spent": session_result.total_spent,
            "total_earned": session_result.total_earned,
            "net_change": session_result.net_change,
            "errors": session_result.errors,
            "competitive_intelligence": "ENABLED",  # New feature!
        }

        logger.info(
            f"Session completed: {len(profit_trades)} profit trades, {len(lineup_trades)} lineup trades"
        )
        logger.info(f"Net change: €{session_result.net_change:,}")

        return {"statusCode": 200, "body": json.dumps(summary, indent=2)}

    except Exception as e:
        logger.error(f"Lambda execution failed: {str(e)}", exc_info=True)
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}


def test_handler():
    """Test handler locally before deploying to Lambda"""

    # Load .env file for local testing
    from dotenv import load_dotenv

    load_dotenv()

    # Simulate Lambda event and context
    event = {}

    class Context:
        def __init__(self):
            self.function_name = "rehoboam-bot-local-test"
            self.memory_limit_in_mb = 512
            self.invoked_function_arn = "arn:aws:lambda:local:000000000000:function:test"
            self.aws_request_id = "test-request-id"

    context = Context()

    print("\n" + "=" * 80)
    print("🤖 TESTING LAMBDA HANDLER LOCALLY")
    print("=" * 80 + "\n")

    # Run handler
    result = lambda_handler(event, context)

    print("\n" + "=" * 80)
    print("📊 LAMBDA EXECUTION RESULT")
    print("=" * 80)

    if result["statusCode"] == 200:
        body = json.loads(result["body"])
        print(json.dumps(body, indent=2))

        print("\n" + "=" * 80)
        print("✅ SUCCESS")
        print("=" * 80)

        if body.get("dry_run"):
            print("\n⚠️  DRY RUN MODE - No real trades were executed")
            print("Set DRY_RUN=false in environment variables to enable real trading")
    else:
        print(f"❌ ERROR: {result['body']}")
        print("=" * 80)

    return result


if __name__ == "__main__":
    # For local testing: python lambda_handler.py
    test_handler()
