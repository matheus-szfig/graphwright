# Requests, authentication, and 2FA

[Guide index](README.md) · [Reference](reference.md) · [Repository authentication](integration.md)

A graph request is a named source of values fetched during a run. It can call an HTTP
API, obtain a time-based code, or poll a mailbox. Declaring it does not execute it.

## Choose the source

| Need | Request kind | Where the secret lives |
|---|---|---|
| Code from the run's credential provider | `totp` without `secret` | Provider decides; an HTTP provider can keep the seed entirely outside the worker. |
| Generate from an explicitly provisioned seed | `totp` with `secret` | The process executing the request receives the seed. |
| Ask your platform for a code by credential/challenge ID | `http` | Platform retains the seed; caller supplies authorized authentication and IDs. |
| Read a code sent by email | `imap` | Mailbox credentials are provisioned as inputs/credential fields. |
| Ask a person for an SMS/email code during a run | No built-in action | Requires an external approval/code service or a future runtime integration. |

There is no need to know an authenticator seed when your platform offers an authorized
code service. Multiple request names can address multiple credential IDs. Naming two
`kind: totp` requests without `secret` does **not** choose two credentials: both call the
same TOTP callback configured for that run.

## Declaration, lazy use, and explicit fetch

Top-level fragment:

```yaml
requests:
  otp:
    kind: http
    method: GET
    url: "{code_service_url}/codes/latest"
    headers:
      Authorization: "Bearer {code_service_token}"
    query:
      challenge: "{vars.challenge_id}"
    extract:
      code: {json: data.code}
    retry:
      attempts: 5
      backoff_s: [2]
      until_extracted: true
```

Declare `code_service_url` and `code_service_token` inputs and ensure `vars.challenge_id`
was set earlier. Adapt the method, URL, authentication, and response path to your actual
service contract; `/codes/latest` is an example, not a built-in engine endpoint.

Use it lazily in the verification node:

```yaml
actions:
  - fill: {target: code_field, value: "{requests.otp.code}"}
  - click: {target: verify_button}
```

The renderer fetches immediately before the fill. Request references do not evaluate
in predicates. `when: {request: {name: otp, status: ok}}` inspects a recorded outcome;
it does not initiate a request or keep retrying one.

For branching, fetch explicitly:

```yaml
obtain_code:
  recognize: {visible: code_field}
  actions:
    - fetch: {request: otp, into: verification, on_error: continue}
  transitions:
    - to: enter_code
      when: {request: {name: otp, status: ok}}
    - to: code_unavailable
enter_code:
  recognize: {visible: code_field}
  actions:
    - fill: {target: code_field, value: "{vars.verification.code}"}
    - click: {target: verify_button}
  transitions:
    - to: done
      when: {url: 're:/account'}
code_unavailable:
  terminal: failure
  result: {status: code_unavailable}
```

This fragment needs the target declarations and `done` node. On failure with
`on_error: continue`, `vars.verification` becomes `null` and request status becomes
`failed`. Without `continue`, the error follows the node's `on_fail` route.

**Reuse the fetched variable when you mean reuse.** Reading `{requests.otp.code}` again
with default caching performs another fetch. Reading `{vars.verification.code}` uses
the snapshot from `fetch`. This matters for one-time codes and services that consume a
challenge when queried. Do not delay between obtaining a code and submitting it.

## Request field reference

| Field | Default / constraint | Meaning |
|---|---|---|
| `kind` | `http`; `http`, `totp`, `imap` | Source implementation. |
| `description` | Empty string | Purpose shown in tooling. |
| `sensitive` | `true` | Extracted values become Secrets and are masked in traces. |
| `cache` | `none`; `none` or `run` | Refetch at use, or reuse successful result for the session/run. |
| `timeout_s` | Unset | HTTP per-attempt timeout override; otherwise engine configuration. Not a total retry deadline or general IMAP timeout. |
| `retry` | One attempt | Policy described below. |
| `extract` | `{}` | Required for HTTP and IMAP by lint; prohibited for TOTP. |

### HTTP fields

| Field | Default / requirement |
|---|---|
| `url` | Required for HTTP; templated string. |
| `method` | `GET`; also `POST`, `PUT`, `PATCH`, `DELETE`. |
| `headers` | `{}`; header values are templated strings. |
| `query` | `{}`; query parameter values are templated strings. |
| `json` | Absent; recursively renders string values in a JSON-compatible structure. |
| `body` | Absent; rendered text body. Mutually exclusive with `json`. |
| `expect_status` | `["2xx"]`; accepts numeric codes or classes such as `"5xx"`. |

