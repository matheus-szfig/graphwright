"""Builder API — FastAPI app. Served behind nginx at /builder/api (prefix stripped), or
directly on :8765 by `builder-api` on the host when you want a headed browser."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from graphwright import plugins
from graphwright.engine import GraphError, Workflow
from graphwright.engine.schema import dump_yaml
from pydantic import BaseModel

from builder_api import graphs as graphs_mod
from builder_api.graphs import (
    GraphStore,
    GraphSummary,
    GraphView,
    StoreError,
    node_to_yaml,
)
from builder_api.sessions import SessionManager


def yaml_dump(data: Any) -> str:
    return dump_yaml(data)  # the engine's writer: indented sequences, insertion order

# Request bodies. Module level on purpose: with `from __future__ import annotations`
# FastAPI cannot resolve classes defined inside create_app() and would read them as
# query parameters.


class CreateGraph(BaseModel):
    name: str
    portal: str
    entry_url: str | None = None


class YamlBody(BaseModel):
    yaml: str


class AddNode(BaseModel):
    name: str
    kind: str = "step"
    intent: str = ""


class Rename(BaseModel):
    new_name: str


class AddEdge(BaseModel):
    source: str
    target: str
    condition: dict[str, Any] | None = None


class InputBody(BaseModel):
    type: str = "string"
    description: str = ""
    required: bool = True
    pii: bool = True
    default: Any = None
    credential: str | None = None


class RequestTestBody(BaseModel):
    inputs: dict[str, Any] = {}
    vars: dict[str, Any] = {}
    credential: str | None = None
    reveal: bool = False


class SessionRequestBody(BaseModel):
    vars: dict[str, Any] = {}
    reveal: bool = False


class ScriptTestBody(BaseModel):
    """Either `code` (+ args/into/limits) or `node` + `index` (the Nth script action
    of that node, as saved)."""

    code: str | None = None
    node: str | None = None
    index: int = 0
    args: dict[str, Any] = {}
    inputs: dict[str, Any] = {}
    vars: dict[str, Any] = {}
    credential: str | None = None
    into: str | None = None
    timeout_ms: int = 1000
    memory_mb: int = 64
    allow_secrets: bool = False


class ConsoleBody(BaseModel):
    script: str
    args: dict[str, Any] = {}
    world: str = "isolated"
    frame: int | str = 0
    timeout_ms: int = 5000


class TargetFromElement(BaseModel):
    element: dict[str, Any]
    name: str | None = None


class RecognizeFromSession(BaseModel):
    session_id: str
    max_elements: int = 3


class CreateSession(BaseModel):
    graph: str
    inputs: dict[str, Any] = {}
    fast: bool = False
    credential: str | None = None


class ResetBody(BaseModel):
    node: str


class EvalBody(BaseModel):
    condition: dict[str, Any]


def _default_graphs_dir() -> Path:
    env = os.environ.get("BUILDER_GRAPHS_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "engine" / "graphs"


def create_app(*, graphs_dir: Path | None = None, headed: bool | None = None,
               request_transport: Any = None) -> FastAPI:
    """``request_transport``: an httpx transport for graph requests (tests pass a
    ``MockTransport``); None = the real network."""
    graphs_dir = graphs_dir or _default_graphs_dir()
    if headed is None:
        headed = os.environ.get("BUILDER_HEADED", "true").lower() not in ("0", "false", "no")
    plugins.load()  # same shapers/aliases the worker has (PLUGINS)

    store = GraphStore(graphs_dir)
    manager = SessionManager(headed=headed, graphs_dir=graphs_dir, request_transport=request_transport)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        await manager.shutdown()

    app = FastAPI(title="graphwright builder api", version="0.1.0", lifespan=lifespan)
    app.state.store = store
    app.state.manager = manager

    app.add_middleware(
        CORSMiddleware,
        allow_origins=os.environ.get("BUILDER_CORS_ORIGINS", "http://localhost:5174,http://localhost:8080").split(","),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(StoreError)
    async def store_error(_: Request, exc: StoreError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content={"detail": str(exc), "errors": exc.errors})

    @app.exception_handler(GraphError)
    async def graph_error(_: Request, exc: GraphError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": "graph does not lint", "errors": exc.errors})

    @app.exception_handler(KeyError)
    async def key_error(_: Request, exc: KeyError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc.args[0]) if exc.args else "not found"})

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "graphs_dir": str(graphs_dir), "headed": headed, "sessions": len(manager.sessions)}

    @app.get("/schema")
    async def schema() -> dict[str, Any]:
        """JSON Schema of the graph language plus the engine's vocabularies — the
        editor derives key/enum completions from this, so they never drift."""
        from typing import get_args

        from graphwright.engine.schema import (
            ACTION_VERBS,
            CONDITION_KEYS,
            Evaluate,
            Extractor,
            Node,
            RequestSpec,
            Script,
            Strategy,
            Workflow,
        )
        from graphwright.engine.shapers import SHAPERS

        def aliases(model: type[BaseModel]) -> list[str]:
            return [f.alias or k for k, f in model.model_fields.items()]

        return {
            "workflow": Workflow.model_json_schema(by_alias=True),
            "node": Node.model_json_schema(by_alias=True),
            "request": RequestSpec.model_json_schema(by_alias=True),
            "shapers": sorted(SHAPERS),
            "verbs": [v.rstrip("_") for v in ACTION_VERBS],
            "node_kinds": list(graphs_mod.NODE_KINDS),
            "script_fields": list(Script.model_fields),
            "evaluate_fields": list(Evaluate.model_fields),
            "condition_kinds": [k.rstrip("_") for k in CONDITION_KEYS],
            "request_kinds": list(get_args(RequestSpec.model_fields["kind"].annotation)),
            "request_fields": aliases(RequestSpec),
            "extractor_fields": aliases(Extractor),
            "strategy_fields": list(Strategy.model_fields),
            "roles": ["button", "link", "textbox", "combobox", "checkbox", "radio", "tab", "menuitem",
                      "option", "switch", "table", "heading", "img", "clickable", "fileinput", "slider", "generic"],
        }

    # --- graphs --------------------------------------------------------------------

    @app.get("/graphs", response_model=list[GraphSummary])
    async def list_graphs() -> list[GraphSummary]:
        return store.list()

    @app.post("/graphs", response_model=GraphView, status_code=201)
    async def create_graph(body: CreateGraph) -> GraphView:
        return store.create(body.name, body.portal, body.entry_url)

    @app.get("/graphs/{name}", response_model=GraphView)
    async def get_graph(name: str) -> GraphView:
        return store.view(name)

    @app.put("/graphs/{name}", response_model=GraphView)
    async def put_graph(name: str, body: YamlBody) -> GraphView:
        return store.put_yaml(name, body.yaml)

    @app.delete("/graphs/{name}", status_code=204)
    async def delete_graph(name: str) -> None:
        store.delete(name)
        await manager.close_graph(name)

    @app.post("/graphs/{name}/lint")
    async def lint_graph(name: str, body: YamlBody) -> dict[str, Any]:
        errors = store.lint_yaml(body.yaml)
        return {"ok": not errors, "errors": errors}

    @app.get("/graphs/{name}/completions")
    async def completions(name: str) -> dict[str, Any]:
        return store.completions(name)

    @app.post("/graphs/{name}/nodes/{node}/lint")
    async def lint_node(name: str, node: str, body: YamlBody) -> dict[str, Any]:
        errors = store.lint_node_yaml(name, node, body.yaml)
        return {"ok": not errors, "errors": errors}

    @app.put("/graphs/{name}/inputs/{input_name}", response_model=GraphView)
    async def put_input(name: str, input_name: str, body: InputBody) -> GraphView:
        from graphwright.engine.schema import InputSpec

        return store.put_input(name, input_name, InputSpec(**body.model_dump()))

    @app.delete("/graphs/{name}/inputs/{input_name}", response_model=GraphView)
    async def delete_input(name: str, input_name: str) -> GraphView:
        return store.delete_input(name, input_name)

    @app.post("/graphs/{name}/requests/{request}/test")
    async def test_request(name: str, request: str, body: RequestTestBody) -> dict[str, Any]:
        """Run one graph request with the given inputs/vars, outside any browser
        session, with its real retry policy. Every attempt comes back, masked unless
        ``reveal`` (local debugging only)."""
        from graphwright.engine.requests import RequestServices
        from graphwright.testing import run_request

        from builder_api.sessions import redeem

        wf = store.load(name)
        if request not in wf.requests:
            raise HTTPException(404, f"request {request!r} not found in {name!r}")
        try:
            credential = redeem(body.credential)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return await run_request(
            wf, request, body.inputs, variables=body.vars, credential=credential,
            services=RequestServices(transport=request_transport), reveal=body.reveal,
        )

    @app.post("/graphs/{name}/scripts/test")
    async def test_script(name: str, body: ScriptTestBody) -> dict[str, Any]:
        """Run a code-node body in the sandbox (no browser) with the given
        inputs/vars — `graphwright.testing.run_script`'s report."""
        from graphwright.testing import run_script

        from builder_api.sessions import redeem

        wf = store.load(name)
        code, args, into = body.code, body.args, body.into
        timeout_ms, memory_mb, allow_secrets = body.timeout_ms, body.memory_mb, body.allow_secrets
        if code is None:
            if body.node is None:
                raise HTTPException(422, "give `code`, or `node` (+ `index`)")
            node = wf.nodes.get(body.node)
            if node is None:
                raise HTTPException(404, f"node {body.node!r} not found")
            scripts = [a.script for a in node.actions if a.script]
            if body.index >= len(scripts):
                raise HTTPException(404, f"node {body.node!r} has {len(scripts)} script action(s)")
            spec = scripts[body.index]
            code, into = spec.code, spec.into
            args = spec.args if not body.args else body.args
            timeout_ms, memory_mb, allow_secrets = spec.timeout_ms, spec.memory_mb, spec.allow_secrets
        try:
            credential = redeem(body.credential)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        # Like a builder session: what the author types for a password/credential
        # input is a Secret, so `allow_secrets` is enforced the way a run enforces it.
        from graphwright.engine.secrets import Secret

        inputs = {
            k: Secret(str(v)) if k in wf.inputs and (wf.inputs[k].credential or wf.inputs[k].type == "password") else v
            for k, v in body.inputs.items()
        }
        return await run_script(wf, code, args=args, inputs=inputs, variables=body.vars, credential=credential,
                                into=into, timeout_ms=timeout_ms, memory_mb=memory_mb, allow_secrets=allow_secrets)

    @app.post("/graphs/{name}/targets/from-element", status_code=201)
    async def target_from_element_ep(name: str, body: TargetFromElement) -> dict[str, Any]:
        target_name, target = graphs_mod.target_from_element(body.element, body.name)
        view = store.add_target(name, target_name, target)
        return {"target": target_name, "yaml": yaml_dump({target_name: target.model_dump(mode="json", exclude_none=True, exclude_defaults=True)}),
                "view": view.model_dump(mode="json")}

    @app.post("/graphs/{name}/nodes/{node}/recognize-from-session", response_model=GraphView)
    async def recognize_from_session(name: str, node: str, body: RecognizeFromSession) -> GraphView:
        last = manager.last_state(body.session_id)
        if last is None:
            raise HTTPException(409, "session has no observation yet")
        condition = graphs_mod.recognize_from_state(last["state"], max_elements=body.max_elements)
        return store.set_recognize(name, node, condition)

    @app.get("/graphs/{name}/nodes/{node}")
    async def get_node(name: str, node: str) -> dict[str, Any]:
        wf = store.load(name)
        if node not in wf.nodes:
            raise HTTPException(404, f"node {node!r} not found")
        return {"name": node, "yaml": node_to_yaml(wf.nodes[node]),
                "node": wf.nodes[node].model_dump(mode="json", by_alias=True, exclude_none=True)}

    @app.put("/graphs/{name}/nodes/{node}", response_model=GraphView)
    async def put_node(name: str, node: str, body: YamlBody) -> GraphView:
        return store.put_node(name, node, body.yaml)

    @app.post("/graphs/{name}/nodes", response_model=GraphView, status_code=201)
    async def add_node(name: str, body: AddNode) -> GraphView:
        return store.add_node(name, body.name, body.kind, body.intent)

    @app.post("/graphs/{name}/nodes/{node}/rename", response_model=GraphView)
    async def rename_node(name: str, node: str, body: Rename) -> GraphView:
        return store.rename_node(name, node, body.new_name)

    @app.delete("/graphs/{name}/nodes/{node}", response_model=GraphView)
    async def delete_node(name: str, node: str) -> GraphView:
        return store.delete_node(name, node)

    @app.post("/graphs/{name}/start/{node}", response_model=GraphView)
    async def set_start(name: str, node: str) -> GraphView:
        return store.set_start(name, node)

    @app.post("/graphs/{name}/edges", response_model=GraphView, status_code=201)
    async def add_edge(name: str, body: AddEdge) -> GraphView:
        return store.add_edge(name, body.source, body.target, body.condition)

    @app.delete("/graphs/{name}/edges/{source}/{target}", response_model=GraphView)
    async def delete_edge(name: str, source: str, target: str, index: int | None = None) -> GraphView:
        return store.delete_edge(name, source, target, index)

    # --- sessions ------------------------------------------------------------------

    @app.get("/sessions")
    async def list_sessions() -> list[dict[str, Any]]:
        return [s.summary() for s in manager.sessions.values()]

    @app.post("/sessions", status_code=201)
    async def create_session(body: CreateSession) -> dict[str, Any]:
        try:
            live = await manager.create(body.graph, body.inputs, fast=body.fast, credential=body.credential)
        except FileNotFoundError:
            raise HTTPException(404, f"graph {body.graph!r} not found") from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return live.summary()

    @app.get("/sessions/{sid}")
    async def get_session(sid: str) -> dict[str, Any]:
        return manager.get(sid).summary()

    @app.delete("/sessions/{sid}", status_code=204)
    async def close_session(sid: str) -> None:
        await manager.close(sid)

    @app.post("/sessions/{sid}/step")
    async def step(sid: str) -> dict[str, Any]:
        return await _busy_guard(manager.step(sid))

    @app.post("/sessions/{sid}/run")
    async def run(sid: str, max_steps: int | None = None) -> dict[str, Any]:
        return await _busy_guard(manager.run(sid, max_steps))

    @app.post("/sessions/{sid}/reset")
    async def reset(sid: str, body: ResetBody) -> dict[str, Any]:
        return await manager.reset(sid, body.node)

    @app.post("/sessions/{sid}/patch")
    async def patch(sid: str, body: YamlBody | None = None) -> dict[str, Any]:
        wf = Workflow.from_yaml_text(body.yaml) if body and body.yaml else None
        return await manager.patch(sid, wf)

    @app.post("/sessions/{sid}/observe")
    async def observe(sid: str) -> dict[str, Any]:
        return await _busy_guard(manager.observe(sid))

    @app.get("/sessions/{sid}/state")
    async def state(sid: str) -> dict[str, Any] | None:
        return manager.last_state(sid)

    @app.get("/sessions/{sid}/targets")
    async def targets(sid: str) -> dict[str, Any]:
        return manager.targets(sid)

    @app.get("/sessions/{sid}/recognize")
    async def recognize(sid: str) -> dict[str, Any]:
        return manager.recognize_all(sid)

    @app.post("/sessions/{sid}/eval")
    async def eval_condition(sid: str, body: EvalBody) -> dict[str, Any]:
        try:
            return manager.evaluate(sid, body.condition)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.post("/sessions/{sid}/requests/{request}")
    async def session_request(sid: str, request: str, body: SessionRequestBody) -> dict[str, Any]:
        """Run a graph request inside a live session (its inputs, vars, credential)."""
        return await _busy_guard(manager.run_request(sid, request, variables=body.vars, reveal=body.reveal))

    @app.post("/sessions/{sid}/evaluate")
    async def session_evaluate(sid: str, body: ConsoleBody) -> dict[str, Any]:
        """The builder's browser console: ad-hoc JS in the session's page."""
        try:
            return await _busy_guard(manager.console(sid, body.script, args=body.args, world=body.world,
                                                     frame=body.frame, timeout_ms=body.timeout_ms))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.get("/sessions/{sid}/screenshot")
    async def screenshot(sid: str) -> dict[str, str]:
        return {"jpeg_base64": await _busy_guard(manager.screenshot(sid))}

    @app.get("/sessions/{sid}/events")
    async def events(sid: str, since: int = 0) -> list[dict[str, Any]]:
        return manager.events(sid, since)

    @app.websocket("/sessions/{sid}/ws")
    async def events_ws(ws: WebSocket, sid: str) -> None:
        await ws.accept()
        try:
            q = manager.subscribe(sid)
        except KeyError:
            await ws.close(code=4404)
            return
        try:
            for ev in manager.events(sid):  # replay history, then live
                await ws.send_json(ev)
            while True:
                ev = await q.get()
                await ws.send_json(ev)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            manager.unsubscribe(sid, q)

    async def _busy_guard(coro: Any) -> Any:
        try:
            return await coro
        except RuntimeError as exc:
            if "busy" in str(exc):
                raise HTTPException(409, "session is busy") from None
            raise

    return app


app = create_app()


def main() -> None:
    """`uv run builder-api [--headed|--headless] [--port 8765]`"""
    import argparse

    import uvicorn

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("BUILDER_PORT", "8765")))
    ap.add_argument("--host", default=os.environ.get("BUILDER_HOST", "127.0.0.1"))
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--root-path", default=os.environ.get("BUILDER_ROOT_PATH", ""))
    args = ap.parse_args()
    # Build the app here, after the flag is known — the module-level `app` (for
    # `uvicorn builder_api.main:app`) reads BUILDER_HEADED from the environment instead.
    uvicorn.run(create_app(headed=not args.headless), host=args.host, port=args.port,
                root_path=args.root_path, loop="asyncio", log_level="info")


if __name__ == "__main__":
    main()
