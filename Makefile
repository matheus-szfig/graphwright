.DEFAULT_GOAL := help

# Recipes must run the same under sh (Linux/macOS) and cmd.exe (native Windows make):
# only `cd dir && cmd` and plain commands here. Anything with logic, env vars or text
# processing lives in a stdlib Python script run through uv (docs/decisions D-26).
PY := uv run --no-project --quiet

.PHONY: help setup setup-env setup-env-local setup-env-force setup-env-local-force \
	install install-engine install-api install-builder install-browsers \
	up up-host-ports down nuke restart watch build ps logs migrate shell-db \
	demo demo-site runs graphs \
	builder-local builder-api builder-web builder-up builder-down \
	test test-engine test-engine-browser test-api test-api-db test-builder \
	lint lint-engine lint-api lint-builder typecheck check

# Service to target with logs / restart / build (e.g. `make logs s=worker`).
s ?=

help: ## Show this help
	@$(PY) scripts/make_help.py

# --- Bootstrap ----------------------------------------------------------------

setup: setup-env setup-env-local install ## Fresh clone: env files + every app's host deps
	@echo "Done. Next: 'make up', then 'make demo' to run the example graphs."

setup-env: ## Copy every .env.example to its .env.docker (root: .env) - idempotent
	@$(PY) scripts/setup_env.py

setup-env-local: ## Same, but writes .env with loopback hosts for host runs
	@$(PY) scripts/setup_env.py --local

setup-env-force: ## Overwrite every .env.docker from its template
	@$(PY) scripts/setup_env.py --force

setup-env-local-force: ## Overwrite every .env from its template
	@$(PY) scripts/setup_env.py --local --force

install: install-engine install-api install-builder ## Host deps for every app (uv sync / npm ci)

install-engine: ## uv sync in engine/
	cd engine && uv sync

install-api: ## uv sync in api/
	cd api && uv sync

install-builder: ## uv sync in builder-api/ + npm ci in builder-web/
	cd builder-api && uv sync
	cd builder-web && npm ci

install-browsers: ## Playwright Chromium on the host (builder, `graphwright step`, browser tests)
	cd engine && uv run playwright install chromium

# --- Stack ----------------------------------------------------------------------

up: ## Build + start the stack -> http://localhost:8080 (API at /api/docs)
	docker compose up -d --build

up-host-ports: ## Same as up, plus postgres/redis/minio/demo-site on 127.0.0.1
	docker compose -f docker-compose.yml -f docker-compose.host-ports.yml up -d --build

down: ## Stop the stack (keeps data volumes)
	docker compose down

nuke: ## Stop the stack AND wipe postgres + minio volumes
	docker compose down -v

restart: ## Restart all services, or one with s=<service>
	docker compose restart $(s)

watch: ## Hot-reload dev loop (sync+restart Python, Vite HMR)
	docker compose watch

build: ## Rebuild images, or one with s=<service>
	docker compose build $(s)

ps: ## Service status
	docker compose ps -a

logs: ## Follow logs for all services, or one with s=<service>
	docker compose logs -f --tail 100 $(s)

migrate: ## Re-run Alembic migrations (after adding a revision)
	docker compose build migrate
	docker compose run --rm migrate

shell-db: ## psql into the dev database
	docker compose exec postgres psql -U graphwright -d graphwright

# --- Runs -----------------------------------------------------------------------

demo: ## Run every example graph against demo-site through the API (stack must be up)
	@$(PY) scripts/run_graph.py --demo

graphs: ## List the graphs the API serves
	@$(PY) scripts/run_graph.py --list

demo-site: ## Serve demo-site on the host (http://127.0.0.1:8090)
	$(PY) demo-site/server.py --port 8090

# --- Graph builder (dev-only - see builder-api/AGENTS.md) -----------------------

NGINX ?= 0
builder-local: ## Builder on the host, HEADED: API + UI in one terminal (NGINX=1 also routes :8080/builder/)
	@$(PY) builder-api/scripts/dev_local.py $(if $(filter 1,$(NGINX)),--nginx)

builder-api: ## Builder API on the host with a headed browser (http://127.0.0.1:8765)
	cd builder-api && uv run builder-api

builder-web: ## Builder UI dev server on the host (http://localhost:5174/builder/)
	cd builder-web && npm run dev

builder-up: ## Containerised builder (headless) behind nginx at /builder/
	docker compose --profile builder up -d --build builder-api builder-web

builder-down: ## Stop the builder containers
	docker compose --profile builder stop builder-api builder-web

# --- Quality --------------------------------------------------------------------

test: test-engine test-api test-builder ## Every test suite that needs no services

test-engine: ## pytest in engine/ (browser tests skipped)
	cd engine && uv run pytest

test-engine-browser: ## engine tests incl. real-Chromium + demo-site e2e (needs install-browsers)
	@$(PY) scripts/with_env.py GW_BROWSER_TESTS=1 -- uv run --directory engine pytest

test-api: ## pytest in api/ (DB tests skip unless POSTGRES_DSN names a *_test database)
	cd api && uv run pytest

# DB tests TRUNCATE between tests, so they only run against a database named *_test.
test-api-db: ## api tests incl. DB, against a throwaway graphwright_test DB (needs `make up-host-ports`)
	@$(PY) scripts/test_db.py

test-builder: ## pytest in builder-api/ + tsc in builder-web/
	cd builder-api && uv run pytest
	cd builder-web && npm run typecheck

lint: lint-engine lint-api lint-builder ## Lint every app

lint-engine: ## ruff + graph lint in engine/
	cd engine && uv run ruff check
	cd engine && uv run graphwright lint

lint-api: ## ruff in api/
	cd api && uv run ruff check

lint-builder: ## ruff in builder-api/
	cd builder-api && uv run ruff check

typecheck: ## tsc in builder-web/
	cd builder-web && npm run typecheck

check: lint typecheck test ## Everything CI runs: lint + typecheck + tests
