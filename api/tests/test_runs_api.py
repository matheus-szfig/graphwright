"""Runs API on fakes — validation, idempotency, enqueue failure, cancel, auth, SSE."""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from graphwright_api.config import Settings
from graphwright_api.main import create_app
from graphwright_api.store import MemoryRunStore
from tests.conftest import HEADERS, FakeEnqueuer, FakeProgress


def create(client: TestClient, **body):
    return client.post("/runs", json={"graph": "fixture_search", "inputs": {"query": "acme"}, **body}, headers=HEADERS)


# --- auth ------------------------------------------------------------------------------


def test_health_is_open_and_reports_checks(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok", "db": True, "redis": True}


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}, {"X-API-Key": ""}])
def test_routes_need_a_valid_key(client: TestClient, headers: dict) -> None:
    for method, path in [("get", "/graphs"), ("get", "/runs"), ("post", "/runs")]:
        r = getattr(client, method)(path, headers=headers, **({"json": {"graph": "x"}} if method == "post" else {}))
        assert r.status_code == 401, (method, path)
        assert r.json()["detail"]["code"] == "unauthorized"


def test_requested_by_is_the_key_id(client: TestClient) -> None:
    r = client.post("/runs", json={"graph": "fixture_search", "inputs": {"query": "x"}},
                    headers={"X-API-Key": "secret-other"})
    assert r.status_code == 202 and r.json()["requested_by"] == "other"


def test_no_keys_configured_is_503(store, enqueuer, progress) -> None:
    app = create_app(settings=Settings(env="test", api_keys=""), store=store, enqueuer=enqueuer, progress=progress)
    with TestClient(app) as c:
        assert c.get("/runs", headers=HEADERS).status_code == 503


def test_dev_placeholder_key_is_refused_outside_dev() -> None:
    with pytest.raises(ValueError, match="dev placeholder"):
        create_app(settings=Settings(env="prod", api_keys="dev:dev-only-change-me"),
                   store=MemoryRunStore(), enqueuer=FakeEnqueuer(), progress=FakeProgress())
    with pytest.raises(ValueError, match="id:secret"):
        Settings(env="prod", api_keys="justasecret").keys()


# --- graphs ----------------------------------------------------------------------------


def test_graph_catalog(client: TestClient) -> None:
    graphs = {g["name"]: g for g in client.get("/graphs", headers=HEADERS).json()}
    assert set(graphs) == {"fixture_search", "fixture_login", "broken_graph"}
    assert graphs["fixture_search"]["ok"] and graphs["fixture_search"]["version"] == 3
    assert graphs["fixture_search"]["inputs"]["password"]["credential"] == "password"
    assert not graphs["broken_graph"]["ok"] and graphs["broken_graph"]["lint"]

    assert client.get("/graphs/fixture_login", headers=HEADERS).json()["portal"] == "fixture"
    assert client.get("/graphs/nope", headers=HEADERS).status_code == 404
    assert client.get("/graphs/Bad-Name", headers=HEADERS).status_code == 404


# --- create ----------------------------------------------------------------------------


def test_create_queues_and_enqueues_by_id(client: TestClient, enqueuer: FakeEnqueuer, store: MemoryRunStore) -> None:
    r = create(client)
    assert r.status_code == 202
    run = r.json()
    assert run["status"] == "queued" and run["graph_version"] == 3
    assert run["inputs"] == {"query": "acme"} and run["has_credential"] is False
    assert "credential_ref" not in run
    assert enqueuer.enqueued == [run["id"]]
    assert store.runs[uuid.UUID(run["id"])]["requested_by"] == "ci"


def test_credential_ref_is_stored_but_never_echoed(client: TestClient, store: MemoryRunStore) -> None:
    r = client.post("/runs", json={"graph": "fixture_login", "credential_ref": "lease-123"}, headers=HEADERS)
    assert r.status_code == 202, r.text
    assert r.json()["has_credential"] is True and "lease-123" not in r.text
    assert store.runs[uuid.UUID(r.json()["id"])]["credential_ref"] == "lease-123"
    assert "lease-123" not in client.get(f"/runs/{r.json()['id']}", headers=HEADERS).text