HTTP runs in the engine/builder API process, **not inside the browser**. It does not
inherit browser cookies, local storage, page JavaScript variables, or logged-in identity.
Requests do not share an automatic cookie jar between separate calls either. Supply
explicit authentication supported by the service. Use `evaluate` only if the task
actually needs browser-context JavaScript; that has different trust and execution behavior.

Use `query` for URL parameters rather than interpolating unescaped values into the URL.
There is no `form`, multipart upload, automatic OAuth flow, or automatic access-token
refresh field in the request schema. The current HTTP client follows redirects.

`REQUESTS_ALLOWED_HOSTS`, if configured, checks the initial HTTP hostname using exact
or subdomain matches; empty means unrestricted by that check. It is not a comprehensive
network sandbox or a redirect/IMAP destination policy. Use deployment network controls
and trusted service URLs for actual isolation.

### Extractors

Each field under `extract` has exactly one source:

```yaml
extract:
  code:
    json: data.codes[0].value
  request_id:
    header: X-Request-Id
    required: false
  expires_in:
    json: expires_in
    required: false
```

| Source | Behavior |
|---|---|
| `json` | Dot-separated object keys plus numeric `[index]`; optional leading `$.`. No wildcard, recursive search, or JSONPath filter language. |
| `regex` | Raw Python regex over response text; first capture group if present, otherwise full match. |
| `header` | HTTP response header lookup, case-insensitive through the HTTP client. |
| `required` | Defaults to `true`. Missing/null/empty extracted values fail that attempt. |

Extracted values are **strings**. JSON objects/lists are serialized to JSON text;
numbers become strings. For non-sensitive data, use a script with `JSON.parse` or
`Number` if you need typed values. Optional absent fields may be omitted from the result,
so blindly templating them can still fail. An extractor named `code` does not enforce
digit count, code freshness, ownership, or challenge matching: that contract belongs
to the service and your workflow.

## Retry and polling

```yaml
retry:
  attempts: 6
  backoff_s: [1, 2, 4]
  on_status: [429, "5xx"]
  on_network_error: true
  until_extracted: true
```

| Field | Default | Meaning |
|---|---|---|
| `attempts` | `1`, range 1–50 | Total attempts, including the first. |
| `backoff_s` | `[1.0]` | Delay between retryable failures; last delay repeats; empty means zero delay. |
| `on_status` | `["5xx", 429]` | Unexpected HTTP statuses eligible for retry. |
| `on_network_error` | `true` | Retry recognized network errors (and mailbox connection errors). |
| `until_extracted` | `false` | Retry an accepted response missing required extraction, or no matching email yet. Needs at least two attempts. |

An HTTP status must first satisfy `expect_status` before extraction is attempted. If
your service responds `404` while a code is pending, configure `on_status` to include
404; `until_extracted` alone does not retry that status. Do not retry invalid credentials
or permission denial indefinitely. Retry does not automatically renew authorization.

With six attempts and delays `[1, 2, 4]`, waits are 1, 2, 4, 4, and 4 seconds, plus
request execution time. A node's `timeout_s` starts its transition wait after actions;
it does not cap this request loop. Node transient retries are another mechanism:
`RequestFailed` has its own failure classification and should be handled through
request policy or routing, not assumed to replay as a transient action.

## Caching and request dependencies

`cache: none` is the default and is normally correct for OTPs. `cache: run` reuses a
successful result; it has no expiry/TTL awareness. Failed results are not reused as
successful cache entries. Patching a request with the same name in a builder session
does not necessarily clear its cached result: start a new session after changing a
cached authentication request.

Requests can refer to other requests:

```yaml
requests:
  platform_login:
    method: POST
    url: "{platform_url}/auth/login"
    json:
      email: "{platform_email}"
      password: "{platform_password}"
    extract:
      token: {json: access_token}
    cache: run
  otp:
    method: POST
    url: "{platform_url}/credentials/{otp_credential_id}/totp"
    headers:
      Authorization: "Bearer {requests.platform_login.token}"
    extract:
      code: {json: code}
```

