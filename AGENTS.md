# graphwright — monorepo agent guide

Graph-driven browser automation. A workflow is a YAML graph of recognizable page states;
a Celery worker runs it in a stealth Playwright Chromium; a runs API is how other systems
start runs and follow them; a local builder lets you step graphs against a real site and
see why each transition fired. `CLAUDE.md` is a symlink to this file.

Read the per-app guide before touching an app: `engine/AGENTS.md`, `api/AGENTS.md`,
`builder-api/AGENTS.md` (covers builder-web too), and the module docstring of
`demo-site/server.py`.

**Past decisions live in `docs/decisions/`** — one dated ADR per decision, indexed in
`docs/decisions/README.md`. Grep the index before changing architecture, and add an entry
(new file + index row) for any choice that would surprise someone reading the code.

## Layout

```
graphwright/
├── engine/        Python package `graphwright` — graph engine, observer, stealth browser,
│                  credential providers, Celery queue + worker tasks, CLI, example graphs
├── api/           runs API (FastAPI): POST /runs, status, events, SSE stream, cancel;
│                  owns ALL Postgres migrations (Alembic) and the worker's DB role
├── builder-api/   DEV-ONLY graph builder API (FastAPI over GraphSession, headed browser)
├── builder-web/   DEV-ONLY graph builder UI (React Flow + CodeMirror, Synthetic Flow design)
├── demo-site/     "Acme Records" — stdlib demo web app the examples, goldens and e2e use
├── nginx/         reverse proxy image (templates/default.conf.template is envsubst'd)
├── scripts/       stdlib Python behind the Makefile (setup_env, run_graph, test_db, …)
├── docs/          decisions/ (ADRs) · design/synthetic-flow/ (builder visual language)
├── .github/       CI: engine (+ browser e2e), api (+ Postgres), builder, compose build
├── Makefile       `make help`
└── docker-compose.yml (+ docker-compose.host-ports.yml for host tooling)
```

## How the pieces relate

- **Run loop**: client → `api` (`POST /runs`) → Postgres `run` row (`queued`) → Redis →
  `worker` runs `engine/graphs/<graph>.yaml` → progress in Redis (key + pub/sub, relayed
  as SSE by the API) → outcome and decision trail in Postgres; on failure, evidence
  (masked screenshot, redacted HTML, PageState, context) in RustFS, served by the API.
- **Credentials**: a run carries a credential *reference*; the worker's credential
  provider (`file`, `env`, `http` lease service, or a plugin) resolves it. Short-lived
  values (2FA codes) are graph **requests** fetched at the moment an action uses them.
- **Builder loop (local only)**: `builder-api` wraps `GraphSession` with a headed
  browser; `builder-web` steps graphs, tests requests, and writes YAML back into
  `engine/graphs/` — the repo is the graph store.

Dependency direction is strict: `api → engine` and `builder-api → engine` (editable path
dependencies), never the reverse. The API imports only `graphwright.contract` /
`catalog` / `plugins` — never the worker's Celery app. The worker image never installs
the API or the builder.

## Running it

```bash
make setup          # env files + uv sync / npm ci everywhere
make up             # docker compose up -d --build → http://localhost:8080 (API docs at /api/docs)
make demo           # run every example graph against demo-site through the API
make watch          # hot-reload loop
make builder-local  # the builder on the host, headed browser → http://localhost:5174/builder/
make check          # lint + typecheck + tests (what CI runs, minus browser/DB jobs)
```

## Standing rules for agents

- **Only nginx publishes a port.** New public routes go in
  `nginx/templates/default.conf.template`. Host-only ports go in
  `docker-compose.host-ports.yml`.
- **Source reaches containers through `develop.watch`, never bind mounts** (D-30). Volumes
  are for persistence; the only exception is `builder-api`'s `engine/graphs`.
- **Graphs are data.** New flows are YAML in `engine/graphs/` (or a consuming project's
  `GRAPHS_DIR`), written through the builder or by hand; every graph must pass
  `graphwright lint`. Site-specific code (shapers, aliases, providers) goes in plugins.
- **White-label.** No product, customer or real-site names, domains or data in this repo.
  Examples target `demo-site`; real-site graphs and goldens live in the consuming project.
- **Credentials never live in graphs, messages, logs or `run.inputs`** (D-06, D-23, D-26).
- **Make targets are thin shells over stdlib Python scripts** (D-22) — they must work under
  `sh` and native Windows `make`.
- **All Postgres migrations live in `api/alembic`.** A column the worker writes needs a
  grant for `graphwright_worker` in the same revision.
- **DB tests only run against a database whose name ends in `_test`** (D-33).
- Commit or push only when asked.
