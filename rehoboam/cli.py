"""CLI interface for Rehoboam — minimal surface for auto + diagnostics."""

import logging
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

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


def _ensure_store() -> None:
    """Refuse to start a session the store cannot serve; say why in one line."""
    import psycopg

    from .store import StoreUnconfigured, ensure_ready

    try:
        ensure_ready()
    except StoreUnconfigured as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from e
    except PermissionError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from e
    except psycopg.OperationalError as e:
        console.print(f"[red]store unreachable: {e}[/red]")
        raise typer.Exit(code=1) from e
    except psycopg.Error as e:
        # Catch-all for the rest of psycopg's tree, e.g. InsufficientPrivilege
        # from a role with no USAGE on the rehoboam schema -- a config error,
        # not a crash, so it gets the same one red line as the cases above.
        console.print(f"[red]store error: {e}[/red]")
        raise typer.Exit(code=1) from e


def _admin_dsn(explicit: str | None) -> str | None:
    """DATABASE_ADMIN_URL when set; None lets connect() fall back to DATABASE_URL."""
    if explicit:
        return explicit
    return get_settings().database_admin_url or None


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

    _ensure_store()

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

    _ensure_store()

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

    Writes straight to the store; run during a quiet window between Function
    sessions.
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
        console.print("\n[dim]Written to the store.[/dim]")


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
            "(recovered from the store) — needed for backtesting past "
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
    transfers: bool = typer.Option(
        False,
        "--transfers",
        help=(
            "Also sweep each player's real transfer history (REH-55) into "
            "player_transfers — the only local source of real, whole-season "
            "market prices for the full-bot replay. One extra request per "
            "player (~527 in the live universe); off by default and "
            "independently resumable from performance/MV."
        ),
    ),
):
    """Sweep the full competition into the store (v2 scorer training data).

    The sweep now writes to the store (``CorpusStore``), not
    ``logs/training_corpus.db``; ``corpus-pull`` materialises a local SQLite
    copy for the offline backtest/training tools that still read
    ``TrainingCorpus`` directly.

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

    ``--transfers`` additionally sweeps ``/leagues/{lid}/players/{pid}/transferHistory``
    for every player into the new ``player_transfers`` table — real
    transaction prices across the whole season, needed by the full-bot
    replay (REH-51) to know what was actually buyable and at what price on
    a given matchday.
    """
    from .bid_learner import BidLearner
    from .enrichment.historical_ids import gather_historical_player_ids
    from .enrichment.sweep import run_sweep
    from .store.corpus_store import CorpusStore

    _ensure_store()

    # The universe endpoint is league-scoped, so we need a league. Reuse the
    # existing helper rather than re-deriving it — it already handles login,
    # league listing and the not-found error path. It returns a 3-tuple
    # (api, settings, league); `settings` is unused here.
    api, _settings, league = _login_and_get_league(0)

    extra_player_ids = None
    if include_historical:
        extra_player_ids = gather_historical_player_ids(BidLearner())
        console.print(
            f"[dim]Recovered {len(extra_player_ids)} historical player ids from the store[/dim]"
        )

    corpus = CorpusStore()
    stats = run_sweep(
        api.client,
        corpus,
        league_id=league.id,
        dry_run=dry_run,
        throttle_seconds=throttle,
        limit=limit or None,
        extra_player_ids=extra_player_ids,
        force_refetch_performance=refetch_performance,
        sweep_transfers=transfers,
    )

    table = Table(title="Corpus enrichment summary")
    table.add_column("Metric")
    table.add_column("Count", justify="right")
    table.add_row("Universe size", str(stats.universe_size))
    table.add_row("Performance fetched", str(stats.performance_fetched))
    table.add_row("MV series fetched", str(stats.mv_fetched))
    if transfers:
        table.add_row("Transfers fetched", str(stats.transfers_fetched))
    table.add_row("Skipped (already done)", str(stats.skipped))
    table.add_row("Failed", str(stats.failed))
    if include_historical:
        table.add_row("Historical positions resolved", str(stats.positions_resolved))
        table.add_row("Historical positions unresolved", str(stats.positions_unresolved))
    console.print(table)
    console.print("[dim]Corpus: the store[/dim]")


