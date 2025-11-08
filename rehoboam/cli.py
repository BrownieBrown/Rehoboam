"""CLI interface for Rehoboam"""

from typing import Optional
import typer
from rich.console import Console
from rich.prompt import Prompt, Confirm
from rich import print as rprint

from .config import get_settings
from .api import KickbaseAPI
from .trader import Trader

app = typer.Typer(
    name="rehoboam",
    help="KICKBASE Trading Bot - Automate your player trades",
    add_completion=False,
)
console = Console()


def get_api() -> KickbaseAPI:
    """Initialize and return the API client"""
    settings = get_settings()
    return KickbaseAPI(settings.kickbase_email, settings.kickbase_password)


@app.command()
def login():
    """Test KICKBASE login credentials"""
    console.print("[cyan]Testing KICKBASE login...[/cyan]")

    api = get_api()
    try:
        success = api.login()
        if success:
            console.print(f"[green]✓ Login successful![/green]")
            console.print(f"[green]  User: {api.user.name}[/green]")

            leagues = api.get_leagues()
            console.print(f"\n[cyan]Your leagues ({len(leagues)}):[/cyan]")
            for i, league in enumerate(leagues, 1):
                console.print(f"  {i}. {league.name}")
    except Exception as e:
        console.print(f"[red]✗ Login failed: {e}[/red]")
        raise typer.Exit(code=1)


@app.command()
def analyze(
    league_index: int = typer.Option(
        0, "--league", "-l", help="League index (0 for first league)"
    ),
    show_all: bool = typer.Option(
        False, "--all", "-a", help="Show all players, not just opportunities"
    ),
):
    """Analyze the market for trading opportunities"""

    settings = get_settings()
    api = get_api()
    api.login()

    leagues = api.get_leagues()
    if not leagues:
        console.print("[red]No leagues found[/red]")
        raise typer.Exit(code=1)

    if league_index >= len(leagues):
        console.print(f"[red]Invalid league index. You have {len(leagues)} leagues.[/red]")
        raise typer.Exit(code=1)

    league = leagues[league_index]
    console.print(f"\n[bold]Analyzing league: {league.name}[/bold]\n")

    trader = Trader(api, settings)

    # Analyze market
    market_analyses = trader.analyze_market(league)

    if show_all:
        trader.display_analysis(market_analyses, title="All Market Players")
    else:
        opportunities = trader.analyzer.find_best_opportunities(market_analyses, top_n=20)
        if opportunities:
            trader.display_analysis(opportunities, title="Top 20 Trading Opportunities")
        else:
            console.print("[yellow]No trading opportunities found[/yellow]")

    # Analyze your team
    console.print("\n")
    team_analyses = trader.analyze_team(league)
    sell_recommendations = [a for a in team_analyses if a.recommendation == "SELL"]

    if sell_recommendations:
        trader.display_analysis(sell_recommendations, title="Players You Should Consider Selling")
    else:
        console.print("[green]No sell recommendations for your current team[/green]")


@app.command()
def trade(
    league_index: int = typer.Option(
        0, "--league", "-l", help="League index (0 for first league)"
    ),
    max_trades: int = typer.Option(
        5, "--max", "-m", help="Maximum number of trades to execute"
    ),
    live: bool = typer.Option(
        False, "--live", help="Execute real trades (overrides DRY_RUN)"
    ),
):
    """Start automated trading"""

    settings = get_settings()

    # Override dry_run if --live flag is used
    if live:
        if not Confirm.ask(
            "[yellow]⚠️  You are about to execute REAL trades. Are you sure?[/yellow]"
        ):
            console.print("[red]Trading cancelled[/red]")
            raise typer.Exit(code=0)
        settings.dry_run = False

    api = get_api()
    api.login()

    leagues = api.get_leagues()
    if not leagues:
        console.print("[red]No leagues found[/red]")
        raise typer.Exit(code=1)

    if league_index >= len(leagues):
        console.print(f"[red]Invalid league index. You have {len(leagues)} leagues.[/red]")
        raise typer.Exit(code=1)

    league = leagues[league_index]
    console.print(f"\n[bold]Trading in league: {league.name}[/bold]")

    trader = Trader(api, settings)
    trader.auto_trade(league, max_trades=max_trades)


@app.command()
def config():
    """Show current configuration"""
    try:
        settings = get_settings()

        console.print("\n[bold cyan]Current Configuration:[/bold cyan]\n")
        console.print(f"  Email: {settings.kickbase_email}")
        console.print(f"  Min Sell Profit: {settings.min_sell_profit_pct}%")
        console.print(f"  Max Loss: {settings.max_loss_pct}%")
        console.print(f"  Min Buy Value Increase: {settings.min_buy_value_increase_pct}%")
        console.print(f"  Max Player Cost: €{settings.max_player_cost:,}")
        console.print(f"  Reserve Budget: €{settings.reserve_budget:,}")
        console.print(f"  Dry Run: {settings.dry_run}")
        console.print("\n[dim]Edit .env file to change configuration[/dim]\n")

    except Exception as e:
        console.print(f"[red]Error loading config: {e}[/red]")
        console.print("[yellow]Make sure you have created a .env file (copy from .env.example)[/yellow]")
        raise typer.Exit(code=1)


@app.callback()
def callback():
    """
    Rehoboam - KICKBASE Trading Bot

    Automate your KICKBASE trading strategy with market value tracking.
    """
    pass


if __name__ == "__main__":
    app()
