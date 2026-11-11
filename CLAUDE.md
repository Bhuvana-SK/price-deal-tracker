# CLAUDE.md

Portfolio data pipeline: collect prices → DuckDB (bronze/silver/gold) → dbt deal logic → alerts + Streamlit.

## Commands
- `make install` · `make check` (lint + mypy + tests; must pass before every commit)
- The virtualenv is `venv/`, not `.venv/`. On macOS, uv marks `.venv` hidden, and Python then ignores the editable `.pth`, which shows up as `ModuleNotFoundError: tracker`. Outside `make`, run `export UV_PROJECT_ENVIRONMENT=venv` or `source venv/bin/activate`.
- `make lock` after changing dependencies in `pyproject.toml` (updates `uv.lock` and `requirements*.txt`)

## Conventions
- Python 3.11, managed with `uv`. Package lives in `src/tracker/`; tests mirror it under `tests/`.
- Small, typed functions with docstrings. mypy runs in strict mode.
- DuckDB timestamps are naive UTC (`TIMESTAMP`). Convert with `storage.db.to_db_ts`. Bulk writes go through `storage.db.bulk_insert`; `executemany` is about 200x slower.
- dbt lives in `dbt/`. Silver models are views in schema `silver`; gold models are tables in `gold`. Tunable thresholds are `vars` in `dbt_project.yml`; never hardcode them in SQL. After `run_dbt()` in a process, open DuckDB without `read_only`, because dbt-duckdb keeps its connection open.
- The pipeline logic lives in `tracker.orchestrate` as plain functions. `tracker.flows` only wraps it in Prefect tasks, so test the logic, not Prefect.
- `nanobind: leaked function` lines at the end of a test run come from DuckDB's bindings when Prefect's threads outlive the interpreter. They're harmless and don't change the exit code.
- Config comes only from `tracker.settings.get_settings()`. Secrets are `SecretStr` and are never logged or hardcoded.
- Log with `tracker.log.get_logger(__name__)` using event names plus key-value pairs (`log.info("rows_loaded", n=3)`), not f-strings.
- Everything must run offline with no API keys: LLM and alert features need a deterministic fallback.
- Tests never touch the network. Use saved files in `tests/fixtures/`.
- Data is synthetic or public sample data only. No personal data.
- Commits: one per build stage or feature, using an imperative subject (`Add idempotent bronze loader`).
