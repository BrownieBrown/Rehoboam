"""Azure Functions handler for automated Kickbase trading with lineup setting"""

import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import azure.functions as func

app = func.FunctionApp()

# Add rehoboam to path (deployed as a subdirectory)
sys.path.insert(0, str(Path(__file__).parent))

# Azure Functions writable directory
TEMP_DIR = "/tmp"


def _send_daily_summary(api, league, settings, session):
    """Email the once-a-day picture. Best-effort: never raises into the timer."""
    from collections import Counter

    from rehoboam.bid_learner import BidLearner
    from rehoboam.config import MAX_PLAYERS_PER_CLUB
    from rehoboam.h2h import matchup_outlook
    from rehoboam.notify.email import send_email
    from rehoboam.notify.render import render_daily_summary
    from rehoboam.notify.telegram import send_message

    squad = api.get_squad(league)
    team_info = api.get_team_info(league)
    budget = int(team_info.get("budget", 0))

    try:
        outlook = matchup_outlook(api, league)
    except Exception:
        logging.warning("daily summary: matchup outlook failed", exc_info=True)
        outlook = None

    # Risks worth a line each. Everything here is something Marco can act on;
    # anything he cannot act on belongs in the logs, not the summary.
    watch: list[str] = []
    free_slots = 15 - len(squad)
    if len(squad) <= 11:
        watch.append(
            f"squad {len(squad)}/15 — no bench, so every player must start "
            f"and one unavailability is an unfillable slot"
        )
    if free_slots > 0 and budget > 20_000_000:
        watch.append(f"{free_slots} free slot(s) and EUR {budget:,} unspent")
    per_club = Counter(str(p.team_id) for p in squad)
    at_limit = [c for c, n in per_club.items() if n >= MAX_PLAYERS_PER_CLUB]
    if at_limit:
        watch.append(f"{len(at_limit)} club(s) at the {MAX_PLAYERS_PER_CLUB}-player limit")

    # REH-119: players Marco is saving toward. Reported every day so the gap
    # is visible while it closes, rather than discovered as a bid refusal.
    try:
        from rehoboam.notify.watch import WatchTarget, parse_watch_ids, render_watch_line

        watch_ids = parse_watch_ids(getattr(settings, "watch_player_ids", ""))
        if watch_ids:
            worth = int(team_info.get("team_value", 0) or 0) + int(budget or 0)
            listings = {str(p.id): p for p in api.get_market(league)}
            for pid in watch_ids:
                listed = listings.get(str(pid))
                if listed is None:
                    watch.append(f"player {pid} — not on the market right now")
                    continue
                watch.append(
                    render_watch_line(
                        WatchTarget(
                            player_id=str(pid),
                            name=listed.last_name,
                            ask=int(listed.price or listed.market_value),
                        ),
                        total_worth=worth or None,
                        max_pct=settings.max_single_buy_pct_of_worth,
                    )
                )
    except Exception:
        logging.warning("watch targets could not be evaluated", exc_info=True)

    learner = BidLearner()
    # Keep the raw rows: the rendered summary needs name+bid, but the Telegram
    # keyboard needs the proposal_id. REH-106 — without the id the summary said
    # "approve <name>" and offered no way to do it, so proposals sat at
    # `pending` until a rival bought the player.
    pending_rows = learner.pending_proposals()
    pending = [(p["player_name"], int(p["bid"])) for p in pending_rows]
    approvals = [(p["proposal_id"], p["player_name"]) for p in pending_rows]

    executed = [
        f"{r.action} {r.player_name} for EUR {r.price:,}"
        for r in (session.profit_trades + session.lineup_trades)
        if r.success
    ]

    # Rows the session wrote after its own buys (executed / refused / failed),
    # plus any pre-PR-2a proposal the webhook resolved. Without this the
    # summary would never mention a EUR 32M offer the session placed.
    #
    # Rows written BEFORE this session started: this session's own offers are
    # already in `session.profit_trades`, and listing them twice under two
    # labels is how "APPROVED" would survive into the summary of a bot that no
    # longer asks. 24h, not 48h — the summary is daily, and a 48h window
    # repeated yesterday's rows every morning.
    resolved = [
        p
        for p in learner.proposals_since(time.time() - 24 * 3600)
        if p["status"] != "pending" and float(p.get("created_at") or 0.0) < session.start_time
    ]
    # The session places its own offers now (spec §1); a row at 'executed'
    # means an offer went out, not that the player was won — PR 2b's ledger
    # will say which. 'refused' is the safety gate's answer.
    executed += [
        f"OFFERED {p['player_name']} at EUR {int(p['bid']):,}"
        for p in resolved
        if p["status"] == "executed"
    ]
    blocked = list(session.errors) + [
        f"offer for {p['player_name']} ended as {p['status']}"
        for p in resolved
        if p["status"] in {"failed", "rejected", "refused"}
    ]

    body = render_daily_summary(
        outlook=outlook,
        squad_size=len(squad),
        budget=budget,
        pending=pending,
        executed=executed,
        rejections=blocked,
        watch=watch,
    )
    if outlook is not None:
        header = (
            f"MD{outlook.matchup.day} vs {outlook.matchup.opponent_name} "
            f"({outlook.margin:+.0f}) — {len(pending)} awaiting approval\n\n"
        )
    else:
        header = f"REHOBOAM DAILY — {len(pending)} awaiting approval\n\n"

    # Telegram is the primary channel: it is already configured for approvals,
    # costs nothing, and needs no mail provider. Proton — the alternative that
    # was considered — requires a paid plan plus a custom domain, and Proton
    # Bridge binds to localhost, which an Azure Function can never reach.
    if not send_message(
        settings.telegram_bot_token,
        settings.telegram_chat_id,
        header + body,
        approvals=approvals,
    ):
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
        from rehoboam.store import ensure_ready

        # The store must be reachable and fully migrated before anything else
        # runs: a session against a half-migrated schema must not start.
        ensure_ready()

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
            app_name="function",
        )

        session = trader.run_full_session(league)

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
    # must not cost a store round trip or a Kickbase login. get_settings() is
    # local/cheap; ensure_ready() and api.login() are not. This must still
    # return 200 (Telegram retries on anything else) even if get_settings()
    # itself blows up.
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
    try:
        from rehoboam.store import ensure_ready

        # The store must be reachable and fully migrated before anything else
        # runs: a session against a half-migrated schema must not start.
        ensure_ready()

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

    return _respond(reply)
