"""Code-node and browser-console endpoints. No browser: scripts run in the sandbox,
the console goes to a fake observer behind a page-less GraphSession."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from graphwright.engine import Workflow, compile_workflow
from graphwright.engine.runtime import GraphSession
from graphwright.engine.secrets import Redactor, Secret

from builder_api.main import create_app
from builder_api.sessions import LiveSession

GRAPH_YAML = """
workflow: code_demo
portal: acme-records
start: parse
inputs:
  date: {pii: false}
  password: {type: password}
nodes:
  parse:
    recognize: {always: true}
    actions:
      - script:
          into: parsed
          args: {d: "{date}"}
          code: |
            const [day, month, year] = args.d.split("/");
            console.log("parsing", args.d);
            return {iso: `${year}-${month}-${day}`, n: (vars.n || 0) + 1};
      - script:
          code: "return {year: Number(args.y)}"
          args: {y: "2026"}
    transitions:
      - to: done
  done: {terminal: success}
"""


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    graphs = tmp_path / "graphs"
    graphs.mkdir()
    (graphs / "code_demo.yaml").write_text(GRAPH_YAML, encoding="utf-8")
    app = create_app(graphs_dir=graphs, headed=False)
    with TestClient(app) as c:
        yield c


def test_script_test_with_code(client: TestClient) -> None:
    r = client.post("/graphs/code_demo/scripts/test", json={
        "code": "console.log('hi', args.x); return {double: args.x * 2}", "args": {"x": 21},
    })
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["ok"] and report["value"] == {"double": 42} and report["sets"] == ["double"]
    assert report["logs"] == ["log hi 21"]


def test_script_test_by_node_and_index_uses_the_saved_action(client: TestClient) -> None:
    r = client.post("/graphs/code_demo/scripts/test", json={"node": "parse", "inputs": {"date": "25/10/2026"},
                                                            "vars": {"n": 4}})
    report = r.json()
    assert report["ok"], report
    assert report["value"] == {"iso": "2026-10-25", "n": 5} and report["sets"] == ["parsed"]
    assert report["logs"] == ["log parsing 25/10/2026"]

    second = client.post("/graphs/code_demo/scripts/test", json={"node": "parse", "index": 1}).json()
    assert second["ok"] and second["value"] == {"year": 2026} and second["sets"] == ["year"]

    assert client.post("/graphs/code_demo/scripts/test", json={"node": "parse", "index": 5}).status_code == 404
    assert client.post("/graphs/code_demo/scripts/test", json={"node": "ghost"}).status_code == 404
    assert client.post("/graphs/code_demo/scripts/test", json={}).status_code == 422


def test_script_test_errors_and_secret_refusal(client: TestClient) -> None:
    bad = client.post("/graphs/code_demo/scripts/test", json={"code": "null.x"}).json()
    assert not bad["ok"] and "TypeError" in bad["error"] and bad["details"]

    slow = client.post("/graphs/code_demo/scripts/test", json={"code": "while (true) {}", "timeout_ms": 100}).json()
    assert not slow["ok"] and "exceeded 100 ms" in slow["error"]

    # a password input is a Secret in the run; a script arg built from it is refused…
    refused = client.post("/graphs/code_demo/scripts/test", json={
        "code": "return {n: args.p.length}", "args": {"p": "{password}"}, "inputs": {"password": "hunter2-pw"},
    }).json()
    assert not refused["ok"] and "allow_secrets" in refused["error"]
    allowed = client.post("/graphs/code_demo/scripts/test", json={
        "code": "return {n: args.p.length, head: args.p.slice(0, 3)}", "args": {"p": "{password}"},
        "inputs": {"password": "hunter2-pw"}, "allow_secrets": True,
    }).json()
    # with allow_secrets the strings it returns are secrets too: the report masks them
    assert allowed["ok"] and allowed["value"] == "***" and "hun" not in json.dumps(allowed)


def test_add_code_node_lints_and_is_runnable(client: TestClient) -> None:
    r = client.post("/graphs/code_demo/nodes", json={"name": "derive", "kind": "code"})
    assert r.status_code == 201, r.text
    view = r.json()
    assert view["lint"] == []
    node = view["workflow"]["nodes"]["derive"]
    assert node["recognize"]["always"] is True
    assert "return {};" in node["actions"][0]["script"]["code"]
    assert any(e["from"] == "parse" and e["to"] == "derive" for e in view["edges"])
    # the stub runs as is
    report = client.post("/graphs/code_demo/scripts/test", json={"node": "derive"}).json()
    assert report["ok"] and report["value"] == {} and report["sets"] == []
    assert client.post("/graphs/code_demo/nodes", json={"name": "x", "kind": "wizard"}).status_code == 400


def test_schema_knows_the_code_verbs(client: TestClient) -> None:
    s = client.get("/schema").json()
    assert "script" in s["verbs"] and "evaluate" in s["verbs"]
    assert "code" in s["node_kinds"]
    assert {"code", "args", "into", "timeout_ms", "memory_mb", "allow_secrets"} <= set(s["script_fields"])
    assert {"script", "world", "frame"} <= set(s["evaluate_fields"])
    defs = json.dumps(s["node"])
    assert "Script" in defs and "Evaluate" in defs


# --- the console (in-session evaluate) ----------------------------------------------


class FakeObserver:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def evaluate(self, body, args=None, *, world="isolated", frame=0):
        self.calls.append((body, args, world, frame))
        if "throw" in body:
            raise RuntimeError("script threw: Error: nope")
        if "hang" in body:
            await asyncio.sleep(5)
        return {"title": "Acme", "token": "tok-sekret-42", "args": args}


@pytest.fixture
def session(client: TestClient) -> tuple[LiveSession, FakeObserver]:
    graph = compile_workflow(Workflow.from_yaml_text(GRAPH_YAML))
    redactor = Redactor(["tok-sekret-42"])
    observer = FakeObserver()
    live = LiveSession(id="s1", graph_name="code_demo", graph_path=Path("x"), inputs={},
                       context=None, page=None, session=None)  # type: ignore[arg-type]
    live.session = GraphSession(graph, None, {"password": Secret("pw")}, observer=observer,  # type: ignore[arg-type]
                                on_event=live.publish, redactor=redactor)
    client.app.state.manager.sessions["s1"] = live
    return live, observer


def test_console_runs_in_the_session_page_redacted(client: TestClient, session) -> None:
    live, observer = session
    r = client.post("/sessions/s1/evaluate", json={"script": "return {title: document.title}", "world": "main",
                                                   "frame": 1, "args": {"q": 1}})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ok"] and out["value"]["title"] == "Acme" and out["value"]["token"] == "***"
    assert observer.calls[-1][1:] == ({"q": 1}, "main", 1)
    ev = client.get("/sessions/s1/events").json()[-1]
    assert ev["kind"] == "evaluate" and ev["source"] == "console" and ev["world"] == "main"
    assert "tok-sekret-42" not in json.dumps(ev)


def test_console_errors_timeouts_and_bad_world(client: TestClient, session) -> None:
    err = client.post("/sessions/s1/evaluate", json={"script": "throw new Error('nope')"}).json()
    assert not err["ok"] and "nope" in err["error"]
    slow = client.post("/sessions/s1/evaluate", json={"script": "hang", "timeout_ms": 50}).json()
    assert not slow["ok"] and "50 ms" in slow["error"]
    assert client.post("/sessions/s1/evaluate", json={"script": "x", "world": "server"}).status_code == 422
    assert client.post("/sessions/nope/evaluate", json={"script": "x"}).status_code == 404
    events = [e for e in client.get("/sessions/s1/events").json() if e["kind"] == "evaluate"]
    assert [e["ok"] for e in events] == [False, False]


def test_console_busy_session_is_409(client: TestClient, session) -> None:
    live, _ = session
    live.busy = True
    assert client.post("/sessions/s1/evaluate", json={"script": "return 1"}).status_code == 409


def test_saved_graph_keeps_code_as_a_block_scalar(client: TestClient, tmp_path: Path) -> None:
    client.post("/graphs/code_demo/nodes", json={"name": "derive", "kind": "code"})
    text = (tmp_path / "graphs" / "code_demo.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    assert data["nodes"]["parse"]["actions"][0]["script"]["code"].startswith("const [day, month, year]")
