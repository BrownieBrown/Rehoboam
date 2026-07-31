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


@app.command("enrich-corpus")
def enrich_corpus(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Fetch the universe only; write no per-player history"
    ),
    limit: int = typer.Option(
        0,
        "--limit",
        help="Cap players processed this run (0 = no cap). Useful for a smoke run.",
    ),
    throttle: float = typer.Option(0.25, "--throttle", help="Seconds to sleep between API calls"),
    include_historical: bool = typer.Option(
        False,
        "--include-historical",
        help=(
            "Also recover players who left the league since last season "
            "(read from logs/bid_learning.db) — needed for backtesting past "
            "matchdays, since /lineup/selection only sees current players."
        ),
    ),
    refetch_performance: bool = typer.Option(
        False,
        "--refetch-performance",
        help=(
            "Force re-fetch of performance history for every player already "
            "marked complete (clears performance_fetched_at only — MV-series "
            "resumability is untouched). Needed once after a bug fix in how "
            "performance rows are parsed; a plain rerun would otherwise skip "
            "everyone via sweep_progress. Not the default — opt in per run."
        ),
    ),
):
    """Sweep the full competition into logs/training_corpus.db (v2 scorer training data).

    Long-running and API-bound — thousands of requests. Safe to interrupt and
    rerun: progress is tracked per player, so a rerun resumes rather than
    restarting.

    Typical first run:
      1. rehoboam enrich-corpus --dry-run          # how many players?
      2. rehoboam enrich-corpus --limit 20         # smoke-test the shapes
      3. rehoboam enrich-corpus                    # the full sweep

    ``--include-historical`` additionally recovers departed players so a
    backtest replaying a past season has a full squad to reconstruct, and
    resolves their position/name/team via a competition-scoped endpoint that
    works for any player id — see ``rehoboam.enrichment.historical_ids`` for
    where those ids come from and ``sweep.run_sweep`` for how position gets
    resolved (an id only keeps ``position IS NULL`` if that lookup itself
    genuinely fails).

    ``--refetch-performance`` is a one-off escape hatch: normally
    ``sweep_progress`` makes reruns skip players already fetched, which is
    exactly what you don't want after a parsing bug is fixed and the stored
    rows need to be regenerated from scratch.
    """
    from .bid_learner import BidLearner
    from .enrichment.corpus import TrainingCorpus
    from .enrichment.historical_ids import gather_historical_player_ids
    from .enrichment.sweep import run_sweep

    # The universe endpoint is league-scoped, so we need a league. Reuse the
    # existing helper rather than re-deriving it — it already handles login,
    # league listing and the not-found error path. It returns a 3-tuple
    # (api, settings, league); `settings` is unused here.
    api, _settings, league = _login_and_get_league(0)

    extra_player_ids = None
    if include_historical:
        learner_db_path = BidLearner().db_path
        extra_player_ids = gather_historical_player_ids(learner_db_path)
        console.print(
            f"[dim]Recovered {len(extra_player_ids)} historical player ids from "
            f"{learner_db_path}[/dim]"
        )

    corpus = TrainingCorpus()
    stats = run_sweep(
        api.client,
        corpus,
        league_id=league.id,
        dry_run=dry_run,
        throttle_seconds=throttle,
        limit=limit or None,
        extra_player_ids=extra_player_ids,
        force_refetch_performance=refetch_performance,
    )

    table = Table(title="Corpus enrichment summary")
    table.add_column("Metric")
    table.add_column("Count", justify="right")
    table.add_row("Universe size", str(stats.universe_size))
    table.add_row("Performance fetched", str(stats.performance_fetched))
    table.add_row("MV series fetched", str(stats.mv_fetched))
    table.add_row("Skipped (already done)", str(stats.skipped))
    table.add_row("Failed", str(stats.failed))
    if include_historical:
        table.add_row("Historical positions resolved", str(stats.positions_resolved))
        table.add_row("Historical positions unresolved", str(stats.positions_unresolved))
    console.print(table)
    console.print(f"[dim]Corpus: {corpus.db_path}[/dim]")


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


