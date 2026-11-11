"""One ingest run: fetch → validate → load, recorded in bronze.ingest_runs."""

import statistics
from collections import deque
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from tracker.domain import Item, RawListing, Rejection
from tracker.ingest.layout_guard import check_layout
from tracker.ingest.validate import validate_all
from tracker.log import get_logger
from tracker.settings import get_settings
from tracker.sources.base import Source, utcnow
from tracker.storage import repo
from tracker.storage.db import Connection, transaction

log = get_logger(__name__)


class ReferenceTracker:
    """Rolling median of each listing's recent accepted prices (for outlier checks)."""

    def __init__(self, history: dict[str, list[Decimal]], window: int = 20) -> None:
        self._window = window
        self._prices = {lid: deque(p, maxlen=window) for lid, p in history.items()}

    def reference(self, listing: RawListing) -> Decimal | None:
        """Typical recent price for this listing, or None with no history."""
        prices = self._prices.get(listing.listing_id)
        return statistics.median(prices) if prices else None

    def observe(self, listing: RawListing) -> None:
        """Add an accepted price to the listing's window."""
        if listing.price is not None:
            self._prices.setdefault(listing.listing_id, deque(maxlen=self._window)).append(
                listing.price
            )


def run_ingest(
    conn: Connection,
    source: Source,
    items: Sequence[Item],
    timestamps: Sequence[datetime | None] = (None,),
    kind: str = "check",
) -> repo.RunRecord:
    """Fetch `items` from `source` at each timestamp (None = now) and load the results.

    A backfill passes many past timestamps (mock source only); a scheduled check
    passes just `None`. Either way it is one run and one transaction, so a failure
    leaves no half-loaded data.
    """
    window_hours = get_settings().check_window_hours
    run = repo.RunRecord(run_id=uuid4().hex, source=source.name, kind=kind, started_at=utcnow())
    repo.start_run(conn, run)
    queries = [item.to_query() for item in items if source.name in item.sources]
    tracker = ReferenceTracker(repo.recent_prices(conn))
    accepted: list[RawListing] = []
    rejected: list[Rejection] = []
    errors: list[str] = []

    for ts in timestamps:
        result = source.fetch(queries, now=ts)
        run.attempted += result.attempted
        run.parse_failures += result.parse_failures
        run.layout_broken |= check_layout(result).broken
        errors += result.errors
        report = validate_all(result.listings, tracker.reference)
        for listing in report.accepted:
            tracker.observe(listing)
        accepted += report.accepted
        rejected += report.rejected

    now = utcnow()
    with transaction(conn):
        repo.upsert_listings(conn, accepted)
        repo.link_item_listings(conn, accepted, now)
        run.loaded = repo.insert_observations(conn, run.run_id, accepted, window_hours, now)
        run.rejected = repo.insert_rejections(conn, run.run_id, rejected, window_hours, now)
    run.duplicate = len(accepted) - run.loaded
    run.errors = tuple(dict.fromkeys(errors))  # dedupe, keep order
    run.status = _status(run)
    run.finished_at = utcnow()
    repo.finish_run(conn, run)
    log.info(
        "ingest_run_finished",
        run_id=run.run_id,
        source=run.source,
        kind=kind,
        status=run.status,
        attempted=run.attempted,
        loaded=run.loaded,
        duplicate=run.duplicate,
        rejected=run.rejected,
        rejected_by_reason=_reason_counts(rejected),
        parse_failures=run.parse_failures,
        layout_broken=run.layout_broken,
        errors=len(run.errors),
    )
    return run


def _status(run: repo.RunRecord) -> str:
    """success: clean; partial: some errors or a layout problem; failed: nothing usable."""
    if run.errors and run.attempted == 0:
        return "failed"
    if run.errors or run.layout_broken:
        return "partial"
    return "success"


def _reason_counts(rejections: Sequence[Rejection]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rejections:
        out[r.reason.value] = out.get(r.reason.value, 0) + 1
    return out
