from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import ClassVar

import pytest

from tests.conftest import make_listing
from tracker.domain import FetchResult, Item, ItemQuery
from tracker.ingest.pipeline import run_ingest
from tracker.sources.base import Source
from tracker.sources.mock import MockSource, hourly
from tracker.storage import repo
from tracker.storage.db import Connection, connect

pytestmark = pytest.mark.integration
T0 = datetime(2026, 6, 1, 12, tzinfo=UTC)
ITEMS = [
    Item(item_id="hp", name="Headphones", query="auralis headphones"),
    Item(item_id="phone", name="Phone", query="kinetix phone"),
]


class FakeSource(Source):
    """Returns whatever FetchResult the test hands it."""

    name: ClassVar[str] = "mock"

    def __init__(self, result: FetchResult) -> None:
        self.result = result

    def fetch(self, queries: Sequence[ItemQuery], now: datetime | None = None) -> FetchResult:
        return self.result


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[Connection]:
    with connect(tmp_path / "test.duckdb") as c:
        yield c


def count(conn: Connection, table: str) -> int:
    row = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
    assert row is not None
    return int(row[0])


def test_rerunning_the_same_window_creates_no_duplicates(conn: Connection) -> None:
    first = run_ingest(conn, MockSource(), ITEMS, [T0])
    second = run_ingest(conn, MockSource(), ITEMS, [T0 + timedelta(minutes=20)])
    assert first.loaded == 9  # 3 products x 3 stores
    assert second.loaded == 0
    assert second.duplicate == 9
    assert count(conn, "bronze.raw_observations") == 9


def test_rerunning_a_backfill_adds_nothing(conn: Connection) -> None:
    ts = hourly(T0, T0 + timedelta(days=10), step_hours=6)
    first = run_ingest(conn, MockSource(), ITEMS, ts, kind="backfill")
    total = count(conn, "bronze.raw_observations") + count(conn, "bronze.rejected_observations")
    second = run_ingest(conn, MockSource(), ITEMS, ts, kind="backfill")
    assert first.loaded + first.rejected == total == len(ts) * 9
    assert second.loaded == 0
    assert second.rejected == 0
    assert count(conn, "bronze.ingest_runs") == 2


def test_bad_prices_are_rejected_logged_and_counted(conn: Connection) -> None:
    ts = hourly(T0, T0 + timedelta(days=90), step_hours=6)
    run = run_ingest(conn, MockSource(), ITEMS, ts, kind="backfill")
    reasons = dict(
        conn.execute(
            "SELECT reason, count(*) FROM bronze.rejected_observations GROUP BY 1"
        ).fetchall()
    )
    assert run.rejected > 0
    assert set(reasons) <= {"non_positive_price", "price_outlier"}
    assert sum(reasons.values()) == run.rejected
    stored = conn.execute("SELECT n_rejected FROM bronze.ingest_runs").fetchone()
    assert stored == (run.rejected,)
    clean = conn.execute(
        "SELECT min(price), max(price) FROM bronze.raw_observations "
        "WHERE listing_id = 'mock:shopmart:AUR-NC700'"
    ).fetchone()
    assert clean is not None
    assert clean[0] > 100
    assert clean[1] < 500  # no 100x spike got through


def test_spike_is_rejected_once_history_exists(conn: Connection) -> None:
    run_ingest(conn, MockSource(), ITEMS, hourly(T0, T0 + timedelta(days=5), step_hours=12))
    spike = make_listing(
        source_listing_id="shopmart:AUR-NC700",
        price=Decimal("24999.00"),
        fetched_at=T0 + timedelta(days=6),
    )
    run = run_ingest(
        conn, FakeSource(FetchResult(source="mock", listings=[spike], attempted=1)), ITEMS
    )
    assert run.loaded == 0
    assert run.rejected == 1
    reason = conn.execute(
        "SELECT reason FROM bronze.rejected_observations WHERE raw_price = '24999.00'"
    ).fetchone()
    assert reason == ("price_outlier",)


def test_layout_change_marks_run_partial(conn: Connection) -> None:
    run = run_ingest(conn, MockSource(layout_version=2), ITEMS, [T0])
    assert run.layout_broken
    assert run.status == "partial"
    assert run.loaded == 0
    assert run.parse_failures == 9


def test_source_failure_marks_run_failed(conn: Connection) -> None:
    broken = FakeSource(FetchResult(source="mock", errors=["hp: connection refused"]))
    run = run_ingest(conn, broken, ITEMS)
    status = conn.execute("SELECT status, errors FROM bronze.ingest_runs").fetchone()
    assert run.status == "failed"
    assert status == ("failed", ["hp: connection refused"])


def test_listings_are_registered_and_linked_to_items(conn: Connection) -> None:
    run_ingest(conn, MockSource(), ITEMS, [T0])
    links = conn.execute(
        "SELECT item_id, count(*) FROM app.item_listings GROUP BY 1 ORDER BY 1"
    ).fetchall()
    assert links == [("hp", 3), ("phone", 6)]  # 'kinetix phone' matches the 128GB and 256GB
    assert count(conn, "app.listings") == 9


def test_failed_load_rolls_back_everything(
    conn: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object, **_kwargs: object) -> int:
        raise RuntimeError("disk full")

    monkeypatch.setattr(repo, "insert_rejections", boom)
    with pytest.raises(RuntimeError):
        run_ingest(conn, MockSource(), ITEMS, [T0])
    assert count(conn, "bronze.raw_observations") == 0
    assert count(conn, "app.listings") == 0


def test_item_upsert_updates_target(conn: Connection) -> None:
    repo.upsert_item(conn, ITEMS[0], T0)
    repo.upsert_item(conn, ITEMS[0].model_copy(update={"target_price": Decimal("199")}), T0)
    (item,) = repo.list_items(conn)
    assert item.target_price == Decimal("199")
