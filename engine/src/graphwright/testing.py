"""Run graph requests outside a browser session — for debugging and tests.

``run_request`` is what ``graphwright request-test`` and the builder's Requests tab
call: it renders the request with the given inputs/vars (plus any credential),
runs it with its real retry policy, and returns every attempt (dependencies
included) as a JSON-safe report. Extracted secrets stay masked unless
``reveal=True`` — local debugging only.
"""

from __future__ import annotations

from typing import Any

from graphwright.catalog import build_inputs
from graphwright.credentials.base import Credential
from graphwright.engine.dsl import ActionContext, fetch_request
from graphwright.engine.errors import EngineError
from graphwright.engine.requests import RequestRunner, RequestServices
from graphwright.engine.schema import Workflow
from graphwright.engine.secrets import Redactor, Secret, SecretInputs
from graphwright.services import request_services


def _inputs_for(wf: Workflow, provided: dict[str, Any], credential: Credential | None) -> SecretInputs:
    """Like a run's inputs, but tolerant: a debug call may only provide what the
    request uses."""
    try:
        return build_inputs(wf, provided, credential)
    except ValueError:
        out = SecretInputs({k: v for k, v in provided.items()})
        for name, spec in wf.inputs.items():
            if spec.credential and credential is not None and credential.values.get(spec.credential):
                out[name] = Secret(credential.values[spec.credential])
            elif name not in out and spec.default is not None:
                out[name] = spec.default
        return out


async def run_request(
    wf: Workflow,
    name: str,
    inputs: dict[str, Any] | None = None,
    *,
    variables: dict[str, Any] | None = None,
    credential: Credential | None = None,
    services: RequestServices | None = None,
    reveal: bool = False,
) -> dict[str, Any]:
    if name not in wf.requests:
        return {"ok": False, "request": name, "error": f"unknown request {name!r}", "requests": list(wf.requests)}
    redactor = Redactor(credential.plaintexts if credential else ())
    services = services or request_services()
    if services.on_secret is None:
        services.on_secret = redactor.add
    if credential is not None and services.totp is None:
        services.totp = credential.totp
    runner = RequestRunner(wf.requests, services)
    events: list[dict[str, Any]] = []
    ctx = ActionContext(
        page=None, observer=None, human=None, workflow=wf,  # type: ignore[arg-type]
        inputs=_inputs_for(wf, inputs or {}, credential), vars=dict(variables or {}),
        log=lambda kind, **data: events.append({"kind": kind, **data}), requests=runner,
    )
    error: str | None = None
    try:
        await fetch_request(name, ctx, raise_on_error=False)
    except EngineError as exc:  # template not set, cycle, unknown dependency
        error = str(exc)
    result = runner.results.get(name)
    report: dict[str, Any] = {
        "ok": bool(result and result.ok) and error is None,
        "request": name,
        "kind": wf.requests[name].kind,
        "error": error or (result.error if result else None),
        "calls": [redactor(e) for e in events if e["kind"] == "request"],
    }
    if result is not None:
        report["values"] = (
            {k: (v.reveal() if isinstance(v, Secret) else v) for k, v in result.values.items()}
            if reveal else result.trace()["values"]
        )
    return report


async def run_script(
    wf: Workflow,
    code: str,
    *,
    args: dict[str, Any] | None = None,
    inputs: dict[str, Any] | None = None,
    variables: dict[str, Any] | None = None,
    credential: Credential | None = None,
    into: str | None = None,
    timeout_ms: int = 1000,
    memory_mb: int = 64,
    allow_secrets: bool = False,
) -> dict[str, Any]:
    """Run a code-node body the way the `script` action would — args rendered from
    the graph's templates, `vars`/`inputs` as non-secret copies — and report the
    value, console lines, error and timing. What `graphwright script-test` and the
    builder's code tester call; no browser involved."""
    from graphwright.engine.dsl import do_script
    from graphwright.engine.schema import Script

    redactor = Redactor(credential.plaintexts if credential else ())
    events: list[dict[str, Any]] = []
    ctx = ActionContext(
        page=None, observer=None, human=None, workflow=wf,  # type: ignore[arg-type]
        inputs=_inputs_for(wf, inputs or {}, credential), vars=dict(variables or {}),
        log=lambda kind, **data: events.append({"kind": kind, **data}),
        requests=RequestRunner(wf.requests, request_services(on_secret=redactor.add)),
    )
    error: str | None = None
    details: list[str] = []
    try:
        spec = Script(code=code, args=args or {}, into="__result", timeout_ms=timeout_ms, memory_mb=memory_mb,
                      allow_secrets=allow_secrets)
        await do_script(spec, ctx)
    except EngineError as exc:
        error, details = str(exc), list(exc.details)
    event = next((e for e in reversed(events) if e["kind"] == "script"), {})
    value = ctx.vars.get("__result") if error is None else None
    if error is None and into is None and not isinstance(value, dict):
        error = f"without `into` the code must return an object (got {type(value).__name__})"
    sets = [into] if into else (sorted(value) if isinstance(value, dict) else [])
    return redactor({
        "ok": error is None,
        "value": None if error else "***" if isinstance(value, Secret) or (isinstance(value, dict) and any(isinstance(v, Secret) for v in value.values()))
        else value,
        "error": error,
        "details": details,
        "logs": event.get("logs", []),
        "elapsed_ms": event.get("elapsed_ms"),
        "sets": sets if error is None else [],  # the vars the action would set
    })
