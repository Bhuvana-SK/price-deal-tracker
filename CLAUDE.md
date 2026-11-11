# CLAUDE.md

Portfolio data pipeline: collect prices → DuckDB (bronze/silver/gold) → dbt deal logic → alerts + Streamlit.

## Commands
- `make install` · `make check` (lint + mypy + tests; must pass before every commit)
- `make lock` after changing dependencies in `pyproject.toml` (updates `uv.lock` and `requirements*.txt`)

## Conventions
- Python 3.11, managed with `uv`. Package lives in `src/tracker/`; tests mirror it under `tests/`.
- Small, typed functions with docstrings. mypy runs in strict mode.
- Config comes only from `tracker.settings.get_settings()`. Secrets are `SecretStr` and are never logged or hardcoded.
- Log with `tracker.log.get_logger(__name__)` using event names plus key-value pairs (`log.info("rows_loaded", n=3)`), not f-strings.
- Everything must run offline with no API keys: LLM and alert features need a deterministic fallback.
- Tests never touch the network. Use saved files in `tests/fixtures/`.
- Data is synthetic or public sample data only. No personal data.
- Commits: one per build stage or feature, using an imperative subject (`Add idempotent bronze loader`).
