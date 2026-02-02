"""AWS Lambda handler for daily analyze notifications via Telegram

This handler runs market analysis and sends results to Telegram.
Designed to run daily at 9am CET via EventBridge schedule.

EventBridge rule: cron(0 8 * * ? *) = 8am UTC = 9am CET (winter)
                  cron(0 7 * * ? *) = 7am UTC = 9am CEST (summer)
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
    Lambda handler for daily market analysis with Telegram notifications.

    Environment Variables Required:
        KICKBASE_EMAIL: Your Kickbase account email
        KICKBASE_PASSWORD: Your Kickbase account password
        TELEGRAM_BOT_TOKEN: Telegram bot token from @BotFather
        TELEGRAM_CHAT_ID: Target Telegram chat ID

    Optional Environment Variables:
        MIN_VALUE_SCORE_TO_BUY: Minimum score for buy recommendations (default: 50.0)
        MAX_PLAYER_COST: Maximum spend on single player (default: 5000000)

    Returns:
        dict: Execution summary with analysis results
    """
    logger.info("Starting Rehoboam daily analyze Lambda execution")

    try:
        # Import here to minimize cold start impact
        from rehoboam.api import KickbaseAPI
        from rehoboam.config import Settings
        from rehoboam.telegram_notifier import SquadSummary, TelegramNotifier
        from rehoboam.trader import Trader
        from rehoboam.value_calculator import PlayerValue

        # Configure paths for Lambda environment
        tmp_dir = Path("/tmp/rehoboam")
        tmp_dir.mkdir(parents=True, exist_ok=True)
        os.environ["REHOBOAM_DB_PATH"] = str(tmp_dir)

        # Validate Telegram credentials
        telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
        telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")

        if not telegram_token or not telegram_chat_id:
            logger.error("Missing Telegram credentials in environment variables")
            return {
                "statusCode": 400,
                "body": json.dumps(
                    {
                        "error": "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID environment variables are required"
                    }
                ),
            }

        # Initialize Telegram notifier
        notifier = TelegramNotifier(telegram_token, telegram_chat_id)

        # Initialize Kickbase settings
        settings = Settings(
            kickbase_email=os.getenv("KICKBASE_EMAIL"),
            kickbase_password=os.getenv("KICKBASE_PASSWORD"),
            dry_run=True,  # Always dry run for analyze
            min_value_score_to_buy=float(os.getenv("MIN_VALUE_SCORE_TO_BUY", "50.0")),
            max_player_cost=int(os.getenv("MAX_PLAYER_COST", "5000000")),
            reserve_budget=int(os.getenv("RESERVE_BUDGET", "1000000")),
            min_sell_profit_pct=float(os.getenv("MIN_SELL_PROFIT_PCT", "5.0")),
            max_loss_pct=float(os.getenv("MAX_LOSS_PCT", "-3.0")),
        )

        if not settings.kickbase_email or not settings.kickbase_password:
            logger.error("Missing Kickbase credentials")
            notifier.send_error_notification("Missing Kickbase credentials")
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "KICKBASE_EMAIL and KICKBASE_PASSWORD are required"}),
            }

        # Initialize API and login
        logger.info(f"Logging in as {settings.kickbase_email}")
        api = KickbaseAPI(settings.kickbase_email, settings.kickbase_password)
        api.login()

        # Get leagues
        leagues = api.get_leagues()
        if not leagues:
            logger.error("No leagues found")
            notifier.send_error_notification("No leagues found for this account")
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "No leagues found"}),
            }

        # Use first league
        league = leagues[0]
        logger.info(f"Analyzing league: {league.name}")

        # Initialize Trader
        trader = Trader(api=api, settings=settings, verbose=False)

        # Run market analysis
        logger.info("Running market analysis")
        market_analyses = trader.analyze_market(league, calculate_risk=True)

        # Filter to BUY recommendations
        buy_analyses = [a for a in market_analyses if a.recommendation == "BUY"]
        buy_analyses = trader.analyzer.find_best_opportunities(market_analyses, top_n=5)
        logger.info(f"Found {len(buy_analyses)} buy opportunities")

        # Get squad and analyze sell candidates
        squad = api.get_squad(league)
        team_info = api.get_team_info(league)
        current_budget = team_info.get("budget", 0)
        team_value = sum(p.market_value for p in squad)

        # Calculate player values and fetch stats
        player_values = {}
        player_stats = {}
        for player in squad:
            try:
                pv = PlayerValue.calculate(player)
                player_values[player.id] = pv.value_score
            except Exception:
                player_values[player.id] = 0.0

            try:
                history = api.client.get_player_market_value_history_v2(
                    player_id=player.id, timeframe=30
                )
                player_stats[player.id] = history
            except Exception:
                player_stats[player.id] = None

        # Get best eleven for protection
        try:
            lineup = api.client.get_best_eleven(league_id=league.id)
            best_eleven_ids = set(lineup.get("it", []))
        except Exception:
            best_eleven_ids = set()

        # Count positions
        from rehoboam.config import POSITION_MINIMUMS

        position_counts = {}
        for player in squad:
            pos = player.position
            position_counts[pos] = position_counts.get(pos, 0) + 1

        # Rank squad for selling
        sell_candidates, _ = trader.analyzer.rank_squad_for_selling(
            squad=squad,
            player_stats=player_stats,
            player_values=player_values,
            best_eleven_ids=best_eleven_ids,
            position_counts=position_counts,
            current_budget=current_budget,
        )

        # Filter to urgent sells
        urgent_sells = [s for s in sell_candidates if s.expendability_score >= 60]
        logger.info(f"Found {len(urgent_sells)} urgent sell candidates")

        # Build squad summary
        position_gaps = []
        for pos, min_count in POSITION_MINIMUMS.items():
            current = position_counts.get(pos, 0)
            if current < min_count:
                position_gaps.append(f"Need {min_count - current} {pos[:3]}")

        # Estimate best eleven points (sum of average points for best 11)
        best_eleven_pts = 0
        if best_eleven_ids:
            for player in squad:
                if player.id in best_eleven_ids:
                    best_eleven_pts += int(player.average_points)

        squad_summary = SquadSummary(
            budget=current_budget,
            team_value=team_value,
            best_eleven_points=best_eleven_pts,
            position_gaps=position_gaps,
        )

        # Format and send digest
        logger.info("Formatting Telegram message")
        message = notifier.format_daily_digest(
            buy_analyses=buy_analyses,
            sell_candidates=sell_candidates[:5] if sell_candidates else None,
            squad_summary=squad_summary,
            league_name=league.name,
        )

        logger.info("Sending Telegram notification")
        success = notifier.send_message(message)

        if success:
            logger.info("Telegram notification sent successfully")
        else:
            logger.warning("Failed to send Telegram notification")

        # Build summary
        summary = {
            "league": league.name,
            "buy_opportunities": len(buy_analyses),
            "urgent_sells": len(urgent_sells),
            "squad_size": len(squad),
            "budget": current_budget,
            "team_value": team_value,
            "telegram_sent": success,
            "top_buys": [
                {
                    "player": f"{a.player.first_name} {a.player.last_name}",
                    "score": round(a.value_score, 1),
                    "price": a.current_price,
                }
                for a in buy_analyses[:3]
            ],
        }

        return {"statusCode": 200, "body": json.dumps(summary, indent=2)}

    except Exception as e:
        logger.error(f"Lambda execution failed: {str(e)}", exc_info=True)

        # Try to send error notification
        try:
            telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
            telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
            if telegram_token and telegram_chat_id:
                notifier = TelegramNotifier(telegram_token, telegram_chat_id)
                notifier.send_error_notification(f"Daily analyze failed: {str(e)}")
        except Exception:
            pass  # Don't fail the whole Lambda if error notification fails

        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}


def test_handler():
    """Test handler locally before deploying to Lambda"""
    from dotenv import load_dotenv

    load_dotenv()

    # Simulate Lambda event and context
    event = {}

    class Context:
        def __init__(self):
            self.function_name = "rehoboam-analyze-local-test"
            self.memory_limit_in_mb = 512
            self.invoked_function_arn = "arn:aws:lambda:local:000000000000:function:test"
            self.aws_request_id = "test-request-id"

    context = Context()

    print("\n" + "=" * 80)
    print("TESTING ANALYZE LAMBDA HANDLER LOCALLY")
    print("=" * 80 + "\n")

    result = lambda_handler(event, context)

    print("\n" + "=" * 80)
    print("LAMBDA EXECUTION RESULT")
    print("=" * 80)

    if result["statusCode"] == 200:
        body = json.loads(result["body"])
        print(json.dumps(body, indent=2))
        print("\n" + "=" * 80)
        print("SUCCESS")
        print("=" * 80)
    else:
        print(f"ERROR: {result['body']}")
        print("=" * 80)

    return result


if __name__ == "__main__":
    test_handler()
