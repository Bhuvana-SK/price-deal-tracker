"""Runs the real dbt project against a temporary DuckDB file (offline)."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from tracker.domain import Item
from tracker.ingest.pipeline import run_ingest
from tracker.settings import get_settings
from tracker.sources.mock import MockSource, hourly
from tracker.storage import repo
from tracker.storage.db import Connection, connect
from tracker.transform import run_dbt

pytestmark = pytest.mark.integration
DBT_PACKAGES = Path("dbt/dbt_packages/dbt_utils")
END = datetime(2026, 9, 1, tzinfo=UTC)
ITEMS = [
    Item(item_id="hp", name="Headphones", query="auralis headphones", target_price=230),
    Item(item_id="tv", name="TV", query="lumora oled"),
]

if not DBT_PACKAGES.exists():  # pragma: no cover
    pytest.skip("run `make dbt-deps` first", allow_module_level=True)


def load(conn: Connection, items: list[Item], days: int, step_hours: int) -> None:
    """Register items (as the CLI does) and backfill mock history ending at END."""
    for item in items:
        repo.upsert_item(conn, item, END)
    timestamps = hourly(END - timedelta(days=days), END, step_hours)
    run_ingest(conn, MockSource(), items, timestamps, "backfill")


@pytest.fixture(scope="module")
def built_db(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """A DB with 60 days of mock history, transformed by dbt once for the module."""
    get_settings.cache_clear()
    db = tmp_path_factory.mktemp("dbt") / "t.duckdb"
    with connect(db) as conn:
        load(conn, ITEMS, days=60, step_hours=6)
    outcome = run_dbt("build", db_path=db)
    assert outcome.success, outcome.failures
    yield db


def query(db: Path, sql: str) -> list[tuple[object, ...]]:
    # Not read_only: dbt-duckdb keeps its own connection open in this process, and DuckDB
    # refuses a second in-process connection with a different configuration.
    with duckdb.connect(str(db)) as conn:
        return conn.execute(sql).fetchall()


def test_current_mart_has_one_row_per_listing(built_db: Path) -> None:
    rows = query(
        built_db, "SELECT item_id, count(*) FROM gold.mart_listing_current GROUP BY 1 ORDER BY 1"
    )
    assert rows == [("hp", 3), ("tv", 3)]


def test_rolling_stats_are_populated(built_db: Path) -> None:
    (row,) = query(
        built_db,
        "SELECT count(*) FILTER (WHERE avg_30d IS NULL), count(*) FILTER (WHERE avg_90d IS NULL),"
        " min(all_time_low <= current_price::double) FROM gold.mart_listing_current",
    )
    assert row == (0, 0, True)


def test_store_comparison_ranks_by_total_cost(built_db: Path) -> None:
    rows = query(
        built_db,
        "SELECT cost_rank, current_total_cost FROM gold.mart_store_comparison"
        " WHERE item_id = 'hp' ORDER BY cost_rank",
    )
    costs = [r[1] for r in rows]
    assert costs == sorted(costs)  # type: ignore[type-var]
    assert rows[0][0] == 1


def test_deal_rule_ignores_fake_discounts(built_db: Path) -> None:
    """Days right after a planted price hike must not be flagged, even when 10%+ below average."""
    (row,) = query(
        built_db,
        "SELECT count(*) FILTER (WHERE was_inflated_14d AND pct_vs_avg_30d <= -0.10),"
        "       count(*) FILTER (WHERE was_inflated_14d AND deal_flag)"
        " FROM gold.fct_listing_daily_signals",
    )
    looks_discounted, flagged = row
    assert looks_discounted > 0  # the scenario exists in the data
    assert flagged == 0


def test_rebuild_is_idempotent(built_db: Path) -> None:
    before = query(built_db, "SELECT count(*) FROM gold.fct_price_daily")
    assert run_dbt("build", db_path=built_db).success
    assert query(built_db, "SELECT count(*) FROM gold.fct_price_daily") == before


def test_dbt_tests_catch_bad_rows_that_bypass_the_validator(tmp_path: Path) -> None:
    db = tmp_path / "bad.duckdb"
    with connect(db) as conn:
        load(conn, ITEMS[:1], days=3, step_hours=12)
        conn.execute("UPDATE bronze.raw_observations SET price = 0 WHERE rowid = 1")
    outcome = run_dbt("build", db_path=db)
    assert not outcome.success
    assert any("accepted_range" in name and "price" in name for name in outcome.failures)
