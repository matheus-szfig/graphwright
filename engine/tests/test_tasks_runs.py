"""The run task (graphwright/tasks/runs.py) with its edges faked: DB, browser pool,
credential provider and GraphSession. What it must get right is the bookkeeping —
claim/skip, refusals recorded as codes, durable events, redaction, cancellation."""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from graphwright.credentials import Credential, CredentialRejected
from graphwright.engine.runtime import Event
from graphwright.storage import cache
from graphwright.storage.db import ClaimedRun
from graphwright.tasks import runs

RUN_ID = str(uuid.UUID(int=7))


class FakeDb:
    def __init__(self, claimed: ClaimedRun | None) -> None:
        self.claimed = claimed
        self.status = "running"
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def claim_run(run_id, **kw):
            self.calls.append(("claim", kw))
            return self.claimed

        def recorder(name):
            async def fn(run_id, **kw):
                self.calls.append((name, kw))
            return fn

        async def set_version(run_id, version):
            self.calls.append(("version", {"version": version}))

        async def insert_events(run_id, events):
            self.calls.append(("events", {"events": events}))

        async def run_status(run_id):
            return self.status

        monkeypatch.setattr(runs.db, "claim_run", claim_run)
        monkeypatch.setattr(runs.db, "set_run_graph_version", set_version)
        monkeypatch.setattr(runs.db, "update_run_progress", recorder("progress"))
        monkeypatch.setattr(runs.db, "mark_run_completed", recorder("completed"))
        monkeypatch.setattr(runs.db, "mark_run_failed", recorder("failed"))
        monkeypatch.setattr(runs.db, "insert_run_events", insert_events)
        monkeypatch.setattr(runs.db, "run_status", run_status)

    def call(self, name: str) -> dict[str, Any]:
        return next(kw for n, kw in self.calls if n == name)

    def names(self) -> list[str]:
        return [n for n, _ in self.calls]


class FakePool:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    @asynccontextmanager
    async def context_for(self, domain: str, **kw):
        self.kwargs = {"domain": domain, **kw}
        page = SimpleNamespace()

        async def new_page():
            return page

        yield SimpleNamespace(new_page=new_page)


class FakeSession:
    """Stands in for GraphSession: emits a scripted event trail, one step per call."""

    script: list[list[tuple[str, dict[str, Any]]]] = []
    final: tuple[str, dict[str, Any] | None, dict[str, Any] | None] = ("done", {"terminal": "success"}, None)
    built: list[FakeSession] = []

    def __init__(self, graph, page, inputs, *, redactor=None, on_event=None, services=None, **_) -> None:
        self.graph, self.inputs, self.redactor, self.on_event, self.services = graph, inputs, redactor, on_event, services
        self.status, self.result, self.failure = "paused", None, None
        self.steps = list(self.script)
        FakeSession.built.append(self)

    def _emit(self, kind: str, node: str | None = None, **data: Any) -> None:
        self.on_event(Event(kind=kind, node=node, data=self.redactor(data)))

    async def start(self) -> None:
        self._emit("goto", url="http://demo-site:8090/login")

    async def step(self):
        if self.steps:
            for kind, data in self.steps.pop(0):
                self._emit(kind, data.pop("node", None), **data)
            return SimpleNamespace(status="paused")
        self.status, self.result, self.failure = self.final
        return SimpleNamespace(status=self.status)


@pytest.fixture
def loop():
    lp = asyncio.new_event_loop()
    yield lp
    lp.close()


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, loop):
    pool = FakePool()
    monkeypatch.setattr(runs, "get_loop_and_pool", lambda: (loop, pool))
    monkeypatch.setattr(runs, "GraphSession", FakeSession)
    FakeSession.built = []
    FakeSession.final = ("done", {"terminal": "success", "outputs": {"x": 1}}, None)
    FakeSession.script = [
        [("observe", {"render": "…"}), ("move", {"from": "sign_in", "to": "verify", "node": "sign_in"})],
        [("request", {"name": "otp", "ok": True, "node": "verify"}),
         ("fill", {"target": "password_field", "value": "demo-password", "node": "sign_in"}),
         ("move", {"from": "verify", "to": "dashboard", "node": "verify"})],
    ]
    return SimpleNamespace(pool=pool, monkeypatch=monkeypatch)


def claimed(graph: str = "acme_account_login", ref: str | None = "demo", inputs: dict | None = None) -> ClaimedRun:
    return ClaimedRun(id=uuid.UUID(RUN_ID), graph=graph, inputs=inputs or {}, credential_ref=ref, attempt=1)


