"""End-to-end: ingest → dbt → alerts on a temporary DuckDB, with a recording notifier."""

from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from tracker import orchestrate
from tracker.alerts.models import Alert
from tracker.domain import Item
from tracker.ingest.pipeline import run_ingest
from tracker.settings import get_settings
from tracker.sources.base import utcnow
from tracker.sources.mock import MockSource, hourly
from tracker.storage import repo
from tracker.storage.db import connect

pytestmark = pytest.mark.integration
ITEMS = [
    # target far above any price, so the target rule always fires
    Item(item_id="hp", name="Headphones", query="auralis headphones", target_price=Decimal("999")),
    Item(item_id="tv", name="TV", query="lumora oled", target_price=Decimal("5000")),
]


class Recorder:
    name = "recorder"

    def __init__(self, fail: bool = False) -> None:
        self.sent: list[Alert] = []
        self.fail = fail

    def send(self, alert: Alert) -> None:
        if self.fail:
            raise ConnectionError("webhook down")
        self.sent.append(alert)


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    path = tmp_path / "pipe.duckdb"
    monkeypatch.setenv("TRACKER_DB_PATH", str(path))
    get_settings.cache_clear()
    end = utcnow().replace(minute=0, second=0, microsecond=0)
    with connect(path) as conn:
        for item in ITEMS:
            repo.upsert_item(conn, item, end)
        run_ingest(conn, MockSource(), ITEMS, hourly(end - timedelta(days=30), end, 12), "backfill")
    yield path
    get_settings.cache_clear()


def alert_rows(db: Path) -> list[tuple[object, ...]]:
    with duckdb.connect(str(db)) as conn:
        return conn.execute("SELECT rule_id, subject_id, status FROM app.alerts_sent").fetchall()


def test_second_run_sends_no_duplicates(db: Path) -> None:
    rec = Recorder()
    first = orchestrate.run_pipeline(["mock"], [rec])
    assert first.ok
    assert first.alerts is not None
    assert {(a.subject_id, a.rule_id) for a in rec.sent} >= {
        ("hp", "target:hp"),
        ("tv", "target:tv"),
    }

    second = orchestrate.run_pipeline(["mock"], [rec])
    assert second.alerts is not None
    assert second.alerts.sent == []
    assert second.alerts.suppressed == second.alerts.matched > 0
    assert len(alert_rows(db)) == len(first.alerts.sent)


def test_failed_delivery_is_logged_and_retried_next_run(db: Path) -> None:
    broken = orchestrate.run_pipeline(["mock"], [Recorder(fail=True)])
    assert broken.alerts is not None
    assert broken.alerts.failed == broken.alerts.matched > 0
    assert {r[2] for r in alert_rows(db)} == {"failed"}

    rec = Recorder()
    retry = orchestrate.run_pipeline(["mock"], [rec])
    assert retry.alerts is not None
    assert len(retry.alerts.sent) == broken.alerts.failed  # failed ones are not treated as sent
    assert {r[2] for r in alert_rows(db)} == {"sent"}


def test_layout_change_sends_one_system_alert(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(orchestrate, "get_source", lambda _name: MockSource(layout_version=2))
    rec = Recorder()
    orchestrate.run_pipeline(["mock"], [rec])
    orchestrate.run_pipeline(["mock"], [rec])
    layout = [a for a in rec.sent if a.rule_id == "system:layout_changed"]
    assert len(layout) == 1
    assert layout[0].subject_id == "source:mock"


def test_dbt_failure_pauses_price_alerts(db: Path) -> None:
    with duckdb.connect(str(db)) as conn:
        conn.execute("UPDATE bronze.raw_observations SET price = 0 WHERE rowid = 1")
    rec = Recorder()
    report = orchestrate.run_pipeline(["mock"], [rec])
    assert not report.ok
    assert report.alerts is None
    assert [a.rule_id for a in rec.sent] == ["system:dbt_failed"]
