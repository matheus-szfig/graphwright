# Complete example graphs

[Guide index](../README.md) · [Validation and commands](../integration.md)

These are complete, compiler-checked graph documents. They live outside the active
graph catalog so documentation does not silently add production workflows. Copy the
chosen file to the repository's graph directory to open it in the builder; retain the
filename/workflow identity or rename both together.

| File | Demonstrates | Runtime prerequisites |
|---|---|---|
| [record_search.yaml](record_search.yaml) | Recognition, targets, branching, extraction, outputs | Acme Records demo `/search` and `/records/` page contract. |
| [login_totp.yaml](login_totp.yaml) | Password login, provider-backed OTP, bounded retry | Acme Records demo login and configured credential/TOTP source. |
| [http_code.yaml](http_code.yaml) | Explicit code fetch, polling, failure branch, snapshot reuse | Service returning `{"data":{"code":"..."}}`; no page navigation required. |
| [multiple_platform_codes.yaml](multiple_platform_codes.yaml) | Two distinct remote credential IDs without seeds | Configured POST endpoint URLs returning `{"code":"..."}` and authorized token. |
| [transform.yaml](transform.yaml) | Typed arguments, JS return values, branching | No site or service; a browser context is still needed for GraphSession observation. |
| [failure_routing.yaml](failure_routing.yaml) | Assertion failure, `_error`, custom recovery | No site/service; deliberately fails the assertion when `approved` is false. |

None contains real credentials. Supply sensitive values through the builder's temporary
inputs or the deployment credential provider. HTTP examples are contract templates,
not claims that the demo server exposes those endpoints. Use mock services when testing
them automatically. The record and login examples match the bundled Acme demo in the
white-label distribution; see each repository's integration guide for availability.

`http_code` and `multiple_platform_codes` do not return codes in outputs. They retain
the fetched Secret values in vars for a subsequent fill when incorporated into a real
login graph. Their terminal output is only non-sensitive readiness metadata.