@pytest.mark.parametrize(
    ("body", "status", "code", "fragment"),
    [
        ({"graph": "nope"}, 404, "graph_not_found", "nope"),
        ({"graph": "broken_graph"}, 422, "graph_invalid", "does not lint"),
        ({"graph": "fixture_search", "inputs": {}}, 422, "inputs_invalid", "missing input 'query'"),
        ({"graph": "fixture_search", "inputs": {"query": "x", "bogus": 1}}, 422, "inputs_invalid", "unknown input 'bogus'"),
        ({"graph": "fixture_search", "inputs": {"query": "x", "password": "hunter2"}}, 422, "inputs_invalid",
         "comes from the credential"),
        ({"graph": "fixture_login"}, 422, "inputs_invalid", "needs a credential"),
    ],
)
def test_create_validation(client: TestClient, enqueuer: FakeEnqueuer, body, status, code, fragment) -> None:
    r = client.post("/runs", json=body, headers=HEADERS)
    assert r.status_code == status, r.text
    assert r.json()["detail"]["code"] == code
    assert fragment in json.dumps(r.json())
    assert enqueuer.enqueued == []


def test_credential_values_never_land_in_the_store(client: TestClient, store: MemoryRunStore) -> None:
    client.post("/runs", json={"graph": "fixture_search", "inputs": {"query": "x", "password": "hunter2"}}, headers=HEADERS)
    assert store.runs == {}


def test_idempotency_key_replays_the_same_run_per_caller(client: TestClient, enqueuer: FakeEnqueuer) -> None:
    first = create(client, idempotency_key="order-7")
    again = create(client, idempotency_key="order-7", inputs={"query": "something else"})
    assert first.status_code == 202 and again.status_code == 200
    assert again.json()["id"] == first.json()["id"] and again.json()["inputs"] == {"query": "acme"}
    assert enqueuer.enqueued == [first.json()["id"]]

    other = client.post("/runs", json={"graph": "fixture_search", "inputs": {"query": "acme"}, "idempotency_key": "order-7"},
                        headers={"X-API-Key": "secret-other"})
    assert other.status_code == 202 and other.json()["id"] != first.json()["id"]


def test_enqueue_failure_marks_the_run_failed(client: TestClient, enqueuer: FakeEnqueuer, store: MemoryRunStore) -> None:
    enqueuer.fail = True
    r = create(client)
    assert r.status_code == 503 and r.json()["detail"]["code"] == "enqueue_failed"
    row = store.runs[uuid.UUID(r.json()["detail"]["run_id"])]
    assert row["status"] == "failed" and row["error_type"] == "enqueue_failed"


# --- read ------------------------------------------------------------------------------


def test_list_filters_and_keyset_pagination(client: TestClient) -> None:
    ids = [create(client, inputs={"query": str(i)}).json()["id"] for i in range(5)]
    client.post("/runs", json={"graph": "fixture_login", "credential_ref": "c"}, headers=HEADERS)

    page = client.get("/runs", params={"graph": "fixture_search", "limit": 2}, headers=HEADERS).json()
    assert [r["id"] for r in page["runs"]] == ids[::-1][:2] and page["next"] == ids[3]
    page2 = client.get("/runs", params={"graph": "fixture_search", "limit": 2, "before": page["next"]}, headers=HEADERS).json()
    assert [r["id"] for r in page2["runs"]] == [ids[2], ids[1]]
    last = client.get("/runs", params={"graph": "fixture_search", "limit": 2, "before": page2["next"]}, headers=HEADERS).json()
    assert [r["id"] for r in last["runs"]] == [ids[0]] and last["next"] is None

    assert len(client.get("/runs", params={"status": "queued"}, headers=HEADERS).json()["runs"]) == 6
    assert client.get("/runs", params={"status": "bogus"}, headers=HEADERS).status_code == 422


def test_get_run_includes_live_progress(client: TestClient, progress: FakeProgress) -> None:
    run_id = create(client).json()["id"]
    assert client.get(f"/runs/{run_id}", headers=HEADERS).json()["progress"] is None
    progress.snapshots[run_id] = {"status": "running", "node": "search", "steps": 1}
    assert client.get(f"/runs/{run_id}", headers=HEADERS).json()["progress"]["node"] == "search"
    assert client.get(f"/runs/{uuid.uuid4()}", headers=HEADERS).status_code == 404
    assert client.get("/runs/not-a-uuid", headers=HEADERS).status_code == 422


