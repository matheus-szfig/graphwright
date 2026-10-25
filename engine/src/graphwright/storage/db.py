"""Postgres connection pool + the worker's writes. Schema: ``api/alembic``.

Thin wrapper — no ORM, no business logic; each helper is one statement. The
pool is owned by ``graphwright.worker.hooks`` (one per worker process) and used
through the worker's long-lived event loop.

The worker connects as ``graphwright_worker``: it reads ``run`` and updates the
outcome columns, appends ``run_event`` and ``artifact`` rows. It cannot create
runs or touch anything else. UUIDs come from Postgres ``uuidv7()``.

Idempotency (``task_acks_late`` redelivers a message whose worker died):
``claim_run`` only moves a run to ``running`` from ``queued`` — or from
``running``, which is what a redelivery after a worker crash looks like — so a
run that already finished, or was cancelled, is never executed again. Known
limit: a duplicate delivery while the first worker is still alive would run it
twice (prefetch 1 + acks-late make that rare); a heartbeat column would close it.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg

from graphwright.config import settings

log = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


# --- pool lifecycle (graphwright.worker.hooks) ---------------------------------------


async def init_pool(min_size: int = 1, max_size: int = 4) -> asyncpg.Pool:
    global _pool
    if _pool is not None:
        return _pool
    _pool = await asyncpg.create_pool(
        dsn=settings.postgres_dsn, min_size=min_size, max_size=max_size,
        command_timeout=30, init=_register_codecs,
    )
    log.info("asyncpg pool created min=%d max=%d", min_size, max_size)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("storage.db pool not initialized — call init_pool() from worker_process_init")
    return _pool


async def _register_codecs(conn: asyncpg.Connection) -> None:
    # asyncpg returns jsonb as str by default; install a transparent json codec.
    for typ in ("jsonb", "json"):
        await conn.set_type_codec(typ, encoder=json.dumps, decoder=json.loads, schema="pg_catalog", format="text")


# --- runs ------------------------------------------------------------------------------


@dataclass(frozen=True)
class ClaimedRun:
    id: UUID
    graph: str
    inputs: dict[str, Any]
    credential_ref: str | None
    attempt: int


async def claim_run(run_id: UUID, *, celery_task_id: str, browser_profile: str | None) -> ClaimedRun | None:
    """Mark the run ``running`` and return what the worker needs; ``None`` when
    there is nothing to do (finished, cancelled, unknown)."""
    sql = """
        UPDATE run
        SET status = 'running',
            started_at = COALESCE(started_at, now()),
            attempt = attempt + 1,
            celery_task_id = $2,
            worker_hostname = $3,
            worker_pid = $4,
            browser_profile = $5
        WHERE id = $1 AND status IN ('queued', 'running')
        RETURNING id, graph, inputs, credential_ref, attempt;
    """
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(sql, run_id, celery_task_id, socket.gethostname(), os.getpid(), browser_profile)
    if row is None:
        return None
    return ClaimedRun(id=row["id"], graph=row["graph"], inputs=row["inputs"] or {},
                      credential_ref=row["credential_ref"], attempt=row["attempt"])


async def set_run_graph_version(run_id: UUID, version: int) -> None:
    async with get_pool().acquire() as conn:
        await conn.execute("UPDATE run SET graph_version = $2 WHERE id = $1;", run_id, version)


async def update_run_progress(run_id: UUID, *, steps_completed: int, node: str | None) -> None:
    async with get_pool().acquire() as conn:
        await conn.execute(
            "UPDATE run SET steps_completed = $2, current_node = $3 WHERE id = $1 AND status = 'running';",
            run_id, steps_completed, node,
        )


async def mark_run_completed(run_id: UUID, *, result: dict[str, Any], steps_completed: int) -> None:
    sql = """
        UPDATE run
        SET status = 'completed', finished_at = now(), result = $2, terminal = $3,
            steps_completed = $4, current_node = NULL
        WHERE id = $1 AND status = 'running';
    """
    async with get_pool().acquire() as conn:
        await conn.execute(sql, run_id, result, result.get("terminal"), steps_completed)


async def mark_run_failed(
    run_id: UUID,
    *,
    exc: BaseException,
    steps_completed: int = 0,
    scrub: Callable[[str], str] | None = None,
    error_type: str | None = None,
    failure: dict[str, Any] | None = None,
) -> None:
    """``scrub`` (a Redactor's ``text``) runs over the message and traceback before
    they are stored — exception text can quote page content. ``failure`` is the
    engine's (already redacted) failure record, kept as structured evidence."""
    sql = """
        UPDATE run
        SET status = 'failed', finished_at = now(), steps_completed = $2,
            error_type = $3, error_message = $4, error_traceback = $5, failure = $6
        WHERE id = $1 AND status = 'running';
    """
    message = str(exc)
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    if scrub is not None:
        message, tb = scrub(message), scrub(tb)
    async with get_pool().acquire() as conn:
        await conn.execute(sql, run_id, steps_completed, error_type or type(exc).__name__, message, tb, failure)


async def run_status(run_id: UUID) -> str | None:
    async with get_pool().acquire() as conn:
        return await conn.fetchval("SELECT status FROM run WHERE id = $1;", run_id)


# --- events & artifacts ------------------------------------------------------------------


async def insert_run_events(run_id: UUID, events: list[dict[str, Any]]) -> None:
    """Durable copy of the run's decision trail (moves, requests, failures…).
    Events arrive redacted from the runtime."""
    if not events:
        return
    rows = [
        (run_id, e.get("seq"), e["kind"], e.get("node"), {k: v for k, v in e.items() if k not in ("seq", "kind", "node")})
        for e in events
    ]
    async with get_pool().acquire() as conn:
        await conn.executemany(
            "INSERT INTO run_event (run_id, seq, kind, node, data) VALUES ($1, $2, $3, $4, $5);", rows
        )


async def insert_artifact(
    *,
    run_id: UUID,
    kind: str,
    bucket: str,
    object_key: str,
    content_type: str | None = None,
    size_bytes: int | None = None,
    node: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> UUID:
    sql = """
        INSERT INTO artifact (run_id, kind, bucket, object_key, content_type, size_bytes, node, metadata)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        RETURNING id;
    """
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(sql, run_id, kind, bucket, object_key, content_type, size_bytes, node, metadata or {})
    return row["id"]
