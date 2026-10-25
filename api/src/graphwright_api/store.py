"""Run persistence — the ``RunStore`` the routes use, backed by Postgres (asyncpg).

Thin, one statement per method, no ORM. ``MemoryRunStore`` implements the same
interface for unit tests; the DB tests run ``PgRunStore`` against a ``*_test``
database.

Rows are plain dicts with the ``run`` table's column names.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

import asyncpg

RUN_COLUMNS = (
    "id, graph, graph_version, status, inputs, credential_ref, requested_by, idempotency_key, attempt, "
    "celery_task_id, worker_hostname, worker_pid, browser_profile, current_node, steps_completed, terminal, "
    "result, failure, error_type, error_message, error_traceback, created_at, started_at, finished_at, "
    "cancel_requested_at"
)


class RunStore(Protocol):
    async def create_run(
        self, *, graph: str, graph_version: int | None, inputs: dict[str, Any], credential_ref: str | None,
        requested_by: str | None, idempotency_key: str | None,
    ) -> tuple[dict[str, Any], bool]:
        """Insert a queued run. With an idempotency key already used by the same
        caller, return that run instead and ``created=False``."""
        ...

    async def get_run(self, run_id: uuid.UUID) -> dict[str, Any] | None: ...

    async def list_runs(
        self, *, graph: str | None, status: str | None, limit: int, before: uuid.UUID | None
    ) -> list[dict[str, Any]]: ...

    async def list_events(self, run_id: uuid.UUID, *, after: int, limit: int) -> list[dict[str, Any]]: ...

    async def mark_enqueue_failed(self, run_id: uuid.UUID, message: str) -> None: ...

    async def cancel_run(self, run_id: uuid.UUID) -> tuple[dict[str, Any] | None, str | None]:
        """Cancel a queued or running run. Returns (row after, status before);
        (row, final status) when it had already finished; (None, None) if unknown."""
        ...

    async def ping(self) -> bool: ...


async def _register_codecs(conn: asyncpg.Connection) -> None:
    for typ in ("jsonb", "json"):
        await conn.set_type_codec(typ, encoder=json.dumps, decoder=json.loads, schema="pg_catalog", format="text")


class PgRunStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    @classmethod
    async def connect(cls, dsn: str, *, min_size: int = 1, max_size: int = 8) -> PgRunStore:
        pool = await asyncpg.create_pool(dsn=dsn, min_size=min_size, max_size=max_size, command_timeout=15,
                                         init=_register_codecs)
        return cls(pool)

    async def close(self) -> None:
        await self.pool.close()

    async def create_run(self, *, graph, graph_version, inputs, credential_ref, requested_by, idempotency_key):
        insert = f"""
            INSERT INTO run (graph, graph_version, inputs, credential_ref, requested_by, idempotency_key)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (requested_by, idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING
            RETURNING {RUN_COLUMNS};
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(insert, graph, graph_version, inputs, credential_ref, requested_by, idempotency_key)
            if row is not None:
                return dict(row), True
            existing = await conn.fetchrow(
                f"SELECT {RUN_COLUMNS} FROM run WHERE requested_by IS NOT DISTINCT FROM $1 AND idempotency_key = $2;",
                requested_by, idempotency_key,
            )
        assert existing is not None  # the conflict proves it exists
        return dict(existing), False

    async def get_run(self, run_id):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(f"SELECT {RUN_COLUMNS} FROM run WHERE id = $1;", run_id)
        return dict(row) if row else None

    async def list_runs(self, *, graph, status, limit, before):
        sql = f"""
            SELECT {RUN_COLUMNS} FROM run
            WHERE ($1::text IS NULL OR graph = $1)
              AND ($2::text IS NULL OR status = $2)
              AND ($3::uuid IS NULL OR id < $3)
            ORDER BY id DESC
            LIMIT $4;
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(sql, graph, status, before, limit)
        return [dict(r) for r in rows]

    async def list_events(self, run_id, *, after, limit):
        sql = """
            SELECT id, seq, kind, node, data, created_at FROM run_event
            WHERE run_id = $1 AND id > $2 ORDER BY id LIMIT $3;
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(sql, run_id, after, limit)
        return [dict(r) for r in rows]

    async def mark_enqueue_failed(self, run_id, message):
        sql = """
            UPDATE run SET status = 'failed', finished_at = now(), error_type = 'enqueue_failed', error_message = $2
            WHERE id = $1 AND status = 'queued';
        """
        async with self.pool.acquire() as conn:
            await conn.execute(sql, run_id, message)

    async def cancel_run(self, run_id):
        # Lock the row so the status we report as "before" is the one we changed.
        sql = f"""
            WITH prev AS (SELECT id, status FROM run WHERE id = $1 FOR UPDATE)
            UPDATE run
            SET status = 'cancelled', cancel_requested_at = now(), finished_at = now(), current_node = NULL
            FROM prev
            WHERE run.id = prev.id AND prev.status IN ('queued', 'running')
            RETURNING {", ".join("run." + c.strip() for c in RUN_COLUMNS.split(","))}, prev.status AS previous_status;
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(sql, run_id)
                if row is not None:
                    out = dict(row)
                    return out, out.pop("previous_status")
                current = await conn.fetchrow(f"SELECT {RUN_COLUMNS} FROM run WHERE id = $1;", run_id)
        if current is None:
            return None, None
        return dict(current), current["status"]

    async def ping(self):
        async with self.pool.acquire() as conn:
            return await conn.fetchval("SELECT 1;") == 1


_last_v7 = 0


def _uuid7() -> uuid.UUID:
    """Time-ordered like Postgres uuidv7() (stdlib has uuid7 only from 3.14):
    48-bit ms timestamp, strictly increasing within the process."""
    global _last_v7
    import os
    import time

    ts = time.time_ns() // 1_000_000
    value = (ts << 80) | (int.from_bytes(os.urandom(10), "big") & ((1 << 80) - 1))
    value = max(value, _last_v7 + 1)
    _last_v7 = value
    value = (value & ~(0xF << 76)) | (0x7 << 76)  # version 7
    value = (value & ~(0x3 << 62)) | (0x2 << 62)  # RFC 4122 variant
    return uuid.UUID(int=value)


class MemoryRunStore:
    """In-process store with the same semantics — unit tests only."""

    def __init__(self) -> None:
        self.runs: dict[uuid.UUID, dict[str, Any]] = {}
        self.events: dict[uuid.UUID, list[dict[str, Any]]] = {}

    async def create_run(self, *, graph, graph_version, inputs, credential_ref, requested_by, idempotency_key):
        if idempotency_key is not None:
            for row in self.runs.values():
                if row["requested_by"] == requested_by and row["idempotency_key"] == idempotency_key:
                    return dict(row), False
        run_id = _uuid7()
        row = {c.strip(): None for c in RUN_COLUMNS.split(",")}
        row.update(id=run_id, graph=graph, graph_version=graph_version, status="queued", inputs=dict(inputs),
                   credential_ref=credential_ref, requested_by=requested_by, idempotency_key=idempotency_key,
                   attempt=0, steps_completed=0, created_at=datetime.now(UTC))
        self.runs[run_id] = row
        return dict(row), True

    async def get_run(self, run_id):
        row = self.runs.get(run_id)
        return dict(row) if row else None

    async def list_runs(self, *, graph, status, limit, before):
        rows = sorted(self.runs.values(), key=lambda r: r["id"], reverse=True)
        rows = [r for r in rows if (graph is None or r["graph"] == graph) and (status is None or r["status"] == status)
                and (before is None or r["id"] < before)]
        return [dict(r) for r in rows[:limit]]

    async def list_events(self, run_id, *, after, limit):
        return [e for e in self.events.get(run_id, []) if e["id"] > after][:limit]

    async def mark_enqueue_failed(self, run_id, message):
        row = self.runs.get(run_id)
        if row and row["status"] == "queued":
            row.update(status="failed", finished_at=datetime.now(UTC), error_type="enqueue_failed", error_message=message)

    async def cancel_run(self, run_id):
        row = self.runs.get(run_id)
        if row is None:
            return None, None
        before = row["status"]
        if before in ("queued", "running"):
            now = datetime.now(UTC)
            row.update(status="cancelled", cancel_requested_at=now, finished_at=now, current_node=None)
        return dict(row), before

    async def ping(self):
        return True
