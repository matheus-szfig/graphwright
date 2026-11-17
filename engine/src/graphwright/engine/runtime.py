"""GraphSession — executes a compiled graph one node at a time.

The loop per step (spec §4):

    observe → recognize current node (else resync / unknown_state)
            → run actions → wait until a transition matches (signature polling)
            → postconditions → move to the next node → record visit

`step()` is the unit the builder drives; `run()` loops it. Every decision is emitted
as an `Event` with the predicate reasons, so a UI can show *why* the engine did what
it did.

Failure routing: a node's `on_fail` names where an error goes — `repair` (default:
structural failures stop in `needs_repair` with the evidence, others in `failed`),
`fail` (always `failed`), or another node, which the session moves to with the
error recorded in `vars._error` so that node can branch on it.
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from graphwright.browser.humanize import Humanizer, StealthBudget
from graphwright.engine import dsl
from graphwright.engine.compiler import CompiledGraph, CompiledNode, compile_workflow
from graphwright.engine.dsl import ActionContext
from graphwright.engine.errors import (
    EngineError,
    JumpSignal,
    LoopGuard,
    NoTransition,
    PostconditionFailed,
    RecognitionFailed,
    TransientError,
    UnknownState,
)
from graphwright.engine.requests import RequestResult, RequestRunner, RequestServices
from graphwright.engine.schema import Node, Workflow
from graphwright.observe.observer import Observer
from graphwright.observe.pagestate import PageState

Status = Literal["idle", "running", "paused", "done", "failed", "needs_repair"]


@dataclass
class Event:
    kind: str
    node: str | None
    data: dict[str, Any] = field(default_factory=dict)
    ts: datetime = field(default_factory=lambda: datetime.now(UTC))

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "node": self.node, "ts": self.ts.isoformat(), **self.data}


@dataclass
class StepResult:
    node: str
    next_node: str | None
    status: Status
    state: PageState | None
    events: list[Event]


class GraphSession:
    def __init__(
        self,
        graph: CompiledGraph,
        page: Any,
        inputs: dict[str, Any],
        *,
        observer: Observer | None = None,
        budget: StealthBudget | None = None,
        on_event: Any = None,
        max_total_steps: int = 60,
        redactor: Any = None,
        services: RequestServices | None = None,
    ) -> None:
        self.graph = graph
        self.page = page
        self.observer = observer or Observer(page, max_elements=graph.workflow.observation.max_elements)
        self._owns_observer = observer is None
        self.human = Humanizer(page, budget or StealthBudget.for_risk(graph.workflow.risk))
        self.inputs = inputs
        # callable(obj) -> obj with known plaintext secrets masked; applied to
        # every event payload and to the failure record (engine/secrets.py).
        self._redact = redactor or (lambda obj: obj)
        services = services or RequestServices()
        if services.on_secret is None and hasattr(redactor, "add"):
            services.on_secret = redactor.add
        self.requests = RequestRunner(graph.workflow.requests, services)
        self.vars: dict[str, Any] = {}
        self.sensitive_elements: set[str] = set()  # fields that hold a typed secret
        self.node: str = graph.workflow.start
        self.status: Status = "idle"
        self.visits: Counter[str] = Counter()
        self.events: list[Event] = []
        self.last_state: PageState | None = None
        self.result: dict[str, Any] | None = None
        self.failure: dict[str, Any] | None = None
        self._on_event = on_event
        self._max_total_steps = max_total_steps
        self._steps = 0
        self._entered = False
        self._lock = asyncio.Lock()

    # -- events --

    def _emit(self, kind: str, **data: Any) -> Event:
        ev = Event(kind=kind, node=self.node, data=self._redact(data))
        self.events.append(ev)
        if self._on_event:
            self._on_event(ev)
        return ev

    @property
    def workflow(self) -> Workflow:
        return self.graph.workflow

    def emit(self, kind: str, **data: Any) -> Event:
        """Add an event to the session's stream (redacted, like every other event) —
        for tools that act on the session from outside a step (the builder console)."""
        return self._emit(kind, **data)

    def redact(self, obj: Any) -> Any:
        """Mask every plaintext secret this session knows (credential values,
        extracted request values) — what every event already goes through."""
        return self._redact(obj)

    @property
    def request_statuses(self) -> dict[str, str]:
        return self.requests.statuses

    async def run_request(self, name: str, *, variables: dict[str, Any] | None = None) -> RequestResult:
        """Run one graph request now, as an action would (dependencies first, event
        emitted, result kept for `request:` conditions). `variables` override vars for
        this call only — the builder's "test in session"."""
        ctx = self.action_context(self.last_state)
        if variables:
            ctx.vars = {**self.vars, **variables}
        return await dsl.fetch_request(name, ctx, raise_on_error=False)

    def action_context(self, state: PageState | None) -> ActionContext:
        return ActionContext(
            page=self.page, observer=self.observer, human=self.human, workflow=self.workflow,
            inputs=self.inputs, vars=self.vars, state=state, log=self._emit, requests=self.requests,
            sensitive=self.sensitive_elements,
        )

    # -- lifecycle --

    async def start(self) -> PageState | None:
        """Navigate to the entry URL (if any) and observe. Does not run a node."""
        if self.workflow.entry_url and not self._entered:
            ctx = self.action_context(None)
            url = await ctx.render(self.workflow.entry_url)
            self._emit("goto", url=ctx.render_template(self.workflow.entry_url, reveal=False))
            await self.page.goto(url, wait_until="domcontentloaded")
            self._entered = True
        self.status = "paused"
        self.last_state = await self.observer.observe()
        self._emit("observe", render=self.last_state.render(), signature=self.last_state.signature)
        return self.last_state

    def reset_to(self, node: str) -> None:
        """Builder: continue from another node without touching the browser."""
        if node not in self.graph.nodes:
            raise KeyError(node)
        self.node = node
        self.status = "paused"
        self.failure = None
        self._emit("reset", to=node)

    def patch(self, workflow: Workflow) -> None:
        """Builder: swap the graph (after lint) and keep position if the node still exists."""
        new_graph = compile_workflow(workflow)
        self.graph = new_graph
        if self._owns_observer:
            self.observer.set_max_elements(workflow.observation.max_elements)
        self.requests.specs = workflow.requests
        self.requests.results = {k: v for k, v in self.requests.results.items() if k in workflow.requests}
        if self.node not in new_graph.nodes:
            self.node = new_graph.workflow.start
        self.failure = None
        if self.status in ("failed", "needs_repair"):
            self.status = "paused"
        self._emit("patched", version=workflow.version, nodes=list(new_graph.nodes))

    # -- the step --

    async def step(self) -> StepResult:
        async with self._lock:
            return await self._step()

    async def _step(self) -> StepResult:
        if self.status in ("done", "failed"):
            return StepResult(self.node, None, self.status, self.last_state, [])
        if not self._entered and self.workflow.entry_url:
            await self.start()
        start_idx = len(self.events)
        self.status = "running"
        node = self.graph[self.node]
        self._steps += 1
        if self._steps > self._max_total_steps:
            return self._fail(LoopGuard(f"more than {self._max_total_steps} steps"), start_idx)

        try:
            if node.spec.is_terminal:
                return self._finish(node, start_idx)
            if node.spec.call:
                raise EngineError(f"subgraph call {node.spec.call!r} not implemented yet")

            state = await self._observe()

            # 1. Recognition guards the expected node. Global recovery is opt-in.
            m = node.recognize(state, self.vars, self.requests.statuses)
            self._emit("recognize", ok=m.ok, reasons=m.reasons)
            if not m.ok:
                if self.workflow.resync == "disabled":
                    raise RecognitionFailed(
                        f"node {node.name!r} did not recognize page {state.route!r}; "
                        "its actions were not run. Global resync is disabled.",
                        details=[f"{node.name}: {reason}" for reason in m.reasons],
                    )
                winner, all_matches = self.graph.resync(state, self.vars, requests=self.requests.statuses)
                self._emit("resync", winner=winner, matches={k: v.ok for k, v in all_matches.items()})
                if winner is None:
                    raise UnknownState(
                        f"node {node.name!r} did not recognize page {state.route!r}; "
                        "global resync found no matching node. Its actions were not run.",
                        details=[f"{k}: {'; '.join(v.reasons)}" for k, v in all_matches.items()],
                    )
                return self._move(winner, node, start_idx)

            # 2. loop guard
            self.visits[self.node] += 1
            if self.visits[self.node] > node.spec.max_visits:
                raise LoopGuard(f"node {self.node!r} visited {self.visits[self.node]} times (max {node.spec.max_visits})")

            # 3. actions
            ctx = self.action_context(state)
            try:
                for action in node.spec.actions:
                    await self._with_retry(node, lambda a=action: dsl.run_action(a, ctx))
            except JumpSignal as jump:
                self._emit("jump", to=jump.to)
                return self._move(jump.to, node, start_idx)

            # 4. wait for a transition
            decision, state = await self._await_transition(node, ctx)

            # 5. postconditions
            failed = [pm for pm in node.check_postconditions(state, self.vars, self.requests.statuses) if not pm.ok]
            if failed:
                raise PostconditionFailed(
                    f"{len(failed)} postcondition(s) failed on {self.node!r}",
                    details=[r for pm in failed for r in pm.reasons],
                )

            return self._move(decision.to, node, start_idx)

        except EngineError as exc:
            route = node.spec.on_fail
            if route not in ("repair", "fail") and route in self.graph.nodes and self.node == node.name:
                return self._route_failure(exc, node, route, start_idx)
            return self._fail(exc, start_idx, force_failed=route == "fail")

    # -- helpers --

    async def _observe(self) -> PageState:
        self.last_state = await self.observer.observe()
        self._emit("observe", render=self.last_state.render(), signature=self.last_state.signature,
                   ms=self.last_state.stats.total_ms)
        return self.last_state

    async def _with_retry(self, node: CompiledNode, fn: Any) -> None:
        attempts = node.spec.retry.transient + 1
        for i in range(attempts):
            try:
                await fn()
                return
            except TransientError as exc:
                if i + 1 >= attempts:
                    raise
                delay = node.spec.retry.backoff_s[min(i, len(node.spec.retry.backoff_s) - 1)]
                self._emit("retry", attempt=i + 1, delay_s=delay, error=str(exc))
                await asyncio.sleep(delay)

    async def _await_transition(self, node: CompiledNode, ctx: ActionContext):
        deadline = time.perf_counter() + node.spec.timeout_s
        last_decision = None
        while True:
            state = await self._observe()
            ctx.state = state
            decision = node.next(state, self.vars, self.requests.statuses)
            last_decision = decision
            self._emit("transitions", to=decision.to, trace=decision.explain())
            if decision.to is not None:
                return decision, state
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                raise NoTransition(
                    f"actions completed on node {self.node!r}, but none of its outgoing transitions "
                    f"matched within {node.spec.timeout_s}s (page {state.route!r})",
                    details=last_decision.explain(),
                )
            await self.observer.wait_for_change(state.signature or "", min(remaining, 2.0))

    def _move(self, to: str, from_node: CompiledNode, start_idx: int) -> StepResult:
        self._emit("move", **{"from": from_node.name, "to": to})
        self.node = to
        self.status = "paused"
        return StepResult(from_node.name, to, self.status, self.last_state, self.events[start_idx:])

    def _finish(self, node: CompiledNode, start_idx: int) -> StepResult:
        spec: Node = node.spec
        outputs = {k: self.vars.get(k) for k in self.workflow.outputs}
        self.result = {"terminal": spec.terminal, **(spec.result or {}), "outputs": outputs}
        self.status = "done"
        self._emit("terminal", terminal=spec.terminal, result=self.result)
        return StepResult(node.name, None, self.status, self.last_state, self.events[start_idx:])

    def _failure_record(self, exc: EngineError) -> dict[str, Any]:
        return self._redact({
            "class": exc.klass, "type": type(exc).__name__, "message": str(exc), "details": exc.details,
            "code": getattr(exc, "code", None), "node": self.node,
            "route": self.last_state.route if self.last_state else None,
        })

    def _route_failure(self, exc: EngineError, node: CompiledNode, to: str, start_idx: int) -> StepResult:
        """`on_fail: <node>` — the failure becomes data the next node can branch on."""
        record = self._failure_record(exc)
        self.vars["_error"] = record
        self._emit("on_fail", to=to, error=record)
        return self._move(to, node, start_idx)

    def _fail(self, exc: EngineError, start_idx: int, *, force_failed: bool = False) -> StepResult:
        self.failure = self._failure_record(exc)
        self.status = "needs_repair" if exc.klass == "structural" and not force_failed else "failed"
        self._emit("failure", **self.failure)
        return StepResult(self.node, None, self.status, self.last_state, self.events[start_idx:])

    async def run(self, *, max_steps: int | None = None, on_step: Callable[[], bool] | None = None) -> StepResult:
        """Run steps; an optional synchronous boundary callback may return False to pause.

        The builder uses this to activate reviewed graph patches between steps.
        The callback never runs inside a node's actions or transition wait.
        """
        result = None
        for _ in range(max_steps or self._max_total_steps):
            result = await self.step()
            if on_step is not None and on_step() is False:
                break
            if result.status in ("done", "failed", "needs_repair"):
                break
            await asyncio.sleep(0)
        assert result is not None
        return result