This is a service-contract illustration, not a universal endpoint convention. Declare
all four inputs (including the password as a credential field in production), adapt
paths, and perform any required password confirmation or scoped-key exchange. The
[integration chapter](integration.md) gives this repository's actual contract.

Dependencies execute first and cycles are rejected. Caching login avoids logging in
again each time a code is used. Dependency evaluation happens even before a parent
request returns its cached result, so consider caching stable dependencies too.
One template referencing a request twice fetches it once for that rendering operation;
separate argument values or actions may render independently and fetch again.

## TOTP from a provider

```yaml
requests:
  otp:
    kind: totp
```

The engine asks the current run's TOTP callback for a code. With a remote credential
provider, only the code need leave the platform. Without a callback or explicit seed,
the request fails with "no TOTP source." This form has no `extract`; its only field is
`code`. Merely defining it does not select a vault entry or redeem a lease.

## Multiple seeds or multiple credential IDs

Explicit seed mode:

```yaml
inputs:
  seed_a: {type: password, credential: seed_a}
  seed_b: {type: password, credential: seed_b}
requests:
  otp_a: {kind: totp, secret: "{seed_a}"}
  otp_b: {kind: totp, secret: "{seed_b}"}
```

`secret` is the base32 seed template. `digits` defaults to 6 and `period` to 30 seconds.
The local generator uses SHA-1; there is no request `algorithm` field. Explicit seed
mode waits near the end of a window before generating. Provider mode uses the provider's
configuration and timing; request `digits`/`period` do not override its callback.

For **seed-free authoring**, use separate HTTP requests with distinct credential ID
inputs and the same authorized platform token. The complete
[multiple_platform_codes.yaml](examples/multiple_platform_codes.yaml) example fetches
both and reports only readiness, never returning the codes as workflow outputs.
Your platform must authorize each credential independently. Supplying two IDs does not
create authorization, and one run's default TOTP provider remains a single callback.

## Email codes through IMAP

```yaml
inputs:
  mailbox_host: {credential: otp_mail_host}
  mailbox_user: {credential: otp_mail_username}
  mailbox_password: {type: password, credential: otp_mail_password}
requests:
  email_otp:
    kind: imap
    imap:
      host: "{mailbox_host}"
      port: 993
      username: "{mailbox_user}"
      password: "{mailbox_password}"
      folder: INBOX
      from: 're:no-reply@example\.test'
      subject: 're:verification code'
      max_age_s: 300
    extract:
      code:
        regex: '(?i)code[:\s]+([0-9]{6})'
    retry:
      attempts: 10
      backoff_s: [2]
      until_extracted: true
```

IMAP connection fields `host`, `username`, and `password` are templated. `folder`,
`from`, and `subject` are static settings/patterns. Only regex extractors are allowed.
Defaults: port 993, folder INBOX, max age 600 seconds. Port 993 uses SSL; other ports
use the plain IMAP client in the current implementation, without automatic STARTTLS.

The runner reads the most recent 20 message IDs in reverse order, checks the message
Date, filters sender/subject, and extracts from subject plus the preferred plain-text
body (falling back to stripped HTML). It ignores messages older than the later of
`run_started - 30 seconds` and `now - max_age_s`. Messages without a usable Date are
skipped. It does not mark messages consumed or correlate them to a challenge beyond
your filters, so shared/busy mailboxes need a stronger service-level correlation strategy.

The login action that triggers delivery must happen before polling. Testing in an old
builder session uses that session's original timing context; standalone tests use a new
request context. The matching message may be absent, old, or for a different attempt.

## Builder testing workflow

1. Declare requests in **Graph YAML** and save. The Requests tab lists existing declarations;
   it is not a separate request-language editor.
2. Supply required inputs or the repository's credential reference/TOTP source.
3. Select a request and press **Test** for a standalone call. **Test in session** uses
   the live run's input values, vars, credential source, cache, and request status.
4. Inspect each attempt: rendered/masked request, status, elapsed time, retries,
   missing fields, response excerpt, and extracted values. Use **reveal** only for a
   deliberate local inspection of sensitive values.
5. Exercise success, pending/missing code, expired authorization, and denied credential
   access. A successful test can consume a one-time value; run a fresh challenge afterward.

Keep tokens and seeds out of YAML defaults and examples. `sensitive: true` protects
extracted values; it does not automatically identify every secret a response body might
contain. Design code-service responses narrowly and avoid including unrelated secrets.
