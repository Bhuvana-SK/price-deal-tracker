from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tracker.cli import app
from tracker.settings import get_settings

pytestmark = pytest.mark.integration
ITEMS_YAML = """
items:
  - item_id: hp
    name: Headphones
    query: auralis headphones
    target_price: 215
"""


@pytest.fixture
def cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[CliRunner]:
    monkeypatch.setenv("TRACKER_DB_PATH", str(tmp_path / "cli.duckdb"))
    items = tmp_path / "items.yaml"
    items.write_text(ITEMS_YAML)
    monkeypatch.setenv("TRACKER_ITEMS_FILE", str(items))
    get_settings.cache_clear()
    yield CliRunner()
    get_settings.cache_clear()


def invoke(cli: CliRunner, *args: str) -> str:
    result = cli.invoke(app, list(args))
    assert result.exit_code == 0, result.output
    return result.stdout


def test_seed_add_check_and_runs(cli: CliRunner) -> None:
    assert "seeded 1 items" in invoke(cli, "seed")
    assert "tracking brewix-pro" in invoke(
        cli, "add-item", "Brewix Pro", "-q", "brewix espresso", "-t", "380"
    )
    listed = invoke(cli, "items")
    assert "hp" in listed
    assert "brewix-pro" in listed
    first = invoke(cli, "check", "-s", "mock")
    second = invoke(cli, "check", "-s", "mock")
    assert "loaded=6" in first
    assert "loaded=0 duplicate=6" in second
    assert "mock" in invoke(cli, "runs")


def test_backfill(cli: CliRunner) -> None:
    invoke(cli, "seed")
    out = invoke(cli, "backfill", "--days", "3", "--step-hours", "12")
    assert "[mock/backfill] status=success" in out


def test_add_item_rejects_unknown_source(cli: CliRunner) -> None:
    result = cli.invoke(app, ["add-item", "X", "-q", "x", "-s", "amazon"])
    assert result.exit_code != 0
