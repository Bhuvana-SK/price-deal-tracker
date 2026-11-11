"""Command-line entry point: `tracker --help`."""

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import typer

from tracker import __version__
from tracker.domain import Item, ItemQuery
from tracker.ingest.layout_guard import check_layout
from tracker.ingest.pipeline import run_ingest
from tracker.ingest.validate import validate_all
from tracker.items import load_items_file, slugify
from tracker.log import configure_logging
from tracker.settings import get_settings
from tracker.sources import SOURCE_NAMES, get_source
from tracker.sources.base import utcnow
from tracker.sources.mock import hourly
from tracker.storage import repo
from tracker.storage.db import connect
from tracker.transform import run_dbt

app = typer.Typer(help="Price & Deal Tracker", no_args_is_help=True, add_completion=False)


@app.callback()
def main() -> None:
    """Configure logging before any command runs."""
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)


@app.command("init-db")
def init_db() -> None:
    """Create the DuckDB file and all tables (safe to re-run)."""
    with connect():
        pass
    typer.echo(f"database ready at {get_settings().db_path}")


@app.command()
def seed(file: Path | None = typer.Option(None, help="Watchlist YAML")) -> None:
    """Load (or update) items from the watchlist file."""
    items = load_items_file(file or get_settings().items_file)
    with connect() as conn:
        for item in items:
            repo.upsert_item(conn, item, utcnow())
    typer.echo(f"seeded {len(items)} items")


@app.command("add-item")
def add_item(
    name: str = typer.Argument(..., help="Display name"),
    query: str = typer.Option(..., "--query", "-q", help="Search term or product URL"),
    target: float | None = typer.Option(None, "--target", "-t", help="Alert below this price"),
    source: list[str] = typer.Option(["mock"], "--source", "-s", help="Repeat for several"),
    currency: str = typer.Option("USD"),
) -> None:
    """Track a new item."""
    unknown = set(source) - set(SOURCE_NAMES)
    if unknown:
        raise typer.BadParameter(f"unknown source(s) {sorted(unknown)}; use {SOURCE_NAMES}")
    item = Item(
        item_id=slugify(name),
        name=name,
        query=query,
        target_price=Decimal(str(target)) if target is not None else None,
        currency=currency,
        sources=source,
    )
    with connect() as conn:
        repo.upsert_item(conn, item, utcnow())
    typer.echo(f"tracking {item.item_id} via {', '.join(item.sources)}")


@app.command("items")
def list_items() -> None:
    """Show tracked items."""
    with connect() as conn:
        for item in repo.list_items(conn):
            target = f"{item.currency} {item.target_price}" if item.target_price else "-"
            typer.echo(f"{item.item_id:<22} target={target:<12} [{','.join(item.sources)}]")


@app.command()
def check(source: list[str] = typer.Option(list(SOURCE_NAMES), "--source", "-s")) -> None:
    """Run one price check now for every tracked item (the scheduled job)."""
    with connect() as conn:
        items = repo.list_items(conn)
        for name in source:
            if not any(name in i.sources for i in items):
                continue
            _print_run(run_ingest(conn, get_source(name), items))


@app.command()
def backfill(
    days: int = typer.Option(120, min=1, max=730),
    step_hours: int = typer.Option(6, min=1, max=24),
) -> None:
    """Generate past price history from the mock source (for demos and the backtest)."""
    end = utcnow().replace(minute=0, second=0, microsecond=0)
    timestamps = hourly(end - timedelta(days=days), end, step_hours)
    with connect() as conn:
        items = repo.list_items(conn)
        run = run_ingest(conn, get_source("mock"), items, timestamps, kind="backfill")
    _print_run(run)


@app.command()
def transform(select: str | None = typer.Option(None, help="dbt node selector")) -> None:
    """Build the silver/gold dbt models and run all data tests."""
    outcome = run_dbt("build", select=select)
    typer.echo(f"dbt build: success={outcome.success} {outcome.counts}")
    for name in outcome.failures:
        typer.echo(f"  failed: {name}")
    if not outcome.success:
        raise typer.Exit(code=1)