@app.command("ingest")
def ingest_cmd(
    deadline_seconds: float | None = typer.Option(None, "--deadline-seconds"),
    max_requests: int | None = typer.Option(None, "--max-requests"),
    throttle: float = typer.Option(0.25, "--throttle", help="Seconds between requests."),
):
    """One budgeted ingestion pass — what func-rehoboam-external runs twice a day."""
    import time

    from .enrichment.ingest import IngestBudget, run_ingestion
    from .store.corpus_store import CorpusStore

    _ensure_store()
    api, settings, league = _login_and_get_league(0)
    budget = IngestBudget(
        deadline=time.time() + (deadline_seconds or settings.ingest_deadline_seconds),
        max_requests=max_requests or settings.ingest_max_requests,
    )
    stats = run_ingestion(
        api.client,
        CorpusStore(),
        league_id=league.id,
        budget=budget,
        stale_after_seconds=settings.ingest_stale_after_hours * 3600.0,
        throttle_seconds=throttle,
    )
    table = Table(title="Ingestion")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    for name in (
        "universe_size",
        "status_written",
        "performance_fetched",
        "mv_fetched",
        "failed",
        "requests",
    ):
        table.add_row(name, str(getattr(stats, name)))
    table.add_row("stopped_by", stats.stopped_by or "—")
    table.add_row("duration_s", f"{stats.duration_s:.0f}")
    console.print(table)


@app.command("backfill-flip-entry-context")
def backfill_flip_entry_context():
    """Reconstruct what the market looked like when each closed flip was bought (REH-104).

    `flip_outcomes.trend_at_buy` has existed since the table was created and was
    NULL in every row: `record_flip_outcome` runs at sell time and the entry
    context was never stored. This derives it from `player_mv_history`, which
    already holds the snapshots, so historical flips become measurable without
    waiting for a season of new ones.

    Purely local — no API calls. Idempotent: rows that already have context are
    left alone, and a flip with no usable MV history stays NULL rather than
    being filled with a fabricated zero.
    """
    from rehoboam.bid_learner import BidLearner

    learner = BidLearner()
    written = learner.backfill_flip_entry_context()
    console.print(f"[green]Annotated {written} flip(s) with entry context[/green]")

    annotated, total = learner.flip_entry_context_coverage()
    console.print(f"[dim]{annotated} of {total} flips now carry entry context[/dim]")


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

    Writes straight to the store; run during a quiet window between Function
    sessions.
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
        console.print("\n[dim]Written to the store.[/dim]")


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


@app.command("diagnose-flips")
def diagnose_flips(
    learner_db: Path = typer.Option(
        Path("logs") / "bid_learning.db",
        "--learner-db",
        help="Path to bid_learning.db (flip_outcomes).",
    ),
    corpus_db: Path = typer.Option(
        Path("logs") / "training_corpus.db",
        "--corpus-db",
        help="Path to training_corpus.db (mv_series + player_match_history).",
    ),
):
    """Decompose every completed round trip's P&L into selection, exit and entry premium (REH-75).

    Read-only, no API calls and no login. See
    docs/superpowers/specs/2026-08-19-reh-75-flip-diagnosis-design.md for the
    identity, the horizon sweep, and the pre-registered dominance rule.
    """
    from rehoboam.diagnostics.flip_diagnosis import run_diagnosis
    from rehoboam.diagnostics.flip_report import format_report

    # soft_wrap + crop=False: Console() reports width=80 whenever stdout is
    # redirected (is_terminal=False) -- the default TTY-shaped wrapping, and
    # Task 6's determinism gate redirects this straight to a file, so without
    # these the report's own tables would wrap mid-number in the delivered
    # artifact.
    console.print(format_report(run_diagnosis(learner_db, corpus_db)), soft_wrap=True, crop=False)


