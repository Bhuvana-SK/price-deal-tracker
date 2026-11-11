.DEFAULT_GOAL := help
.PHONY: help install lint format typecheck test check lock

help: ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Create .venv with pinned deps and install git hooks
	uv sync --frozen
	uv run pre-commit install

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
