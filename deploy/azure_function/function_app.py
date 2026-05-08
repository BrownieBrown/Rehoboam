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
LOGS_DIR = Path(TEMP_DIR) / "logs"


def _blob_settings() -> tuple[str | None, str]:
    return (
        os.getenv("AZURE_STORAGE_CONNECTION_STRING"),
        os.getenv("BLOB_CONTAINER", "rehoboam-data"),
    )


def download_databases():
    """Download learning databases from Azure Blob Storage."""
    from rehoboam.azure_blob import fetch_state

    conn_str, container = _blob_settings()
    if not conn_str:
        logging.info("No AZURE_STORAGE_CONNECTION_STRING - skipping DB download")
        return

    results = fetch_state(conn_str, container, LOGS_DIR, backup=False, dry_run=False)
    for r in results:
        if r.status == "downloaded":
            logging.info(f"Downloaded {r.db_file} ({r.blob.size} bytes)")
        elif r.status == "missing_in_blob":
            logging.info(f"No existing {r.db_file} in blob storage - will create new")
        elif r.status == "error":
            logging.warning(f"Could not download {r.db_file}: {r.error}")


def upload_databases():
    """Upload learning databases to Azure Blob Storage."""
    from rehoboam.azure_blob import push_state

    conn_str, container = _blob_settings()
    if not conn_str:
        logging.info("No AZURE_STORAGE_CONNECTION_STRING - skipping DB upload")
        return

    results = push_state(conn_str, container, LOGS_DIR, dry_run=False)
    for r in results:
        if r.status == "uploaded":
            logging.info(f"Uploaded {r.db_file} ({r.local_size} bytes)")
        elif r.status == "error":
            logging.warning(f"Could not upload {r.db_file}: {r.error}")


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
