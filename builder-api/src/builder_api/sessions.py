"""Live sessions — one Playwright browser per API process, one context per session.

A session wraps graphwright's `GraphSession`. Events are fanned out to WebSocket subscribers
as they happen; the latest PageState, target resolutions and a screenshot are
available on demand for the inspector.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from graphwright.browser.humanize import StealthBudget
from graphwright.browser.profiles import get_profile
from graphwright.config import settings
from graphwright.credentials import Credential, CredentialRejected, get_provider
from graphwright.engine import Workflow, compile_workflow
from graphwright.engine.compiler import targets_report
from graphwright.engine.conditions import evaluate
from graphwright.engine.errors import EngineError
from graphwright.engine.runtime import Event, GraphSession
from graphwright.engine.schema import Condition
from graphwright.engine.secrets import Redactor, Secret, SecretInputs
from graphwright.services import request_services

log = logging.getLogger(__name__)

LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--no-first-run",
]


@dataclass
class LiveSession:
    id: str
    graph_name: str
    graph_path: Path
    inputs: dict[str, Any]
    context: Any
    page: Any
    session: GraphSession
    credential_ref: str | None = None
    subscribers: list[asyncio.Queue] = field(default_factory=list)
    busy: bool = False
    operation: asyncio.Task | None = None

    _state_pending: bool = field(default=False, init=False)

    def _broadcast(self, payload: dict[str, Any]) -> None:
        for q in list(self.subscribers):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                log.warning("dropping event for slow subscriber")

    def state_message(self) -> dict[str, Any]:
        s = self.session
        return {"kind": "session_state", "session": {
            "id": self.id, "graph": self.graph_name, "node": s.node,
            "status": s.status, "visits": dict(s.visits), "busy": self.busy,
            "failure": s.redact(s.failure), "result": s.redact(s.result),
            "requests": s.request_statuses,
        }}

    def publish_state(self) -> None:
        self._state_pending = False
        self._broadcast(self.state_message())

    def publish(self, ev: Event) -> None:
        self._broadcast(ev.as_dict())
        # Move events precede the runtime's node assignment. Publish after this
        # synchronous turn, coalescing bursts while preserving the event stream.
        if self.subscribers and not self._state_pending:
            self._state_pending = True
            asyncio.get_running_loop().call_soon(self.publish_state)

    def summary(self) -> dict[str, Any]:
        s = self.session
        return {
            "id": self.id,
            "graph": self.graph_name,
            "inputs": self.inputs,
            "credential": self.credential_ref,
            "requests": s.request_statuses,
            "status": s.status,
            "node": s.node,
            "visits": dict(s.visits),
            "events": len(s.events),
            "failure": s.failure,
            "result": s.result,
            "vars": s.redact(s.vars),
            "url": self.page.url if self.page else None,
            "busy": self.busy,
        }


def session_inputs(wf: Workflow, provided: dict[str, Any], credential: Credential | None) -> SecretInputs:
    """Inputs for a builder session: defaults, then what the author typed, then the
    credential's fields. Unlike a worker run, the author may type a credential input by
    hand (no provider configured); it is wrapped as a Secret either way."""
    out = SecretInputs()
    for name, spec in wf.inputs.items():
        if spec.credential and credential is not None and credential.values.get(spec.credential):
            out[name] = Secret(credential.values[spec.credential])
        elif name in provided and provided[name] not in (None, ""):
            secret = bool(spec.credential) or spec.type == "password"
            out[name] = Secret(str(provided[name])) if secret else provided[name]
        elif spec.default is not None:
            out[name] = spec.default
    missing = [k for k, spec in wf.inputs.items() if spec.required and k not in out]
    if missing:
        raise ValueError(f"missing inputs: {missing}")
    return out


def redeem(ref: str | None) -> Credential | None:
    """A credential from the configured provider (CREDENTIAL_PROVIDER), for authors who
    keep test accounts in a local credentials file or .env."""
    if not ref:
        return None
    try:
        return get_provider().redeem(ref, run_id="builder")
    except CredentialRejected as exc:
        raise ValueError(f"credential {ref!r} rejected: {exc.code}") from exc


class SessionManager:
    def __init__(self, *, headed: bool = True, graphs_dir: Path, request_transport: Any = None) -> None:
        self.headed = headed
        self.graphs_dir = graphs_dir
        # Tests inject an httpx.MockTransport here; None = the real network.
        self.request_transport = request_transport
        self._pw: Any = None
        self._browser: Any = None
        self.sessions: dict[str, LiveSession] = {}

    async def _browser_instance(self) -> Any:
        if self._browser is None:
            from playwright.async_api import async_playwright

            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(headless=not self.headed, args=LAUNCH_ARGS)
            log.info("browser launched headed=%s", self.headed)
        return self._browser

    async def create(self, graph_name: str, inputs: dict[str, Any], *, fast: bool = False,
                     credential: str | None = None) -> LiveSession:
        path = self.graphs_dir / f"{graph_name}.yaml"
        graph = compile_workflow(Workflow.from_yaml(path))
        cred = redeem(credential)
        run_inputs = session_inputs(graph.workflow, inputs, cred)
        redactor = Redactor(cred.plaintexts if cred else ())
        redactor.add(*(v.reveal() for v in run_inputs.values() if isinstance(v, Secret)))
        browser = await self._browser_instance()
        # The worker's machine profile, so the builder sees the identity production presents.
        context = await browser.new_context(**get_profile(settings.browser_default_profile).context_kwargs())
        page = await context.new_page()
        sid = uuid.uuid4().hex[:8]
        shown = {k: ("***" if isinstance(v, Secret) else v) for k, v in run_inputs.items()}
        live = LiveSession(id=sid, graph_name=graph_name, graph_path=path, inputs=shown, context=context,
                           page=page, session=None, credential_ref=credential)  # type: ignore[arg-type]
        budget = StealthBudget(scale=0.15) if fast else None
        services = request_services(totp=cred.totp if cred else None, on_secret=redactor.add,
                                   transport=self.request_transport)
        live.session = GraphSession(graph, page, run_inputs, budget=budget, on_event=live.publish,
                                    redactor=redactor, services=services)
        self.sessions[sid] = live
        await self._guard(live, live.session.start())
        return live

    def get(self, sid: str) -> LiveSession:
        try:
            return self.sessions[sid]
        except KeyError:
            raise KeyError(f"session {sid!r} not found") from None

    async def _guard(self, live: LiveSession, coro: Any) -> Any:
        if live.busy:
            coro.close()
            raise RuntimeError("session is busy")
        live.busy = True
        live.operation = asyncio.current_task()
        live.publish_state()
        try:
            return await coro
        finally:
            live.operation = None
            live.busy = False
            live.publish_state()

    async def step(self, sid: str) -> dict[str, Any]:
        live = self.get(sid)
        await self._guard(live, live.session.step())
        return live.summary()

    async def run(self, sid: str, max_steps: int | None = None) -> dict[str, Any]:
        live = self.get(sid)
        await self._guard(live, live.session.run(max_steps=max_steps))
        return live.summary()

    async def reset(self, sid: str, node: str) -> dict[str, Any]:
        live = self.get(sid)
        live.session.reset_to(node)
        return live.summary()

    async def patch(self, sid: str, workflow: Workflow | None = None) -> dict[str, Any]:
        """Reload the graph (from disk by default) into the running session."""
        live = self.get(sid)
        wf = workflow or Workflow.from_yaml(live.graph_path)
        live.session.patch(wf)
        return live.summary()

    async def observe(self, sid: str) -> dict[str, Any]:
        live = self.get(sid)
        state = await self._guard(live, live.session.observer.observe())
        live.session.last_state = state
        return {"render": state.render(), "state": state.model_dump(mode="json")}

    def last_state(self, sid: str) -> dict[str, Any] | None:
        live = self.get(sid)
        s = live.session.last_state
        return {"render": s.render(), "state": s.model_dump(mode="json")} if s else None

    def targets(self, sid: str) -> dict[str, Any]:
        live = self.get(sid)
        s = live.session.last_state
        if s is None:
            return {}
        wf = live.session.workflow
        report = targets_report(wf.targets, s)
        return {
            name: {
                "ok": r.ok,
                "element": r.element.model_dump(mode="json") if r.element else None,
                "attempts": r.explain(wf.targets[name]),
                "description": wf.targets[name].description,
            }
            for name, r in report.items()
        }

    def recognize_all(self, sid: str) -> dict[str, Any]:
        """Every node's `recognize` against the last observation — the canvas colours."""
        live = self.get(sid)
        s = live.session.last_state
        if s is None:
            return {}
        _, matches = live.session.graph.resync(s, live.session.vars, requests=live.session.request_statuses)
        return {name: {"ok": m.ok, "reasons": m.reasons} for name, m in matches.items()}

    def evaluate(self, sid: str, condition: dict[str, Any]) -> dict[str, Any]:
        live = self.get(sid)
        s = live.session.last_state
        if s is None:
            return {"ok": False, "reasons": ["no observation yet"]}
        cond = Condition.model_validate(condition)
        m = evaluate(cond, live.session.action_context(s).eval_ctx(s))
        return {"ok": m.ok, "reasons": m.reasons}

    async def console(self, sid: str, script: str, *, args: dict[str, Any] | None = None, world: str = "isolated",
                      frame: int | str = 0, timeout_ms: int = 5000) -> dict[str, Any]:
        """Run ad-hoc JavaScript in the session's page — the builder's browser console.
        Same path as the `evaluate` action (an async function body with `args`), and
        the result lands in the session's event stream, redacted."""
        import json
        import time

        live = self.get(sid)
        s = live.session
        if world not in ("isolated", "main"):
            raise ValueError(f"world must be 'isolated' or 'main', got {world!r}")
        t0 = time.perf_counter()

        async def go() -> Any:
            return await asyncio.wait_for(s.observer.evaluate(script, args or {}, world=world, frame=frame),
                                          timeout_ms / 1000)

        try:
            value = await self._guard(live, go())
        except RuntimeError as exc:
            if "busy" in str(exc):
                raise
            error = str(exc)
        except TimeoutError:
            error = f"did not finish in {timeout_ms} ms"
        except Exception as exc:  # noqa: BLE001 — the page threw / frame gone: that is the answer
            error = str(exc)
        else:
            elapsed = int((time.perf_counter() - t0) * 1000)
            try:
                json.dumps(value)
            except (TypeError, ValueError):
                value = repr(value)
            value = s.redact(value)
            s.emit("evaluate", ok=True, world=world, frame=frame, source="console", elapsed_ms=elapsed, value=value)
            return {"ok": True, "value": value, "error": None, "elapsed_ms": elapsed}
        elapsed = int((time.perf_counter() - t0) * 1000)
        error = s.redact(error)
        s.emit("evaluate", ok=False, world=world, frame=frame, source="console", elapsed_ms=elapsed, error=error)
        return {"ok": False, "value": None, "error": error, "elapsed_ms": elapsed}

    async def run_request(self, sid: str, name: str, *, variables: dict[str, Any] | None = None,
                          reveal: bool = False) -> dict[str, Any]:
        """Run one graph request inside a live session: the session's inputs, vars and
        credential apply, and the `request` event lands in the session's stream. The
        result becomes the session's, so a `request:` condition sees it on the next
        step. `variables` override vars for this call only."""
        live = self.get(sid)
        s = live.session
        if name not in s.workflow.requests:
            raise KeyError(f"request {name!r} not found in {s.workflow.workflow!r}")
        start = len(s.events)
        error: str | None = None
        try:
            await self._guard(live, s.run_request(name, variables=variables))
        except EngineError as exc:
            error = str(exc)
        result = s.requests.results.get(name)
        report: dict[str, Any] = {
            "ok": bool(result and result.ok) and error is None,
            "request": name,
            "kind": s.workflow.requests[name].kind,
            "error": error or (result.error if result else None),
            "calls": [e.as_dict() for e in s.events[start:] if e.kind == "request"],
            "session": sid,
        }
        if result is not None:
            report["values"] = (
                {k: (v.reveal() if isinstance(v, Secret) else v) for k, v in result.values.items()}
                if reveal else result.trace()["values"]
            )
        return report

    async def screenshot(self, sid: str) -> str:
        live = self.get(sid)
        png = await self._guard(live, live.page.screenshot(type="jpeg", quality=70))
        return base64.b64encode(png).decode("ascii")

    def events(self, sid: str, since: int = 0) -> list[dict[str, Any]]:
        return [e.as_dict() for e in self.get(sid).session.events[since:]]

    def subscribe(self, sid: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self.get(sid).subscribers.append(q)
        return q

    def unsubscribe(self, sid: str, q: asyncio.Queue) -> None:
        live = self.sessions.get(sid)
        if live and q in live.subscribers:
            live.subscribers.remove(q)

    async def close(self, sid: str) -> None:
        live = self.sessions.pop(sid, None)
        if live is None:
            return
        if live.operation and live.operation is not asyncio.current_task():
            live.operation.cancel()
            await asyncio.gather(live.operation, return_exceptions=True)
        try:
            await live.session.observer.close()
        except Exception:
            log.debug("observer close failed", exc_info=True)
        if live.context is not None:
            await live.context.close()

    async def close_graph(self, graph_name: str) -> None:
        for sid in [s for s, live in self.sessions.items() if live.graph_name == graph_name]:
            await self.close(sid)

    async def shutdown(self) -> None:
        for sid in list(self.sessions):
            await self.close(sid)
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None
