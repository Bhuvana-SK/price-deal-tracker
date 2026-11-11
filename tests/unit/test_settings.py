from pathlib import Path

import pytest
from typer.testing import CliRunner

from tracker import __version__
from tracker.cli import app
from tracker.settings import Settings


def test_defaults_need_no_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.anthropic_api_key is None
    assert s.claude_model == "claude-haiku-5-5"
    assert s.db_path == Path("data/tracker.duckdb")


def test_secret_is_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.anthropic_api_key is not None
    assert "sk-test" not in repr(s)
    assert s.anthropic_api_key.get_secret_value() == "sk-test-not-real"


def test_cli_version() -> None:
    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout
