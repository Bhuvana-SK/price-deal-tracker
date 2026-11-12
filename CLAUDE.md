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
- LLM calls go only through `tracker.llm.client`: `messages.parse` with a Pydantic schema, and any failure (no key, API error, refusal, truncation) raises `LLMUnavailableError`. Every caller has a deterministic fallback, and tests use `tests/fakes.FakeLLM`, never the real API. The default model is `CLAUDE_MODEL` (Haiku 5.5).
- Matching thresholds come from the dev split of `eval/data/product_pairs.csv`. Re-tune with `tracker eval-matching` and never tune on the test split. The pipeline compares listings with short item names, so it uses `match_item_floor`, not `match_low`.
- Config comes only from `tracker.settings.get_settings()`. Secrets are `SecretStr` and are never logged or hardcoded.
- Log with `tracker.log.get_logger(__name__)` using event names plus key-value pairs (`log.info("rows_loaded", n=3)`), not f-strings.
- Everything must run offline with no API keys: LLM and alert features need a deterministic fallback.
- Tests never touch the network; use saved files in `tests/fixtures/`. The one exception is the real-embedding test in `test_matching_pipeline.py`, which downloads the model once into `data/models/` (cached in CI). An autouse fixture forces the hashing embedder and removes `ANTHROPIC_API_KEY` everywhere else.
- Docker: one image serves every compose service (`init`, `pipeline`, `dashboard`), and data lives on the `tracker-data` volume. CI (`.github/workflows/ci.yml`) runs `make lint typecheck test` and builds the image. `scheduled.yml` runs `tracker run` every 6 hours, carrying the DuckDB file between runs in the Actions cache.
- Results in the README come from `make backtest` and `tracker eval-matching`. Regenerate them whenever the deal rule, the validator or the matcher changes.
- Data is synthetic or public sample data only. No personal data.
- Commits: one per build stage or feature, using an imperative subject (`Add idempotent bronze loader`).
