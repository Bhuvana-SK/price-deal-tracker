"""Render the real Streamlit app headlessly (AppTest) against a freshly built database.

The database is built in a subprocess: dbt-duckdb keeps an in-process connection
open, and the dashboard must open the file read-only, as it does in production.
"""

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from tracker.dashboard import data
from tracker.settings import get_settings

pytestmark = pytest.mark.integration
APP = Path(__file__).resolve().parents[2] / "dashboard" / "app.py"
ITEMS_YAML = """
items:
  - {item_id: hp, name: Headphones, query: auralis headphones, target_price: 215}
  - {item_id: tv, name: TV, query: lumora oled}
"""


def cli(*args: str, env: dict[str, str]) -> None:
    code = "from tracker.cli import app; app()"
    subprocess.run([sys.executable, "-c", code, *args], env=env, check=True, capture_output=True)


@pytest.fixture(scope="module")
def built_db(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    tmp = tmp_path_factory.mktemp("dash")
    items = tmp / "items.yaml"
    items.write_text(ITEMS_YAML)
    db = tmp / "dash.duckdb"
    env = {**os.environ, "TRACKER_DB_PATH": str(db), "TRACKER_ITEMS_FILE": str(items)}
    for args in (["seed"], ["backfill", "--days", "45"], ["transform"]):
        cli(*args, env=env)
    yield db


@pytest.fixture
def app_env(built_db: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("TRACKER_DB_PATH", str(built_db))
    get_settings.cache_clear()
    yield built_db
    get_settings.cache_clear()


def run_app() -> AppTest:
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    return at


def test_dashboard_renders_without_errors(app_env: Path) -> None:
    at = run_app()
    assert not at.exception
    assert at.title[0].value.endswith("Price & Deal Tracker")
    assert [t.label for t in at.tabs] == [
        "Deals now",
        "Price history",
        "Store comparison",
        "Data quality",
        "Alerts",
    ]
    labels = [m.label for m in at.metric]
    assert "Genuine deals now" in labels
    assert "Bad prices caught" in labels


def test_switching_item_and_range_rerenders(app_env: Path) -> None:
    at = run_app()
    at.selectbox(key="hist_item").set_value("tv").run()
    at.radio(key="hist_range").set_value("30 days").run()
    assert not at.exception


def test_missing_database_shows_guidance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRACKER_DB_PATH", str(tmp_path / "nope.duckdb"))
    get_settings.cache_clear()
    at = run_app()
    assert not at.exception
    assert "No database yet" in at.info[0].value
    get_settings.cache_clear()


def test_data_layer_reads_gold_tables(built_db: Path) -> None:
    with data.read_only(built_db) as conn:
        assert data.has_gold_tables(conn)
        current = data.current_listings(conn)
        assert set(current["item_id"]) == {"hp", "tv"}
        assert len(data.price_history(conn, "hp", 30)) > 0
        assert data.quality_totals(conn)["loaded"] > 0
        assert not data.store_comparison(conn, "hp").empty
