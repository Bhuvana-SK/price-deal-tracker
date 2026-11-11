"""Detect when a source's page/API layout changes and parsing starts failing."""

from dataclasses import dataclass

from tracker.domain import FetchResult
from tracker.log import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class LayoutStatus:
    """Outcome of the layout check for one fetch."""

    source: str
    broken: bool
    failure_rate: float
    attempted: int


def check_layout(
    result: FetchResult, threshold: float = 0.5, min_attempted: int = 3
) -> LayoutStatus:
    """Flag a source as broken when most of its records fail to parse.

    A single bad record is noise; a majority failing at once almost always means
    the source changed its markup or API shape.
    """
    rate = result.parse_failures / result.attempted if result.attempted else 0.0
    broken = result.attempted >= min_attempted and rate >= threshold
    if broken:
        log.warning(
            "source_layout_changed",
            source=result.source,
            failure_rate=round(rate, 2),
            attempted=result.attempted,
        )
    return LayoutStatus(result.source, broken, rate, result.attempted)