@app.command("replay-season")
def replay_season(
    corpus: Path = typer.Option(  # noqa: B008
        Path("logs/training_corpus.db"), help="Path to the training corpus DB"
    ),
    learning_db: Path = typer.Option(  # noqa: B008
        Path("logs/bid_learning.db"), help="Path to the learning DB with real standings"
    ),
    min_ep_gain: float | None = typer.Option(
        None,
        help=(
            "Marginal EP gain floor in real points. Defaults to the value the live "
            "bot ships with; override only for a labelled sensitivity check."
        ),
    ),
    with_flips: bool = typer.Option(
        False,
        "--with-flips",
        help=(
            "Model profit flipping (REH-68): take profit at min_sell_profit_pct "
            "and cut losses at max_loss_pct. Real flipping LOST EUR 55.3M over "
            "151 flips, so expect this to lower the result."
        ),
    ),
    with_competition: bool = typer.Option(
        False,
        "--with-competition",
        help=(
            "Model bid competition (REH-68): bid via SmartBidding and win only by "
            "exceeding what the real buyer paid. Incomplete until profit flipping "
            "lands, so treat the output as a diagnostic, not a season result."
        ),
    ),
    with_flip_buys: bool = typer.Option(
        False,
        "--with-flip-buys",
        help=(
            "Model profit-flip BUYING (REH-71): candidates from the real "
            "ProfitTrader, bid at an economic ceiling. The live bot does this; "
            "--with-flips alone only models the selling half."
        ),
    ),
    pacing: bool = typer.Option(
        True,
        "--pacing/--no-pacing",
        help=(
            "Model capital pacing (REH-85): cap each bid so a reserve for the "
            "moves still needed survives it. Only bites with --with-competition, "
            "since an uncapped listing is bought at the real price regardless. "
            "On by default, matching the shipped pacing_enabled default; "
            "--no-pacing gives the unpaced comparison run."
        ),
    ),
    pacing_min_moves: int | None = typer.Option(
        None,
        "--pacing-min-moves",
        help=(
            "Moves the pacing reserve protects once the squad is full at 15/15. "
            "Defaults to the shipped pacing_in_season_min_moves Settings value; "
            "override to sweep the knob."
        ),
    ),
    pacing_max_reserve_fraction: float | None = typer.Option(
        None,
        "--pacing-max-reserve-fraction",
        help=(
            "REH-101: hard ceiling on the pacing reserve as a fraction of "
            "current budget. Defaults to the shipped "
            "pacing_max_reserve_fraction Settings value; 1.0 reproduces "
            "REH-85's unbounded reserve, 0.0 disables the reserve."
        ),
    ),
    pacing_window_days: int | None = typer.Option(
        None,
        "--pacing-window-days",
        help=(
            "Trailing window, in days, used to measure the median buy price "
            "behind the pacing reserve. Defaults to the shipped "
            "pacing_window_days Settings value; override to sweep the knob."
        ),
    ),
    pacing_min_spendable_moves: float | None = typer.Option(
        None,
        "--pacing-min-spendable-moves",
        help=(
            "REH-107: moves of typical size the reserve must always leave "
            "affordable. Defaults to the shipped pacing_min_spendable_moves "
            "Settings value; 0.0 reproduces REH-101's behaviour, higher "
            "values free up more budget per buy."
        ),
    ),
) -> None:
    """Replay the full bot across 2025/26 and report the counterfactual finish."""
    from rehoboam.replay.driver import run_replay
    from rehoboam.scoring.v2.dataset import TRAIN_MAX_SEASON

    # The replay covers 2025/26. Once that season is inside the training set,
    # the scorer has already seen every result being replayed and the number
    # comes back flattering. Say so loudly rather than let it be quoted as a
    # gate for a scorer change.
    if TRAIN_MAX_SEASON >= "2025/2026":
        console.print(
            "[yellow]LEAKY: the scorer is fitted through "
            f"{TRAIN_MAX_SEASON}, which includes the replayed 2025/26 season. "
            "This result is optimistic and is NOT a valid gate for scoring "
            "changes — compare scorers on a held-out season instead.[/yellow]"
        )

    if not corpus.exists():
        console.print(f"[red]Corpus not found: {corpus}[/red]")
        raise typer.Exit(1)
    if not learning_db.exists():
        console.print(f"[red]Learning DB not found: {learning_db}[/red]")
        raise typer.Exit(1)

    _result, report = run_replay(
        corpus_path=corpus,
        learning_db_path=learning_db,
        min_ep_gain=min_ep_gain,
        with_competition=with_competition,
        with_flips=with_flips,
        with_flip_buys=with_flip_buys,
        pacing_enabled=pacing,
        pacing_min_moves=pacing_min_moves,
        pacing_window_days=pacing_window_days,
        pacing_max_reserve_fraction=pacing_max_reserve_fraction,
        pacing_min_spendable_moves=pacing_min_spendable_moves,
    )
    console.print(report)