def provider(monkeypatch, credential: Credential | Exception) -> None:
    class P:
        name = "fake"

        def redeem(self, ref: str, *, run_id: str) -> Credential:
            assert run_id == RUN_ID
            if isinstance(credential, Exception):
                raise credential
            return credential

    monkeypatch.setattr(runs, "get_provider", lambda: P())


DEMO = Credential(ref="demo", portal="acme-records",
                  values={"username": "demo", "password": "demo-password"}, totp=lambda: "123456")


def run_task() -> dict[str, Any]:
    return runs.run_graph.apply(args=[RUN_ID], task_id="celery-1").get(disable_sync_subtasks=False)


def test_nothing_to_do_when_the_run_cannot_be_claimed(env) -> None:
    db = FakeDb(None)
    db.install(env.monkeypatch)
    assert run_task() == {"run_id": RUN_ID, "skipped": True}
    assert db.names() == ["claim"] and FakeSession.built == []
    assert db.call("claim")["celery_task_id"] == "celery-1"


def test_success_persists_redacted_durable_events_and_the_result(env) -> None:
    db = FakeDb(claimed())
    db.install(env.monkeypatch)
    provider(env.monkeypatch, DEMO)

    out = run_task()

    assert out == {"run_id": RUN_ID, "status": "completed", "terminal": "success"}
    assert db.call("version") == {"version": 1}
    done = db.call("completed")
    assert done["result"]["outputs"] == {"x": 1} and done["steps_completed"] == 2
    events = db.call("events")["events"]
    kinds = [e["kind"] for e in events]
    assert kinds == ["goto", "move", "request", "fill", "move"]  # observe is not durable
    assert [e["seq"] for e in events] == sorted(e["seq"] for e in events)
    assert "demo-password" not in json.dumps(events)  # the run's redactor saw the credential
    # the browser context: no shared cookie jar, flagged as authenticated
    assert env.pool.kwargs["persist_state"] is False and env.pool.kwargs["authenticated"] is True
    session = FakeSession.built[0]
    assert session.inputs["password"].reveal() == "demo-password"
    assert session.services.totp() == "123456"
    progress = cache.get_run_progress(RUN_ID)
    assert progress["status"] == "completed" and progress["node"] == "dashboard" and progress["steps"] == 2


def test_progress_is_written_per_move(env, loop) -> None:
    db = FakeDb(claimed())
    db.install(env.monkeypatch)
    provider(env.monkeypatch, DEMO)
    run_task()
    loop.run_until_complete(asyncio.sleep(0))  # flush the fire-and-forget progress updates
    assert [kw["node"] for n, kw in db.calls if n == "progress"] == ["verify", "dashboard"]


@pytest.mark.parametrize(
    ("setup", "code"),
    [
        (lambda m: provider(m, Credential(ref="x", portal="other-site", values={"username": "u", "password": "p"})),
         "portal_mismatch"),
        (lambda m: provider(m, CredentialRejected("lease_expired")), "lease_expired"),
        (lambda m: provider(m, Credential(ref="x", values={"username": "u"})), "missing_inputs"),
    ],
)
def test_refusals_are_recorded_as_codes(env, setup, code) -> None:
    db = FakeDb(claimed())
    db.install(env.monkeypatch)
    setup(env.monkeypatch)

    out = run_task()

    assert out == {"run_id": RUN_ID, "status": "failed", "error_type": code}
    failed = db.call("failed")
    assert failed["error_type"] == code
    assert FakeSession.built == []  # never got to the browser
    assert cache.get_run_progress(RUN_ID)["status"] == "failed"


def test_graph_errors_are_codes(env) -> None:
    db = FakeDb(claimed(graph="no_such_graph", ref=None))
    db.install(env.monkeypatch)
    assert run_task()["error_type"] == "graph_not_found"


def test_graph_failure_keeps_the_engine_failure_record(env) -> None:
    db = FakeDb(claimed())
    db.install(env.monkeypatch)
    provider(env.monkeypatch, DEMO)
    FakeSession.final = ("needs_repair", None, {"type": "NoTransition", "message": "no transition on verify", "details": []})

    out = run_task()

    assert out["error_type"] == "graph_needs_repair"
    failed = db.call("failed")
    assert failed["failure"]["type"] == "NoTransition" and failed["steps_completed"] == 2
    assert "events" in db.names()


def test_anonymous_run_does_not_redeem(env) -> None:
    db = FakeDb(claimed(graph="acme_record_search", ref=None, inputs={"reference": "ACM-2024-000123"}))
    db.install(env.monkeypatch)
    env.monkeypatch.setattr(runs, "get_provider", lambda: pytest.fail("no credential to redeem"))

    assert run_task()["status"] == "completed"
    assert env.pool.kwargs["authenticated"] is False and env.pool.kwargs["client_certificates"] is None
    assert FakeSession.built[0].inputs["reference"] == "ACM-2024-000123"


