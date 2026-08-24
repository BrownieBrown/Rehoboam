"""Azure Functions handler for automated Kickbase trading with lineup setting"""

import logging
import os
import sys
import time
from collections.abc import Collection
from datetime import datetime, timezone
from pathlib import Path

import azure.functions as func

app = func.FunctionApp()

# Add rehoboam to path (deployed as a subdirectory)
sys.path.insert(0, str(Path(__file__).parent))

# Azure Functions writable directory
TEMP_DIR = "/tmp"
LOGS_DIR = Path(TEMP_DIR) / "logs"


def _blob_settings() -> tuple[str | None, str]:
    return (
        os.getenv("AZURE_STORAGE_CONNECTION_STRING"),
        os.getenv("BLOB_CONTAINER", "rehoboam-data"),
    )


def download_databases(only: Collection[str] | None = None):
    """Download learning databases from Azure Blob Storage.

    ``only`` restricts the sync to a subset of the DB files (default: all of
    them, matching prior behaviour — ``trading_session`` relies on this).
    The telegram approval trigger passes ``only={"bid_learning.db"}``: that's
    the only file it reads or writes, and pulling the rest (player_history.db
    alone is 20MB+) on every callback risks a Telegram webhook timeout.
    """
    from rehoboam.azure_blob import fetch_state

    conn_str, container = _blob_settings()
    if not conn_str:
        logging.info("No AZURE_STORAGE_CONNECTION_STRING - skipping DB download")
        return

    results = fetch_state(conn_str, container, LOGS_DIR, backup=False, dry_run=False, only=only)
    for r in results:
        if r.status == "downloaded":
            logging.info(f"Downloaded {r.db_file} ({r.blob.size} bytes)")
        elif r.status == "missing_in_blob":
            logging.info(f"No existing {r.db_file} in blob storage - will create new")
        elif r.status == "error":
            logging.warning(f"Could not download {r.db_file}: {r.error}")


def upload_databases(only: Collection[str] | None = None) -> bool:
    """Upload learning databases to Azure Blob Storage.

    ``only`` restricts the sync to a subset of the DB files (default: all of
    them, matching prior behaviour — ``trading_session`` relies on this). See
    ``download_databases`` for why the telegram approval trigger passes
    ``only={"bid_learning.db"}``.

    Returns whether ``bid_learning.db`` specifically reached the blob. The
    timer trigger ignores this return value (backward compatible); the
    telegram approval trigger uses it because a caller that already spent
    money on the strength of a claim written to that file needs to know a
    per-file upload failure doesn't raise — ``push_state`` just records
    ``status="error"`` and returns normally.
    """
    from rehoboam.azure_blob import learning_db_synced, push_state

    conn_str, container = _blob_settings()
    if not conn_str:
        logging.info("No AZURE_STORAGE_CONNECTION_STRING - skipping DB upload")
        return True

    results = push_state(conn_str, container, LOGS_DIR, dry_run=False, only=only)
    for r in results:
        if r.status == "uploaded":
            logging.info(f"Uploaded {r.db_file} ({r.local_size} bytes)")
        elif r.status == "error":
            logging.warning(f"Could not upload {r.db_file}: {r.error}")
    return learning_db_synced(results)