@app.command()
def deals() -> None:
    """Show each listing's current price and the 'is this really a deal?' verdict."""
    with connect(read_only=True) as conn:
        rows = conn.execute(
            """
            SELECT item_id, store, currency, current_price, avg_30d, min_90d, all_time_low,
                   pct_vs_avg_30d, deal_flag, is_below_target, deal_reason
            FROM gold.mart_listing_current
            ORDER BY deal_flag DESC, pct_vs_avg_30d NULLS LAST
            """
        ).fetchall()
    typer.echo(
        f"{'item':<20} {'store':<15} {'price':>9} {'30d avg':>9} {'90d low':>9} {'ATL':>9} "
        f"{'vs 30d':>7}  verdict"
    )
    for r in rows:
        pct = f"{r[7] * 100:+.0f}%" if r[7] is not None else "-"
        flags = ("DEAL " if r[8] else "") + ("<=TARGET " if r[9] else "")
        typer.echo(
            f"{r[0]:<20} {r[1]:<15} {r[3]:>9} {_fmt(r[4]):>9} {_fmt(r[5]):>9} {_fmt(r[6]):>9} "
            f"{pct:>7}  {flags}{r[10]}"
        )


@app.command()
def runs(limit: int = typer.Option(10)) -> None:
    """Show recent ingest runs and data-quality counters."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT started_at, source, kind, status, n_attempted, n_loaded, n_duplicate, "
            "n_rejected, n_parse_failures, layout_broken FROM bronze.ingest_runs "
            "ORDER BY started_at DESC LIMIT ?",
            [limit],
        ).fetchall()
        reasons = conn.execute(
            "SELECT reason, count(*) FROM bronze.rejected_observations GROUP BY 1 ORDER BY 2 DESC"
        ).fetchall()
    typer.echo("started_at           source          kind      status   att  load  dup  rej  pf")
    for r in rows:
        layout = "  LAYOUT CHANGED" if r[9] else ""
        typer.echo(
            f"{r[0]:%Y-%m-%d %H:%M:%S}  {r[1]:<15} {r[2]:<9} {r[3]:<8} {r[4]:>4} {r[5]:>5} "
            f"{r[6]:>4} {r[7]:>4} {r[8]:>3}{layout}"
        )
    typer.echo("rejected (all time): " + (", ".join(f"{k}={v}" for k, v in reasons) or "none"))


@app.command()
def fetch(
    source: str = typer.Argument(..., help=f"One of: {', '.join(SOURCE_NAMES)}"),
    query: str = typer.Argument(..., help="Search term or product URL"),
) -> None:
    """Fetch and validate prices once, without storing them (a dry run)."""
    result = get_source(source).fetch([ItemQuery(item_id="adhoc", query=query)])
    report = validate_all(result.listings, reference=lambda _listing: None)
    layout = check_layout(result)
    for listing in report.accepted:
        stock = "" if listing.in_stock else "  (out of stock)"
        typer.echo(
            f"OK   {listing.source_listing_id:<28} {listing.currency} {listing.price:>9} "
            f"+ship {listing.shipping:>5}  {listing.title[:50]}{stock}"
        )
    for r in report.rejected:
        typer.echo(f"REJ  {r.listing.source_listing_id:<28} {r.reason.value}: {r.detail}")
    typer.echo(
        f"\nattempted={result.attempted} accepted={len(report.accepted)} "
        f"rejected={len(report.rejected)} parse_failures={result.parse_failures} "
        f"layout_broken={layout.broken} errors={len(result.errors)}"
    )


def _fmt(value: object) -> str:
    """Format an optional number with 2 decimals."""
    return "-" if value is None else f"{float(value):.2f}"  # type: ignore[arg-type]


def _print_run(run: repo.RunRecord) -> None:
    typer.echo(
        f"[{run.source}/{run.kind}] status={run.status} attempted={run.attempted} "
        f"loaded={run.loaded} duplicate={run.duplicate} rejected={run.rejected} "
        f"parse_failures={run.parse_failures} layout_broken={run.layout_broken}"
    )
    for err in run.errors:
        typer.echo(f"  error: {err}")
