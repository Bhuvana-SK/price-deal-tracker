"""Smoke test: the Prefect flow wires the steps together (uses a throwaway Prefect DB)."""

from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from prefect.testing.utilities import prefect_test_harness

from tracker.domain import Item
from tracker.flows.price_check import price_check
from tracker.ingest.pipeline import run_ingest
from tracker.settings import get_settings
from tracker.sources.base import utcnow
from tracker.sources.mock import MockSource, hourly
from tracker.storage import repo
from tracker.storage.db import connect

pytestmark = pytest.mark.integration


@pytest.fixture
def seeded_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    path = tmp_path / "flow.duckdb"
    monkeypatch.setenv("TRACKER_DB_PATH", str(path))
    get_settings.cache_clear()
    item = Item(
        item_id="hp",
        name="Auralis NC700 headphones",
        query="auralis headphones",
        target_price=Decimal("999"),
    )
    end = utcnow().replace(minute=0, second=0, microsecond=0)
    with connect(path) as conn:
        repo.upsert_item(conn, item, end)
        run_ingest(
            conn, MockSource(), [item], hourly(end - timedelta(days=20), end, 12), "backfill"
        )
    yield path
    get_settings.cache_clear()


def test_flow_runs_all_steps(seeded_db: Path) -> None:
    with prefect_test_harness():
        result = price_check()
    assert result == {"sources": {"mock": "success"}, "dbt_success": True, "alerts_sent": 1}
