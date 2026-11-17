# builder — graph builder (dev-only, standalone)

A local tool for writing and debugging graphs in `engine/graphs/`. It is **not part of
the runtime**: the worker and the runs API never import it, and it ships as two optional
containers under the `builder` compose profile. Edits are written straight to the YAML
files (lint-gated), so every change is a reviewable git diff — the repo is the graph
store (D-13). This guide covers both halves; `builder-web/` has no guide of its own.
`CLAUDE.md` is a symlink to this file.

```
builder-api/   FastAPI over graphwright's GraphSession (uv project; `graphwright` is an editable path dependency)
  scripts/dev_local.py   `make builder:local`
builder-web/   React Flow canvas + CodeMirror editors (Vite, base /builder/)
```

## Running it

The point is watching a **headed** browser, which Docker cannot show you:

```bash
make builder:local            # installs deps on first run, starts API (headed) + UI,
                              # Ctrl-C stops both → http://localhost:5174/builder/
make builder:local NGINX=1    # also re-points the stack's nginx at the host so
                              # http://localhost:8080/builder/ works while it runs
```

`dev_local.py` flags: `--api-port` (8765), `--web-port` (5174), `--nginx`; env `API_PORT`,
`WEB_PORT`, `NGINX`, `HTTP_PORT`. `make builder:api` / `make builder:web` run the halves
separately. Fully containerised and headless: `make builder:up` (`docker compose --profile
builder up -d`); `engine/graphs` is bind-mounted into `builder-api` so edits land in the
repo (the one bind mount the stack allows, D-30).

The example graphs default `base_url` to `http://demo-site:8090` (the compose network).
From the host, start the stack with `make stack:up-host-ports` (publishes demo-site on
127.0.0.1:8090) or `make demo:site`, and set the `base_url` input to
`http://127.0.0.1:8090`. Credentials for sessions come from the engine's configured
provider (`CREDENTIAL_PROVIDER` & co. in the environment or `builder-api/.env`); the
"Credential reference" field in the Inputs panel names one (`demo` for demo-site).

## What the UI does

Visual language: the Synthetic Flow export (`docs/design/synthetic-flow/`, D-19). Tokens
in `builder-web/src/index.css` (`@theme`), shared controls in
`builder-web/src/components/ui.tsx` — use `cls.*`, `<Icon>`, `<Badge>`, `<Chip>`,
`<Section>` rather than raw palette colours. Confirms and prompts go through
`useDialog()` (`components/dialog.tsx`) — **never** `window.alert/confirm/prompt`.

- **Left rail**: Graph (pick, create, delete — with an in-theme confirm — and add nodes:
  `step`, `code`, `outcome`, `success`, `failure`; a `code` node is `recognize: {always: true}`
  + a `script` stub, hooked from start so it lints) ·
  Inputs (declare with type — `secure` for secrets, `password` is a legacy alias —
  description, default, `credential:` field, required, PII; fill session values;
  credential reference).
- **Canvas** (React Flow + dagre): node cards with kind, intent, action/edge chips and a
  live "recognizes the last observation?" marker; current node glows, failing node is
  rose; a node whose actions are all `script`/`evaluate` shows as **CODE** (`code` icon).
  Edges: cyan `always`, indigo conditional, rose dashed `on_fail`, purple `call`.
  Drag port→port adds a transition; Delete removes nodes/edges — all through the API.
- **Player HUD**: Start · fast/human timing · Step (F10) · Run (F5) · Observe · Patch ·
  Go to selected node · Stop, plus a status chip.
- **Inspector tabs**: Node / Graph YAML (CodeMirror, Tab-to-accept completions from
  `/schema`, the graph — nodes, targets, shapers, `{input}` and `{requests.<name>.<field>}`
  — and the live page; Save accepts invalid drafts and keeps their diagnostics, D-42) ·
  Page (categorized inventory with filters saved as `page_filter`, D-41; **+ target** per
  element) · Targets (edit/delete each target, see its references and every strategy's
  attempt) · **Code** (the code actions of
  the selected node — or pick any node that has some: `script` bodies are tested in the
  sandbox with args/inputs/vars and show value, console lines, error + details, elapsed and
  the vars the action would set; `evaluate` bodies run in the session's page; code edits here
  are a scratch copy, the YAML is what is saved — plus a **Page console**: ad-hoc async JS
  in the live page, `isolated`/`main` world toggle, frame index, Ctrl+Enter, last 20 results)
  · **Requests** (each graph
  request with kind, policy and live status; test it standalone or inside the running
  session with inputs/vars, a local-only reveal toggle, and an attempt timeline: outcome,
  status, elapsed, backoff, missing fields, masked request line, body excerpt, extracted
  values) · Events (recognize reasons, transition traces, locate attempts, `request`
  timelines, `script`/`evaluate` results with logs and set vars, console runs, `on_fail`
  routing, failures) · Screenshot.

## API (`builder-api`)

Graphs: `GET /graphs`, `POST /graphs`, `GET|PUT|DELETE /graphs/{name}`,
`POST /graphs/{name}/lint`, `GET /graphs/{name}/completions`,
`GET|PUT|POST|DELETE /graphs/{name}/nodes…` (`/rename`, `/lint`, `/recognize-from-session`),
`POST /graphs/{name}/start/{node}`, `POST|DELETE /graphs/{name}/edges…`,
`PUT|DELETE /graphs/{name}/inputs/{input}`, `POST /graphs/{name}/targets/from-element`,
`GET /graphs/{name}/targets[?session_id]`, `PUT|DELETE /graphs/{name}/targets/{target}`,
`PUT /graphs/{name}/page-filter` `{value, expected}`,
`POST /graphs/{name}/requests/{req}/test` `{inputs, vars, credential?, reveal}` →
`graphwright.testing.run_request` report.
`POST /graphs/{name}/scripts/test` `{code | node+index, args, inputs, vars, credential?, into?,
timeout_ms?, memory_mb?, allow_secrets?}` → `graphwright.testing.run_script` report `{ok, value,
error, details, logs, elapsed_ms, sets}` (sandbox only, no browser; secure/credential inputs
are Secrets, so `allow_secrets` is enforced as in a run; `ref` resolves through
`Workflow.script_source`).

