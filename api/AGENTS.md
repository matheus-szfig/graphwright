# api — runs API agent guide

The only service client apps talk to. It owns the **database schema** (Alembic),
writes the `run` row, enqueues a Celery message that carries **only the run id**,
and serves status, the durable event trail and live progress (SSE). It never runs
a browser and never imports `graphwright.queue.app` (that is the worker).
`CLAUDE.md` is a symlink to this file.

```
api/
├── src/graphwright_api/
│   ├── config.py     Settings — the only env boundary (ENV, POSTGRES_DSN, REDIS_URL, API_KEYS, GRAPHS_DIR, PLUGINS, DB_PASSWORD_WORKER)
│   ├── main.py       create_app() factory + routes; `graphwright-api` CLI (uvicorn)
│   ├── store.py      RunStore protocol · PgRunStore (asyncpg) · MemoryRunStore (unit tests)
│   ├── queue.py      CeleryEnqueuer — send_task / revoke by name from graphwright.contract
│   ├── progress.py   RedisProgress — snapshot key + pub/sub channel the worker writes
│   ├── auth.py       X-API-Key → key id (constant-time)
│   └── schemas.py    request/response models
├── alembic/          ALL migrations: run, run_event, artifact, graphwright_worker role
└── tests/            unit (fakes) + DB tests (only against a *_test database)
```

## Endpoints

All but `/health` need `X-API-Key: <secret>`. Behind nginx the `/api` prefix is
stripped (uvicorn `--root-path /api`).

| Method | Path | Body / query | Response |
|---|---|---|---|
| GET | `/health` | — | `{status: ok\|degraded, db, redis}` |
| GET | `/graphs` | — | `[GraphSummary]` — name, workflow, version, portal, entry_url, inputs, requests, lint, ok |
| GET | `/graphs/{name}` | — | `GraphSummary` · 404 `graph_not_found` |
| POST | `/runs` | `{graph, inputs?, credential_ref?, idempotency_key?}` | **202** `Run` · **200** `Run` on an idempotent replay · 404 `graph_not_found` · 422 `graph_invalid` / `inputs_invalid` (`detail.errors`) · 503 `enqueue_failed` (`detail.run_id`; the run is recorded as failed) |
| GET | `/runs` | `?graph= &status= &limit=1..200 &before=<run id>` | `{runs: [Run], next}` — newest first; `next` → `before` |
| GET | `/runs/{id}` | — | `RunDetail` = `Run` + worker_hostname, browser_profile, error_traceback, `progress` (live Redis snapshot or null) |
| GET | `/runs/{id}/events` | `?after=<event id> &limit=1..2000` | `{events: [{id, seq, kind, node, data, created_at}], next}` |
| GET | `/runs/{id}/stream` | — | SSE: `event: run` (snapshot) → `event: progress` (each worker publish) → `event: end` (final `RunDetail`); `: ping` comments every 15 s |
| POST | `/runs/{id}/cancel` | — | `Run` · 409 `run_final` (`detail.run_status`) · 404 |

`Run`: id, graph, graph_version, status (`queued|running|completed|failed|cancelled`),
inputs, **has_credential** (the reference itself is never echoed), requested_by,
idempotency_key, attempt, current_node, steps_completed, terminal, result, failure,
error_type, error_message, created_at, started_at, finished_at, cancel_requested_at.

Errors are `{"detail": {"code", "message", …}}`.

## Rules

- **The message is the run id.** `POST /runs` inserts the row (status `queued`)
  first, then `send_task(TASK_RUN_GRAPH, args=[run_id], queue=QUEUE_RUNS,
  task_id=run_id)`. Graph, inputs and credential reference are read by the worker
  from the row. The Celery task id is the run id, so cancel can revoke it.
- **Inputs never carry credential values.** `catalog.validate_inputs` rejects
  inputs that a graph declares as `credential:`; credentials are a *reference*
  (`credential_ref`) the worker's provider redeems. Plain inputs are stored in
  `run.inputs` as given — don't put secrets in non-credential inputs.
- **Single-tenant.** Every key sees and can cancel every run; `requested_by` is
  audit, not isolation. Idempotency keys are scoped per key id.
- **Cancel is two-sided.** Queued → `cancelled` immediately (the worker's
  `claim_run` skips it). Running → `cancelled` + `revoke(terminate=True)`; the
  worker also checks the status between steps, and its final `UPDATE … WHERE
  status = 'running'` cannot overwrite the cancellation.
- **Schema changes go here.** Columns the worker writes are the contract with
  `engine/src/graphwright/storage/db.py`; change both together, extend
  `WORKER_RUN_COLUMNS` grants in a new revision, and keep `tests/test_db.py`'s
  worker-contract test passing — it drives the engine's real db module as
  `graphwright_worker`.
- **Least privilege.** `graphwright_worker`: SELECT on `run`, UPDATE on the
  outcome/progress columns only, INSERT on `run_event`, SELECT+INSERT on
  `artifact`. Outside `ENV=dev/test` the migration refuses to create it without
  `DB_PASSWORD_WORKER`, and the app refuses the `dev-only-change-me` API key.
- UUIDs are Postgres 18 `uuidv7()`: `ORDER BY id` is creation order, and keyset
  pagination (`before=<id>`) needs nothing else.
- Raw SQL in `store.py` and migrations; no ORM (Alembic's SQLAlchemy is plumbing only).

## Running

```bash
uv sync
uv run graphwright-api --reload                     # http://127.0.0.1:8000 (host; needs postgres + redis)
uv run alembic upgrade head                         # migrate POSTGRES_DSN (owner role)
uv run pytest                                       # unit tests; DB tests skip
POSTGRES_DSN=postgresql://graphwright:graphwright@127.0.0.1:5432/graphwright_test uv run pytest
                                                    # + DB tests: migrates the *_test db, TRUNCATEs per test
uv run ruff check
```

DB tests run only when `POSTGRES_DSN` names a reachable database ending in `_test`
(they TRUNCATE); anything else skips them. They migrate it themselves.