def _send_daily_summary(api, league, settings, session):
    """Email the once-a-day picture. Best-effort: never raises into the timer."""
    from rehoboam.bid_learner import BidLearner
    from rehoboam.notify.email import send_email
    from rehoboam.notify.render import render_daily_summary
    from rehoboam.notify.telegram import send_message

    squad = api.get_squad(league)
    budget = int(api.get_team_info(league).get("budget", 0))
    market = sorted(
        api.get_market(league),
        key=lambda p: getattr(p, "average_points", 0.0) or 0.0,
        reverse=True,
    )[:10]

    learner = BidLearner()
    pending = [(p["player_name"], int(p["bid"])) for p in learner.pending_proposals()]

    executed = [
        f"{r.action} {r.player_name} for EUR {r.price:,}"
        for r in (session.profit_trades + session.lineup_trades)
        if r.success
    ]

    # A proposal Marco approved is executed by the webhook, in a different
    # invocation entirely — it appears in no session's results. Without this
    # the email would never mention a EUR 32M purchase he authorised, nor a
    # proposal the safety gate refused after he tapped approve.
    resolved = [
        p for p in learner.proposals_since(time.time() - 48 * 3600) if p["status"] != "pending"
    ]
    executed += [
        f"APPROVED {p['player_name']} for EUR {int(p['bid']):,}"
        for p in resolved
        if p["status"] == "executed"
    ]
    blocked = list(session.errors) + [
        f"proposal for {p['player_name']} ended as {p['status']}"
        for p in resolved
        if p["status"] in {"failed", "rejected"}
    ]

    body = render_daily_summary(
        lineup=session.lineup,
        squad_size=len(squad),
        budget=budget,
        market=[
            (p.last_name, int(p.market_value), float(getattr(p, "average_points", 0.0) or 0.0))
            for p in market
        ],
        pending=pending,
        executed=executed,
        rejections=blocked,
    )
    header = f"REHOBOAM DAILY — {len(pending)} awaiting approval\n\n"

    # Telegram is the primary channel: it is already configured for approvals,
    # costs nothing, and needs no mail provider. Proton — the alternative that
    # was considered — requires a paid plan plus a custom domain, and Proton
    # Bridge binds to localhost, which an Azure Function can never reach.
    if not send_message(settings.telegram_bot_token, settings.telegram_chat_id, header + body):
        logging.warning("daily summary: telegram delivery failed or not configured")

    # Email stays available for anyone who configures SMTP; absent config makes
    # this a no-op rather than an error.
    send_email(
        host=settings.smtp_host,
        port=settings.smtp_port,
        user=settings.smtp_user,
        password=settings.smtp_password,
        sender=settings.smtp_user,
        recipient=settings.alert_email_to,
        subject=f"Rehoboam daily — {len(pending)} awaiting approval",
        body=body,
    )


# Timer trigger: runs 2x daily at 08:00 and 20:00 UTC
# (10:00 and 22:00 Europe/Berlin in summer, 09:00 and 21:00 in winter)
@app.timer_trigger(
    schedule="0 0 8,20 * * *",
    arg_name="timer",
    run_on_startup=False,
)
def trading_session(timer: func.TimerRequest):
    """Run automated trading session on schedule"""
    from rehoboam.api import KickbaseAPI
    from rehoboam.auto_trader import AutoTrader
    from rehoboam.config import get_settings

    logging.info("Starting Rehoboam trading session...")

    # Work in /tmp (writable on Azure Functions)
    os.chdir(TEMP_DIR)
    os.makedirs(f"{TEMP_DIR}/logs", exist_ok=True)

    try:
        # Download databases from blob storage
        download_databases()

        # Initialize
        settings = get_settings()
        api = KickbaseAPI(settings.kickbase_email, settings.kickbase_password)
        api.login()
        logging.info(f"Logged in as {api.user.name}")

        # Get league
        leagues = api.get_leagues()
        if not leagues:
            logging.error("No leagues found")
            return

        league_index = int(os.getenv("LEAGUE_INDEX", "0"))
        league = leagues[league_index]
        logging.info(f"Trading in league: {league.name}")

        # Run trading session
        dry_run = os.getenv("DRY_RUN", "true").lower() == "true"

        # Mirror `rehoboam auto --aggressive` behaviour: higher trade cap,
        # lower EP upgrade threshold, bigger spend limit.
        # Set AGGRESSIVE=false in app settings to fall back to normal mode.
        aggressive = os.getenv("AGGRESSIVE", "true").lower() == "true"

        if aggressive:
            settings.min_ep_upgrade_threshold = max(settings.min_ep_upgrade_threshold - 2, 3.0)
            max_trades = settings.auto_max_trades_aggressive
            max_spend = 75_000_000
            logging.info(
                f"AGGRESSIVE MODE: EP threshold {settings.min_ep_upgrade_threshold:.0f}, "
                f"max {max_trades} trades, €{max_spend:,} spend limit"
            )
        else:
            max_trades = settings.auto_max_trades_normal
            max_spend = 50_000_000

        # Environment overrides take precedence
        max_trades = int(os.getenv("MAX_TRADES", str(max_trades)))

        trader = AutoTrader(
            api=api,
            settings=settings,
            max_trades_per_session=max_trades,
            max_daily_spend=max_spend,
            dry_run=dry_run,
        )

        session = trader.run_full_session(league)

        # Upload databases back to blob storage
        upload_databases()

        # Once-a-day owner summary — only the morning run emails, so the
        # inbox gets one message per day instead of two.
        if datetime.now(tz=timezone.utc).hour < 12:
            try:
                _send_daily_summary(api, league, settings, session)
            except Exception:
                logging.warning("daily summary failed", exc_info=True)

        mode = "DRY RUN" if dry_run else "LIVE"
        profit_ok = len([r for r in session.profit_trades if r.success])
        lineup_ok = len([r for r in session.lineup_trades if r.success])

        logging.info(
            f"Session complete [{mode}]: {profit_ok} profit + {lineup_ok} lineup trades, "
            f"net €{session.net_change:,}"
        )

        # Per-trade detail so we can see what the bot actually did.
        # The bot's internal Rich console output isn't captured by App Insights,
        # so we log each result here.
        for r in session.profit_trades + session.lineup_trades:
            status = "OK" if r.success else "FAIL"
            msg = f"  [{status}] {r.action} {r.player_name} " f"@ €{r.price:,} — {r.reason}"
            if r.error:
                msg += f" (error: {r.error})"
            logging.info(msg)

        if session.errors:
            for err in session.errors:
                logging.warning(f"Session error: {err}")

    except Exception as e:
        logging.error(f"Trading session failed: {e}", exc_info=True)


