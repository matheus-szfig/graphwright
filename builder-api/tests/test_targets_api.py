"""Target authoring is independent of browser execution and preserves invalid drafts."""
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient
from graphwright.observe.pagestate import PageState

from builder_api.main import create_app

GRAPH = """workflow: targets
portal: test
start: fill
inputs:
  code: {}
nodes:
  fill:
    recognize: {visible: otp}
    actions:
      - fill: {target: otp, value: '{code}'}
    transitions: [{to: done}]
  done: {terminal: success}
targets:
  otp:
    description: Login code
    strategies: [{role: textbox, dom_id: '^old$'}]
"""


@pytest.fixture
def client(tmp_path):
    (tmp_path / "targets.yaml").write_text(GRAPH, encoding="utf-8")
    with TestClient(create_app(graphs_dir=tmp_path, headed=False)) as client:
        yield client


def test_offline_edit_delete_and_repair_referenced_target(client):
    base = "/graphs/targets/targets"
    entry = client.get(base).json()["otp"]
    assert "report" not in entry and len(entry["references"]) == 2
    before = client.get("/graphs/targets").json()["workflow"]["nodes"]
    response = client.put(f"{base}/otp", json={"yaml": "description: Changed\nstrategies: []", "expected_yaml": entry["yaml"]})
    assert response.status_code == 200 and response.json()["lint"]
    # A schema-invalid target remains listed and editable.
    invalid = client.get(base).json()["otp"]
    assert invalid["description"] == "Changed"
    assert yaml.safe_load(invalid["yaml"])["strategies"] == []
    stale = client.put(f"{base}/otp", json={"yaml": entry["yaml"], "expected_yaml": entry["yaml"]})
    assert stale.status_code == 409
    repaired = client.put(f"{base}/otp", json={"yaml": entry["yaml"], "expected_yaml": invalid["yaml"]})
    assert repaired.status_code == 200 and repaired.json()["lint"] == []
    removed = client.delete(f"{base}/otp")
    assert removed.status_code == 200 and removed.json()["lint"]
    assert removed.json()["workflow"]["nodes"] == before
    assert client.get(base).json() == {}
    assert client.delete(f"{base}/otp").status_code == 404
    recreated = client.put(f"{base}/otp", json={"yaml": entry["yaml"], "expected_yaml": ""})
    assert recreated.status_code == 200 and recreated.json()["lint"] == []


def test_live_status_uses_current_saved_targets_without_patching_session(client, monkeypatch):
    state = PageState.model_validate({"url": "https://test/", "portal": "test", "route": "/", "elements": [
        {"id": "e1", "role": "textbox", "tag": "input", "dom_id": "new", "name": "OTP", "bbox": {"x": 0, "y": 0, "w": 100, "h": 30}},
    ]})
    live = SimpleNamespace(graph_name="targets", session=SimpleNamespace(last_state=state))
    monkeypatch.setattr(client.app.state.manager, "get", lambda sid: live)
    base = "/graphs/targets/targets"
    assert not client.get(f"{base}?session_id=s1").json()["otp"]["report"]["ok"]
    client.put(f"{base}/otp", json={"yaml": "description: New code field\nstrategies: [{dom_id: 're:^new$'}]"})
    report = client.get(f"{base}?session_id=s1").json()["otp"]["report"]
    assert report["ok"] and report["element"]["id"] == "e1"
    assert "report" not in client.get(base).json()["otp"]
    live.graph_name = "other"
    assert client.get(f"{base}?session_id=s1").status_code == 409
