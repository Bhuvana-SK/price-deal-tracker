"""Read/write functions for the app and bronze tables. All SQL lives here."""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from tracker.alerts.dedupe import LastSent
from tracker.alerts.models import Alert, ListingState, Rule
from tracker.domain import Item, RawListing, Rejection
from tracker.storage.db import Connection, bulk_insert, to_db_ts


def observation_key(listing: RawListing, window_hours: int) -> str:
    """Stable key for one listing in one check window (the idempotency key)."""
    ts = to_db_ts(listing.fetched_at)
    window_start = ts - timedelta(
        hours=ts.hour % window_hours,
        minutes=ts.minute,
        seconds=ts.second,
        microseconds=ts.microsecond,
    )
    raw = f"{listing.source}|{listing.source_listing_id}|{window_start.isoformat()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


# ------------------------------------------------------------------------------ items
def upsert_item(conn: Connection, item: Item, now: datetime) -> None:
    """Insert an item or update its settings (keeps created_at)."""
    conn.execute(
        """
        INSERT INTO app.items
            (item_id, name, query, target_price, currency, sources, active, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (item_id) DO UPDATE SET
            name = excluded.name, query = excluded.query, target_price = excluded.target_price,
            currency = excluded.currency, sources = excluded.sources, active = excluded.active
        """,
        [
            item.item_id,
            item.name,
            item.query,
            item.target_price,
            item.currency,
            item.sources,
            item.active,
            to_db_ts(now),
        ],
    )


def list_items(conn: Connection, active_only: bool = True) -> list[Item]:
    """All tracked items, ordered by id."""
    where = "WHERE active" if active_only else ""
    rows = conn.execute(
        "SELECT item_id, name, query, target_price, currency, sources, active "
        f"FROM app.items {where} ORDER BY item_id"
    ).fetchall()
    return [
        Item(
            item_id=r[0],
            name=r[1],
            query=r[2],
            target_price=r[3],
            currency=r[4],
            sources=list(r[5]),
            active=r[6],
        )
        for r in rows
    ]


# ------------------------------------------------------------------------------ history
def recent_prices(conn: Connection, per_listing: int = 20) -> dict[str, list[Decimal]]:
    """The last N accepted prices per listing, oldest first (validator reference)."""
    rows = conn.execute(
        """
        SELECT listing_id, price FROM (
            SELECT listing_id, price, fetched_at,
                   row_number() OVER (PARTITION BY listing_id ORDER BY fetched_at DESC) AS rn
            FROM bronze.raw_observations
        ) WHERE rn <= ? ORDER BY listing_id, fetched_at
        """,
        [per_listing],
    ).fetchall()
    out: dict[str, list[Decimal]] = {}
    for listing_id, price in rows:
        out.setdefault(listing_id, []).append(price)
    return out


# ------------------------------------------------------------------------------ runs
@dataclass
class RunRecord:
    """Counters written to bronze.ingest_runs when a run finishes."""

    run_id: str
    source: str
    kind: str
    started_at: datetime
    status: str = "running"
    finished_at: datetime | None = None
    attempted: int = 0
    parse_failures: int = 0
    loaded: int = 0
    duplicate: int = 0
    rejected: int = 0
    layout_broken: bool = False
    errors: tuple[str, ...] = ()


def start_run(conn: Connection, run: RunRecord) -> None:
    """Record that a run started (so crashed runs stay visible as 'running')."""
    conn.execute(
        "INSERT INTO bronze.ingest_runs (run_id, source, kind, status, started_at) "
        "VALUES (?, ?, ?, 'running', ?)",
        [run.run_id, run.source, run.kind, to_db_ts(run.started_at)],
    )


def finish_run(conn: Connection, run: RunRecord) -> None:
    """Write final counters and status."""
    conn.execute(
        """
        UPDATE bronze.ingest_runs SET
            status = ?, finished_at = ?, n_attempted = ?, n_parse_failures = ?, n_loaded = ?,
            n_duplicate = ?, n_rejected = ?, layout_broken = ?, errors = ?
        WHERE run_id = ?
        """,
        [
            run.status,
            to_db_ts(run.finished_at) if run.finished_at else None,
            run.attempted,
            run.parse_failures,
            run.loaded,
            run.duplicate,
            run.rejected,
            run.layout_broken,
            list(run.errors),
            run.run_id,
        ],
    )


