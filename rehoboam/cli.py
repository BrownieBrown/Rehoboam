"""CLI interface for Rehoboam — minimal surface for auto + diagnostics."""

import logging
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import azure_blob
from .api import KickbaseAPI
from .config import AzureBlobSettings, get_settings
from .logging_setup import setup_logging

logger = logging.getLogger(__name__)

app = typer.Typer(
    name="rehoboam",
    help="KICKBASE Trading Bot — automated EP-driven trading",
    add_completion=False,
)
console = Console()


def _get_api() -> KickbaseAPI:
    settings = get_settings()
    return KickbaseAPI(settings.kickbase_email, settings.kickbase_password)


def _login_and_get_league(league_index: int):
    """Log in and return (api, settings, league) — shared bootstrap."""
    api = _get_api()
    settings = get_settings()

    console.print("[cyan]Logging in…[/cyan]")
    api.login()
    console.print(f"[green]✓ Logged in as {api.user.name}[/green]")

    leagues = api.get_leagues()
    if league_index >= len(leagues):
        console.print(f"[red]League index {league_index} not found[/red]")
        raise typer.Exit(code=1)

    league = leagues[league_index]
    console.print(f"[cyan]League: {league.name}[/cyan]\n")
    return api, settings, league


@app.command()
def login():
    """Test KICKBASE login credentials and list your leagues."""
    api = _get_api()
    try:
        api.login()
        console.print("[green]✓ Login successful[/green]")
        console.print(f"[green]  User: {api.user.name}[/green]")

        leagues = api.get_leagues()
        console.print(f"\n[cyan]Your leagues ({len(leagues)}):[/cyan]")
        for i, league in enumerate(leagues, 1):
            console.print(f"  {i}. {league.name}")
    except Exception as e:
        console.print(f"[red]✗ Login failed: {e}[/red]")
        raise typer.Exit(code=1) from e


@app.command()
def auto(
    league_index: int = typer.Option(0, "--league", "-l", help="League index (0 for first league)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Simulate trades without executing"),
    max_trades: int = typer.Option(10, "--max-trades", help="Max trades per session"),
    max_spend: int = typer.Option(50_000_000, "--max-spend", help="Max daily spend"),
    aggressive: bool = typer.Option(
        False,
        "--aggressive",
        help="Up to 15 trades, lower EP threshold, +50% spend limit",
    ),
):
    """Run one automated trading session (unified EP pipeline + profit flips)."""
    from .auto_trader import AutoTrader

    console.print("[bold cyan]🤖 Automated Trading Session[/bold cyan]")
    if dry_run:
        console.print("[yellow]DRY RUN MODE — No trades will be executed[/yellow]")

    api, settings, league = _login_and_get_league(league_index)

    if aggressive:
        settings.min_ep_upgrade_threshold = max(settings.min_ep_upgrade_threshold - 2, 3.0)
        max_trades = settings.auto_max_trades_aggressive
        max_spend = int(max_spend * 1.5)
        console.print(
            f"[yellow]AGGRESSIVE MODE: EP threshold "
            f"{settings.min_ep_upgrade_threshold:.0f}, max {max_trades} trades, "
            f"€{max_spend:,} spend limit[/yellow]\n"
        )

    auto_trader = AutoTrader(
        api=api,
        settings=settings,
        max_trades_per_session=max_trades,
        max_daily_spend=max_spend,
        dry_run=dry_run,
    )

    session = auto_trader.run_full_session(league)

    console.print("\n[bold]Session Complete[/bold]")
    console.print(f"Duration: {session.end_time - session.start_time:.1f}s")
    successful = len([r for r in session.profit_trades + session.lineup_trades if r.success])
    console.print(f"Trades executed: {successful}")

    if session.net_change != 0:
        color = "green" if session.net_change > 0 else "red"
        console.print(f"Net budget change: [{color}]€{session.net_change:,}[/{color}]")


