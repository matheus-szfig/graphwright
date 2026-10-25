"""Postgres: the migration, PgRunStore, and the worker contract.

Runs only against a reachable ``*_test`` database (POSTGRES_DSN); every test
TRUNCATEs. The schema is migrated once per session with ``alembic upgrade head``.
The contract test drives the engine's real ``graphwright.storage.db`` as the
``graphwright_worker`` role, so a column rename on either side fails here.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest

from tests.conftest import DB_DSN, requires_db

pytestmark = requires_db

API_ROOT = Path(__file__).resolve().parents[1]


def worker_dsn() -> str:
    parts = urlsplit(DB_DSN)
    password = os.environ.get("DB_PASSWORD_WORKER") or "graphwright_worker"
    host = parts.hostname or "localhost"
    netloc = f"graphwright_worker:{password}@{host}" + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


@pytest.fixture(scope="session", autouse=True)
def migrated() -> None:
    from alembic.config import Config

    from alembic import command

    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    command.upgrade(cfg, "head")


@pytest.fixture
async def store():
    from graphwright_api.store import PgRunStore

    s = await PgRunStore.connect(DB_DSN, max_size=2)
    async with s.pool.acquire() as conn:
        await conn.execute("TRUNCATE run, run_event, artifact RESTART IDENTITY CASCADE;")
    yield s
    await s.close()


async def make(store, **kw):
    args = {"graph": "g", "graph_version": 1, "inputs": {"q": "x"}, "credential_ref": None,
            "requested_by": "ci", "idempotency_key": None, **kw}
    return await store.create_run(**args)


async def test_create_get_list_and_idempotency(store) -> None:
    a, created = await make(store)
    assert created and a["status"] == "queued" and a["inputs"] == {"q": "x"} and a["id"].version == 7
    b, _ = await make(store, graph="other")
    assert (await store.get_run(a["id"]))["graph"] == "g"
    assert [r["id"] for r in await store.list_runs(graph=None, status=None, limit=10, before=None)] == [b["id"], a["id"]]
    assert [r["id"] for r in await store.list_runs(graph="g", status="queued", limit=10, before=None)] == [a["id"]]
    assert await store.list_runs(graph=None, status=None, limit=10, before=a["id"]) == []

    k1, c1 = await make(store, idempotency_key="k")
    k2, c2 = await make(store, idempotency_key="k", inputs={"q": "other"})
    k3, c3 = await make(store, idempotency_key="k", requested_by="someone-else")
    assert c1 and not c2 and c3
    assert k2["id"] == k1["id"] and k2["inputs"] == {"q": "x"} and k3["id"] != k1["id"]


async def test_cancel_and_enqueue_failure(store) -> None:
    queued, _ = await make(store)
    row, before = await store.cancel_run(queued["id"])
    assert before == "queued" and row["status"] == "cancelled" and row["finished_at"] is not None
    row, before = await store.cancel_run(queued["id"])
    assert before == "cancelled" and row["status"] == "cancelled"
    assert await store.cancel_run(uuid.uuid4()) == (None, None)

    lost, _ = await make(store)
    await store.mark_enqueue_failed(lost["id"], "ConnectionError: could not reach the queue")
    row = await store.get_run(lost["id"])
    assert row["status"] == "failed" and row["error_type"] == "enqueue_failed"


async def test_worker_contract_as_the_restricted_role(store, monkeypatch: pytest.MonkeyPatch) -> None:
    from graphwright.config import settings as engine_settings
    from graphwright.storage import db as worker_db

    run, _ = await make(store, credential_ref="lease-1")
    cancelled, _ = await make(store)
    await store.cancel_run(cancelled["id"])

    monkeypatch.setattr(engine_settings, "postgres_dsn", worker_dsn())
    await worker_db.init_pool(min_size=1, max_size=2)
    try:
        claimed = await worker_db.claim_run(run["id"], celery_task_id=str(run["id"]), browser_profile="p")
        assert claimed is not None and claimed.graph == "g" and claimed.credential_ref == "lease-1" and claimed.attempt == 1
        assert await worker_db.claim_run(cancelled["id"], celery_task_id="x", browser_profile=None) is None

        await worker_db.set_run_graph_version(run["id"], 4)
        await worker_db.update_run_progress(run["id"], steps_completed=1, node="search")
        await worker_db.insert_run_events(run["id"], [
            {"seq": 1, "kind": "move", "node": "start", "to": "search"},
            {"seq": 2, "kind": "request", "node": "search", "name": "otp", "ok": True},
        ])
        art = await worker_db.insert_artifact(run_id=run["id"], kind="screenshot", bucket="b", object_key="k.jpg")
        assert art.version == 7
        await worker_db.mark_run_completed(run["id"], result={"terminal": "success", "outputs": {}}, steps_completed=2)
        assert await worker_db.run_status(run["id"]) == "completed"

        # least privilege: the worker cannot create runs or rewrite what was asked
        pool = worker_db.get_pool()
        async with pool.acquire() as conn:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute("INSERT INTO run (graph) VALUES ('evil');")
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute("UPDATE run SET inputs = '{}'::jsonb WHERE id = $1;", run["id"])
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute("DELETE FROM run;")
    finally:
        await worker_db.close_pool()

    row = await store.get_run(run["id"])
    assert row["status"] == "completed" and row["terminal"] == "success" and row["graph_version"] == 4
    assert row["steps_completed"] == 2 and row["worker_hostname"] and row["started_at"] and row["finished_at"]
    events = await store.list_events(run["id"], after=0, limit=10)
    assert [e["kind"] for e in events] == ["move", "request"] and events[1]["data"]["name"] == "otp"

    # a cancelled run's late outcome does not overwrite the cancellation
    await worker_db.init_pool(min_size=1, max_size=1)
    try:
        await worker_db.mark_run_failed(cancelled["id"], exc=RuntimeError("late"), error_type="late")
    finally:
        await worker_db.close_pool()
    assert (await store.get_run(cancelled["id"]))["status"] == "cancelled"


async def test_api_routes_against_postgres(store) -> None:
    import httpx

    from graphwright_api.config import Settings
    from graphwright_api.main import create_app
    from tests.conftest import FIXTURE_GRAPHS, HEADERS, KEY, FakeEnqueuer, FakeProgress

    enq = FakeEnqueuer()
    app = create_app(settings=Settings(env="test", api_keys=f"ci:{KEY}", graphs_dir=str(FIXTURE_GRAPHS)),
                     store=store, enqueuer=enq, progress=FakeProgress())
    # In-loop client: the asyncpg pool belongs to this test's event loop.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api") as c:
        body = {"graph": "fixture_search", "inputs": {"query": "q"}, "idempotency_key": "i"}
        r = await c.post("/runs", json=body, headers=HEADERS)
        assert r.status_code == 202, r.text
        assert (await c.post("/runs", json=body, headers=HEADERS)).status_code == 200
        assert enq.enqueued == [r.json()["id"]]
        assert (await c.get(f"/runs/{r.json()['id']}", headers=HEADERS)).json()["graph_version"] == 3
        assert (await c.post(f"/runs/{r.json()['id']}/cancel", headers=HEADERS)).json()["status"] == "cancelled"
        assert (await c.get("/health")).json()["db"] is True
