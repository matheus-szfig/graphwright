# graphwright

Graph-driven browser automation. You describe a site flow as a **graph of recognizable
page states** in YAML; a Celery worker drives a stealth Playwright Chromium through it,
observing the page, recognizing where it is, acting, and waiting for a transition — and
stopping with evidence when the site no longer matches. A runs API is how your systems
start runs and follow them, and a local builder lets you step a graph against a real site
and see *why* each transition fired.

```
 client ──► api (POST /runs) ──► Postgres run row ──► Redis ──► worker ──► Chromium ──► site
   ▲            │                                        │         │
   └── SSE ◄────┴──────────── progress (key + pub/sub) ◄─┘         └─► credential provider,
                                                                       graph requests (2FA…)
```

For workflow authors, start with the [YAML language guide](docs/engine-yaml/README.md):
tutorial, complete reference, 2FA/API recipes, executable examples, and debugging.

## Quick start

Requirements: Docker, [uv](https://docs.astral.sh/uv/), Node 22+, GNU make (optional — every
target is a thin wrapper over a script in `scripts/`).

```bash
make setup     # env files from the templates + uv sync / npm ci
make up        # the whole stack → http://localhost:8080   (API docs: /api/docs)
make demo      # run the example graphs against the bundled demo site, through the API
```

`make demo` submits runs of the example graphs in `engine/graphs/` — a public search with
extraction, a not-found outcome, a login whose TOTP code is computed at the moment it is
typed, and a login whose code comes from an HTTP relay that is polled until it answers —
and streams their progress.

## A graph

```yaml
# engine/graphs/acme_account_login.yaml (abridged)
workflow: acme_account_login
portal: acme-records
entry_url: "{base_url}/login"
inputs:
  base_url: {type: url, pii: false, default: "http://demo-site:8090"}
  username: {credential: username}               # from the run's credential, as a Secret
  password: {type: password, credential: password}
requests:
  otp: {kind: totp}        # a code from the credential's seed — computed when it is typed
outputs:
  account: acme_account_v1
start: sign_in
nodes:
  sign_in:
    recognize: {all: [{url: "re:^/login$"}, {visible: username_field}]}
    actions:
      - fill: {target: username_field, value: "{username}"}
      - fill: {target: password_field, value: "{password}"}
      - click: {target: sign_in_button}
    transitions:
      - {to: invalid_credentials, when: {text: "re:invalid username or password"}}
      - {to: verify, when: {url: "re:^/login/verify"}}
  verify:
    recognize: {visible: code_field}
    actions:
      - fill: {target: code_field, value: "{requests.otp.code}"}   # fetched right now
      - click: {target: verify_button}
    transitions:
      - {to: dashboard, when: {text: "re:^welcome,", in: headings}}
      - {to: verify, when: {text: "re:invalid verification code"}}  # retry with a fresh code
    max_visits: 2
    on_fail: code_rejected
  dashboard:
    recognize: {url: "re:^/account"}
    actions:
      - extract:
          into: account
          fields: {welcome: {css: h1, required: true}}
          tables: {activity: {match: "re:recent activity"}}
    transitions: [{to: done}]
  invalid_credentials: {terminal: failure, result: {status: invalid_credentials}}
  code_rejected: {terminal: failure, result: {status: code_rejected}}
  done: {terminal: success}
targets:
  username_field:
    description: textbox "Username"
    strategies: [{role: textbox, name: "re:^username$"}, {role: textbox, dom_id: "re:^username$"}]
  # …password_field, sign_in_button, code_field, verify_button
```

Key ideas (details in `engine/AGENTS.md`):

- **Nodes recognize pages**; transitions are structured conditions that explain
  themselves; business outcomes are terminal nodes, not errors.
- **Targets** are ordered locator strategies against an observed `PageState` (accessible
  name → HTML id → nearby text → css), resolved with per-strategy reasons.
- **Requests** fetch short-lived values (2FA codes, magic links, tokens) at the moment of
  use: `http` (any API, with retry / backoff / poll-until-present), `totp`, `imap`. Every
  attempt is traced (masked) for debugging; transitions can branch on
  `request: {name, status: failed}`.
- **Credentials** are references resolved by a pluggable provider (`file`, `env`, an HTTP
  lease service, or your own); values live in `Secret` objects and every log, event and
  error column passes through a redactor.
- **Action readiness**: clicks wait for a clickable, stable target; click/fill support
  `delay_s`, and `wait_for` supports explicit element states.
- **Images**: `capture_image` captures rendered pixels; graph HTTP requests call a model
  using caller-provided inputs. See [images](docs/engine-yaml/images.md).
- **`extract`** reads fields and tables into the run's outputs.
- **Code nodes** run JavaScript in a sandbox (fresh V8 isolate, no I/O, CPU and memory
  limits) to turn vars into other vars; **`evaluate`** runs arbitrary JavaScript in the
  page itself — in an isolated world the page can't see, or in the page's own context.
- **Failure evidence**: when a run fails, the worker keeps what the page looked like — a
  screenshot (typed secrets masked first), the redacted HTML, the observed PageState and
  the decision trail — in S3 (RustFS in compose), listed at `GET /api/runs/{id}/artifacts`.

## Using the API

```bash
curl -s -X POST http://localhost:8080/api/runs \
  -H 'X-API-Key: dev-only-change-me' -H 'Content-Type: application/json' \
  -d '{"graph": "acme_record_search", "inputs": {"reference": "ACM-2024-000123"}}'

curl -N http://localhost:8080/api/runs/<id>/stream -H 'X-API-Key: dev-only-change-me'   # live (SSE)
curl -s http://localhost:8080/api/runs/<id>        -H 'X-API-Key: dev-only-change-me'   # status + result
curl -s http://localhost:8080/api/runs/<id>/artifacts -H 'X-API-Key: dev-only-change-me' # failure evidence
```

See `api/AGENTS.md` for every endpoint and `/api/docs` for the OpenAPI UI.

## The builder (local only)

```bash
make install-browsers
make builder-local        # API (headed Chromium) + UI → http://localhost:5174/builder/
```

Step a graph against a real browser, watch recognize/transition reasons, generate targets
from observed elements, edit YAML with schema- and page-aware completions, and test graph
requests with a per-attempt timeline. Edits are written to `engine/graphs/` (lint-gated).

## Repository

| Folder | What |
| --- | --- |
| `engine/` | the `graphwright` package: engine, observer, browser, credentials, Celery worker, CLI |
| `api/` | runs API + all migrations |
| `builder-api/`, `builder-web/` | the dev-only builder |
| `demo-site/` | the demo app examples and tests run against |
| `docs/decisions/` | architecture decisions (ADR log) |

`make help` lists every target; `make check` runs what CI runs (minus the browser and DB jobs,
which are `make test-engine-browser` and `make test-api-db`).
