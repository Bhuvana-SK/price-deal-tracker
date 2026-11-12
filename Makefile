.DEFAULT_GOAL := help
# Use venv/ rather than .venv/: on macOS uv's .venv gets the hidden file flag and
# Python then ignores its .pth files, so the editable package cannot be imported.
export UV_PROJECT_ENVIRONMENT := venv
.PHONY: help install dbt-deps transform run flow serve backtest demo dashboard up down logs lint format typecheck test check lock

help: ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Create venv/ with pinned deps, dbt packages and git hooks
	uv sync --frozen
	$(MAKE) dbt-deps
	uv run pre-commit install

dbt-deps: ## Install dbt packages (dbt_utils)
	cd dbt && uv run dbt deps --profiles-dir .

transform: ## Build dbt models and run data tests
	uv run tracker transform

run: ## One pipeline run: ingest all sources -> match -> dbt -> alerts
	uv run tracker run

flow: ## The same pipeline as a Prefect flow run
	uv run python -m tracker.flows.price_check

serve: ## Prefect scheduler: price check every 6h, digest on Mondays (blocks)
	uv run tracker serve

backtest: ## Deal rule vs. naive baselines -> eval/results/deal_backtest.md
	uv run tracker backtest

demo: ## Fresh demo DB: seed items, 120 days of mock history, dbt, one alert pass
	uv run tracker init-db
	uv run tracker seed
	uv run tracker backfill --days 120
	uv run tracker run

dashboard: ## Open the Streamlit dashboard on http://localhost:8501
	uv run streamlit run dashboard/app.py

up: ## Docker: build and start init + scheduler + dashboard (http://localhost:8501)
	docker compose up -d --build

down: ## Docker: stop the stack (data volume is kept)
	docker compose down

logs: ## Docker: follow the scheduler and dashboard logs
	docker compose logs -f pipeline dashboard

lock: ## Re-resolve dependencies and export pinned requirements files
	uv lock
	uv export --frozen --no-dev --no-hashes --no-emit-project -o requirements.txt
	uv export --frozen --only-dev --no-hashes --no-emit-project -o requirements-dev.txt

lint: ## Ruff lint + format check
	uv run ruff check .
	uv run ruff format --check .

format: ## Auto-format
	uv run ruff check --fix .
	uv run ruff format .

typecheck: ## mypy (strict)
	uv run mypy

test: ## Run the test suite
	uv run pytest --cov=tracker --cov-report=term-missing:skip-covered

check: lint typecheck test ## Everything CI runs
