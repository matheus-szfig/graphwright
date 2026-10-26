# engine — agent guide

The `graphwright` Python package: the graph engine, the page observer, the stealth
browser, credential providers, and the Celery worker that runs graphs. It consumes Redis,
PostgreSQL and S3 (RustFS in compose) as external services and owns none of them (the schema lives in
`api/alembic`). `CLAUDE.md` is a symlink to this file.

```
engine/
├── src/graphwright/
│   ├── config.py          Pydantic Settings — the ONLY env-var boundary
│   ├── contract.py        task names, queues, Redis keys — shared with the runs API
│   ├── catalog.py         graphs on disk: list / load (compile) / validate_inputs / build_inputs
│   ├── plugins.py         import modules named in PLUGINS (shapers, aliases, providers)
│   ├── cli.py             `graphwright` CLI: lint · graphs · step · request-test · observe · watch
│   ├── testing.py         run_request() — run one graph request outside a session (CLI, builder)
│   ├── engine/            the graph language and its runtime
│   │   ├── schema.py      Pydantic Workflow/Node/Target/Condition/Action/RequestSpec + Pattern
│   │   ├── conditions.py  evaluate(condition) → Match(ok, reasons)
│   │   ├── targets.py     strategy resolution against a PageState, per-strategy attempts
│   │   ├── compiler.py    lint + CompiledGraph (recognize / next / resync / edges)
│   │   ├── dsl.py         action executors + ActionContext (templates, lazy requests)
│   │   ├── requests.py    graph requests: http / totp / imap, retry, extract, traces
│   │   ├── sandbox.py     code nodes: fresh V8 isolate per `script` (no I/O, CPU + memory limits)
│   │   ├── runtime.py     GraphSession: step()/run()/patch()/reset_to(), events, on_fail routing
│   │   ├── shapers.py     register_shaper + generic built-ins
│   │   ├── secrets.py     Secret (prints ***), SecretInputs, Redactor
│   │   └── errors.py      Transient / Blocked / Structural / request failure classes
│   ├── observe/           page → PageState (pagestate.js in a CDP isolated world per frame)
│   ├── browser/           Playwright lives here ONLY: pool, profiles, cdp, humanize, stealth.js
│   ├── credentials/       providers: none · file · env · http (+ RFC 6238 totp)
│   ├── queue/             Celery app, broker/backend config, routing
│   ├── tasks/             runs.run_graph(run_id) · maintenance.health_check
│   ├── worker/            hooks.py — per-process event loop, BrowserPool, asyncpg pool
│   ├── evidence.py        failure evidence: masked screenshot, redacted HTML, PageState, context → S3
│   └── storage/           cache.py (Redis progress + pub/sub) · db.py (run rows) · blobs.py (S3 objects)
├── graphs/                example graphs (YAML) — run against demo-site
├── tests/                 pytest; goldens in tests/fixtures/goldens; GW_BROWSER_TESTS=1 for Chromium
└── Dockerfile             worker image: python:3.13-slim + uv + Playwright Chromium
```

## Quick reference

| What | Command |
| --- | --- |
| Install / update deps | `uv sync` |
| Tests (no services, no browser) | `uv run pytest` |
| Tests incl. real Chromium + demo-site e2e | `GW_BROWSER_TESTS=1 uv run pytest` (`make test-engine-browser`) |
| Lint code / graphs | `uv run ruff check` · `uv run graphwright lint` |
| Step a graph in a headed browser | `uv run graphwright step acme_record_search --input base_url=http://127.0.0.1:8090 --headed` |
| Debug one graph request | `uv run graphwright request-test acme_login_relay_otp otp --input base_url=http://127.0.0.1:8090` |
| Run a node's code-node script | `uv run graphwright script-test acme_record_search summarize --var 'record={"status":"Active","events":[]}' --var rows_on_page=0` |
| Worker on the host | `uv run celery -A graphwright.queue.app worker -Q graphwright.runs,graphwright.maintenance --concurrency=1` |
| Follow a run | `uv run graphwright watch <run-id>` |

