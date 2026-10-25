"""Failure evidence through the API: listing a run's artifacts and serving their bytes."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from graphwright_api.blobs import ObjectNotFound
from graphwright_api.main import create_app
from graphwright_api.store import MemoryRunStore

HEADERS = {"X-API-Key": "secret-ci"}  # the `ci` key from conftest.settings


class FakeBlobs:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    async def stream(self, bucket: str, key: str) -> AsyncIterator[bytes]:
        if (bucket, key) not in self.objects:
            raise ObjectNotFound(f"{bucket}/{key}")
        body = self.objects[(bucket, key)]
        for i in range(0, len(body), 4):
            yield body[i : i + 4]


@pytest.fixture
def blobs() -> FakeBlobs:
    return FakeBlobs()


@pytest.fixture
def api(settings, store, enqueuer, progress, blobs):
    app = create_app(settings=settings, store=store, enqueuer=enqueuer, progress=progress, blobs=blobs, heartbeat_s=0.05)
    with TestClient(app) as c:
        yield c


def failed_run_with_evidence(store: MemoryRunStore, blobs: FakeBlobs) -> tuple[uuid.UUID, dict[str, uuid.UUID]]:
    import asyncio

    row, _ = asyncio.run(store.create_run(graph="fixture_search", graph_version=1, inputs={}, credential_ref=None,
                                          requested_by="ci", idempotency_key=None))
    run_id = row["id"]
    ids: dict[str, uuid.UUID] = {}
    for kind, ext, ctype, body in [
        ("screenshot", "jpg", "image/jpeg", b"\xff\xd8\xff\xe0jpegbytes"),
        ("html", "html", "text/html; charset=utf-8", b"<html><script>alert(1)</script></html>"),
        ("context", "json", "application/json", b'{"node": "search"}'),
    ]:
        art_id = uuid.uuid4()
        key = f"runs/{run_id}/1/{kind}.{ext}"
        store.artifacts.setdefault(run_id, []).append({
            "id": art_id, "run_id": run_id, "kind": kind, "bucket": "graphwright-dev-evidence", "object_key": key,
            "content_type": ctype, "size_bytes": len(body), "node": "search", "metadata": {"reason": "failed"},
            "created_at": datetime.now(UTC),
        })
        blobs.objects[("graphwright-dev-evidence", key)] = body
        ids[kind] = art_id
    return run_id, ids


def test_list_artifacts(api: TestClient, store, blobs) -> None:
    run_id, ids = failed_run_with_evidence(store, blobs)

    r = api.get(f"/runs/{run_id}/artifacts", headers=HEADERS)

    assert r.status_code == 200
    arts = r.json()["artifacts"]
    assert [a["kind"] for a in arts] == ["screenshot", "html", "context"]
    assert arts[0]["url"] == f"/runs/{run_id}/artifacts/{ids['screenshot']}"
    assert "bucket" not in arts[0] and "object_key" not in arts[0]  # storage layout stays internal


def test_download_streams_the_bytes_with_safe_headers(api: TestClient, store, blobs) -> None:
    run_id, ids = failed_run_with_evidence(store, blobs)

    shot = api.get(f"/runs/{run_id}/artifacts/{ids['screenshot']}", headers=HEADERS)
    assert shot.status_code == 200 and shot.content == b"\xff\xd8\xff\xe0jpegbytes"
    assert shot.headers["content-type"] == "image/jpeg"
    assert shot.headers["x-content-type-options"] == "nosniff"
    assert shot.headers["content-disposition"].startswith("inline")

    page = api.get(f"/runs/{run_id}/artifacts/{ids['html']}", headers=HEADERS)
    assert page.status_code == 200 and b"<script>" in page.content
    # captured HTML never renders in the API's origin
    assert page.headers["content-disposition"].startswith("attachment")
    assert "sandbox" in page.headers["content-security-policy"]


def test_artifact_errors(api: TestClient, store, blobs) -> None:
    run_id, ids = failed_run_with_evidence(store, blobs)
    assert api.get(f"/runs/{run_id}/artifacts/{uuid.uuid4()}", headers=HEADERS).status_code == 404
    assert api.get(f"/runs/{uuid.uuid4()}/artifacts", headers=HEADERS).status_code == 404
    # another run's artifact id is not reachable through this run
    other, _ = failed_run_with_evidence(store, blobs)
    assert api.get(f"/runs/{other}/artifacts/{ids['html']}", headers=HEADERS).status_code == 404
    # row present, object gone
    blobs.objects.clear()
    r = api.get(f"/runs/{run_id}/artifacts/{ids['context']}", headers=HEADERS)
    assert r.status_code == 404 and r.json()["detail"]["code"] == "artifact_missing"


def test_artifacts_need_a_key(api: TestClient, store, blobs) -> None:
    run_id, ids = failed_run_with_evidence(store, blobs)
    assert api.get(f"/runs/{run_id}/artifacts").status_code == 401
    assert api.get(f"/runs/{run_id}/artifacts/{ids['html']}").status_code == 401
