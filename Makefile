.DEFAULT_GOAL := help

# Recipes must run the same under sh (Linux/macOS) and cmd.exe (native Windows make):
# only `cd dir && cmd` and plain commands here. Anything with logic, env vars or text
# processing lives in a stdlib Python script run through uv (docs/decisions D-26).
PY := uv run --no-project --quiet

# Targets are named silo:action (`make builder:local`). The colon is escaped in rule
# names; aggregates call $(MAKE) with the goals because GNU make 3.81 (the native
# Windows build) does not unescape `\:` in prerequisite lists. `cd . &&` makes make
# use a shell, so the quoted $(MAKE) path may contain spaces.

.PHONY: help setup env\:setup env\:local env\:force env\:local-force \
	install engine\:install api\:install builder\:install engine\:browsers \
	stack\:up stack\:up-host-ports stack\:down stack\:nuke stack\:restart stack\:watch stack\:build stack\:ps stack\:logs db\:migrate db\:shell \
	demo\:run demo\:site demo\:graphs \
	builder\:local builder\:api builder\:web builder\:up builder\:down \
	test engine\:test engine\:test-browser api\:test api\:test-db builder\:test \
	lint engine\:lint api\:lint builder\:lint builder\:typecheck check

# Service to target with logs / restart / build (e.g. `make stack:logs s=worker`).
s ?=

help: ## Show this help
	@$(PY) scripts/make_help.py

# --- Bootstrap ----------------------------------------------------------------

setup: ## Fresh clone: env files + every app's host deps
	@cd . && "$(MAKE)" --no-print-directory env:setup env:local install
	@echo "Done. Next: 'make stack:up', then 'make demo:run' to run the example graphs."

env\:setup: ## Copy every .env.example to its .env.docker (root: .env) - idempotent
	@$(PY) scripts/setup_env.py

env\:local: ## Same, but writes .env with loopback hosts for host runs
	@$(PY) scripts/setup_env.py --local

env\:force: ## Overwrite every .env.docker from its template
	@$(PY) scripts/setup_env.py --force

env\:local-force: ## Overwrite every .env from its template
	@$(PY) scripts/setup_env.py --local --force

install: ## Host deps for every app (uv sync / npm ci)
	@cd . && "$(MAKE)" --no-print-directory engine:install api:install builder:install

engine\:install: ## uv sync in engine/
	cd engine && uv sync

api\:install: ## uv sync in api/
	cd api && uv sync

builder\:install: ## uv sync in builder-api/ + npm ci in builder-web/
	cd builder-api && uv sync
	cd builder-web && npm ci

engine\:browsers: ## Playwright Chromium on the host (builder, `graphwright step`, browser tests)
	cd engine && uv run playwright install chromium

# --- Stack ----------------------------------------------------------------------

stack\:up: ## Build + start the stack -> http://localhost:8080 (API at /api/docs)
	docker compose up -d --build

stack\:up-host-ports: ## Same as up, plus postgres/redis/rustfs/demo-site on 127.0.0.1
	docker compose -f docker-compose.yml -f docker-compose.host-ports.yml up -d --build

stack\:down: ## Stop the stack (keeps data volumes)
	docker compose down

stack\:nuke: ## Stop the stack AND wipe postgres + rustfs volumes
	docker compose down -v

stack\:restart: ## Restart all services, or one with s=<service>
	docker compose restart $(s)

stack\:watch: ## Hot-reload dev loop (sync+restart Python, Vite HMR)
	docker compose watch

stack\:build: ## Rebuild images, or one with s=<service>
	docker compose build $(s)

stack\:ps: ## Service status
	docker compose ps -a

stack\:logs: ## Follow logs for all services, or one with s=<service>
	docker compose logs -f --tail 100 $(s)

db\:migrate: ## Re-run Alembic migrations (after adding a revision)
	docker compose build migrate
	docker compose run --rm migrate

db\:shell: ## psql into the dev database
	docker compose exec postgres psql -U graphwright -d graphwright

# --- Runs -----------------------------------------------------------------------

demo\:run: ## Run every example graph against demo-site through the API (stack must be up)
	@$(PY) scripts/run_graph.py --demo

demo\:graphs: ## List the graphs the API serves
	@$(PY) scripts/run_graph.py --list

demo\:site: ## Serve demo-site on the host (http://127.0.0.1:8090)
	$(PY) demo-site/server.py --port 8090

# --- Graph builder (dev-only - see builder-api/AGENTS.md) -----------------------

NGINX ?= 0
builder\:local: ## Builder on the host, HEADED: API + UI in one terminal (NGINX=1 also routes :8080/builder/)
	@$(PY) builder-api/scripts/dev_local.py $(if $(filter 1,$(NGINX)),--nginx)

builder\:api: ## Builder API on the host with a headed browser (http://127.0.0.1:8765)
	cd builder-api && uv run builder-api

builder\:web: ## Builder UI dev server on the host (http://localhost:5174/builder/)
	cd builder-web && npm run dev

builder\:up: ## Containerised builder (headless) behind nginx at /builder/
	docker compose --profile builder up -d --build builder-api builder-web

builder\:down: ## Stop the builder containers
	docker compose --profile builder stop builder-api builder-web

# --- Quality --------------------------------------------------------------------

test: ## Every test suite that needs no services
	@cd . && "$(MAKE)" --no-print-directory engine:test api:test builder:test

engine\:test: ## pytest in engine/ (browser tests skipped)
	cd engine && uv run pytest

engine\:test-browser: ## engine tests incl. real-Chromium + demo-site e2e (needs engine:browsers)
	@$(PY) scripts/with_env.py GW_BROWSER_TESTS=1 -- uv run --directory engine pytest

api\:test: ## pytest in api/ (DB tests skip unless POSTGRES_DSN names a *_test database)
	cd api && uv run pytest

# DB tests TRUNCATE between tests, so they only run against a database named *_test.
api\:test-db: ## api tests incl. DB, against a throwaway graphwright_test DB (needs `make stack:up-host-ports`)
	@$(PY) scripts/test_db.py

builder\:test: ## pytest in builder-api/ + tsc in builder-web/
	cd builder-api && uv run pytest
	cd builder-web && npm run typecheck

lint: ## Lint every app
	@cd . && "$(MAKE)" --no-print-directory engine:lint api:lint builder:lint

engine\:lint: ## ruff + graph lint in engine/
	cd engine && uv run ruff check
	cd engine && uv run graphwright lint

api\:lint: ## ruff in api/
	cd api && uv run ruff check

builder\:lint: ## ruff in builder-api/
	cd builder-api && uv run ruff check

builder\:typecheck: ## tsc in builder-web/
	cd builder-web && npm run typecheck

check: ## Everything CI runs: lint + typecheck + tests
	@cd . && "$(MAKE)" --no-print-directory lint builder:typecheck test