@app.command("replay-buy-control")
def replay_buy_control(
    corpus: Path = typer.Option(  # noqa: B008
        Path("logs/training_corpus.db"), help="Path to the training corpus DB"
    ),
    learning_db: Path = typer.Option(  # noqa: B008
        Path("logs/bid_learning.db"), help="Path to the learning DB with real standings"
    ),
) -> None:
    """Control run (REH-67): does EP ranking beat ranking by market value?"""
    from rehoboam.replay.driver import run_buy_control

    if not corpus.exists():
        console.print(f"[red]Corpus not found: {corpus}[/red]")
        raise typer.Exit(1)
    if not learning_db.exists():
        console.print(f"[red]Learning DB not found: {learning_db}[/red]")
        raise typer.Exit(1)

    console.print(run_buy_control(corpus_path=corpus, learning_db_path=learning_db))


@app.command("replay-flip-policy")
def replay_flip_policy(
    corpus: Path = typer.Option(  # noqa: B008
        Path("logs/training_corpus.db"), help="Path to the training corpus DB"
    ),
    learning_db: Path = typer.Option(  # noqa: B008
        Path("logs/bid_learning.db"), help="Path to the learning DB with real standings"
    ),
) -> None:
    """REH-71: 2x2 over flip buys x profit sells, with competition on."""
    from rehoboam.replay.driver import run_flip_policy

    if not corpus.exists():
        console.print(f"[red]Corpus not found: {corpus}[/red]")
        raise typer.Exit(1)
    if not learning_db.exists():
        console.print(f"[red]Learning DB not found: {learning_db}[/red]")
        raise typer.Exit(1)

    console.print(run_flip_policy(corpus_path=corpus, learning_db_path=learning_db))


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

    Trains on seasons up to 2024/25 and reports the train/holdout row split
    against the held-out 2025/26 season. Never fits on the holdout — that
    season is what the whole rebuild is judged against. Does not score
    predictions against the holdout; that's REH-56's job.
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


