"""Command-line entry point: `tracker --help`."""

import typer

from tracker import __version__
from tracker.domain import ItemQuery
from tracker.ingest.layout_guard import check_layout
from tracker.ingest.validate import validate_all
from tracker.log import configure_logging, get_logger
from tracker.settings import get_settings
from tracker.sources import SOURCE_NAMES, get_source

app = typer.Typer(help="Price & Deal Tracker", no_args_is_help=True, add_completion=False)


@app.callback()
def main() -> None:
    """Configure logging before any command runs."""
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)


@app.command()
def version() -> None:
    """Print the package version."""
    get_logger("cli").debug("version_requested")
    typer.echo(__version__)


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