# ------------------------------------------------------------------------------ loads
def upsert_listings(conn: Connection, listings: Sequence[RawListing]) -> None:
    """Register listings and keep title/url/last_seen current."""
    latest: dict[str, RawListing] = {}
    first: dict[str, datetime] = {}
    for listing in listings:
        lid = listing.listing_id
        if lid not in latest or listing.fetched_at > latest[lid].fetched_at:
            latest[lid] = listing
        first[lid] = min(first.get(lid, listing.fetched_at), listing.fetched_at)
    rows = list(latest.values())
    bulk_insert(
        conn,
        "app.listings",
        [
            ("listing_id", "VARCHAR", [r.listing_id for r in rows]),
            ("source", "VARCHAR", [r.source for r in rows]),
            ("source_listing_id", "VARCHAR", [r.source_listing_id for r in rows]),
            ("title", "VARCHAR", [r.title for r in rows]),
            ("url", "VARCHAR", [r.url for r in rows]),
            ("currency", "VARCHAR", [r.currency for r in rows]),
            ("first_seen_at", "TIMESTAMP", [to_db_ts(first[r.listing_id]) for r in rows]),
            ("last_seen_at", "TIMESTAMP", [to_db_ts(r.fetched_at) for r in rows]),
        ],
        on_conflict="""(listing_id) DO UPDATE SET
            title = excluded.title, url = excluded.url,
            first_seen_at = least(app.listings.first_seen_at, excluded.first_seen_at),
            last_seen_at = greatest(app.listings.last_seen_at, excluded.last_seen_at)""",
    )


def link_item_listings(conn: Connection, listings: Sequence[RawListing], now: datetime) -> None:
    """Remember which item's query surfaced each listing."""
    pairs = sorted({(r.item_id, r.listing_id) for r in listings})
    bulk_insert(
        conn,
        "app.item_listings",
        [
            ("item_id", "VARCHAR", [p[0] for p in pairs]),
            ("listing_id", "VARCHAR", [p[1] for p in pairs]),
            ("created_at", "TIMESTAMP", [to_db_ts(now)] * len(pairs)),
        ],
    )


def insert_observations(
    conn: Connection, run_id: str, listings: Sequence[RawListing], window_hours: int, now: datetime
) -> int:
    """Append validated observations; returns how many were new (not duplicates)."""
    listings = _unique_by_key(listings, window_hours)
    bulk_insert(
        conn,
        "bronze.raw_observations",
        [
            ("observation_key", "VARCHAR", [observation_key(r, window_hours) for r in listings]),
            ("run_id", "VARCHAR", [run_id] * len(listings)),
            ("listing_id", "VARCHAR", [r.listing_id for r in listings]),
            ("source", "VARCHAR", [r.source for r in listings]),
            ("source_listing_id", "VARCHAR", [r.source_listing_id for r in listings]),
            ("title", "VARCHAR", [r.title for r in listings]),
            ("url", "VARCHAR", [r.url for r in listings]),
            ("price", "DECIMAL(12,2)", [r.price for r in listings]),
            ("shipping", "DECIMAL(12,2)", [r.shipping for r in listings]),
            ("currency", "VARCHAR", [r.currency for r in listings]),
            ("in_stock", "BOOLEAN", [r.in_stock for r in listings]),
            ("raw_payload", "JSON", [json.dumps(r.raw, default=str) for r in listings]),
            ("fetched_at", "TIMESTAMP", [to_db_ts(r.fetched_at) for r in listings]),
            ("loaded_at", "TIMESTAMP", [to_db_ts(now)] * len(listings)),
        ],
    )
    return _count_for_run(conn, "bronze.raw_observations", run_id)


def insert_rejections(
    conn: Connection, run_id: str, rejections: Sequence[Rejection], window_hours: int, now: datetime
) -> int:
    """Store rejected observations; returns how many were new."""
    seen: dict[str, Rejection] = {}
    for r in rejections:
        seen.setdefault(observation_key(r.listing, window_hours), r)
    rejections = list(seen.values())
    ls = [r.listing for r in rejections]
    bulk_insert(
        conn,
        "bronze.rejected_observations",
        [
            ("observation_key", "VARCHAR", [observation_key(x, window_hours) for x in ls]),
            ("run_id", "VARCHAR", [run_id] * len(ls)),
            ("listing_id", "VARCHAR", [x.listing_id for x in ls]),
            ("source", "VARCHAR", [x.source for x in ls]),
            ("reason", "VARCHAR", [r.reason.value for r in rejections]),
            ("detail", "VARCHAR", [r.detail for r in rejections]),
            ("raw_price", "VARCHAR", [None if x.price is None else str(x.price) for x in ls]),
            ("fetched_at", "TIMESTAMP", [to_db_ts(x.fetched_at) for x in ls]),
            ("detected_at", "TIMESTAMP", [to_db_ts(now)] * len(ls)),
        ],
    )
    return _count_for_run(conn, "bronze.rejected_observations", run_id)