def test_events_page(client: TestClient, store: MemoryRunStore) -> None:
    run_id = create(client).json()["id"]
    from datetime import UTC, datetime

    store.events[uuid.UUID(run_id)] = [
        {"id": i, "seq": i, "kind": "move", "node": "search", "data": {"to": "done"}, "created_at": datetime.now(UTC)}
        for i in range(1, 4)
    ]
    page = client.get(f"/runs/{run_id}/events", params={"limit": 2}, headers=HEADERS).json()
    assert [e["id"] for e in page["events"]] == [1, 2] and page["next"] == 2
    rest = client.get(f"/runs/{run_id}/events", params={"after": 2}, headers=HEADERS).json()
    assert [e["id"] for e in rest["events"]] == [3] and rest["next"] is None


# --- cancel ----------------------------------------------------------------------------


def test_cancel_queued_running_and_final(client: TestClient, store: MemoryRunStore, enqueuer: FakeEnqueuer) -> None:
    queued = create(client).json()["id"]
    r = client.post(f"/runs/{queued}/cancel", headers=HEADERS)
    assert r.status_code == 200 and r.json()["status"] == "cancelled" and r.json()["cancel_requested_at"]
    assert enqueuer.revoked == []  # nothing is running; the worker will skip the message

    running = create(client).json()["id"]
    store.runs[uuid.UUID(running)]["status"] = "running"
    r = client.post(f"/runs/{running}/cancel", headers=HEADERS)
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert enqueuer.revoked == [running]

    again = client.post(f"/runs/{running}/cancel", headers=HEADERS)
    assert again.status_code == 409 and again.json()["detail"]["run_status"] == "cancelled"
    assert client.post(f"/runs/{uuid.uuid4()}/cancel", headers=HEADERS).status_code == 404


# --- stream ----------------------------------------------------------------------------


def read_sse(client: TestClient, run_id: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    event = None
    with client.stream("GET", f"/runs/{run_id}/stream", headers=HEADERS) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line.startswith(": "):
                out.append(("comment", line[2:]))
            elif line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: ") and event:
                out.append((event, line[6:]))
    return out


def test_stream_relays_progress_until_final(client: TestClient, store: MemoryRunStore, progress: FakeProgress) -> None:
    run_id = create(client).json()["id"]
    store.runs[uuid.UUID(run_id)]["status"] = "running"
    progress.published[run_id] = [
        {"status": "running", "node": "search", "steps": 0},
        {"status": "completed", "terminal": "success"},
    ]

    def finish_on_final(original):
        async def get_run(rid):
            row = await original(rid)
            if row and not progress.published[run_id]:
                row["status"] = "completed"
            return row
        return get_run

    store.get_run = finish_on_final(store.get_run)  # type: ignore[method-assign]
    frames = read_sse(client, run_id)

    kinds = [k for k, _ in frames]
    assert kinds[0] == "run" and kinds[-1] == "end"
    assert [json.loads(d)["status"] for k, d in frames if k == "progress"] == ["running", "completed"]
    assert json.loads(frames[-1][1])["status"] == "completed"


def test_stream_heartbeats_and_ends_when_the_db_says_final(client: TestClient, store: MemoryRunStore) -> None:
    run_id = create(client).json()["id"]
    row = store.runs[uuid.UUID(run_id)]
    row["status"] = "running"
    calls = {"n": 0}
    original = store.get_run

    async def get_run(rid):
        calls["n"] += 1
        if calls["n"] > 3:
            row["status"] = "cancelled"  # cancelled elsewhere; no publish ever arrives
        return await original(rid)

    store.get_run = get_run  # type: ignore[method-assign]
    frames = read_sse(client, run_id)

    assert ("comment", "ping") in frames
    assert frames[-1][0] == "end" and json.loads(frames[-1][1])["status"] == "cancelled"


def test_stream_of_a_final_run_ends_immediately(client: TestClient, store: MemoryRunStore) -> None:
    run_id = create(client).json()["id"]
    store.runs[uuid.UUID(run_id)]["status"] = "failed"
    assert [k for k, _ in read_sse(client, run_id)] == ["run", "end"]