@app.command()
def status(
    league_index: int = typer.Option(0, "--league", "-l", help="League index (0 for first league)"),
):
    """Read-only diagnostic: show current squad, budget, and what `auto` would do.

    Runs the full EP pipeline in dry-run mode so you can see the bot's intended
    actions without executing anything.
    """
    from .auto_trader import AutoTrader

    api, settings, league = _login_and_get_league(league_index)

    # Fetch squad + budget for summary
    squad = api.get_squad(league)
    team_info = api.get_team_info(league)
    budget = team_info.get("budget", 0)
    team_value = team_info.get("team_value", 0)

    console.print("[bold cyan]📊 Squad Status[/bold cyan]")
    console.print(
        f"Squad: {len(squad)}/15  |  Budget: €{int(budget):,}  |  Team value: €{int(team_value):,}\n"
    )

    positions: dict[str, list] = {}
    for p in squad:
        positions.setdefault(p.position, []).append(p)
    for pos in ["Goalkeeper", "Defender", "Midfielder", "Forward"]:
        players = positions.get(pos, [])
        console.print(f"[bold]{pos} ({len(players)})[/bold]")
        for p in sorted(players, key=lambda x: x.market_value, reverse=True):
            console.print(
                f"  • {p.last_name}  €{int(p.market_value):,}  avg={int(p.average_points or 0)}"
            )
    console.print()

    # Run the auto session in dry-run mode to see recommendations
    console.print("[bold cyan]🤖 Dry-run session (what auto would do)[/bold cyan]\n")
    auto_trader = AutoTrader(
        api=api,
        settings=settings,
        max_trades_per_session=settings.auto_max_trades_normal,
        max_daily_spend=50_000_000,
        dry_run=True,
    )
    auto_trader.run_full_session(league)


def _fmt_size(n: int | None) -> str:
    if n is None:
        return "—"
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KiB"
    return f"{n / (1024 * 1024):.1f} MiB"