def test_cancellation_between_steps(env) -> None:
    db = FakeDb(claimed())
    db.install(env.monkeypatch)
    provider(env.monkeypatch, DEMO)
    db.status = "cancelled"

    out = run_task()

    assert out == {"run_id": RUN_ID, "status": "cancelled"}
    assert "completed" not in db.names() and "failed" not in db.names()
    events = db.call("events")["events"]
    assert events[-1]["kind"] == "cancelled"
    assert cache.get_run_progress(RUN_ID)["status"] == "cancelled"


def test_unexpected_errors_are_recorded_and_reraised(env) -> None:
    db = FakeDb(claimed())
    db.install(env.monkeypatch)
    provider(env.monkeypatch, DEMO)

    async def boom(self):
        raise RuntimeError("browser crashed while typing demo-password")

    env.monkeypatch.setattr(FakeSession, "start", boom)
    result = runs.run_graph.apply(args=[RUN_ID], task_id="celery-1")
    assert result.failed()
    failed = db.call("failed")
    assert failed["error_type"] == "RuntimeError"
    # mark_run_failed scrubs with the run's redactor before storing
    assert failed["scrub"]("typing demo-password") == "typing ***"


# --- failure evidence -------------------------------------------------------------------


@pytest.fixture
def captured(env) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def capture(session, run_id, attempt, *, reason, failure=None):
        calls.append({"session": session, "run_id": run_id, "attempt": attempt, "reason": reason, "failure": failure})
        return [{"id": "art-1", "kind": "screenshot"}]

    env.monkeypatch.setattr(runs.evidence, "capture", capture)
    return calls


def test_a_failed_run_carries_its_evidence(env, captured) -> None:
    db = FakeDb(claimed())
    db.install(env.monkeypatch)
    provider(env.monkeypatch, DEMO)
    FakeSession.final = ("needs_repair", None, {"type": "NoTransition", "message": "stuck", "details": []})

    run_task()

    assert [c["reason"] for c in captured] == ["failed"]
    assert captured[0]["run_id"] == uuid.UUID(RUN_ID) and captured[0]["attempt"] == 1
    assert captured[0]["session"] is FakeSession.built[0]  # taken while the page was open
    assert db.call("failed")["failure"]["evidence"] == [{"id": "art-1", "kind": "screenshot"}]


def test_a_failure_terminal_carries_evidence_in_the_result(env, captured) -> None:
    db = FakeDb(claimed())
    db.install(env.monkeypatch)
    provider(env.monkeypatch, DEMO)
    FakeSession.final = ("done", {"terminal": "failure", "status": "invalid_credentials"}, None)

    assert run_task()["status"] == "completed"
    assert [c["reason"] for c in captured] == ["failure"]
    assert db.call("completed")["result"]["evidence"] == [{"id": "art-1", "kind": "screenshot"}]


def test_success_captures_nothing_unless_evidence_on_always(env, captured) -> None:
    db = FakeDb(claimed())
    db.install(env.monkeypatch)
    provider(env.monkeypatch, DEMO)
    run_task()
    assert captured == [] and "evidence" not in db.call("completed")["result"]

    env.monkeypatch.setattr(runs.evidence.settings, "evidence_on", "always")
    db = FakeDb(claimed())
    db.install(env.monkeypatch)
    run_task()
    assert [c["reason"] for c in captured] == ["success"]


def test_unexpected_errors_still_try_to_capture(env, captured) -> None:
    db = FakeDb(claimed())
    db.install(env.monkeypatch)
    provider(env.monkeypatch, DEMO)

    async def boom(self):
        raise RuntimeError("navigation failed")

    env.monkeypatch.setattr(FakeSession, "start", boom)
    assert runs.run_graph.apply(args=[RUN_ID], task_id="celery-1").failed()
    assert captured[0]["reason"] == "error" and captured[0]["failure"]["type"] == "RuntimeError"
    assert db.call("failed")["failure"] == {"type": "RuntimeError", "evidence": [{"id": "art-1", "kind": "screenshot"}]}


def test_evidence_off_means_no_capture(env, captured) -> None:
    env.monkeypatch.setattr(runs.evidence.settings, "evidence_on", "never")
    db = FakeDb(claimed())
    db.install(env.monkeypatch)
    provider(env.monkeypatch, DEMO)
    FakeSession.final = ("failed", None, {"type": "RequestFailed", "message": "otp", "details": []})
    run_task()
    assert captured == [] and "evidence" not in db.call("failed")["failure"]
