"""Graph request debugging endpoints — standalone and inside a session. No browser:
HTTP goes through an httpx.MockTransport, the session wraps a page-less GraphSession."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from graphwright.engine import Workflow, compile_workflow
from graphwright.engine.requests import RequestServices
from graphwright.engine.runtime import GraphSession
from graphwright.engine.secrets import Redactor

from builder_api.main import create_app
from builder_api.sessions import LiveSession

GRAPH_YAML = """
workflow: otp_login
portal: acme-records
start: login
inputs:
  otp_api: {type: url, pii: false}
  account: {pii: false}
requests:
  otp:
    description: current 2FA code from the relay
    url: "{otp_api}/codes/latest"
    query: {account: "{account}"}
    headers: {Authorization: "Bearer relay-token-123"}
    extract:
      code: {json: data.code}
    retry: {attempts: 3, backoff_s: [0], until_extracted: true}
nodes:
  login:
    recognize: {always: true}
    actions:
      - fetch: {request: otp, on_error: continue}
    transitions:
      - to: done
        when: {request: {name: otp, status: ok}}
      - to: no_code
  no_code: {terminal: failure}
  done: {terminal: success}
"""

CALLS: list[httpx.Request] = []


def relay(request: httpx.Request) -> httpx.Response:
    CALLS.append(request)
    if request.url.params.get("account") == "slow" and sum(1 for c in CALLS if c.url.params.get("account") == "slow") < 2:
        return httpx.Response(200, json={"data": {}})  # not there yet → polled again
    if request.url.params.get("account") == "down":
        return httpx.Response(503, text="maintenance")
    return httpx.Response(200, json={"data": {"code": "731902"}})


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    CALLS.clear()
    graphs = tmp_path / "graphs"
    graphs.mkdir()
    (graphs / "otp_login.yaml").write_text(GRAPH_YAML, encoding="utf-8")
    app = create_app(graphs_dir=graphs, headed=False, request_transport=httpx.MockTransport(relay))
    with TestClient(app) as c:
        yield c


INPUTS = {"otp_api": "https://relay.example", "account": "jane"}


def test_standalone_test_masks_values_and_shows_attempts(client: TestClient) -> None:
    r = client.post("/graphs/otp_login/requests/otp/test", json={"inputs": {**INPUTS, "account": "slow"}})
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["ok"] is True and report["kind"] == "http"
    assert report["values"] == {"code": "***"}
    [call] = report["calls"]
    assert [a["outcome"] for a in call["attempts"]] == ["retry", "ok"]
    assert call["attempts"][0]["missing"] == ["code"]
    first = call["attempts"][0]["request"]
    assert first["url"] == "https://relay.example/codes/latest" and first["headers"]["Authorization"] == "***"
    assert "731902" not in json.dumps(report)
    assert CALLS[-1].headers["Authorization"] == "Bearer relay-token-123"  # real value went out


def test_standalone_test_reveal_and_failure(client: TestClient) -> None:
    r = client.post("/graphs/otp_login/requests/otp/test", json={"inputs": INPUTS, "reveal": True})
    assert r.json()["values"] == {"code": "731902"}

    r = client.post("/graphs/otp_login/requests/otp/test", json={"inputs": {**INPUTS, "account": "down"}})
    report = r.json()
    assert report["ok"] is False and "HTTP 503" in report["error"]
    assert len(report["calls"][0]["attempts"]) == 3

    r = client.post("/graphs/otp_login/requests/otp/test", json={"inputs": {"account": "jane"}})
    assert r.json()["ok"] is False and "{otp_api}" in r.json()["error"]

    assert client.post("/graphs/otp_login/requests/nope/test", json={}).status_code == 404


def test_completions_and_schema_know_requests(client: TestClient) -> None:
    c = client.get("/graphs/otp_login/completions").json()
    assert c["requests"] == {"otp": ["code"]}


def test_request_inside_a_session_uses_session_inputs_and_feeds_conditions(client: TestClient) -> None:
    app = client.app
    manager = app.state.manager
    graph = compile_workflow(Workflow.from_yaml_text(GRAPH_YAML))
    redactor = Redactor()
    live = LiveSession(id="s1", graph_name="otp_login", graph_path=Path("x"), inputs=INPUTS,
                       context=None, page=None, session=None)  # type: ignore[arg-type]
    live.session = GraphSession(graph, None, dict(INPUTS), observer=object(), on_event=live.publish,  # type: ignore[arg-type]
                                redactor=redactor,
                                services=RequestServices(transport=httpx.MockTransport(relay), on_secret=redactor.add))
    manager.sessions["s1"] = live

    r = client.post("/sessions/s1/requests/otp", json={})
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["ok"] and report["values"] == {"code": "***"} and report["session"] == "s1"
    # the session now knows the request succeeded (what a `request:` condition reads)…
    assert client.get("/sessions/s1").json()["requests"] == {"otp": "ok"}
    # …and the event is in its stream, masked
    events = client.get("/sessions/s1/events").json()
    assert events[-1]["kind"] == "request" and events[-1]["values"] == {"code": "***"}
    assert "731902" not in json.dumps(events)

    # vars override applies to this call only
    r = client.post("/sessions/s1/requests/otp", json={"vars": {"x": 1}, "reveal": True})
    assert r.json()["values"] == {"code": "731902"}

    assert client.post("/sessions/s1/requests/ghost", json={}).status_code == 404
