.DEFAULT_GOAL := help
.PHONY: help install lint format test test-unit test-integration test-live test-cov \
        security evals evals-live evals-adversarial \
        run-cli run-ui db-up db-down db-migrate db-migrate-create db-rollback \
        db-history db-current db-verify clean

help: ## List available targets
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Install dependencies via uv
	uv sync

lint: ## Run ruff and mypy on src and tests
	uv run ruff check src tests
	uv run mypy src

format: ## Format code with ruff
	uv run ruff format src tests

test: ## Run unit and integration tests
	uv run pytest tests/unit tests/integration -v

test-unit: ## Run unit tests only
	uv run pytest tests/unit -v

test-integration: ## Run integration tests only
	uv run pytest tests/integration -v

test-live: ## Run live e2e tests (skipped by default)
	uv run pytest tests/e2e -v -m live

test-cov: ## Run tests with coverage
	uv run pytest --cov=src/store_assistant --cov-report=term-missing

security: ## Run security scans
	./scripts/security_scan.sh

evals: ## Run the offline eval harness in replay mode (deterministic, free)
	uv run python -m evals.runner --mode replay

evals-live: ## Run live evals (RUN_EVALS_LIVE=1 + ANTHROPIC_API_KEY required, costs money)
	RUN_EVALS_LIVE=1 uv run python -m evals.runner --mode live

evals-adversarial: ## Replay only the adversarial trace subset
	uv run python -m evals.runner --mode replay --filter category=adversarial

run-cli: ## Run the CLI entrypoint
	uv run python -m store_assistant.main

run-ui: ## Run the Streamlit UI on http://localhost:8501
	@if [ -f .env ]; then set -a; . ./.env; set +a; fi; \
	uv run streamlit run src/store_assistant/ui/streamlit_app.py --server.port 8501 --server.address 0.0.0.0

db-up: ## Start Postgres via docker compose
	docker compose up -d postgres

db-down: ## Stop docker compose services
	docker compose down

db-migrate: ## Apply migrations to head
	uv run alembic upgrade head

db-migrate-create: ## Autogenerate a new migration: make db-migrate-create name="description"
	uv run alembic revision --autogenerate -m "$(name)"

db-rollback: ## Roll back the most recent migration
	uv run alembic downgrade -1

db-history: ## Show full migration history
	uv run alembic history --verbose

db-current: ## Show currently applied head
	uv run alembic current

db-verify: ## Verify migrations downgrade and upgrade cleanly
	uv run alembic downgrade base && uv run alembic upgrade head

clean: ## Remove caches and build artifacts
	rm -rf .pytest_cache .ruff_cache .mypy_cache .coverage htmlcov coverage.xml \
		build dist *.egg-info src/*.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
