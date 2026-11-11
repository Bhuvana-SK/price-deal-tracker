"""Backtest the deal rule against naive baselines on mock history with known answers.

The mock source plants genuine sales and fake "raise then discount" deals, so
every listing-day has a ground-truth label. Each strategy is a SQL condition over
gold.fct_listing_daily_signals (which only looks backwards, so no leakage).
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from tracker.ingest.pipeline import run_ingest
from tracker.items import load_items_file
from tracker.sources.base import utcnow
from tracker.sources.mock import DEFAULT_SEED, MockSource, PriceEvent, event_at, hourly
from tracker.storage import repo
from tracker.storage.db import Connection, connect
from tracker.transform import run_dbt

STRATEGIES: dict[str, str] = {
    "Naive: 10%+ below 30-day avg": "coalesce(pct_vs_avg_30d <= -0.10, false)",
    "Naive: 10%+ drop vs. yesterday": (
        "coalesce((prev_close_price - close_price) / prev_close_price >= 0.10, false)"
    ),
    "Naive: at 90-day low": "is_90d_low",
    "Deal rule (this project)": "deal_flag",
}
Labeler = Callable[[str, date], PriceEvent | None]


@dataclass(frozen=True)
class Day:
    """One listing-day with each strategy's yes/no."""

    listing_id: str
    price_date: date
    flags: dict[str, bool]


@dataclass(frozen=True)
class StrategyResult:
    """How one strategy did against the planted ground truth."""

    name: str
    flagged_days: int
    precision: float  # share of flagged days that were inside a genuine sale
    sales_caught: int  # genuine sale events with at least one flagged day
    sales_total: int
    fakes_flagged: int  # fake-deal events with at least one flagged day
    fakes_total: int
    false_alarms_per_listing_month: float

    @property
    def sale_recall(self) -> float:
        """Share of genuine sales caught."""
        return self.sales_caught / self.sales_total if self.sales_total else 0.0


def mock_label(listing_id: str, day: date) -> PriceEvent | None:
    """Ground truth for a mock listing on a day."""
    noon = datetime.combine(day, time(12), UTC)
    return event_at(DEFAULT_SEED, listing_id.removeprefix("mock:"), noon)


def score(days: Sequence[Day], label: Labeler, strategy: str) -> StrategyResult:
    """Score one strategy at the day level (precision) and event level (recall)."""
    caught: dict[tuple[str, str, int], bool] = {}  # (listing, kind, start_day) -> any day flagged
    flagged = true_pos = 0
    for d in days:
        event = label(d.listing_id, d.price_date)
        hit = d.flags[strategy]
        flagged += hit
        if event is None or event.kind == "inflated":
            continue
        true_pos += hit and event.kind == "genuine_deal"
        key = (d.listing_id, event.kind, event.start_day)
        caught[key] = caught.get(key, False) or hit
    sales = {k: v for k, v in caught.items() if k[1] == "genuine_deal"}
    fakes = {k: v for k, v in caught.items() if k[1] == "fake_deal"}
    listing_months = len(days) / 30 or 1
    return StrategyResult(
        name=strategy,
        flagged_days=flagged,
        precision=true_pos / flagged if flagged else 0.0,
        sales_caught=sum(sales.values()),
        sales_total=len(sales),
        fakes_flagged=sum(fakes.values()),
        fakes_total=len(fakes),
        false_alarms_per_listing_month=(flagged - true_pos) / listing_months,
    )


def load_days(conn: Connection) -> list[Day]:
    """Listing-days with enough history, one row per (listing, day), mock source only."""
    exprs = ", ".join(f"{sql} AS s{i}" for i, sql in enumerate(STRATEGIES.values()))
    rows = conn.execute(
        f"""
        SELECT DISTINCT ON (listing_id, price_date) listing_id, price_date, {exprs}
        FROM gold.fct_listing_daily_signals
        WHERE source = 'mock' AND has_enough_history
        ORDER BY listing_id, price_date
        """
    ).fetchall()
    names = list(STRATEGIES)
    return [Day(r[0], r[1], {n: bool(r[2 + i]) for i, n in enumerate(names)}) for r in rows]


def build_backtest_db(db_path: Path, items_file: Path, days: int) -> None:
    """Fresh DB with `days` of mock history for every mock item, transformed by dbt."""
    db_path.unlink(missing_ok=True)
    items = [i for i in load_items_file(items_file) if "mock" in i.sources]
    end = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    with connect(db_path) as conn:
        for item in items:
            repo.upsert_item(conn, item, end)
        run_ingest(
            conn, MockSource(), items, hourly(end - timedelta(days=days), end, 6), "backfill"
        )
    outcome = run_dbt("build", db_path=db_path)
    if not outcome.success:
        raise RuntimeError(f"dbt build failed: {outcome.failures}")


def to_markdown(results: Sequence[StrategyResult], n_days: int, n_listings: int) -> str:
    """Results table for the README."""
    lines = [
        f"Backtest on {n_days:,} listing-days ({n_listings} mock listings, "
        f"seed {DEFAULT_SEED}; first 14 days of each listing excluded as warm-up).",
        "",
        "| Strategy | Days flagged | Precision | Genuine sales caught | Fake deals flagged "
        "| False alarms / listing / month |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        lines.append(
            f"| {r.name} | {r.flagged_days:,} | {r.precision:.0%} | "
            f"{r.sales_caught}/{r.sales_total} ({r.sale_recall:.0%}) | "
            f"{r.fakes_flagged}/{r.fakes_total} | {r.false_alarms_per_listing_month:.2f} |"
        )
    return "\n".join(lines) + "\n"


def run_backtest(db_path: Path, items_file: Path, days: int, out: Path | None) -> str:
    """Build the data, score every strategy, optionally write the markdown table."""
    build_backtest_db(db_path, items_file, days)
    with connect(db_path) as conn:
        all_days = load_days(conn)
    results = [score(all_days, mock_label, s) for s in STRATEGIES]
    table = to_markdown(results, len(all_days), len({d.listing_id for d in all_days}))
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(table, encoding="utf-8")
    return table