Runs are started through the runs API (`api/`), never by enqueueing directly.

## Layering

Strict import direction, no upward imports:

```
config.py · contract.py
      │
      ├──► storage/ ─────────────┐        (leaves: import nothing else from graphwright)
      ├──► credentials/          │
      ▼                          ▼
   observe/ ◄── browser/ ◄── engine/          (engine uses observe + browser.humanize)
      │                          │
      └──────────► catalog.py ◄──┘
                      │
                      ▼
             tasks/ · worker/ · queue/        (Celery: bridges sync ↔ async on the worker loop)
```

- `config.py` is the only place that reads the environment (`credentials/local.py`'s env
  provider is the documented exception: reading `GW_CRED_*` is its job).
- `browser/` is the only place Playwright is imported (the CLI and tests launch browsers
  through it or through `playwright.async_api` directly for scripts).
- `contract.py` is imported by the runs API; keep it free of Celery/worker imports.
- Workers own the **long-lived event loop**: tasks call `loop.run_until_complete(...)` on the
  loop `worker_process_init` created. Never `asyncio.run()` inside a task — Playwright
  contexts and asyncpg connections are bound to that loop.

## The run, end to end

1. The API validates and inserts a `run` row (`queued`) and sends `run_graph(run_id)`.
2. `tasks/runs.py`: `db.claim_run` (no-op for finished/cancelled runs — redelivery is
   safe) → `catalog.load_graph` (compiles; lint errors → `graph_invalid`) → the
   credential provider redeems `credential_ref` → `portal` check → `build_inputs`
   (defaults, run inputs, `credential:` fields as Secrets) → `GraphSession` stepped until a
   terminal, a failure, or the row says `cancelled`.
