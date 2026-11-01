"""Invalid edits persist and reopen, but never cross the execution boundary."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from builder_api.graphs import GraphStore
from builder_api.main import create_app

GRAPH = """workflow: draft
portal: test
start: first
targets:
  button: {description: button, strategies: [{role: button}]}
nodes:
  first:
    recognize: {always: true}
    actions: [{click: {target: button}}]
    transitions: [{to: done}]
  done: {terminal: success}
"""


@pytest.fixture
def client(tmp_path):
    (tmp_path / "draft.yaml").write_text(GRAPH, encoding="utf-8")
    with TestClient(create_app(graphs_dir=tmp_path, headed=False)) as client:
        yield client


@pytest.mark.parametrize("bad", [
    "workflow: [unfinished\n",  # malformed YAML
    "nodes: []\n",  # invalid schema
    GRAPH.replace("target: button", "target: renamed"),  # unresolved target
    GRAPH.replace("actions: [{click: {target: button}}]", "actions: [{script: {code: 'return ('}}]"),
])
def test_invalid_graphs_persist_reopen_and_never_start(client, tmp_path, monkeypatch, bad):
    browser = AsyncMock()
    monkeypatch.setattr(client.app.state.manager, "_browser_instance", browser)
    saved = client.put("/graphs/draft", json={"yaml": bad})
    assert saved.status_code == 200, saved.text
    assert saved.json()["lint"]
    assert saved.json()["yaml"] == bad
    # A new store has no in-memory fallback to a previously valid graph.
    assert GraphStore(tmp_path).view("draft").yaml == bad
    assert client.get("/graphs/draft").json()["yaml"] == bad
    assert client.get("/graphs").json()[0]["lint_ok"] is False
    assert client.get("/graphs/draft/completions").status_code == 200
    response = client.post("/sessions", json={"graph": "draft"})
    assert response.status_code == 422
    browser.assert_not_called()
    assert not client.app.state.manager.sessions


@pytest.mark.parametrize("node_text", ["actions: [unfinished\n", "terminal: nope\n", "null\n"])
def test_bad_node_can_be_reopened_and_fixed_without_losing_other_nodes(client, node_text):
    original = client.get("/graphs/draft/nodes/first").json()["yaml"]
    saved = client.put("/graphs/draft/nodes/first", json={"yaml": node_text})
    assert saved.status_code == 200 and saved.json()["lint"]
    assert "first" in saved.json()["workflow"]["nodes"]
    assert client.get("/graphs/draft/nodes/first").status_code == 200
    assert client.get("/graphs/draft/nodes/done").json()["node"]["terminal"] == "success"
    assert client.post("/graphs/draft/nodes/first/lint", json={"yaml": original}).json()["ok"]
    fixed = client.put("/graphs/draft/nodes/first", json={"yaml": original})
    assert fixed.status_code == 200 and fixed.json()["lint"] == []


def test_target_can_be_renamed_in_separate_graph_and_node_saves(client):
    graph = GRAPH.replace("  button:", "  renamed:")
    saved = client.put("/graphs/draft", json={"yaml": graph})
    assert saved.status_code == 200 and saved.json()["lint"]
    node = client.get("/graphs/draft/nodes/first").json()["yaml"].replace("target: button", "target: renamed")
    fixed = client.put("/graphs/draft/nodes/first", json={"yaml": node})
    assert fixed.status_code == 200 and fixed.json()["lint"] == []
    assert "renamed" in fixed.json()["workflow"]["targets"]


def test_invalid_disk_graph_blocks_existing_session_execution_and_patch(client):
    manager = client.app.state.manager
    session = SimpleNamespace(step=AsyncMock(), run=AsyncMock(), patch=AsyncMock())
    manager.sessions["live"] = SimpleNamespace(graph_name="draft", session=session)
    client.put("/graphs/draft", json={"yaml": "workflow: ["})
    try:
        for operation in ("step", "run", "patch"):
            response = client.post(f"/sessions/live/{operation}")
            assert response.status_code == 422, response.text
        session.step.assert_not_called()
        session.run.assert_not_called()
        session.patch.assert_not_called()
    finally:
        manager.sessions.clear()
