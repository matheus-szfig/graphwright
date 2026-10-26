# Running the language in Graphwright

[Guide index](README.md) · [Requests and 2FA](requests-and-2fa.md)

Graphwright is a standalone, white-label automation engine. Its language is independent
of a particular identity system or secret store. The consuming application supplies
credentials through a provider; the runs API authenticates run-management calls separately.

## Locations and commands

| Component | Location |
|---|---|
| Python package | `engine/src/graphwright`, imported as `graphwright` |
| Active graph catalog | `engine/graphs`, or `GRAPHS_DIR` |
| Builder API / UI | `builder-api` / `builder-web` |
| Fictional test website | `demo-site/server.py` |
| Documentation examples | `docs/engine-yaml/examples` (not automatically catalog entries) |

From the repository root, run `make builder-local` for the headed builder at
`http://localhost:5174/builder/`. In another terminal, `make demo-site` serves the
fictional Acme Records site at `http://127.0.0.1:8090`. Use that host URL as `base_url`
for local browser runs. Compose workers reach the demo at `http://demo-site:8090`.
These are different network contexts; a container's localhost is not your host machine.

From `engine/`:

```console
uv run python ../docs/engine-yaml/validate.py
uv run graphwright lint ../docs/engine-yaml/examples/record_search.yaml
uv run graphwright step ../docs/engine-yaml/examples/record_search.yaml --input base_url=http://127.0.0.1:8090 --input reference=ACM-2024-000123 --headed
uv run graphwright script-test ../docs/engine-yaml/examples/transform.yaml normalize --input date_text=25/10/2026
```

The documentation checker compiles all complete examples, parses YAML/JSON snippets,
and checks relative file links without starting a browser or calling services.
`graphwright lint` without filenames validates the configured graph catalog.

For the bundled demo account, after configuring the demo credential provider from the
environment template:

```console
uv run graphwright step ../docs/engine-yaml/examples/login_totp.yaml --input base_url=http://127.0.0.1:8090 --credential demo --headed
uv run graphwright request-test ../docs/engine-yaml/examples/login_totp.yaml otp --credential demo
```

`request-test` prints a masked attempt report by default; `--reveal` is an explicit
local debugging option. The HTTP code examples require your own matching service
contract. The bundled [relay graph](../../engine/graphs/acme_login_relay_otp.yaml)
demonstrates the actual demo server's request-then-poll contract.

## Built-in shapers and extensions

Built-ins: `digits_only`, `alnum_only`, `upper`, `lower`, `strip`, and `collapse_spaces`.
`digits_only` removes non-digit characters; `alnum_only` retains ASCII letters/digits;
`collapse_spaces` reduces whitespace runs and trims the result. Use plugins for
site-specific formatting; do not add customer-specific behavior to the core engine.

Plugins are importable modules configured in `PLUGINS`. They register shapers, portal
aliases, or credential providers. YAML `imports` is not the plugin loader. Configure
the same plugins for linting, builder, API catalog, and workers so a saved graph behaves
consistently everywhere. See [plugins.py](../../engine/src/graphwright/plugins.py).

`fill` has no `literal` override in this distribution. Keep credential/personal-data
fills templated. `extract` requires fields or tables at lint time; a `schema` label alone
does not request automatic extraction or validation.

## Three separate kinds of authentication

| Purpose | Mechanism |
|---|---|
| Submit/manage runs through the runs API | `X-API-Key` from the deployment's `API_KEYS` configuration. |
| Resolve the run's browser/service credentials | `credential_ref` redeemed by the configured provider. |
| Call an external code/token API declared in YAML | Its own supported token/key/session, provisioned via credential fields. |

The runs API key does not automatically authenticate an external platform's OTP endpoint.
A credential reference is not an access token. A `kind: totp` request does not choose a
provider by itself; it invokes the callback attached to the current resolved credential.

## Credential providers

`CREDENTIAL_PROVIDER` accepts `none`, `file`, `env`, or `http`, plus registered plugins.
Configure it in the engine's environment. The runs API accepts the reference, not secret
values, and the worker resolves it at execution time.

| Provider | Configuration and behavior |
|---|---|
| `none` | Default code configuration; refuses a supplied credential reference. |
| `file` | `CREDENTIALS_FILE` points to YAML/JSON mapping refs to entries. Use a protected external/mounted secret file. |
| `env` | `CREDENTIALS_ENV_PREFIX` defaults to `GW_CRED_`; ref `demo-user` maps to `GW_CRED_DEMO_USER_*`. |
| `http` | `CREDENTIALS_HTTP_URL`, `CREDENTIALS_HTTP_KEY_ID`, `CREDENTIALS_HTTP_KEY`; optional client certificate/CA configuration. |