3. Every event goes through the run's `Redactor`; decision-trail kinds (`goto move jump
   resync request fill select extract retry on_fail failure terminal cancelled`) are kept
   and written to `run_event`; progress snapshots go to Redis (key + pub/sub).
4. Terminal → `completed` with `result` (`terminal`, the node's `result`, `outputs`);
   otherwise `failed` with `error_type` (a code), scrubbed message/traceback and the
   structured `failure`.
5. Evidence (D-34): on a failure — or a `terminal: failure` — and while the page is still
   open, `evidence.capture` masks fields that received secrets, takes a screenshot, and
   stores it with the redacted HTML, PageState and a context record in
   `<S3_BUCKET_PREFIX>-evidence`; artifact ids go to `failure.evidence` /
   `result.evidence`. Best effort: it never changes the outcome. `EVIDENCE_ON`
   (`failure` | `always` | `never`) and `EVIDENCE_*` tune it.

## Graph authoring rules (`graphs/*.yaml`)

- One YAML per site flow; file stem = graph name (`snake_case`). `entry_url` is a
  template (`"{base_url}/search"`). `portal` names the site; credentials may pin it.
- Every non-terminal node has `recognize`. Business outcomes (`not_found`) are
  `terminal: outcome` nodes with a `result`, never failures. `always` transitions last.
- Targets: ordered strategies — accessible `name` first (survives id renames), then
  `dom_id` (`re:^id$`; for generated ids anchor the stable suffix `re::fieldName$`), then
  `nearby`/`landmark`; `css`/`xpath` last (they need the live DOM, so not in `recognize`).
  Patterns: `re:…` (case-insensitive), `/…/flags`, or substring.
- Values come from `inputs` (`{reference}`) — PII by default, redacted in events, never
  literals in fills to user/password/document/OTP-like targets (lint rejects them).
  `default:` makes an input optional; `credential: password` fills it from the run's
  credential instead of the run's inputs. `fill.shape` names a registered shaper.
- **Short-lived values are requests, not inputs.** A 2FA code is
  `requests: {otp: {kind: totp}}` used as `{requests.otp.code}` in the fill that types it —
  fetched right then, every time (`cache: none`). Codes from an API: `kind: http` with
  `extract: {code: {json: data.code}}` and `retry: {attempts: 5, backoff_s: [2], until_extracted: true}`.
  Codes by e-mail: `kind: imap`. To branch on failure use
  `fetch: {request: otp, on_error: continue}` and a transition
  `when: {request: {name: otp, status: failed}}`. Requests can use other requests'
  values (`{requests.token.value}`); no cycles.
- `extract: {into, fields, tables}` reads values; mark the ones that must exist
  `required: true` so a redesign fails loudly. `outputs:` names the vars a run returns.
- **Code nodes** (D-36): `script: {code, args, into}` runs JavaScript in a sandbox — a
  function body with `args` (templates; `"{vars.record.events}"` keeps the list),
  `vars`, `inputs` and `console`, returning JSON into `vars[into]` (or, without `into`,
  one var per returned key). No I/O at all: computation only. A "code node" is a node
  whose actions are scripts (`recognize` on the current page, or `always`).
- **JavaScript in the page**: `evaluate: {script, args, into, world, frame}` — async body,
  awaited. `world: isolated` (default; DOM yes, page globals no, invisible to the page) or
  `main` (the page's own JS — reach app state, but the page can notice). Prefer the DSL and
  `extract`; reach for `evaluate` when the page hides data in JS or needs a nudge.
  Both refuse secret args unless `allow_secrets: true`.
- `on_fail: <node>` turns a failure into a transition (`vars._error` holds the record);
  `on_fail: fail` ends the run as failed even for structural errors.
- Validate with `uv run graphwright lint` (or the builder); step it with
  `graphwright step … --headed` before relying on it.

## Standing rules

- **Secrets are `Secret` objects, never strings in run state.** They print as `***` and
  open only in `ActionContext.render_template`. Anything that could quote one — events,
  failures, `run.error_*`, the progress key — goes through the run's `Redactor`. Never put
  a credential in Celery args, `run.inputs`, a log line or an exception message.
- **Authenticated runs don't share cookie jars** (`context_for(..., persist_state=False,
  authenticated=True)`); with `BROWSER_IGNORE_HTTPS_ERRORS=true` they refuse to start.
- **The worker's DB role is `graphwright_worker`**: claim/update runs, append events and
  artifacts. Don't widen it; new columns the worker writes need a grant in the migration.
- **Concurrency stays at 1** per worker process; scale out with more containers.
  `--max-tasks-per-child=10` recycles Chromium. `task_acks_late` + `claim_run` make
  redelivery safe; keep task bodies idempotent on the run row.
- **UUIDs are DB-generated** (`uuidv7()`, Postgres 18). Never generate them in Python.
- Headless toggle: `BROWSER_HEADLESS=true|false|new`.
- Graph requests can reach any host unless `REQUESTS_ALLOWED_HOSTS` is set — set it in
  production.

## Where things go

| Adding a… | Lives in | Also touches |
| --- | --- | --- |
| Graph for a site | `graphs/<name>.yaml` (or the builder) | goldens if tests need them |
| Site-specific shaper / portal alias / credential provider | a plugin module named in `PLUGINS` | — |
| New action verb / condition kind / request kind | `engine/schema.py` + `dsl.py` / `conditions.py` / `requests.py` + lint in `compiler.py` | builder completions come from `/schema` automatically; ADR |
| New env var | `config.py` | `.env.example` |
| New column the worker writes | Alembic revision in `api/` (with the grant) | `storage/db.py` |
| Machine profile | `browser/profiles.py` | coherence tests in `tests/test_profiles.py` |

## Gotchas

- Celery autodiscovers `graphwright.tasks`; new task modules must be imported in
  `tasks/__init__.py`.
- `render_template` is sync and never fetches; async code uses `await ctx.render(text)`,
  which runs the requests a template references first.
- `docker compose watch` restarts the worker on every `.py` save; an in-flight run is
  redelivered and resumes from a fresh browser (it re-claims the same row).
- Browser tests are opt-in (`GW_BROWSER_TESTS=1`); they need `uv run playwright install chromium`.
