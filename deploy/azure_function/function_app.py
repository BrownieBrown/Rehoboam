"""Azure Functions handler for automated Kickbase trading with lineup setting"""

import logging
import os
import sys
from pathlib import Path

import azure.functions as func

app = func.FunctionApp()

# Add rehoboam to path (deployed as a subdirectory)
sys.path.insert(0, str(Path(__file__).parent))

# Azure Functions writable directory
TEMP_DIR = "/tmp"

# SQLite databases to persist in Azure Blob Storage
DB_FILES = [
    "bid_learning.db",
    "value_tracking.db",
    "market_prices.db",
    "player_history.db",
]


def download_databases():
    """Download learning databases from Azure Blob Storage"""
    connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    container_name = os.getenv("BLOB_CONTAINER", "rehoboam-data")

    if not connection_string:
        logging.info("No AZURE_STORAGE_CONNECTION_STRING - skipping DB download")
        return

    from azure.storage.blob import BlobServiceClient

    blob_service = BlobServiceClient.from_connection_string(connection_string)
    container = blob_service.get_container_client(container_name)

    os.makedirs(f"{TEMP_DIR}/logs", exist_ok=True)

    for db_file in DB_FILES:
        db_path = f"{TEMP_DIR}/logs/{db_file}"
        try:
            blob_client = container.get_blob_client(db_file)
            with open(db_path, "wb") as f:
                f.write(blob_client.download_blob().readall())
            logging.info(f"Downloaded {db_file}")
        except Exception as e:
            if "BlobNotFound" in str(e):
                logging.info(f"No existing {db_file} in blob storage - will create new")
            else:
                logging.warning(f"Could not download {db_file}: {e}")


def upload_databases():
    """Upload learning databases to Azure Blob Storage"""
    connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    container_name = os.getenv("BLOB_CONTAINER", "rehoboam-data")

    if not connection_string:
        logging.info("No AZURE_STORAGE_CONNECTION_STRING - skipping DB upload")
        return

    from azure.storage.blob import BlobServiceClient

    blob_service = BlobServiceClient.from_connection_string(connection_string)
    container = blob_service.get_container_client(container_name)

    for db_file in DB_FILES:
        db_path = f"{TEMP_DIR}/logs/{db_file}"
        if not os.path.exists(db_path):
            continue
        try:
            blob_client = container.get_blob_client(db_file)
            with open(db_path, "rb") as f:
                blob_client.upload_blob(f, overwrite=True)
            logging.info(f"Uploaded {db_file}")
        except Exception as e:
            logging.warning(f"Could not upload {db_file}: {e}")


def send_telegram_notification(message: str):
    """Send a Telegram notification"""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        return

    import requests

    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:
        logging.warning(f"Telegram notification failed: {e}")


# Timer trigger: runs 6 times daily during trading hours
# 6:30, 9:30, 12:30, 15:30, 18:30, 21:30 UTC
@app.timer_trigger(
    schedule="0 30 6,9,12,15,18,21 * * *",
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
        trader = AutoTrader(
            api=api,
            settings=settings,
            max_trades_per_session=5,
            max_daily_spend=50_000_000,
            dry_run=dry_run,
        )

        session = trader.run_full_session(league)

        # Upload databases back to blob storage
        upload_databases()

        # Send notification
        mode = "DRY RUN" if dry_run else "LIVE"
        profit_ok = len([r for r in session.profit_trades if r.success])
        lineup_ok = len([r for r in session.lineup_trades if r.success])
        msg = (
            f"*Rehoboam [{mode}]*\n"
            f"League: {league.name}\n"
            f"Profit trades: {profit_ok}/{len(session.profit_trades)}\n"
            f"Lineup trades: {lineup_ok}/{len(session.lineup_trades)}\n"
            f"Net: €{session.net_change:,}\n"
            f"Lineup: set automatically"
        )
        if session.errors:
            msg += f"\n⚠️ Errors: {len(session.errors)}"
        send_telegram_notification(msg)

        logging.info(
            f"Session complete: {profit_ok} profit + {lineup_ok} lineup trades, "
            f"net €{session.net_change:,}"
        )

    except Exception as e:
        logging.error(f"Trading session failed: {e}", exc_info=True)
        send_telegram_notification(f"*Rehoboam ERROR*\n{e!s}")