File entries contain `kind`, optional `portal`, `values`, optional `metadata`, and
optional `totp_secret`. Environment provider reserves KIND, PORTAL, and TOTP_SECRET;
other suffixes become lower-case credential field names. Local providers receive the
seed and generate codes locally. A remote provider can keep the seed outside the worker.
Keep real provider files and credentials out of version control.

Inputs explicitly select credential fields:

```yaml
inputs:
  account_user: {credential: username}
  account_password: {type: password, credential: password}
  code_service_token: {type: password, credential: token}
requests:
  otp: {kind: totp}
```

In production, do not submit these values as ordinary run inputs. The API rejects
supplied values for fields declared `credential`; plain inputs are stored as run data.
`type: password` alone is not a substitute for `credential` provisioning in production.
Workers check the credential's optional portal restriction and wrap credential values
as Secrets. Authenticated runs do not share a persisted browser cookie jar.

### HTTP lease provider contract

The built-in HTTP provider sends `X-Key-Id` and `X-Key` to a configured service:

```http
POST <CREDENTIALS_HTTP_URL>/leases/{ref}/redeem
Content-Type: application/json

{"run_id":"<run-id>"}
```

Expected JSON response shape:

```json
{
  "credential": {
    "kind": "password",
    "portal": "acme-records",
    "metadata": {},
    "values": {}
  },
  "totp": true
}
```

`values` contains the actual fields needed by the graph; this shape illustration is
empty to avoid putting credentials in documentation. With `totp: true`, the provider
attaches a callback that calls `POST /leases/{ref}/totp` with the same `run_id` and
expects a JSON `code` field. The seed need never leave that service.

Lease creation, authorization, expiry, revocation, and any temporary-key minting are
the external service's responsibility. This provider currently implements redemption
and code retrieval; it does not automatically mint or revoke external keys at run end.
See [HTTP provider source](../../engine/src/graphwright/credentials/http.py) for the exact contract.

## Builder credential references

The Graphwright builder has a credential-reference input and redeems it through the
configured provider with `run_id="builder"`. The reference applies to the session and
request/script tests. Configure a provider suitable for development; a lease service
must understand this builder context rather than assuming a queued production run ID.

The builder can also accept manually typed credential inputs for local testing. Those
are wrapped as Secrets. A matching provider credential field takes precedence when
present. Changing the reference or input text does not retrofit an already-created
session: start a new session for a clean credential context.

For multiple independent code sources, declare separate HTTP requests using distinct
remote credential IDs, or provision separate explicit seed inputs. The single default
TOTP callback does not expose a per-request credential-selector field. There is no
built-in generic user-login UI for arbitrary external code services; use their supported
authentication contract and credential provisioning.

## Run submission and results

A runs API payload names the catalog graph and credential reference:

```json
{
  "graph": "acme_account_login",
  "inputs": {"base_url": "http://demo-site:8090"},
  "credential_ref": "demo"
}
```

Send it to `POST /api/runs` with the deployment's `X-API-Key`. The API publishes a run
ID; the worker reads configuration from the run row and resolves credentials. This is
separate from running a graph directly with the CLI or builder.

Declared terminal results complete the graph; inspect the returned `terminal` in
addition to the run's status. `terminal: failure` is also treated as failure for the
worker's evidence capture policy. Runtime errors have failure records; cancellation
has its own run status. API consumers should distinguish these cases.

See [API guide](../../api/AGENTS.md) for run status, event/SSE, cancellation, and artifact
endpoints. The current API is single-tenant: configured API keys are not per-customer
authorization boundaries. The language does not add tenancy isolation.

## Implementation sources

- [Schema](../../engine/src/graphwright/engine/schema.py) and [compiler](../../engine/src/graphwright/engine/compiler.py).
- [Runtime](../../engine/src/graphwright/engine/runtime.py), [actions](../../engine/src/graphwright/engine/dsl.py),
  [conditions](../../engine/src/graphwright/engine/conditions.py), and [targets](../../engine/src/graphwright/engine/targets.py).
- [Requests](../../engine/src/graphwright/engine/requests.py), [sandbox](../../engine/src/graphwright/engine/sandbox.py),
  and [catalog/input validation](../../engine/src/graphwright/catalog.py).
- [Credential providers](../../engine/src/graphwright/credentials/__init__.py),
  [builder sessions](../../builder-api/src/builder_api/sessions.py), and [worker task](../../engine/src/graphwright/tasks/runs.py).

The common language examples are intentionally customer-independent. Keep real-site
graphs, credential contracts, and private test fixtures in the consuming project.