def _unique_by_key(listings: Sequence[RawListing], window_hours: int) -> list[RawListing]:
    """Drop repeats within one batch (e.g. two items' searches surfacing one listing)."""
    seen: dict[str, RawListing] = {}
    for listing in listings:
        seen.setdefault(observation_key(listing, window_hours), listing)
    return list(seen.values())


def _count_for_run(conn: Connection, table: str, run_id: str) -> int:
    row = conn.execute(f"SELECT count(*) FROM {table} WHERE run_id = ?", [run_id]).fetchone()
    return int(row[0]) if row else 0


# ------------------------------------------------------------------------------ alerts
_STATE_COLUMNS = (
    "item_id, item_name, listing_id, store, title, url, currency, current_price, "
    "current_total_cost, in_stock, as_of, target_price, pct_drop_24h, avg_30d, min_90d, "
    "all_time_low, is_90d_low, has_enough_history, deal_flag, deal_reason"
)


def current_listing_states(conn: Connection) -> list[ListingState]:
    """Read gold.mart_listing_current (built by dbt)."""
    cursor = conn.execute(f"SELECT {_STATE_COLUMNS} FROM gold.mart_listing_current")
    names = [d[0] for d in cursor.description]
    return [ListingState(**dict(zip(names, row, strict=True))) for row in cursor.fetchall()]


def list_rules(conn: Connection) -> list[Rule]:
    """Active custom rules."""
    rows = conn.execute(
        "SELECT rule_id, rule_type, item_id, threshold FROM app.rules WHERE active ORDER BY rule_id"
    ).fetchall()
    return [Rule(rule_id=r[0], rule_type=r[1], item_id=r[2], threshold=r[3]) for r in rows]


def add_rule(
    conn: Connection, rule: Rule, now: datetime, raw_text: str | None = None, via: str = "cli"
) -> None:
    """Insert or replace a custom rule."""
    conn.execute(
        """
        INSERT INTO app.rules (rule_id, item_id, rule_type, threshold, raw_text, created_via, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (rule_id) DO UPDATE SET item_id = excluded.item_id,
            rule_type = excluded.rule_type, threshold = excluded.threshold,
            raw_text = excluded.raw_text, active = TRUE
        """,
        [
            rule.rule_id,
            rule.item_id,
            rule.rule_type.value,
            rule.threshold,
            raw_text,
            via,
            to_db_ts(now),
        ],
    )


def last_sent_alert(conn: Connection, subject_id: str, rule_id: str) -> LastSent | None:
    """Most recent *successful* alert for this subject and rule."""
    row = conn.execute(
        "SELECT price, sent_at FROM app.alerts_sent WHERE subject_id = ? AND rule_id = ? "
        "AND status = 'sent' ORDER BY sent_at DESC LIMIT 1",
        [subject_id, rule_id],
    ).fetchone()
    return LastSent(price=row[0], sent_at=row[1].replace(tzinfo=UTC)) if row else None


def record_alert(
    conn: Connection,
    *,
    key: str,
    alert: Alert,
    channels: Sequence[str],
    status: str,
    error: str | None,
    now: datetime,
) -> None:
    """Log a send attempt. A later retry of a failed alert overwrites its row."""
    conn.execute(
        """
        INSERT INTO app.alerts_sent (alert_key, subject_id, rule_id, rule_type, listing_id, price,
                                     currency, message, channels, status, error, sent_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (alert_key) DO UPDATE SET channels = excluded.channels,
            status = excluded.status, error = excluded.error, sent_at = excluded.sent_at
        """,
        [
            key,
            alert.subject_id,
            alert.rule_id,
            alert.rule_type,
            alert.listing_id,
            alert.price,
            alert.currency,
            alert.text(),
            list(channels),
            status,
            error,
            to_db_ts(now),
        ],
    )
