"""Graph store + HTTP API tests — no browser. Works on a temp copy of tests/fixtures."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from builder_api.main import create_app

FIXTURES = Path(__file__).with_name("fixtures")
GRAPH = "acme_record_search"


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    graphs = tmp_path / "graphs"
    graphs.mkdir()
    shutil.copy(FIXTURES / f"{GRAPH}.yaml", graphs / f"{GRAPH}.yaml")
    app = create_app(graphs_dir=graphs, headed=False)
    with TestClient(app) as c:
        yield c


def test_list_and_get_graph(client: TestClient) -> None:
    r = client.get("/graphs")
    assert r.status_code == 200
    [g] = r.json()
    assert g["name"] == GRAPH and g["lint_ok"] is True and g["nodes"] == 6

    r = client.get(f"/graphs/{GRAPH}")
    view = r.json()
    assert view["workflow"]["start"] == "search_form"
    assert {"from": "record_detail", "to": "done", "label": "always"} in view["edges"]
    assert view["lint"] == []
    assert "search_form:" in view["yaml"]


def test_shared_scripts_roundtrip_completions_and_sandbox_test(client: TestClient) -> None:
    graph = '''workflow: shared
portal: example
scripts:
  answer: |
    return {value: args.n * 2};
start: start
nodes:
  start:
    recognize: {always: true}
    actions:
      - script: {ref: answer, args: {n: 21}, into: result}
    transitions: [{to: done}]
  done: {terminal: success}
'''
    response = client.put(f'/graphs/{GRAPH}', json={'yaml': graph})
    assert response.status_code == 200
    assert response.json()['lint'] == []
    assert response.json()['workflow']['nodes']['start']['actions'][0]['script']['ref'] == 'answer'
    assert client.get(f'/graphs/{GRAPH}/completions').json()['scripts'] == ['answer']
    result = client.post(f'/graphs/{GRAPH}/scripts/test', json={'node': 'start', 'index': 0})
    assert result.status_code == 200
    assert result.json()['value'] == {'value': 42}
    invalid = client.put(f'/graphs/{GRAPH}', json={'yaml': graph.replace('ref: answer', 'ref: missing')})
    assert invalid.status_code == 200 and invalid.json()['lint']
    rejected = client.post(f'/graphs/{GRAPH}/scripts/test', json={'node': 'start', 'index': 0})
    assert rejected.status_code == 422
    assert 'Unknown shared script' in rejected.json()['detail']


def test_image_target_and_capture_schema(client: TestClient) -> None:
    schema = client.get("/schema").json()
    assert "capture_image" in schema["verbs"]
    assert "CaptureImage" in schema["node"]["$defs"]
    response = client.post(f"/graphs/{GRAPH}/targets/from-element", json={
        "name": "captcha_image",
        "element": {"id": "e3", "role": "img", "tag": "img",
                    "name": "Verification image", "nearby": [], "frame": 0},
    })
    assert response.status_code == 201, response.text
    view = response.json()["view"]
    strategies = view["workflow"]["targets"]["captcha_image"]["strategies"]
    assert strategies[0]["role"] == "img"
    assert "Verification" in strategies[0]["name"]


def test_put_yaml_saves_invalid_work_and_returns_diagnostics(client: TestClient, tmp_path: Path) -> None:
    before = (tmp_path / "graphs" / f"{GRAPH}.yaml").read_text(encoding="utf-8")
    bad = before.replace("to: done", "to: nowhere")
    r = client.put(f"/graphs/{GRAPH}", json={"yaml": bad})
    assert r.status_code == 200
    assert any("unknown node 'nowhere'" in e for e in r.json()["lint"])
    assert (tmp_path / "graphs" / f"{GRAPH}.yaml").read_text(encoding="utf-8") == bad

    r = client.post(f"/graphs/{GRAPH}/lint", json={"yaml": "workflow: [oops"})
    assert r.status_code == 200 and r.json()["ok"] is False and "invalid YAML" in r.json()["errors"][0]


def test_node_roundtrip_and_edit(client: TestClient) -> None:
    r = client.get(f"/graphs/{GRAPH}/nodes/record_detail")
    assert r.status_code == 200
    node_yaml = r.json()["yaml"]
    assert "intent:" in node_yaml and "transitions:" in node_yaml

    edited = node_yaml.rstrip() + "\ntimeout_s: 42\n"
    r = client.put(f"/graphs/{GRAPH}/nodes/record_detail", json={"yaml": edited})
    assert r.status_code == 200, r.text
    assert r.json()["workflow"]["nodes"]["record_detail"]["timeout_s"] == 42

    r = client.put(f"/graphs/{GRAPH}/nodes/record_detail", json={"yaml": "terminal: success\ntransitions: [{to: done}]"})
    assert r.status_code == 200 and "terminal nodes carry no" in " ".join(r.json()["lint"])


def test_canvas_operations_add_node_edge_rename_delete(client: TestClient) -> None:
    r = client.post(f"/graphs/{GRAPH}/nodes", json={"name": "captcha_gate", "intent": "solve captcha"})
    assert r.status_code == 201, r.text
    wf = r.json()["workflow"]
    assert "captcha_gate" in wf["nodes"]
    # hooked from start so it lints; the hook is the last (lowest-priority) transition
    assert wf["nodes"]["search_form"]["transitions"][-1]["to"] == "captcha_gate"

    r = client.post(f"/graphs/{GRAPH}/edges", json={
        "source": "captcha_gate", "target": "search_form", "condition": {"url": "/search"}})
    assert r.status_code == 201
    edges = r.json()["edges"]
    assert {"from": "captcha_gate", "to": "search_form", "label": "url '/search'"} in edges
    # the conditional edge was inserted before the pre-existing `always` one
    tr = r.json()["workflow"]["nodes"]["captcha_gate"]["transitions"]
    assert tr[0]["when"]["url"] == "/search" and tr[-1]["to"] == "search_form"

    r = client.post(f"/graphs/{GRAPH}/nodes/captcha_gate/rename", json={"new_name": "gate"})
    assert r.status_code == 200
    wf = r.json()["workflow"]
    assert "gate" in wf["nodes"] and "captcha_gate" not in wf["nodes"]
    assert wf["nodes"]["search_form"]["transitions"][-1]["to"] == "gate"

    assert client.delete(f"/graphs/{GRAPH}/edges/gate/search_form").status_code == 200

    r = client.delete(f"/graphs/{GRAPH}/nodes/gate")
    assert r.status_code == 200
    wf = r.json()["workflow"]
    assert "gate" not in wf["nodes"]
    assert all(t["to"] != "gate" for t in wf["nodes"]["search_form"]["transitions"])

    assert client.delete(f"/graphs/{GRAPH}/nodes/search_form").status_code == 400  # start node


def test_create_graph_from_template(client: TestClient) -> None:
    r = client.post("/graphs", json={"name": "acme_login", "portal": "acme-records", "entry_url": "https://records.acme.example/login"})
    assert r.status_code == 201, r.text
    assert r.json()["lint"] == []
    assert r.json()["workflow"]["entry_url"].startswith("https://records")
    assert client.post("/graphs", json={"name": "acme_login", "portal": "x"}).status_code == 409
    assert client.post("/graphs", json={"name": "Bad Name", "portal": "x"}).status_code == 400


def test_delete_graph_removes_the_file(tmp_path: Path) -> None:
    graphs = tmp_path / "graphs"
    graphs.mkdir()
    broken = graphs / "half_written.yaml"
    broken.write_text("workflow: [", encoding="utf-8")
    with TestClient(create_app(graphs_dir=graphs, headed=False)) as c:
        assert c.post("/graphs", json={"name": "acme_login", "portal": "acme-records"}).status_code == 201
        assert c.delete("/graphs/half_written").status_code == 204  # unparseable graphs can still go
        assert not broken.exists()
        assert c.delete("/graphs/acme_login").status_code == 204
        assert not (graphs / "acme_login.yaml").exists()
        assert c.get("/graphs").json() == []
        assert c.get("/graphs/acme_login").status_code == 404
        assert c.delete("/graphs/acme_login").status_code == 404
        assert c.delete("/graphs/Bad Name").status_code == 400


def test_schema_and_completions_feed_the_editor(client: TestClient) -> None:
    r = client.get("/schema")
    assert r.status_code == 200
    s = r.json()
    assert "nodes" in s["workflow"]["properties"] and "recognize" in s["node"]["properties"]
    assert "requests" in s["workflow"]["properties"]
    assert "digits_only" in s["shapers"] and "click" in s["verbs"] and "visible" in s["condition_kinds"]
    assert "fetch" in s["verbs"] and "request" in s["condition_kinds"]
    assert s["request_kinds"] == ["http", "totp", "imap"]
    assert {"url", "extract", "retry", "sensitive", "cache"} <= set(s["request_fields"])
    assert "dom_id" in s["strategy_fields"]

    c = client.get(f"/graphs/{GRAPH}/completions").json()
    assert "search_form" in c["nodes"] and "reference_field" in c["targets"] and c["inputs"] == ["reference"]
    assert c["requests"] == {}

    r = client.post(f"/graphs/{GRAPH}/nodes/record_detail/lint", json={"yaml": "intent: x\nrecognize: {url: /a/}\ntransitions: [{to: nowhere}]"})
    assert r.status_code == 200 and r.json()["ok"] is False
    assert any("unknown node 'nowhere'" in e for e in r.json()["errors"])

    # A dangling `- ` list item is a null — say what the item needs instead of Pydantic's wording.
    dangling = "intent: x\nrecognize: {always: true}\nactions:\n  - click: {target: search_button}\n  - \ntransitions:\n  - to: done\n"
    r = client.post(f"/graphs/{GRAPH}/nodes/record_detail/lint", json={"yaml": dangling})
    [err] = r.json()["errors"]
    assert err.startswith("actions.1: empty item — give it a verb: click, fill")


def test_generators_target_from_element_and_recognize() -> None:
    from builder_api.graphs import recognize_from_state, target_from_element

    name, t = target_from_element({"id": "e24", "role": "textbox", "name": "Reference number. Enter the full reference",
                                   "dom_id": "referenceNumber", "nearby": [], "landmark": "form#searchForm"})
    assert name.startswith("reference_number_enter_the_full") and len(name) <= 40
    assert t.strategies[0].name.startswith("re:^R") and t.strategies[0].role == "textbox"
    assert t.strategies[1].dom_id == "re:^referenceNumber$"

    # JSF-style generated id → anchored stable suffix; no accessible name → nearby + landmark
    name, t = target_from_element({"id": "f1e2", "role": "radio", "name": "", "dom_id": "searchForm:j_id161:kind",
                                   "nearby": ["Exact match"], "landmark": "form#searchForm"}, name="kind_radio")
    assert name == "kind_radio"
    assert t.strategies[0].dom_id == "re::kind$"
    assert t.strategies[1].nearby == "re:^Exact\\ match$" and t.strategies[1].landmark == "re:searchForm"

    cond = recognize_from_state({
        "route": "/search/open ?x",
        "elements": [
            {"role": "link", "name": "Home", "in_viewport": True, "landmark": "nav"},
            {"role": "textbox", "name": "", "dom_id": "reference", "in_viewport": True, "landmark": "form#f"},
            {"role": "button", "name": "Search", "dom_id": None, "in_viewport": True, "landmark": "form#f"},
            {"role": "button", "name": "Offscreen", "in_viewport": False},
        ],
    })
    assert cond.kind == "all" and len(cond.all) == 3
    assert cond.all[0].url == "re:/search/open"
    assert cond.all[1].visible.dom_id == "re:^reference$"
    assert cond.all[2].visible.name == "re:^Search$"


def test_target_from_element_and_recognize_endpoints(client: TestClient) -> None:
    r = client.post(f"/graphs/{GRAPH}/targets/from-element", json={
        "element": {"id": "e9", "role": "link", "name": "New search", "dom_id": None, "nearby": [], "landmark": "main"}})
    assert r.status_code == 201, r.text
    assert r.json()["target"] == "new_search"
    assert "new_search" in r.json()["view"]["workflow"]["targets"]
    assert "re:^New\\ search$" in r.json()["yaml"]

    # recognize-from-session needs a live session; without one it 404s cleanly
    r = client.post(f"/graphs/{GRAPH}/nodes/search_form/recognize-from-session", json={"session_id": "nope"})
    assert r.status_code == 404


def test_inputs_can_be_added_and_removed(client: TestClient) -> None:
    r = client.put(f"/graphs/{GRAPH}/inputs/password", json={"type": "password", "credential": "password",
                                                             "description": "account password"})
    assert r.status_code == 200, r.text
    assert r.json()["workflow"]["inputs"]["password"] == {
        "type": "password", "description": "account password", "required": True, "pii": True, "credential": "password"}
    r = client.put(f"/graphs/{GRAPH}/inputs/region", json={"type": "string", "pii": False, "required": False, "default": "eu"})
    assert r.json()["workflow"]["inputs"]["region"]["default"] == "eu"
    assert client.put(f"/graphs/{GRAPH}/inputs/Bad Name", json={}).status_code == 400
    # a credential input cannot have a default (lint)
    r = client.put(f"/graphs/{GRAPH}/inputs/user", json={"credential": "username", "default": "x"})
    assert r.status_code == 200 and r.json()["lint"]

    # References can be repaired after saving a related edit.
    r = client.delete(f"/graphs/{GRAPH}/inputs/reference")
    assert r.status_code == 200 and any("unknown input {reference}" in e for e in r.json()["lint"])
    assert "reference" not in client.get(f"/graphs/{GRAPH}").json()["workflow"]["inputs"]

    assert client.delete(f"/graphs/{GRAPH}/inputs/password").status_code == 200


def test_session_endpoints_404_for_unknown_session(client: TestClient) -> None:
    assert client.get("/sessions/nope").status_code == 404
    assert client.post("/sessions/nope/step").status_code == 404
    assert client.post("/sessions/nope/requests/otp", json={}).status_code == 404
    assert client.get("/sessions").json() == []
