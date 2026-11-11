"""Command-line entry point: `tracker --help`."""

import typer

from tracker import __version__
from tracker.log import configure_logging, get_logger
from tracker.settings import get_settings

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
