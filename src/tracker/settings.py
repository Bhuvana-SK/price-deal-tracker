"""Runtime configuration loaded from environment variables and an optional `.env` file."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All tunables in one place. Secrets are `SecretStr` so they never appear in logs."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="TRACKER_", extra="ignore")

    db_path: Path = Path("data/tracker.duckdb")
    items_file: Path = Path("config/items.yaml")
    # Observations of one listing inside the same window count as one (idempotency).
    check_window_hours: int = Field(default=1, ge=1, le=24)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "console"
    user_agent: str = "price-deal-tracker/0.1 (portfolio project; +https://github.com)"

    # Secrets / integrations: read without the TRACKER_ prefix (standard names).
    anthropic_api_key: SecretStr | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    claude_model: str = Field(default="claude-haiku-5-5", alias="CLAUDE_MODEL")
    discord_webhook_url: SecretStr | None = Field(default=None, alias="DISCORD_WEBHOOK_URL")
    telegram_bot_token: SecretStr | None = Field(default=None, alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str | None = Field(default=None, alias="TELEGRAM_CHAT_ID")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings (cached)."""
    return Settings()
