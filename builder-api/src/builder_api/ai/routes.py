"""Workspace-capability endpoints; all state is process memory only."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, SecretStr

from builder_api.ai.providers import ProviderError, list_models
from builder_api.ai.service import Harness
from builder_api.config import settings


class ProviderKey(BaseModel):
    provider: Literal["openai", "anthropic"]
    key: SecretStr = SecretStr("")


MODEL_ID = r"^[a-zA-Z0-9._:/-]+$"


class Connect(ProviderKey):
    # Optional: empty picks the default from the key's catalog; switch later with POST /ai/model.
    model: str = Field(default="", max_length=150, pattern=r"^[a-zA-Z0-9._:/-]*$")
    triage_model: str = Field(default="", max_length=150, pattern=r"^[a-zA-Z0-9._:/-]*$")


class ModelChoice(BaseModel):
    model: str = Field(min_length=1, max_length=150, pattern=MODEL_ID)


class Context(BaseModel):
    graph: str
    selected: str | None = None
    session_id: str | None = None
    values: dict[str, Any] = Field(default_factory=dict)
    private_values: list[str] = Field(default_factory=list)


class Turn(Context):
    message: str = Field(min_length=1, max_length=16000)


class Steer(BaseModel):
    message: str = Field(min_length=1, max_length=16000)


class Apply(BaseModel):
    session_id: str | None = None
    patch: bool = False
    inputs: dict[str, Any] = Field(default_factory=dict)


def router(harness: Harness) -> APIRouter:
    routes = APIRouter(prefix="/ai")

    def origin(request: Request) -> None:
        allowed = settings.ai_allowed_origins.split(",")
        if request.headers.get("origin") and request.headers["origin"] not in allowed:
            raise HTTPException(403, "AI requests must originate from the local builder.")
        if request.headers.get("sec-fetch-site") == "cross-site":
            raise HTTPException(403, "Cross-site AI requests are disabled.")

    def authenticated(request: Request):
        origin(request)
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        try:
            return token, harness.connection(token)
        except PermissionError as exc:
            raise HTTPException(401, str(exc)) from None

    @routes.post("/models")
    async def models(body: ProviderKey, request: Request):
        origin(request)
        try:
            key = harness.resolve_key(body.provider, body.key.get_secret_value())
            return {"models": await list_models(body.provider, key, transport=harness.transport)}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @routes.post("/connect")
    async def connect(body: Connect, request: Request):
        origin(request)
        try:
            token = await harness.connect(body.provider, body.key.get_secret_value(), body.model, body.triage_model)
        except (ValueError, ProviderError) as exc:
            raise HTTPException(422, str(exc)) from None
        conn = harness.connections[token]
        return {"token": token, "provider": body.provider, "model": conn.model, "models": conn.models,
                "triage_model": conn.triage_model, "expires_in": settings.ai_connection_ttl_s}

    @routes.post("/model")
    async def model(body: ModelChoice, request: Request):
        _, conn = authenticated(request)
        try:
            await harness.set_model(conn, body.model)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return {"model": conn.model}

    @routes.delete("/connection", status_code=204)
    async def disconnect(request: Request):
        token, _ = authenticated(request)
        harness.disconnect(token)

    @routes.post("/context")
    async def context(body: Context, request: Request):
        _, conn = authenticated(request)
        try:
            projection, context, _ = harness.context(body.graph, body.session_id, body.values,
                                             [conn.key, *body.private_values])
            from builder_api.ai.context import masked, select_context
            if body.graph in conn.last_context:
                return masked(conn.last_context[body.graph], projection)
            resources = harness.resources(projection, conn.history.get(body.graph, []))

            async def ignore(_event):
                pass

            prepared, _ = await select_context(resources, context, "", body.selected, provider=conn.provider,
                key=conn.key, model="off", transport=harness.transport, emit=ignore,
                context_chars=settings.ai_context_chars)
            return prepared
        except (ValueError, TypeError, KeyError):
            raise HTTPException(422, "Cannot build context. Check the graph and attached session.") from None

    @routes.post("/cancel", status_code=204)
    async def cancel(request: Request):
        _, conn = authenticated(request)
        if conn.task:
            conn.task.cancel()
            await asyncio.gather(conn.task, return_exceptions=True)

    @routes.post("/steer", status_code=202)
    async def steer(body: Steer, request: Request):
        _, conn = authenticated(request)
        try:
            harness.steer(conn, body.message)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from None
        return {"accepted": True}

    @routes.post("/clear", status_code=204)
    async def clear(request: Request):
        _, conn = authenticated(request)
        if conn.task and not conn.task.done():
            raise HTTPException(409, "Stop the current turn first.")
        conn.history.clear()
        conn.proposals.clear()
        conn.last_context.clear()

    @routes.delete("/proposals/{pid}", status_code=204)
    async def discard(pid: str, request: Request):
        _, conn = authenticated(request)
        conn.proposals.pop(pid, None)

    @routes.post("/proposals/{pid}/apply")
    async def apply(pid: str, body: Apply, request: Request):
        _, conn = authenticated(request)
        try:
            return await harness.apply(conn, pid, body.session_id, body.patch, body.inputs)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @routes.post("/turn")
    async def turn(body: Turn, request: Request):
        _, conn = authenticated(request)
        if conn.task and not conn.task.done():
            raise HTTPException(409, "An AI turn is already running.")
        queue: asyncio.Queue = asyncio.Queue()
        started = time.monotonic()

        async def work():
            try:
                async with asyncio.timeout(settings.ai_timeout_s or None):
                    await harness.turn(conn, body.graph, body.session_id, body.message,
                                       body.values, body.private_values, queue.put, body.selected)
            except asyncio.CancelledError:
                await queue.put({"kind": "cancelled", "text": "Stopped by you. Completed proposals are still available."})
            except ProviderError as exc:
                await queue.put({"kind": "error", "text": str(exc)})
            except TimeoutError:
                await queue.put({"kind": "error", "text": f"AI turn reached the configured {settings.ai_timeout_s}s deadline. "
                                 "Set BUILDER_AI_TIMEOUT_S=0 to disable it. Partial text and completed proposals are retained."})
            except Exception:  # noqa: BLE001 — exception bodies may include private input
                # Do not serialize exceptions: YAML validation may embed private input values.
                await queue.put({"kind": "error", "text": "AI operation failed. Check the graph, connection and session."})
            finally:
                conn.expires = time.monotonic() + settings.ai_connection_ttl_s
                await queue.put(None)

        task = asyncio.create_task(work())
        conn.task = task

        async def events():
            sequence = 0
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), 10)
                    except TimeoutError:
                        event = {"kind": "heartbeat"}
                    if event is None:
                        break
                    sequence += 1
                    yield "data: " + json.dumps({**event, "sequence": sequence,
                                                "elapsed_ms": round((time.monotonic() - started) * 1000)},
                                               ensure_ascii=False) + "\n\n"
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                if conn.task is task:
                    conn.task = None

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    return routes
