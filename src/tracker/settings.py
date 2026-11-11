"""Runtime configuration loaded from environment variables and an optional `.env` file."""

from decimal import Decimal
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
    dbt_project_dir: Path = Path("dbt")
    # Observations of one listing inside the same window count as one (idempotency).
    check_window_hours: int = Field(default=1, ge=1, le=24)
    # Alerts: repeat an alert only after the cooldown, or if the price fell by rearm_pct more.
    alert_cooldown_hours: int = Field(default=168, ge=1)
    alert_rearm_pct: Decimal = Field(default=Decimal("0.02"), ge=0, lt=1)
    # Product matching (thresholds picked on the dev split: see eval/results/matching.md)
    # "hashing" selects the no-download test embedder.
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    models_dir: Path = Path("data/models")
    match_low: float = 0.813  # below: confidently different (keeps every dev-split match)
    match_high: float = 1.01  # above: auto-accept. >1 means always verify with the LLM
    match_fallback: float = 0.833  # no LLM: best-F1 threshold on the dev split
    # Pipeline: a listing is compared with the (short) item *name*, which scores lower
    # than title-vs-title; real item/listing pairs go down to ~0.79 and unrelated products
    # sit near 0.50, so only exclude on similarity below this floor.
    match_item_floor: float = 0.60
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
