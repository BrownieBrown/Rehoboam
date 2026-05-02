"""CLI interface for Rehoboam — minimal surface for auto + diagnostics."""

import logging

import typer
from rich.console import Console

from .api import KickbaseAPI
from .config import get_settings
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