Sessions: `POST /sessions` `{graph, inputs, fast, credential?}`, `GET|DELETE /sessions/{id}`,
`POST /sessions/{id}/{step|run|reset|patch|observe|eval}`,
`POST /sessions/{id}/requests/{req}` `{vars, reveal}` (runs inside the session via
`GraphSession.run_request`; the event joins the stream and the status feeds `request:`
conditions), `POST /sessions/{id}/evaluate` `{script, args, world: isolated|main, frame, timeout_ms}` →
`{ok, value, error, elapsed_ms}` (the console: same path as the `evaluate` action, result
redacted and emitted as an `evaluate` event with `source: console`; busy → 409),
`GET /sessions/{id}/{state|targets|recognize|screenshot|events}`,
`WS /sessions/{id}/ws`. AI: `/ai/*` (see below). `GET /schema` is derived from the engine's Pydantic models (plus
`node_kinds`, `script_fields`, `evaluate_fields`).

One Chromium per API process, one context per session, launched with the worker's machine
profile. Behind nginx the prefix `/builder/api` is stripped; the UI always calls
`<base>/api`.

Env: `BUILDER_GRAPHS_DIR` (default `<repo>/engine/graphs`), `BUILDER_HEADED` (default
true), `BUILDER_PORT` 8765, `BUILDER_HOST`, `BUILDER_ROOT_PATH`, `BUILDER_CORS_ORIGINS`,
`BUILDER_AI_*` (`builder_api/config.py`, documented in `.env.example`), plus the engine's
own settings (`CREDENTIAL_PROVIDER`, `PLUGINS`, …).

## Tests

```bash
cd builder-api && uv run pytest        # graph store + HTTP API + request/code + AI harness (fake providers)
cd builder-web && npm run typecheck && npm run build
# opt-in browser UI tests (Chromium):
BUILDER_AI_UI_TESTS=1 uv run pytest tests/test_ai_browser.py      # uses builder-web/dist
BUILDER_WEB_TEST_URL=http://127.0.0.1:5174/builder/ uv run pytest  # with `npm run dev` running
```

## Rules

- The builder depends on the engine; the engine must never import from the builder.
- Graph mutations go through `GraphStore`. Saving accepts invalid drafts (D-42); Start,
  Step, Run and Patch compile the saved file and refuse an invalid one.
- No screencast / remote browser streaming — the builder is local by design (D-12).
- Revealed request values exist only in the browser tab that asked; nothing persists them.

## Builder deletion and live state (D-38)

Deletion submits one selection (nodes and incident edges) in one API request. It
removes incoming transitions, jump actions and failure/call references together.
Deleting the start node requires selecting a replacement first. Incomplete deletion
drafts remain inspectable with diagnostics; execution still requires a valid graph.
The canvas updates only from the server response, avoiding optimistic disappearance
when deletion is rejected and competing writes from edge/node callbacks.

Builder WebSockets carry transient session_state frames after engine events have
finished mutating runtime state. The canvas updates its current/running/failed node
and visit counters during Run; these presentation frames are not engine events.
Stop releases the browser but preserves the last summary, page and chronological
event log in UI memory. A successful new run clears them. Reloading the page clears
UI memory. Logs support full JSON export, full-log copy and individual-event copy;
export and copy include the original redacted event payload, not truncated display text.

## AI authoring (D-43, D-44)

The collapsible bottom AI panel works with or without a session. Provider adapters
(plain httpx, fixed URLs), expiring memory-only connections, documentation tools,
private graph projections and review proposals live in `src/builder_api/ai/`; the UI
is `builder-web/src/ai.ts` and `components/Ai*.tsx`. Never wire a raw debugging
response into a model tool: page text, screenshots, runtime values and request results
are withheld, and private graph handles restore only at their original paths
(`ai/privacy.py`). Graph JavaScript is readable through `Projection.code`, which swaps
private values and PII-looking literals for inline handles bound to that code field.
Never add short (< 4 chars) hidden literals to the echo mask.

Graph authoring is session-independent. Apply goes through `GraphStore` with a
base-text check; dirty UI drafts block it. Save & patch queues runtime changes at
`GraphSession.run`'s `on_step` boundary (`SessionManager.patch`), never resetting the
current node or changing existing input bindings. Each live action needs a reviewed
proposal tied to the runtime state; arbitrary page JS stays manual.

Context (`ai/context.py`) is a selected view with `$elided` stubs, explored through
`explore_graph`, `list_context`, `search_context` and `read_context`; long text is
edited with `replace_text` after it was read this turn. Keep the system prompt and
tools static (prompt caching); per-turn context rides with the task message. The
pre-selection model is opt-in (`BUILDER_AI_TRIAGE_AUTO`). Provider text streams
through a boundary-aware privacy buffer; never mask raw tokens independently.
Enter steers a running turn (`POST /ai/steer`); Esc/Stop abort locally at once and
cancel the server turn in the background; interrupted turns enter history. The model
is switched between turns with `POST /ai/model`. The manual is served from
`docs/engine-yaml` (copied into the image, synced by compose watch); see its
`ai-assistant.md` for setup and the privacy contract. Test providers with
`httpx.MockTransport` via `create_app(ai_transport=...)`.