_APPROVAL_DB_FILES = {"bid_learning.db"}  # the only DB the approval path reads or writes


@app.route(route="telegram", auth_level=func.AuthLevel.FUNCTION)
def telegram_approval(req: func.HttpRequest) -> func.HttpResponse:
    """Telegram approval callbacks. Public endpoint — see notify/approval.py."""
    import json

    from rehoboam.api import KickbaseAPI
    from rehoboam.bid_learner import BidLearner
    from rehoboam.config import get_settings
    from rehoboam.notify.approval import authorize, build_callback_response, handle_callback

    os.chdir(TEMP_DIR)
    os.makedirs(f"{TEMP_DIR}/logs", exist_ok=True)

    try:
        body = req.get_json()
    except Exception:
        logging.warning("telegram approval: unparseable request body")
        body = {}

    def _respond(text: str) -> func.HttpResponse:
        logging.info("telegram approval: %s", text)
        return func.HttpResponse(
            json.dumps(build_callback_response(body, text)), mimetype="application/json"
        )

    # Authenticate before spending anything: a forged/unauthenticated caller
    # must not cost a blob round trip or a Kickbase login. get_settings() is
    # local/cheap; download_databases() and api.login() are not. This must
    # still return 200 (Telegram retries on anything else) even if
    # get_settings() itself blows up.
    try:
        settings = get_settings()
    except Exception:
        logging.exception("telegram approval: could not load settings")
        return _respond("Something went wrong — check the logs.")

    secret_header = req.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if not authorize(secret_header, settings.telegram_webhook_secret):
        logging.warning("telegram approval: unauthorized callback rejected before any work")
        return _respond("Unauthorized.")

    reply = "Something went wrong — check the logs."
    downloaded = False
    try:
        # Only bid_learning.db is read/written by this path — syncing the
        # rest (player_history.db alone is 20MB+) on every callback risks
        # exceeding Telegram's webhook timeout and triggering a retry.
        download_databases(only=_APPROVAL_DB_FILES)
        downloaded = True

        api = KickbaseAPI(settings.kickbase_email, settings.kickbase_password)
        api.login()

        league_index = int(os.getenv("LEAGUE_INDEX", "0"))
        leagues = api.get_leagues()
        if not leagues:
            logging.error("telegram approval: no leagues found")
            reply = "No leagues found."
        else:
            league = leagues[league_index]
            reply = handle_callback(
                body,
                secret_header,
                settings=settings,
                learner=BidLearner(),
                api=api,
                league=league,
            )
    except Exception:
        logging.exception("telegram approval: handler raised")
    finally:
        if not downloaded:
            # Never push a db we failed to pull: BidLearner would have created
            # an empty one, and uploading it would erase the real state.
            logging.error("telegram approval: skipping upload, download failed")
        else:
            try:
                if not upload_databases(only=_APPROVAL_DB_FILES):
                    reply = "NOT SAVED - do not tap again. " + reply
            except Exception:
                logging.exception("telegram approval: upload failed after handling")
                reply = "NOT SAVED - do not tap again. " + reply

    return _respond(reply)
