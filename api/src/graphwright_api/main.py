"""Runs API.

    GET  /health                 liveness + db/redis checks (no key)
    GET  /graphs                 the catalog: inputs, requests, lint, version
    GET  /graphs/{name}
    POST /runs                   queue a run (202; 200 on an idempotent replay)
    GET  /runs                   newest first; ?graph= &status= &limit= &before=<run id>
    GET  /runs/{id}              the run + its live progress snapshot
    GET  /runs/{id}/events       the durable decision trail; ?after=<event id> &limit=
    GET  /runs/{id}/stream       Server-Sent Events until the run is final
    POST /runs/{id}/cancel       queued/running → cancelled; final → 409

Every route but /health needs ``X-API-Key``. Behind nginx the ``/api`` prefix is
stripped (uvicorn runs with ``--root-path /api``).
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from graphwright import catalog, plugins
from graphwright.contract import FINAL_STATUSES, RUN_STATUSES

from graphwright_api.auth import require_key
from graphwright_api.config import Settings
from graphwright_api.config import settings as default_settings
from graphwright_api.progress import Progress, RedisProgress
from graphwright_api.queue import CeleryEnqueuer, Enqueuer
from graphwright_api.schemas import CreateRun, GraphSummary, Run, RunDetail, RunEvent, RunEvents, RunList
from graphwright_api.store import PgRunStore, RunStore

log = logging.getLogger(__name__)

HEARTBEAT_S = 15.0


def _error(status: int, code: str, message: str, **extra: Any) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


def create_app(
    *,
    settings: Settings | None = None,
    store: RunStore | None = None,
    enqueuer: Enqueuer | None = None,
    progress: Progress | None = None,
    heartbeat_s: float = HEARTBEAT_S,
) -> FastAPI:
    cfg = settings or default_settings
    keys = cfg.keys()  # validates API_KEYS at startup (refuses the dev secret outside dev)
    graphs_dir = Path(cfg.graphs_dir) if cfg.graphs_dir else None
    plugins.load(cfg.plugins)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned: list[Any] = []
        if app.state.store is None:
            app.state.store = await PgRunStore.connect(cfg.postgres_dsn)
            owned.append(app.state.store)
        if app.state.progress is None:
            app.state.progress = RedisProgress.from_url(cfg.redis_url)
            owned.append(app.state.progress)
        if app.state.enqueuer is None:
            app.state.enqueuer = CeleryEnqueuer(cfg.redis_url)
        try:
            yield
        finally:
            for res in owned:
                await res.close()

    app = FastAPI(title="graphwright runs API", version="0.1.0", lifespan=lifespan)
    app.state.keys = keys
    app.state.store = store
    app.state.enqueuer = enqueuer
    app.state.progress = progress

    def deps(request: Request) -> tuple[RunStore, Enqueuer, Progress]:
        s = request.app.state
        return s.store, s.enqueuer, s.progress

    # --- health ----------------------------------------------------------------------

    @app.get("/health")
    async def health(request: Request) -> dict[str, Any]:
        store_, _, progress_ = deps(request)
        checks: dict[str, bool] = {}
        for name, probe in (("db", store_.ping), ("redis", progress_.ping)):
            try:
                checks[name] = bool(await probe())
            except Exception:  # noqa: BLE001 — a failed probe is the answer
                checks[name] = False
        return {"status": "ok" if all(checks.values()) else "degraded", **checks}

    # --- graphs ----------------------------------------------------------------------

    @app.get("/graphs", response_model=list[GraphSummary], dependencies=[Depends(require_key)])
    async def list_graphs() -> list[GraphSummary]:
        return [GraphSummary(**asdict(g), ok=g.ok) for g in catalog.list_graphs(graphs_dir)]

    @app.get("/graphs/{name}", response_model=GraphSummary, dependencies=[Depends(require_key)])
    async def get_graph(name: str) -> GraphSummary:
        try:
            g = catalog.get_graph(name, graphs_dir)
        except catalog.GraphNotFound as exc:
            raise _error(404, "graph_not_found", str(exc)) from exc
        return GraphSummary(**asdict(g), ok=g.ok)

    # --- runs ------------------------------------------------------------------------

    @app.post("/runs", response_model=Run, status_code=202)
    async def create_run(body: CreateRun, request: Request, key_id: str = Depends(require_key)) -> Any:
        store_, enqueuer_, _ = deps(request)
        try:
            graph = catalog.load_graph(body.graph, graphs_dir)
        except catalog.GraphNotFound as exc:
            raise _error(404, "graph_not_found", str(exc)) from exc
        except catalog.GraphInvalid as exc:
            raise _error(422, "graph_invalid", f"graph {body.graph!r} does not lint", errors=exc.errors) from exc
        errors = catalog.validate_inputs(graph.workflow, body.inputs, has_credential=body.credential_ref is not None)
        if errors:
            raise _error(422, "inputs_invalid", "; ".join(errors), errors=errors)

        row, created = await store_.create_run(
            graph=body.graph, graph_version=graph.workflow.version, inputs=body.inputs,
            credential_ref=body.credential_ref, requested_by=key_id, idempotency_key=body.idempotency_key,
        )
        if not created:
            return JSONResponse(status_code=200, content=Run.from_row(row).model_dump(mode="json"))
        try:
            await enqueuer_.enqueue(str(row["id"]))
        except Exception as exc:  # noqa: BLE001 — broker down, misconfigured…
            log.exception("enqueue failed for run %s", row["id"])
            await store_.mark_enqueue_failed(row["id"], f"{type(exc).__name__}: could not reach the queue")
            raise _error(503, "enqueue_failed", "the run was recorded but could not be queued", run_id=str(row["id"])) from exc
        return Run.from_row(row)

    @app.get("/runs", response_model=RunList, dependencies=[Depends(require_key)])
    async def list_runs(
        request: Request,
        graph: str | None = None,
        status: str | None = None,
        limit: int = Query(default=50, ge=1, le=200),
        before: UUID | None = None,
    ) -> RunList:
        if status is not None and status not in RUN_STATUSES:
            raise _error(422, "invalid_status", f"status must be one of {list(RUN_STATUSES)}")
        store_, _, _ = deps(request)
        rows = await store_.list_runs(graph=graph, status=status, limit=limit, before=before)
        return RunList(runs=[Run.from_row(r) for r in rows], next=rows[-1]["id"] if len(rows) == limit else None)

    async def _run_or_404(store_: RunStore, run_id: UUID) -> dict[str, Any]:
        row = await store_.get_run(run_id)
        if row is None:
            raise _error(404, "run_not_found", f"no run {run_id}")
        return row

    @app.get("/runs/{run_id}", response_model=RunDetail, dependencies=[Depends(require_key)])
    async def get_run(run_id: UUID, request: Request) -> RunDetail:
        store_, _, progress_ = deps(request)
        row = await _run_or_404(store_, run_id)
        return RunDetail.from_row(row, await progress_.get(str(run_id)))

    @app.get("/runs/{run_id}/events", response_model=RunEvents, dependencies=[Depends(require_key)])
    async def run_events(
        run_id: UUID, request: Request,
        after: int = Query(default=0, ge=0), limit: int = Query(default=500, ge=1, le=2000),
    ) -> RunEvents:
        store_, _, _ = deps(request)
        await _run_or_404(store_, run_id)
        rows = await store_.list_events(run_id, after=after, limit=limit)
        return RunEvents(events=[RunEvent(**r) for r in rows], next=rows[-1]["id"] if len(rows) == limit else None)

    @app.get("/runs/{run_id}/stream", dependencies=[Depends(require_key)])
    async def stream(run_id: UUID, request: Request) -> StreamingResponse:
        store_, _, progress_ = deps(request)
        row = await _run_or_404(store_, run_id)

        def sse(event: str, data: str) -> bytes:
            return f"event: {event}\ndata: {data}\n\n".encode()

        async def detail() -> RunDetail:
            current = await store_.get_run(run_id) or row
            return RunDetail.from_row(current, await progress_.get(str(run_id)))

        async def events() -> AsyncIterator[bytes]:
            snapshot = await detail()
            yield sse("run", snapshot.model_dump_json())
            if snapshot.status in FINAL_STATUSES:
                yield sse("end", snapshot.model_dump_json())
                return
            async for payload in progress_.subscribe(str(run_id), poll_s=heartbeat_s):
                if payload is None:
                    # Heartbeat tick: keep proxies from closing the stream, and catch
                    # a final status whose publish we missed (cancel, dead worker).
                    yield b": ping\n\n"
                    current = await detail()
                    if current.status in FINAL_STATUSES:
                        yield sse("end", current.model_dump_json())
                        return
                    continue
                yield sse("progress", _json(payload))
                if payload.get("status") in FINAL_STATUSES:
                    yield sse("end", (await detail()).model_dump_json())
                    return

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/runs/{run_id}/cancel", response_model=Run, dependencies=[Depends(require_key)])
    async def cancel_run(run_id: UUID, request: Request) -> Run:
        store_, enqueuer_, _ = deps(request)
        row, before = await store_.cancel_run(run_id)
        if row is None:
            raise _error(404, "run_not_found", f"no run {run_id}")
        if before in FINAL_STATUSES:
            raise _error(409, "run_final", f"run is already {before}", run_status=before)
        if before == "running":
            try:
                await enqueuer_.revoke(str(run_id))
            except Exception:  # noqa: BLE001 — the worker's between-step check still stops it
                log.warning("revoke failed for run %s; the worker will stop at its next step", run_id)
        return Run.from_row(row)

    return app


def _json(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, default=str)


def main() -> None:
    import uvicorn

    ap = argparse.ArgumentParser(prog="graphwright-api")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--root-path", default="", help="prefix stripped by the proxy, e.g. /api")
    ap.add_argument("--reload", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    uvicorn.run("graphwright_api.main:create_app", factory=True, host=args.host, port=args.port,
                root_path=args.root_path, reload=args.reload, proxy_headers=True)


if __name__ == "__main__":
    main()