@app.command("derive-thresholds")
def derive_thresholds(
    league_index: int = typer.Option(0, "--league", help="League index"),
):
    """Measure the v2 marginal-gain distribution and propose decision thresholds.

    The constants in config.py and bidding_strategy.py were calibrated against
    the old 0-100 EP index. On real points they mean something different, and
    the old firing rates cannot be recovered (predicted_eps.marginal_ep_gain is
    NULL on every production row). This measures the real distribution against
    the current squad and market, and proposes thresholds by rarity.

    Read-only: reports numbers, changes nothing.
    """
    from .scoring.decision import DecisionEngine
    from .scoring.v2.thresholds import build_report
    from .trader import Trader

    api, settings, league = _login_and_get_league(league_index)
    trader = Trader(api, settings)
    result = trader.get_ep_recommendations_with_trends(league)

    # NOTE: `get_ep_recommendations_with_trends` returns a **dict**, not a
    # dataclass. Verified keys: buy_recs, trade_pairs, sell_recs, squad_scores,
    # lineup_map, budget, squad_size, squad_players, market_players,
    # market_scores, competitor_player_ids.
    #
    # Do NOT read marginal gains off `buy_recs`: `recommend_buys` returns only
    # the top-N already filtered and ranked, so its gains sample the good tail
    # and would push every derived threshold upward. Thresholds must be measured
    # over ALL market candidates.
    squad_scores = result["squad_scores"]
    squad_players = result["squad_players"]  # {player_id: MarketPlayer}
    market_players = result["market_players"]  # {player_id: MarketPlayer}
    market_scores = result["market_scores"]  # {player_id: PlayerScore}
    squad = list(squad_players.values())

    # calculate_marginal_ep doesn't read min_ep_to_buy/min_ep_upgrade (those
    # only gate recommend_buys/recommend_sells), so defaults are fine here —
    # DecisionEngine has no `settings` kwarg to pass through.
    engine = DecisionEngine()

    gains: list[float] = []
    for pid, player in market_players.items():
        candidate_score = market_scores.get(pid)
        if candidate_score is None:
            continue
        mep = engine.calculate_marginal_ep(
            candidate_score=candidate_score,
            candidate_player=player,
            squad=squad,
            squad_scores=squad_scores,
        )
        gains.append(mep.marginal_ep_gain)

    report = build_report(gains, min_gain=settings.min_ep_upgrade_threshold)

    table = Table(title=f"v2 marginal-gain distribution (n={report.n_candidates} positive)")
    table.add_column("percentile")
    table.add_column("marginal EP gain", justify="right")
    for name, value in report.percentiles.items():
        table.add_row(name, f"{value:.1f}")
    console.print(table)

    console.print(
        f"[cyan]{report.n_qualifying} of {report.n_candidates} candidates clear the "
        f"{settings.min_ep_upgrade_threshold:.1f} buy threshold · tiers by "
        f"{report.method}[/cyan]"
    )
    proposed = Table(title="Proposed tier thresholds")
    proposed.add_column("tier")
    by_rarity = report.method == "rarity"
    proposed.add_column("basis")
    proposed.add_column("threshold", justify="right")
    for name, rarity, multiple in (
        ("must_have", "top 15%", "2.5x"),
        ("strong_upgrade", "top 30%", "1.5x"),
        ("solid_upgrade", "top 50%", "1.0x"),
    ):
        # Labelling a fallback tier "top 15%" would claim a rarity that was
        # never measured.
        basis = rarity if by_rarity else f"{multiple} threshold"
        proposed.add_row(name, basis, f"{report.proposed[name]:.1f}")
    console.print(proposed)
    console.print("[dim]Read-only. Apply these by editing config.py / bidding_strategy.py.[/dim]")


# ---------------------------------------------------------------------------
# The store (spec 2026-09-11 §1): Supabase Postgres via the transaction pooler
# ---------------------------------------------------------------------------


@app.command("migrate")
def migrate_cmd(
    dsn: str | None = typer.Option(
        None, "--dsn", help="Override DATABASE_ADMIN_URL / DATABASE_URL for this run."
    ),
):
    """Apply unapplied store migrations (idempotent)."""
    from .store import StoreUnconfigured, connect
    from .store.migrate import migrate

    try:
        with connect(_admin_dsn(dsn)) as conn:
            applied = migrate(conn)
    except StoreUnconfigured as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from e
    console.print(f"applied {len(applied)} migration(s): {', '.join(applied) or 'none'}")


