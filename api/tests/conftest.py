"""Fixtures. Unit tests run the app on fakes (memory store, recorded enqueues,
queued progress). DB tests (``requires_db``) run only when POSTGRES_DSN names a
database ending in ``_test`` and it is reachable — they TRUNCATE tables, so they
refuse anything else."""

from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from graphwright_api.config import Settings
from graphwright_api.main import create_app
from graphwright_api.store import MemoryRunStore

FIXTURE_GRAPHS = Path(__file__).with_name("fixtures") / "graphs"
KEY = "secret-ci"
HEADERS = {"X-API-Key": KEY}


class FakeEnqueuer:
    def __init__(self) -> None:
        self.enqueued: list[str] = []
        self.revoked: list[str] = []
        self.fail = False

    async def enqueue(self, run_id: str) -> None:
        if self.fail:
            raise ConnectionError("broker down")
        self.enqueued.append(run_id)

    async def revoke(self, run_id: str) -> None:
        self.revoked.append(run_id)


class FakeProgress:
    def __init__(self) -> None:
        self.snapshots: dict[str, dict[str, Any]] = {}
        self.published: dict[str, list[dict[str, Any]]] = defaultdict(list)

    async def get(self, run_id: str) -> dict[str, Any] | None:
        return self.snapshots.get(run_id)

    async def subscribe(self, run_id: str, *, poll_s: float = 15.0) -> AsyncIterator[dict[str, Any] | None]:
        while True:
            queue = self.published[run_id]
            if queue:
                yield queue.pop(0)
            else:
                await asyncio.sleep(poll_s)
                yield None

    async def ping(self) -> bool:
        return True


@pytest.fixture
def settings() -> Settings:
    return Settings(env="test", api_keys=f"ci:{KEY},other:secret-other", graphs_dir=str(FIXTURE_GRAPHS),
                    postgres_dsn="postgresql://unused/unused", redis_url="redis://unused:6379/0")


@pytest.fixture
def store() -> MemoryRunStore:
    return MemoryRunStore()


@pytest.fixture
def enqueuer() -> FakeEnqueuer:
    return FakeEnqueuer()


@pytest.fixture
def progress() -> FakeProgress:
    return FakeProgress()


@pytest.fixture
def client(settings: Settings, store: MemoryRunStore, enqueuer: FakeEnqueuer, progress: FakeProgress):
    app = create_app(settings=settings, store=store, enqueuer=enqueuer, progress=progress, heartbeat_s=0.05)
    with TestClient(app) as c:
        yield c


# --- DB gating ------------------------------------------------------------------------

DB_DSN = os.environ.get("POSTGRES_DSN", "")


def _is_test_database(dsn: str) -> bool:
    return bool(dsn) and urlsplit(dsn).path.lstrip("/").endswith("_test")


def _reachable(dsn: str) -> bool:
    if not _is_test_database(dsn):
        return False
    import asyncpg

    async def probe() -> bool:
        try:
            conn = await asyncpg.connect(dsn, timeout=3)
        except (OSError, asyncpg.PostgresError):
            return False
        await conn.close()
        return True

    return asyncio.run(probe())


HAS_DB = _reachable(DB_DSN)
requires_db = pytest.mark.skipif(
    not HAS_DB,
    reason="DB tests need POSTGRES_DSN pointing at a reachable *_test database"
    if not _is_test_database(DB_DSN) else "POSTGRES_DSN *_test database is unreachable",
)