@app.command("backtest-baseline")
def backtest_baseline(
    season: str = typer.Option("2025/2026", "--season", help="Season to replay, e.g. 2025/2026."),
    max_squad_size: int = typer.Option(
        15,
        "--max-squad-size",
        help=(
            "Cap each reconstructed squad at this size — fielded eleven kept "
            "first, then the most-recently-bought remainder. Pass 0 for "
            "uncapped (the original, upward-biased headline figure)."
        ),
    ),
    learner_db: Path = typer.Option(
        Path("logs") / "bid_learning.db",
        "--learner-db",
        help="Path to bid_learning.db (matchday_lineup_results + flip_outcomes).",
    ),
    corpus_db: Path = typer.Option(
        Path("logs") / "training_corpus.db",
        "--corpus-db",
        help="Path to training_corpus.db (player_match_history + player_universe).",
    ),
):
    """Reproduce the season-average baseline regret measurement (week 1 headline number).

    Read-only, no API calls and no login: replays ``matchday_lineup_results``
    and ``flip_outcomes`` from the learning DB against ``player_match_history``
    and ``player_universe`` in the training corpus, and reports how a naive
    season-average lineup picker performs against the hindsight-optimal
    eleven. This is the bar weeks 2-3 must beat with the real scorer, on an
    identical fixture set — see
    docs/superpowers/specs/2026-07-29-rehoboam-v2-design.md §6 for why the
    uncapped figure is reported as an upper bound rather than a point
    estimate.
    """
    from .backtest.baseline_driver import run_baseline

    cap = None if max_squad_size <= 0 else max_squad_size
    report, stats = run_baseline(
        learner_db_path=learner_db,
        corpus_db_path=corpus_db,
        season=season,
        max_squad_size=cap,
    )

    console.print(f"[bold cyan]Backtest baseline — {season}[/bold cyan]")
    console.print(
        f"Matchdays: {stats.matchdays_total} total, {stats.matchdays_usable} usable "
        f"({stats.matchdays_skipped_small_squad} skipped — reconstructed squad "
        f"below {12} players)\n"
    )

    table = Table(title=f"season_average_baseline (max_squad_size={max_squad_size or 'uncapped'})")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")
    table.add_row("Mean regret", f"{report.mean_regret:.1f} pts/matchday")
    table.add_row("Mean rank correlation", f"{report.mean_rank_correlation:+.3f}")
    if report.total_best_points:
        captured = 100 * report.total_chosen_points / report.total_best_points
        table.add_row("Points captured", f"{captured:.1f}%")
    table.add_row("Total chosen points", f"{report.total_chosen_points:,.0f}")
    table.add_row("Total best-possible points", f"{report.total_best_points:,.0f}")
    console.print(table)


@app.command("fit-scorer")
def fit_scorer(
    availability_k: float = typer.Option(
        20.0, "--availability-k", help="Shrinkage pseudo-count for the transition model"
    ),
    rate_k: float = typer.Option(
        5.0, "--rate-k", help="Shrinkage pseudo-count for per-player quality"
    ),
):
    """Fit the v2 scorer components and write coefficients.json.

    Trains on seasons up to 2024/25 and reports calibration on the held-out
    2025/26 season. Never fits on the holdout — that season is what the whole
    rebuild is judged against.
    """
    from .enrichment.corpus import TrainingCorpus
    from .scoring.v2.availability import fit_availability
    from .scoring.v2.coefficients import COEFFICIENTS_PATH, save_coefficients
    from .scoring.v2.dataset import (
        HOLDOUT_SEASON,
        TRAIN_MAX_SEASON,
        load_match_rows,
        load_positions,
        split_rows,
    )
    from .scoring.v2.features import build_feature_rows
    from .scoring.v2.rate import fit_rate

    corpus = TrainingCorpus()
    by_player = load_match_rows(corpus.db_path)
    positions = load_positions(corpus.db_path)

    all_rows = []
    for matches in by_player.values():
        all_rows.extend(build_feature_rows(matches))

    train, holdout = split_rows(all_rows)
    console.print(
        f"[cyan]train {len(train):,} rows (≤{TRAIN_MAX_SEASON}) · "
        f"holdout {len(holdout):,} rows ({HOLDOUT_SEASON})[/cyan]"
    )
    if not train:
        console.print("[red]No training rows — is the corpus populated?[/red]")
        raise typer.Exit(1)

    availability = fit_availability(train, shrinkage_k=availability_k)
    rate = fit_rate(train, positions, shrinkage_k=rate_k)

    save_coefficients(
        availability,
        rate,
        {
            "train_max_season": TRAIN_MAX_SEASON,
            "holdout_season": HOLDOUT_SEASON,
            "train_rows": len(train),
            "availability_k": availability_k,
            "rate_k": rate_k,
        },
    )

    table = Table(title="Availability transitions (fitted)")
    table.add_column("prev")
    for s in (1, 3, 4, 5):
        table.add_column(f"→{s}", justify="right")
    for prev in (1, 3, 4, 5):
        probs = availability.predict(prev)
        table.add_row(str(prev), *(f"{probs[s]:.1%}" for s in (1, 3, 4, 5)))
    console.print(table)

    rates = Table(title="Base rate by status (real points)")
    rates.add_column("status")
    rates.add_column("points", justify="right")
    for s in (1, 3, 4, 5):
        rates.add_row(str(s), f"{rate.base_rate.get(s, 0.0):.1f}")
    console.print(rates)

    console.print(f"[dim]Coefficients: {COEFFICIENTS_PATH}[/dim]")


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