def _fmt_dt(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC") if dt else "—"


_FETCH_STATUS_STYLE = {
    "downloaded": "green",
    "missing_in_blob": "yellow",
    "skipped_dry_run": "cyan",
    "error": "red",
}

_PUSH_STATUS_STYLE = {
    "uploaded": "green",
    "missing_local": "yellow",
    "skipped_dry_run": "cyan",
    "error": "red",
}


def _render_fetch_table(results: list[azure_blob.FetchResult], *, dry_run: bool) -> Table:
    title = "Would fetch" if dry_run else "Fetched"
    table = Table(title=title)
    table.add_column("DB", style="bold")
    table.add_column("Blob last modified")
    table.add_column("Blob size", justify="right")
    table.add_column("Local target")
    table.add_column("Backup")
    table.add_column("Status")

    for r in results:
        backup = str(r.backed_up_to) if r.backed_up_to else "—"
        status_label = r.status.replace("_", " ")
        if r.status == "error" and r.error:
            status_label = f"error: {r.error[:40]}"
        table.add_row(
            r.db_file,
            _fmt_dt(r.blob.last_modified),
            _fmt_size(r.blob.size),
            str(r.local_path),
            backup,
            f"[{_FETCH_STATUS_STYLE[r.status]}]{status_label}[/{_FETCH_STATUS_STYLE[r.status]}]",
        )
    return table


def _render_push_table(results: list[azure_blob.PushResult], *, dry_run: bool) -> Table:
    title = "Would push" if dry_run else "Pushed"
    table = Table(title=title)
    table.add_column("DB", style="bold")
    table.add_column("Local path")
    table.add_column("Local size", justify="right")
    table.add_column("Status")

    for r in results:
        status_label = r.status.replace("_", " ")
        if r.status == "error" and r.error:
            status_label = f"error: {r.error[:40]}"
        table.add_row(
            r.db_file,
            str(r.local_path),
            _fmt_size(r.local_size),
            f"[{_PUSH_STATUS_STYLE[r.status]}]{status_label}[/{_PUSH_STATUS_STYLE[r.status]}]",
        )
    return table


@app.command("fetch-azure-state")
def fetch_azure_state(
    dry_run: bool = typer.Option(False, "--dry-run", help="List blobs without downloading"),
    backup: bool = typer.Option(
        True,
        "--backup/--no-backup",
        help="Rename existing local files to .local-bak before overwriting",
    ),
):
    """Pull SQLite state from Azure Blob Storage into ./logs/ for prod debugging."""
    blob_settings = AzureBlobSettings()
    try:
        results = azure_blob.fetch_state(
            connection_string=blob_settings.azure_storage_connection_string,
            container_name=blob_settings.blob_container,
            dest_dir=Path("logs"),
            backup=backup,
            dry_run=dry_run,
        )
    except azure_blob.MissingAzureCredentials as e:
        console.print(f"[red]✗ {e}[/red]")
        raise typer.Exit(code=1) from e

    console.print(_render_fetch_table(results, dry_run=dry_run))

    if not dry_run and any(r.status == "error" for r in results):
        raise typer.Exit(code=1)


@app.command("push-azure-state")
def push_azure_state(
    confirm: bool = typer.Option(
        False,
        "--i-know-what-im-doing",
        help="Required to actually upload — without it the command refuses.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="List local files without uploading"),
    force: bool = typer.Option(
        False,
        "--force",
        help="Bypass the freshness check and clobber even if blob has been "
        "modified since fetch (DANGEROUS — likely overwrites the bot's writes).",
    ),
):
    """Push local ./logs/ SQLite state to Azure Blob Storage (DANGEROUS).

    Overwrites the live bot's persistent state. Refuses to run without
    --i-know-what-im-doing. By default, also refuses if the blob has been
    modified since the last fetch (Function ran in the meantime); re-fetch
    or pass --force to override. Use --dry-run to preview.
    """
    if not confirm:
        console.print(
            "[red]⛔ Refusing to overwrite prod state from local.[/red]\n"
            "This will replace the live bot's databases (bid_learning.db, "
            "value_tracking.db, market_prices.db, player_history.db) with "
            "whatever is in ./logs/.\n"
            "Re-run with [bold]--i-know-what-im-doing[/bold] if you actually want this."
        )
        raise typer.Exit(code=1)

    blob_settings = AzureBlobSettings()
    try:
        results = azure_blob.push_state(
            connection_string=blob_settings.azure_storage_connection_string,
            container_name=blob_settings.blob_container,
            source_dir=Path("logs"),
            dry_run=dry_run,
            force=force,
        )
    except azure_blob.MissingAzureCredentials as e:
        console.print(f"[red]✗ {e}[/red]")
        raise typer.Exit(code=1) from e
    except azure_blob.BlobChangedSinceFetch as e:
        console.print("[red]⛔ Refusing to push — blob has been modified since fetch.[/red]")
        for s in e.stale:
            console.print(
                f"  • {s.db_file}: fetched at "
                f"[cyan]{s.fetched_last_modified.isoformat()}[/cyan]"
                f", current blob at [yellow]{s.current_last_modified.isoformat()}[/yellow]"
            )
        console.print(
            "\nThe Azure Function probably ran since you fetched. Either:\n"
            "  1. Re-run [bold]rehoboam fetch-azure-state[/bold] (preserves your local "
            "work as .local-bak), redo your local mutations, then push again, OR\n"
            "  2. Pass [bold]--force[/bold] to clobber the bot's writes (NOT recommended)."
        )
        raise typer.Exit(code=1) from e

    console.print(_render_push_table(results, dry_run=dry_run))

    if not dry_run and any(r.status == "error" for r in results):
        raise typer.Exit(code=1)


@app.command("backfill-mv-history")
def backfill_mv_history(
    league_index: int = typer.Option(0, "--league", "-l", help="League index (0 for first league)"),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Run all HTTP calls but skip DB writes; reports row-count estimates.",
    ),
    timeframe_days: int = typer.Option(
        365,
        "--timeframe",
        help="Days of MV history to fetch per player (default: 365 = full season).",
    ),
):
    """One-shot backfill of player_mv_history for all flipped players (REH-40).

    Walks every distinct player_id in flip_outcomes and fetches the v2 MV
    history endpoint, writing daily snapshots into player_mv_history. This
    populates the trajectory data REH-32 / REH-33 calibrations need.

    Idempotent: rerunning silently skips duplicates via the existing
    UNIQUE(player_id, snapshot_at) constraint.

    Workflow when targeting prod state:
      1. rehoboam fetch-azure-state
      2. rehoboam backfill-mv-history
      3. rehoboam push-azure-state --i-know-what-im-doing
    """
    from .bid_learner import BidLearner
    from .mv_backfill import run_mv_backfill

    api, _settings, _league = _login_and_get_league(league_index)
    learner = BidLearner()

    console.print("\n[bold cyan]🔁 Backfilling player_mv_history…[/bold cyan]")
    if dry_run:
        console.print("[yellow]DRY RUN — no DB writes; counts are upper-bound estimates[/yellow]")

    stats = run_mv_backfill(
        client=api.client, learner=learner, dry_run=dry_run, timeframe_days=timeframe_days
    )

    table = Table(title="MV backfill summary")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")
    table.add_row("Players processed", f"[green]{stats.players_processed}[/green]")
    table.add_row("Players with no MV data", f"{stats.players_skipped_no_data}")
    table.add_row(
        "Players failed (HTTP errors)",
        f"[red]{stats.players_failed}[/red]" if stats.players_failed else "0",
    )
    table.add_row("Rows attempted", f"{stats.rows_attempted}")
    console.print(table)

    if not dry_run:
        console.print(
            "\n[dim]Next step: rehoboam push-azure-state --i-know-what-im-doing  "
            "(during a quiet window — between 08:02 and 19:58 UTC, or after 20:02)[/dim]"
        )


@app.command("backfill-history")
def backfill_history(
    league_index: int = typer.Option(0, "--league", "-l", help="League index (0 for first league)"),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Run all HTTP calls but skip DB writes; reports row-count estimates.",
    ),
):
    """Backfill foundation tables from KICKBASE history (REH-39).

    One-shot command that derives historical rows from the KICKBASE API:
      • flip_outcomes           ← per-manager transfer history (FIFO pairing)
      • matchday_lineup_results ← per-matchday teamcenter (lineup + actual points)
      • league_rank_history     ← per-matchday ranking (one row per manager)

    Idempotent: rerunning silently skips duplicates.

    Workflow when targeting prod state:
      1. rehoboam fetch-azure-state
      2. rehoboam backfill-history
      3. rehoboam push-azure-state --i-know-what-im-doing
    """
    from .backfill import run_backfill
    from .bid_learner import BidLearner

    api, _settings, league = _login_and_get_league(league_index)
    user_id = api.user.id
    learner = BidLearner()

    console.print("\n[bold cyan]🔁 Backfilling foundation tables…[/bold cyan]")
    if dry_run:
        console.print("[yellow]DRY RUN — no DB writes; counts are upper-bound estimates[/yellow]")

    stats = run_backfill(
        client=api.client,
        league=league,
        user_id=user_id,
        manager_id=user_id,
        learner=learner,
        dry_run=dry_run,
    )

    table = Table(title="Backfill summary")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")
    table.add_row("Transfer pages walked", f"{stats.transfers_paginated}")
    table.add_row(
        "flip_outcomes inserted",
        f"[green]{stats.flip_outcomes_inserted}[/green]",
    )
    table.add_row(
        "flip_outcomes skipped (duplicate)",
        f"[cyan]{stats.flip_outcomes_skipped_duplicate}[/cyan]",
    )
    table.add_row(
        "Unpaired buys (still in squad)",
        f"{stats.flip_outcomes_unpaired_buys}",
    )
    table.add_row(
        "Orphaned sells (data gap)",
        (
            f"[yellow]{stats.flip_outcomes_orphaned_sells}[/yellow]"
            if stats.flip_outcomes_orphaned_sells
            else "0"
        ),
    )
    table.add_row(
        "Matchdays processed",
        f"{stats.matchdays_processed} (skipped {stats.matchdays_skipped_no_lineup})",
    )
    table.add_row(
        "matchday_lineup_results inserted",
        f"[green]{stats.matchday_lineup_results_inserted}[/green]",
    )
    table.add_row(
        "league_rank_history inserted",
        f"[green]{stats.league_rank_history_inserted}[/green]",
    )
    console.print(table)

    if not dry_run:
        console.print(
            "\n[dim]Next step: rehoboam push-azure-state --i-know-what-im-doing  "
            "(during a quiet window — between 08:02 and 19:58 UTC, or after 20:02)[/dim]"
        )


@app.callback()
def callback(
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable DEBUG logging on the console (file log is always DEBUG).",
    ),
):
    """Rehoboam — KICKBASE trading bot with aggressive auto mode."""
    setup_logging(verbose=verbose)
    logger.debug("CLI invoked (verbose=%s)", verbose)