@app.command("db-bootstrap")
def db_bootstrap_cmd(
    admin_dsn: str | None = typer.Option(
        None,
        "--admin-dsn",
        help="Admin connection string (the postgres user). Defaults to DATABASE_ADMIN_URL / DATABASE_URL.",
    ),
    role_password: str | None = typer.Option(
        None,
        "--role-password",
        help="Password for rehoboam_bot; generated when omitted.",
    ),
):
    """Create the rehoboam_bot role and grant it the rehoboam schema (idempotent)."""
    import secrets

    from .store import StoreUnconfigured, connect
    from .store.bootstrap import ROLE, bootstrap

    generated = role_password is None
    password = role_password or secrets.token_urlsafe(24)
    try:
        with connect(_admin_dsn(admin_dsn)) as conn:
            result = bootstrap(conn, password)
    except StoreUnconfigured as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from e
    if result["role_created"]:
        console.print(f"[green]created role {ROLE}[/green]")
        if generated:
            console.print(
                "[yellow]Generated password (shown once — put it in Key Vault and .env):[/yellow]"
            )
            console.print(password)
        console.print(
            f"[dim]Pooler username for this role is {ROLE}.<project-ref>; "
            "port 6543; database postgres.[/dim]"
        )
    else:
        console.print(
            f"role {ROLE} already existed; grants refreshed, password unchanged "
            f"— rotate with: alter role {ROLE} password '…'"
        )


@app.command("import-sqlite")
def import_sqlite_cmd(
    learning: Path = typer.Option(  # noqa: B008
        Path("logs/bid_learning.db"), "--learning", help="bid_learning.db to import."
    ),
    corpus: Path = typer.Option(  # noqa: B008
        Path("logs/training_corpus.db"),
        "--corpus",
        help="training_corpus.db to import.",
    ),
    cache: Path = typer.Option(  # noqa: B008
        Path("logs/player_history.db"), "--cache", help="player_history.db to import."
    ),
    dsn: str | None = typer.Option(None, "--dsn", help="Override DATABASE_URL for this run."),
):
    """Copy the SQLite state into the store; safe to re-run (rows never duplicate)."""
    from .store import StoreUnconfigured, connect
    from .store.import_sqlite import import_all
    from .store.migrate import migrate

    try:
        with connect(_admin_dsn(dsn)) as conn:
            migrate(conn)
            reports = import_all(conn, learning=learning, corpus=corpus, cache=cache)
    except StoreUnconfigured as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from e
    table = Table(title="import-sqlite")
    table.add_column("table")
    table.add_column("sqlite rows", justify="right")
    table.add_column("postgres rows (total)", justify="right")
    table.add_column("skipped columns")
    any_skipped = False
    for r in reports:
        sqlite_rows = "absent" if r.sqlite_rows < 0 else str(r.sqlite_rows)
        skipped = ", ".join(r.skipped_columns) if r.skipped_columns else "—"
        if r.skipped_columns:
            any_skipped = True
        table.add_row(r.table, sqlite_rows, str(r.postgres_rows), skipped)
    console.print(table)
    if any_skipped:
        console.print(
            "[yellow]Some source columns have no home in the store — "
            "see the skipped columns above.[/yellow]"
        )


@app.command("corpus-pull")
def corpus_pull_cmd(
    out: Path = typer.Option(  # noqa: B008
        Path("logs/training_corpus.db"),
        "--out",
        help="SQLite file for the corpus tables.",
    ),
    learning_out: Path = typer.Option(  # noqa: B008
        Path("logs/bid_learning.db"),
        "--learning-out",
        help="SQLite file for the replay's learning tables (flip_outcomes, "
        "matchday_lineup_results, league_rank_history).",
    ),
    dsn: str | None = typer.Option(None, "--dsn", help="Override DATABASE_URL for this run."),
):
    """Write the corpus and the replay's learning tables from the store into local SQLite files."""
    from .store import StoreUnconfigured, connect
    from .store.corpus_pull import pull_corpus, pull_replay_tables

    try:
        with connect(dsn) as conn:
            written = pull_corpus(conn, out)
            replay_written = pull_replay_tables(conn, learning_out)
    except StoreUnconfigured as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from e
    for name, n in written.items():
        console.print(f"{name}: {n} row(s) written")
    console.print(f"[green]corpus written to {out}[/green]")
    for name, n in replay_written.items():
        console.print(f"{name}: {n} row(s) written")
    console.print(f"[green]replay tables written to {learning_out}[/green]")


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
