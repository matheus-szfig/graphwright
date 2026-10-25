# builder — graph builder (dev-only, standalone)

A local tool for writing and debugging graphs in `engine/graphs/`. It is **not part of
the runtime**: the worker and the runs API never import it, and it ships as two optional
containers under the `builder` compose profile. Edits are written straight to the YAML
files (lint-gated), so every change is a reviewable git diff — the repo is the graph
store (D-13). This guide covers both halves; `builder-web/` has no guide of its own.
`CLAUDE.md` is a symlink to this file.

```
builder-api/   FastAPI over graphwright's GraphSession (uv project; `graphwright` is an editable path dependency)
  scripts/dev_local.py   `make builder-local`
builder-web/   React Flow canvas + CodeMirror editors (Vite, base /builder/)
```

## Running it

The point is watching a **headed** browser, which Docker cannot show you:

```bash
make builder-local            # installs deps on first run, starts API (headed) + UI,
                              # Ctrl-C stops both → http://localhost:5174/builder/
make builder-local NGINX=1    # also re-points the stack's nginx at the host so
                              # http://localhost:8080/builder/ works while it runs
```

`dev_local.py` flags: `--api-port` (8765), `--web-port` (5174), `--nginx`; env `API_PORT`,
`WEB_PORT`, `NGINX`, `HTTP_PORT`. `make builder-api` / `make builder-web` run the halves
separately. Fully containerised and headless: `make builder-up` (`docker compose --profile
builder up -d`); `engine/graphs` is bind-mounted into `builder-api` so edits land in the
repo (the one bind mount the stack allows, D-30).

The example graphs default `base_url` to `http://demo-site:8090` (the compose network).
From the host, start the stack with `make up-host-ports` (publishes demo-site on
127.0.0.1:8090) or `make demo-site`, and set the `base_url` input to
`http://127.0.0.1:8090`. Credentials for sessions come from the engine's configured
provider (`CREDENTIAL_PROVIDER` & co. in the environment or `builder-api/.env`); the
"Credential reference" field in the Inputs panel names one (`demo` for demo-site).

## What the UI does

Visual language: the Synthetic Flow export (`docs/design/synthetic-flow/`, D-19). Tokens
in `builder-web/src/index.css` (`@theme`), shared controls in
`builder-web/src/components/ui.tsx` — use `cls.*`, `<Icon>`, `<Badge>`, `<Chip>`,
`<Section>` rather than raw palette colours. Confirms and prompts go through
`useDialog()` (`components/dialog.tsx`) — **never** `window.alert/confirm/prompt`.

- **Left rail**: Graph (pick, create, delete — with an in-theme confirm — and add nodes) ·
  Inputs (declare with type, description, default, `credential:` field, required, PII;
  fill session values; credential reference).
- **Canvas** (React Flow + dagre): node cards with kind, intent, action/edge chips and a
  live "recognizes the last observation?" marker; current node glows, failing node is
  rose. Edges: cyan `always`, indigo conditional, rose dashed `on_fail`, purple `call`.
  Drag port→port adds a transition; Delete removes nodes/edges — all through the API.
- **Player HUD**: Start · fast/human timing · Step (F10) · Run (F5) · Observe · Patch ·
  Go to selected node · Stop, plus a status chip.
- **Inspector tabs**: Node / Graph YAML (CodeMirror, Tab-to-accept completions from
  `/schema`, the graph — nodes, targets, shapers, `{input}` and `{requests.<name>.<field>}`
  — and the live page; Save disabled while lint fails) · Page (render or element table,
  **+ target** per element) · Targets (every strategy's attempt) · **Requests** (each graph
  request with kind, policy and live status; test it standalone or inside the running
  session with inputs/vars, a local-only reveal toggle, and an attempt timeline: outcome,
  status, elapsed, backoff, missing fields, masked request line, body excerpt, extracted
  values) · Events (recognize reasons, transition traces, locate attempts, `request`
  timelines, `on_fail` routing, failures) · Screenshot.

## API (`builder-api`)

Graphs: `GET /graphs`, `POST /graphs`, `GET|PUT|DELETE /graphs/{name}`,
`POST /graphs/{name}/lint`, `GET /graphs/{name}/completions`,
`GET|PUT|POST|DELETE /graphs/{name}/nodes…` (`/rename`, `/lint`, `/recognize-from-session`),
`POST /graphs/{name}/start/{node}`, `POST|DELETE /graphs/{name}/edges…`,
`PUT|DELETE /graphs/{name}/inputs/{input}`, `POST /graphs/{name}/targets/from-element`,
`POST /graphs/{name}/requests/{req}/test` `{inputs, vars, credential?, reveal}` →
`graphwright.testing.run_request` report.

Sessions: `POST /sessions` `{graph, inputs, fast, credential?}`, `GET|DELETE /sessions/{id}`,
`POST /sessions/{id}/{step|run|reset|patch|observe|eval}`,
`POST /sessions/{id}/requests/{req}` `{vars, reveal}` (runs inside the session via
`GraphSession.run_request`; the event joins the stream and the status feeds `request:`
conditions), `GET /sessions/{id}/{state|targets|recognize|screenshot|events}`,
`WS /sessions/{id}/ws`. `GET /schema` is derived from the engine's Pydantic models.

One Chromium per API process, one context per session, launched with the worker's machine
profile. Behind nginx the prefix `/builder/api` is stripped; the UI always calls
`<base>/api`.

Env: `BUILDER_GRAPHS_DIR` (default `<repo>/engine/graphs`), `BUILDER_HEADED` (default
true), `BUILDER_PORT` 8765, `BUILDER_HOST`, `BUILDER_ROOT_PATH`, `BUILDER_CORS_ORIGINS`,
plus the engine's own settings (`CREDENTIAL_PROVIDER`, `PLUGINS`, …).

## Tests

```bash
cd builder-api && uv run pytest        # graph store + HTTP API + request endpoints, no browser
cd builder-web && npm run typecheck && npm run build
```

## Rules

- The builder depends on the engine; the engine must never import from the builder.
- Graph mutations go through `GraphStore` so nothing that fails lint is written.
- No screencast / remote browser streaming — the builder is local by design (D-12).
- Revealed request values exist only in the browser tab that asked; nothing persists them.
