# One entry point for everything CI runs. `make test` is what "all green" means.
.PHONY: test test-api test-pipeline test-ml test-web lint build ci

test: test-api test-pipeline test-ml test-web

test-api:
	cd services/api && uv run pytest -q

test-api-fast:  # no Docker needed
	cd services/api && uv run pytest -q -m 'not postgres'

test-pipeline:
	cd services/pipeline && uv run pytest -q

test-ml:
	cd ml && uv run pytest -q

test-web:
	cd web && npm test --silent

lint:
	cd services/api && uv run ruff check src tests migrations && uv run ruff format --check src tests migrations
	cd services/pipeline && uv run ruff check src tests && uv run ruff format --check src tests
	cd ml && uv run ruff check src tests && uv run ruff format --check src tests
	cd web && npx tsc -b

build:
	cd web && npm run build

ci: lint test build
