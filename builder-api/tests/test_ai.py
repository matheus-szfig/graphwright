"""AI authoring with fake providers: no paid requests, credentials or browser needed."""
import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from graphwright.engine import Workflow, compile_workflow
from graphwright.engine.runtime import GraphSession, StepResult
from graphwright.engine.secrets import Redactor, Secret
from graphwright.observe.pagestate import Element, PageState

from builder_api.ai.privacy import Projection, page_context
from builder_api.ai.service import Harness
from builder_api.main import create_app
from builder_api.sessions import LiveSession, SessionManager, session_inputs

GRAPH = """workflow: test
portal: example
inputs:
  account_password: {type: secure}
  person: {pii: true, required: false, default: 'Alice Private'}
targets:
  login: {description: Login control, strategies: [{role: textbox, name: 'Alice Private'}]}
start: start
nodes:
  start:
    intent: Private legal matter
    recognize: {always: true}
    transitions: [{to: done}]
  done: {terminal: success}
"""
EDITS = [{"op": "set", "path": ["requests", "otp"], "value": {
    "kind": "http", "url": "https://otp.example.test/code", "sensitive": True,
    "extract": {"code": {"json": "data.otp"}}}},
    {"op": "set", "path": ["nodes", "start", "actions"], "value": [{"fetch": {"request": "otp", "into": "otp"}}]}]


class Provider:
    def __init__(self, provider="openai", calls=None):
        self.provider = provider
        self.calls = calls if calls is not None else [("propose_edits", {"edits_json": json.dumps(EDITS)})]
        self.payloads = []

    def __call__(self, request):
        if request.method == "GET" and request.url.path.startswith("/v1/models/"):
            return httpx.Response(200, json={"id": "test-model", "max_tokens": 128000})
        if request.method == "GET" and request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "test-model", "display_name": "Test model"},
                                                       {"id": "test-model-2", "display_name": "Test model 2"}], "has_more": False})
        data = json.loads(request.content)
        self.payloads.append(data)
        setup = not data.get("tools")
        number = len(self.payloads) - 2
        call = self.calls[number] if not setup and 0 <= number < len(self.calls) else None
        if self.provider == "openai":
            output = [{"type": "function_call", "call_id": f"call{number}", "name": call[0],
                       "arguments": json.dumps(call[1])}] if call else [
                           {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Ready" if setup else "Proposal prepared."}]}]
            return httpx.Response(200, json={"output": output, "usage": {"input_tokens": 12, "output_tokens": 8}})
        content = [{"type": "tool_use", "id": f"call{number}", "name": call[0], "input": call[1]}] if call else [
            {"type": "text", "text": "Ready" if setup else "Proposal prepared."}]
        return httpx.Response(200, json={"content": content, "usage": {"input_tokens": 12, "output_tokens": 8}})


def create_client(tmp_path, provider):
    (tmp_path / "test.yaml").write_text(GRAPH, encoding="utf-8")
    return TestClient(create_app(graphs_dir=tmp_path, headed=False, ai_transport=httpx.MockTransport(provider)))


def connect(client, provider="openai"):
    response = client.post("/ai/connect", json={"provider": provider, "model": "test-model", "key": "provider-secret"})
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["token"]}


def turn(client, headers, **kwargs):
    response = client.post("/ai/turn", headers=headers, json={"graph": "test", "message": "Add an OTP request",
                                                           **kwargs})
    assert response.status_code == 200, response.text
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


@pytest.mark.parametrize("provider_name", ["openai", "anthropic"])
def test_offline_request_authoring_is_private_and_reviewable(tmp_path, provider_name):
    provider = Provider(provider_name)
    with create_client(tmp_path, provider) as client:
        headers = connect(client, provider_name)
        events = turn(client, headers, values={"account_password": "xy", "person": "Bob Private"},
                      message="Add the request; hide xy and Bob Private")
        assert events[-1]["kind"] == "done", events
        proposal = next(e["proposal"] for e in events if e["kind"] == "proposal")
        assert not proposal["errors"]
        assert "requests:" not in (tmp_path / "test.yaml").read_text()
        sent = json.dumps(provider.payloads)
        for private in ["provider-secret", "Alice Private", "Bob Private", "Private legal matter", "hide xy"]:
            assert private not in sent
        assert events[0]["context"]["session"]["authoring_available"]
        response = client.post(f'/ai/proposals/{proposal["id"]}/apply', headers=headers, json={})
        assert response.status_code == 200, response.text
        wf = Workflow.from_yaml_text(response.json()["view"]["yaml"])
        assert wf.requests["otp"].extract["code"].json_path == "data.otp"
        assert wf.inputs["person"].default == "Alice Private"
        assert wf.nodes["start"].intent == "Private legal matter"
        assert wf.targets["login"].strategies[0].name == "Alice Private"


def test_stale_proposal_and_foreign_capability_are_rejected(tmp_path):
    with create_client(tmp_path, Provider()) as client:
        headers = connect(client)
        proposal = next(e["proposal"] for e in turn(client, headers) if e["kind"] == "proposal")
        other = connect(client)
        assert client.post(f'/ai/proposals/{proposal["id"]}/apply', headers=other, json={}).status_code == 404
        client.put("/graphs/test", json={"yaml": GRAPH + "\n# human edit\n"})
        response = client.post(f'/ai/proposals/{proposal["id"]}/apply', headers=headers, json={})
        assert response.status_code == 409
        assert "human edit" in (tmp_path / "test.yaml").read_text()


def test_context_requires_capability_and_origin_and_expiry(tmp_path):
    with create_client(tmp_path, Provider()) as client:
        assert client.post("/ai/context", json={"graph": "test"}).status_code == 401
        assert client.post("/ai/connect", headers={"Origin": "https://attacker.test"},
                           json={"provider": "openai", "model": "m", "key": "secret"}).status_code == 403
        headers = connect(client)
        token = headers["Authorization"][7:]
        conn = client.app.state.ai.connections[token]
        conn.expires = time.monotonic() - 1
        assert client.post("/ai/context", headers=headers, json={"graph": "test"}).status_code == 401
        assert conn.key == ""


@pytest.mark.parametrize("provider_name", ["openai", "anthropic"])
def test_connect_with_key_only_then_switch_model_mid_conversation(tmp_path, provider_name):
    provider = Provider(provider_name, calls=[])
    with create_client(tmp_path, provider) as client:
        response = client.post("/ai/connect", json={"provider": provider_name, "key": "provider-secret"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["model"] == "test-model"
        assert [m["id"] for m in body["models"]] == ["test-model", "test-model-2"]
        assert "provider-secret" not in response.text
        headers = {"Authorization": "Bearer " + body["token"]}
        assert turn(client, headers, message="First question")[-1]["kind"] == "done"

        response = client.post("/ai/model", headers=headers, json={"model": "test-model-2"})
        assert response.status_code == 200, response.text
        assert response.json() == {"model": "test-model-2"}
        assert turn(client, headers, message="Second question")[-1]["kind"] == "done"

        turns = [p for p in provider.payloads if p.get("tools")]
        assert [p["model"] for p in turns] == ["test-model", "test-model-2"]
        assert "First question" in json.dumps(turns[-1])  # history carries over to the new model

        assert client.post("/ai/model", headers=headers, json={"model": "not-listed"}).status_code == 422
        assert client.post("/ai/model", json={"model": "test-model"}).status_code == 401


def test_model_switch_is_rejected_while_a_turn_runs(tmp_path):
    with create_client(tmp_path, Provider()) as client:
        headers = connect(client)
        conn = client.app.state.ai.connections[headers["Authorization"][7:]]
        conn.task = SimpleNamespace(done=lambda: False)
        response = client.post("/ai/model", headers=headers, json={"model": "test-model-2"})
        assert response.status_code == 409
        assert conn.model == "test-model"
        conn.task = None


def test_openai_catalog_hides_non_chat_models_and_prefers_default():
    from builder_api.ai.providers import chat_models, default_model

    catalog = [{"id": i, "label": i} for i in
               ["gpt-4o", "gpt-5", "gpt-5-mini", "text-embedding-3-large", "whisper-1", "gpt-image-1", "tts-1"]]
    assert [m["id"] for m in chat_models("openai", catalog)] == ["gpt-4o", "gpt-5", "gpt-5-mini"]
    assert default_model("openai", catalog) == "gpt-5"
    claude = [{"id": i, "label": i} for i in ["claude-haiku-9", "claude-opus-9", "claude-sonnet-9"]]
    assert default_model("anthropic", claude) == "claude-opus-9"
    assert default_model("anthropic", []) == ""


def test_private_handles_cannot_be_moved_or_embedded():
    projection = Projection(["xy"])
    safe = projection.graph({"inputs": {"key": {"default": "xy"}}, "targets": {"name": "Alice"}})
    handle = safe["inputs"]["key"]["default"]
    assert projection.restore(safe)["inputs"]["key"]["default"] == "xy"
    with pytest.raises(ValueError):
        projection.restore({"url": handle})
    with pytest.raises(ValueError):
        projection.restore({"code": "return '" + handle + "';"})
    assert "Alice" not in json.dumps(safe)
    assert "xy" not in projection.text("xy")


def test_code_is_readable_with_inline_handles_bound_to_its_field():
    source = "const LIMIT = args.limit || 300000;\nconst who = 'alice@example.test';\nreturn token_xy + LIMIT;"
    projection = Projection(["token_xy"])
    graph = {"scripts": {"parse": source}, "nodes": {"a": {"intent": "0", "actions": [
        {"script": {"code": "return 1;"}}, {"evaluate": {"script": "return document.title;"}}]}}}
    safe = projection.graph(graph)
    code = safe["scripts"]["parse"]
    assert "args.limit" in code and "300000" not in code and "alice@" not in code and "token_xy" not in code
    assert code.count("__private_") == 3 and projection.text(code) == code
    assert safe["nodes"]["a"]["actions"][0]["script"]["code"] == "return 1;"
    assert safe["nodes"]["a"]["actions"][1]["evaluate"]["script"] == "return document.title;"
    assert projection.restore(safe) == graph
    edited = code.replace("return", "const x = 1;\nreturn")
    assert projection.restore({"scripts": {"parse": edited}})["scripts"]["parse"] == source.replace("return", "const x = 1;\nreturn")
    token = next(t for t in projection.inline if projection.hidden[t][1] == "300000")
    with pytest.raises(ValueError, match="code field where they appeared"):
        projection.restore({"scripts": {"other": f"return {token};"}})
    with pytest.raises(ValueError, match="Masked text"):
        projection.restore({"scripts": {"parse": "return '[private]';"}})
    # A hidden one-character literal must not garble every later text ("0" -> [private]).
    assert projection.text("timeout_ms: 1000") == "timeout_ms: 1000"


def test_assistant_reads_and_fixes_existing_code_in_place(tmp_path):
    script = ("const LIMIT = args.limit || 300000;\nconst owner = 'alice@example.test';\n"
              "const title = document.title;\nreturn {title, owner, limit: LIMIT};")
    graph = GRAPH.replace("start: start\n", "start: start\nscripts:\n  parse: " + json.dumps(script) + "\n")
    fixed = "const title = document.querySelector('td.infraEventoDescricao')?.innerText.trim() ?? null;"
    provider = Provider(calls=[
        ("read_context", {"resource": "scripts/parse", "path": "", "lines": ""}),
        ("propose_edits", {"edits_json": json.dumps([{"op": "replace_text", "path": ["scripts", "parse"],
                                                      "old": "const title = document.title;", "new": fixed}])}),
    ])
    with create_client(tmp_path, provider) as client:
        (tmp_path / "test.yaml").write_text(graph, encoding="utf-8")
        headers = connect(client)
        events = turn(client, headers, message="Fix the title in parse")
        results = [json.loads(e["result"]) for e in events if e["kind"] == "tool_end"]
        assert "document.title" in results[0]["content"] and "proposal" in results[1], results
        sent = json.dumps(provider.payloads)
        assert "args.limit" in sent and "alice@example.test" not in sent and "300000" not in sent
        response = client.post(f'/ai/proposals/{results[1]["proposal"]}/apply', headers=headers, json={})
        assert response.status_code == 200, response.text
        saved = Workflow.from_yaml_text((tmp_path / "test.yaml").read_text(encoding="utf-8")).scripts["parse"]
        assert saved == script.replace("const title = document.title;", fixed)


def test_ai_cannot_downgrade_protection():
    before = yaml.safe_load(GRAPH)
    after = yaml.safe_load(GRAPH)
    after["inputs"]["account_password"]["type"] = "string"
    with pytest.raises(ValueError, match="Secure"):
        Harness.check_protection(before, after)
    after = yaml.safe_load(GRAPH)
    after["inputs"]["person"]["pii"] = False
    with pytest.raises(ValueError, match="PII"):
        Harness.check_protection(before, after)


def test_full_docs_schema_and_synthetic_extractor(tmp_path):
    provider = Provider(calls=[("read_context", {"resource": "requests-and-2fa.md", "path": "", "lines": ""}),
                               ("list_context", {"category": "schema", "query": "RequestSpec", "offset": "0"}),
                               ("test_extractor", {"sample_json": '{"data":{"otp":"123456"}}', "path": "data.otp"})])
    with create_client(tmp_path, provider) as client:
        headers = connect(client)
        events = turn(client, headers)
        assert events[-1]["kind"] == "done"
        payload = json.dumps(provider.payloads[-1])
        assert "Requests, authentication" in payload and "RequestSpec" in payload
        outputs = [json.loads(item['output']) for item in provider.payloads[-1]['input'] if item.get('type') == 'function_call_output']
        assert any(item.get('found') is True for item in outputs)


LONG_SCRIPT = "const MAX = 10;\n" + "// filler\n" * 90 + "return MAX;"


def test_long_code_is_edited_by_unique_replacement_not_resent(tmp_path):
    provider = Provider(calls=[
        ("propose_edits", {"edits_json": json.dumps([{"op": "set", "path": ["scripts", "big"], "value": LONG_SCRIPT}])}),
        ("propose_edits", {"edits_json": json.dumps([{"op": "replace_text", "path": ["scripts", "big"],
                                                      "old": "// filler", "new": "//"}])}),
        ("propose_edits", {"edits_json": json.dumps([{"op": "replace_text", "path": ["scripts", "big"],
                                                      "old": "const MAX = 10;", "new": "const MAX = 20;"}])}),
        ("propose_edits", {"edits_json": json.dumps([{"op": "replace_text", "path": ["inputs", "person", "default"],
                                                      "old": "Alice", "new": "Bob"}])}),
    ])
    with create_client(tmp_path, provider) as client:
        headers = connect(client)
        events = turn(client, headers)
        results = [json.loads(e["result"]) for e in events if e["kind"] == "tool_end"]
        assert "occurs 90 times" in results[1]["error"]
        assert results[2]["proposal"] == results[0]["proposal"]
        assert "private handle" in results[3]["error"]
        response = client.post(f'/ai/proposals/{results[0]["proposal"]}/apply', headers=headers, json={})
        assert response.status_code == 200, response.text
        wf = Workflow.from_yaml_text(response.json()["view"]["yaml"])
        assert wf.scripts["big"] == LONG_SCRIPT.replace("MAX = 10", "MAX = 20")
        assert wf.inputs["person"].default == "Alice Private"


def test_anthropic_prompt_prefix_is_static_and_cached(tmp_path):
    provider = Provider("anthropic", calls=[])
    with create_client(tmp_path, provider) as client:
        turn(client, connect(client, "anthropic"))
    payload = provider.payloads[-1]
    assert payload["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "selection_stats" not in json.dumps(payload["system"])
    task = payload["messages"][-1]["content"][-1]
    assert task["cache_control"] == {"type": "ephemeral"}
    assert task["text"].startswith("Context (selected view") and task["text"].endswith("Task:\nAdd an OTP request")


def test_context_preselection_is_opt_in(monkeypatch):
    from builder_api.config import settings

    assert Harness.triage_choice("anthropic", "") == "off"
    assert Harness.triage_choice("anthropic", "auto") == settings.ai_triage_anthropic_model
    assert Harness.triage_choice("openai", "custom-model") == "custom-model"
    monkeypatch.setattr(settings, "ai_triage_auto", True)
    assert Harness.triage_choice("openai", "") == settings.ai_triage_openai_model


@pytest.mark.parametrize("provider_name", ["openai", "anthropic"])
def test_tool_timeline_has_correlated_results_and_masks_private_values(tmp_path, provider_name):
    provider = Provider(provider_name, calls=[
        ("test_script", {"code": "return args.value;", "args_json": '{"value":"provider-secret"}'}),
        ("read_context", {"resource": "missing.md", "path": "", "lines": ""}),
    ])
    with create_client(tmp_path, provider) as client:
        events = turn(client, connect(client, provider_name))
    starts = [event for event in events if event["kind"] == "tool_start"]
    ends = [event for event in events if event["kind"] == "tool_end"]
    assert [event["tool_id"] for event in starts] == [event["tool_id"] for event in ends]
    assert len(starts) == len(ends) == 2
    assert [event["ok"] for event in ends] == [True, False]
    assert "Unknown resource" in ends[1]["result"]
    assert "[private]" in starts[0]["arguments"] and "[private]" in ends[0]["result"]
    assert "provider-secret" not in json.dumps(events)
    assert all(event["duration_ms"] >= 0 for event in ends)
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
    assert [event["elapsed_ms"] for event in events] == sorted(event["elapsed_ms"] for event in events)
    assert len([event for event in events if event["kind"] == "model_done"]) == 3
    assert events[-1]["kind"] == "done" and events[-1]["reason"] == "complete"


async def test_tool_start_is_emitted_before_slow_tool_finishes(tmp_path):
    provider = Provider(calls=[("redetect", {})])
    with create_client(tmp_path, provider) as client:
        headers = connect(client)
        harness = client.app.state.ai
        _, live = make_live(tmp_path)
        live.session.last_state = PageState(url="about:blank", portal="example", route="blank")
        harness.manager.sessions[live.id] = live
        entered, release = asyncio.Event(), asyncio.Event()

        async def observe(_sid):
            entered.set()
            await release.wait()

        harness.manager.observe = observe
        events = []

        async def emit(event):
            events.append(event)

        task = asyncio.create_task(harness.turn(harness.connection(headers["Authorization"][7:]),
                                               "test", live.id, "Observe", {}, [], emit))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            assert events[-1]["kind"] == "tool_start"
            assert events[-1]["name"] == "redetect"
            assert not task.done()
        finally:
            release.set()
            await asyncio.wait_for(task, 2)
        assert any(event["kind"] == "tool_end" and event["ok"] for event in events)


def test_provider_errors_do_not_echo_keys_or_response_bodies(tmp_path):
    def fail(request):
        return httpx.Response(401, json={"error": "provider-secret"})
    with create_client(tmp_path, fail) as client:
        response = client.post("/ai/connect", json={"provider": "openai", "model": "m", "key": "provider-secret"})
        assert response.status_code == 422
        assert "provider-secret" not in response.text


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_model_catalog_uses_provider_auth_and_no_prompt(tmp_path, provider):
    calls = []

    def catalog(request):
        calls.append(request)
        assert request.method == "GET" and request.url.path == "/v1/models"
        assert not request.content
        if provider == "openai":
            assert request.headers["authorization"] == "Bearer catalog-key"
            assert request.url.host == "api.openai.com"
        else:
            assert request.headers["x-api-key"] == "catalog-key"
            assert request.url.host == "api.anthropic.com"
        return httpx.Response(200, json={"data": [{"id": "model-b", "display_name": "Model B"}, {"id": "model-a"}], "has_more": False})

    with create_client(tmp_path, catalog) as client:
        response = client.post("/ai/models", json={"provider": provider, "key": "catalog-key"})
        assert response.status_code == 200
        assert response.json()["models"] == [{"id": "model-a", "label": "model-a"}, {"id": "model-b", "label": "Model B"}]
        assert "catalog-key" not in response.text and response.headers["cache-control"] == "no-store"
        assert not client.app.state.ai.connections
        assert len(calls) == 1


def test_anthropic_model_catalog_paginates_and_uses_server_key(tmp_path, monkeypatch):
    from pydantic import SecretStr

    from builder_api.config import settings

    monkeypatch.setattr(settings, "ai_anthropic_key", SecretStr("server-secret"))

    def catalog(request):
        assert request.headers["x-api-key"] == "server-secret"
        if request.url.params.get("after_id") == "first":
            return httpx.Response(200, json={"data": [{"id": "second"}], "has_more": False})
        return httpx.Response(200, json={"data": [{"id": "first"}], "has_more": True, "last_id": "first"})

    with create_client(tmp_path, catalog) as client:
        response = client.post("/ai/models", json={"provider": "anthropic"})
        assert response.status_code == 200
        assert [m["id"] for m in response.json()["models"]] == ["first", "second"]
        assert "server-secret" not in response.text


def test_model_catalog_error_and_origin_are_safe(tmp_path):
    with create_client(tmp_path, lambda request: httpx.Response(401, json={"error": "sensitive-key"})) as client:
        response = client.post("/ai/models", json={"provider": "openai", "key": "sensitive-key"})
        assert response.status_code == 422 and "sensitive-key" not in response.text
        response = client.post("/ai/models", headers={"Origin": "https://attacker.test"},
                               json={"provider": "openai", "key": "sensitive-key"})
        assert response.status_code == 403


def make_live(tmp_path):
    workflow = Workflow.from_yaml_text(GRAPH)
    (tmp_path / "test.yaml").write_text(GRAPH, encoding="utf-8")
    observer = SimpleNamespace(close=AsyncMock())
    session = GraphSession(compile_workflow(workflow), None, session_inputs(workflow, {"account_password": "short"}),
                           observer=observer, redactor=Redactor())
    session.status = "paused"
    live = LiveSession("run", "test", tmp_path / "test.yaml", {}, None, None, session)
    manager = SessionManager(graphs_dir=tmp_path, headed=False)
    manager.sessions[live.id] = live
    return manager, live


def test_page_projection_withholds_unknown_pii(tmp_path):
    _, live = make_live(tmp_path)
    live.session.last_state = PageState(url="https://example.test/Alice?token=secret", portal="example", route="/Alice",
        title="Alice Private", alerts=["OTP 123456"], elements=[Element(id="e1", role="textbox", tag="input",
        name="Alice", value="xy", dom_id="ssn-123456", nearby=["Private"], bbox={"x": 0, "y": 0, "w": 1, "h": 1})])
    text = json.dumps(page_context(live))
    for value in ["Alice", "123456", "secret", "xy", "ssn-"]:
        assert value not in text
    assert '"handle": "e1"' in text


async def test_mid_run_patch_waits_for_boundary_and_accepts_local_new_input(tmp_path):
    manager, live = make_live(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()

    async def step():
        live.session.status = "running"
        entered.set()
        await release.wait()
        live.session.vars["already_done"] = 1
        live.session.visits["start"] = 1
        live.session.node = "done"
        live.session.status = "paused"
        return StepResult("start", "done", "paused", None, [])

    live.session.step = step
    running = asyncio.create_task(manager.run(live.id, max_steps=1))
    await entered.wait()
    data = yaml.safe_load(GRAPH)
    data["inputs"]["api_token"] = {"type": "secure"}
    data["requests"] = {"otp": EDITS[0]["value"]}
    patched = Workflow.model_validate(data)
    summary = await manager.patch(live.id, patched, {"api_token": "new-private-token"})
    assert summary["patch_pending"]
    assert "otp" not in live.session.workflow.requests
    release.set()
    await running
    assert live.session.node == "done" and live.session.visits["start"] == 1
    assert live.session.vars["already_done"] == 1
    assert "otp" in live.session.workflow.requests
    assert isinstance(live.session.inputs["api_token"], Secret)
    assert live.session.inputs["api_token"].reveal() == "new-private-token"
    assert "new-private-token" not in json.dumps(live.summary())


async def test_incompatible_patch_preserves_runtime(tmp_path):
    manager, live = make_live(tmp_path)
    data = yaml.safe_load(GRAPH)
    data["inputs"]["new_token"] = {"type": "secure"}
    with pytest.raises(ValueError, match="required inputs"):
        await manager.patch(live.id, Workflow.model_validate(data))
    assert "new_token" not in live.session.workflow.inputs
    assert live.session.node == "start"


@pytest.mark.parametrize("kind", ["password", "secure"])
def test_secure_input_alias(kind):
    wf = Workflow.from_yaml_text(GRAPH.replace("type: secure", f"type: {kind}"))
    assert isinstance(session_inputs(wf, {"account_password": "value"})["account_password"], Secret)


def test_live_redetect_and_graph_authoring_do_not_send_runtime_data(tmp_path):
    provider = Provider(calls=[("redetect", {}), ("propose_edits", {"edits_json": json.dumps(EDITS)})])
    with create_client(tmp_path, provider) as client:
        _, live = make_live(tmp_path)
        state = PageState(url="https://example.test/?token=PRIVATE", portal="example", route="/", title="Private Person")
        live.session.observer.observe = AsyncMock(return_value=state)
        live.session.vars = {"encoded_secret": "YXNkZmdoc2VjcmV0", "person": "Private Person"}
        client.app.state.manager.sessions[live.id] = live
        headers = connect(client)
        events = turn(client, headers, session_id=live.id)
        live.session.observer.observe.assert_awaited_once()
        proposal = next(e["proposal"] for e in events if e["kind"] == "proposal")
        response = client.post(f'/ai/proposals/{proposal["id"]}/apply', headers=headers,
                               json={"session_id": live.id, "patch": True})
        assert response.status_code == 200, response.text
        assert "otp" in live.session.workflow.requests
        assert live.session.node == "start"
        sent = json.dumps(provider.payloads)
        assert "Private Person" not in sent and "YXNkZmdoc2VjcmV0" not in sent and "?token=PRIVATE" not in sent


@pytest.mark.parametrize("stale", [False, True])
def test_live_action_is_reviewed_once_and_rejects_stale_state(tmp_path, stale):
    provider = Provider(calls=[("propose_action", {"action": "step", "name": ""})])
    with create_client(tmp_path, provider) as client:
        _, live = make_live(tmp_path)
        live.session.step = AsyncMock(return_value=StepResult("start", None, "paused", None, []))
        client.app.state.manager.sessions[live.id] = live
        headers = connect(client)
        events = turn(client, headers, session_id=live.id)
        proposal = next(e["proposal"] for e in events if e["kind"] == "proposal")
        live.session.step.assert_not_awaited()
        if stale:
            live.session.visits["start"] += 1
        response = client.post(f'/ai/proposals/{proposal["id"]}/apply', headers=headers, json={})
        assert response.status_code == (409 if stale else 200), response.text
        assert live.session.step.await_count == (0 if stale else 1)
        if not stale:
            assert client.post(f'/ai/proposals/{proposal["id"]}/apply', headers=headers, json={}).status_code == 404


def test_synthetic_code_tool_has_no_session_inputs(tmp_path):
    provider = Provider(calls=[("test_script", {"code": "return {keys: Object.keys(inputs), answer: args.n * 2};",
                                               "args_json": '{"n": 21}'})])
    with create_client(tmp_path, provider) as client:
        headers = connect(client)
        events = turn(client, headers, values={"account_password": "NEVER-SEND-THIS"})
        assert events[-1]["kind"] == "done"
        payload = json.dumps(provider.payloads[-1])
        outputs = [json.loads(item['output']) for item in provider.payloads[-1]['input'] if item.get('type') == 'function_call_output']
        assert any(item.get('value') == {'keys': [], 'answer': 42} for item in outputs)
        assert "NEVER-SEND-THIS" not in payload


def test_reviewed_action_reobserves_and_rejects_a_changed_page(tmp_path):
    provider = Provider(calls=[("propose_action", {"action": "step", "name": ""})])
    with create_client(tmp_path, provider) as client:
        _, live = make_live(tmp_path)
        live.session.last_state = PageState(url="https://example.test/first", portal="example", route="/first")
        live.session.observer.observe = AsyncMock(return_value=PageState(
            url="https://example.test/changed", portal="example", route="/changed"))
        live.session.step = AsyncMock()
        client.app.state.manager.sessions[live.id] = live
        headers = connect(client)
        proposal = next(e["proposal"] for e in turn(client, headers, session_id=live.id) if e["kind"] == "proposal")
        response = client.post(f'/ai/proposals/{proposal["id"]}/apply', headers=headers, json={})
        assert response.status_code == 409 and "Page changed" in response.text
        live.session.step.assert_not_awaited()


async def test_cancel_interrupts_provider_request_and_connection_cleanup(tmp_path, monkeypatch):
    (tmp_path / "test.yaml").write_text(GRAPH, encoding="utf-8")
    app = create_app(graphs_dir=tmp_path, headed=False)
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def slow(*args, **kwargs):
        if not args[5]:  # connection test has no tools
            return {"text": "ready"}
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr("builder_api.ai.service.complete", slow)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        result = await client.post("/ai/connect", json={"provider": "openai", "model": "m", "key": "secret"})
        headers = {"Authorization": "Bearer " + result.json()["token"]}
        task = asyncio.create_task(client.post("/ai/turn", headers=headers,
                                             json={"graph": "test", "message": "Add a request"}))
        await asyncio.wait_for(entered.wait(), 2)
        response = await client.post("/ai/cancel", headers=headers)
        assert response.status_code == 204
        await asyncio.wait_for(task, 2)
        assert cancelled.is_set()
        conn = next(iter(app.state.ai.connections.values()))
        await client.delete("/ai/connection", headers=headers)
        assert not conn.key and not app.state.ai.connections


async def test_queued_patch_rechecks_node_at_boundary_without_reset(tmp_path):
    manager, live = make_live(tmp_path)
    data = yaml.safe_load(GRAPH)
    # Current start exists at queue time; the run then advances to a node the patch removes.
    data["nodes"]["finish"] = data["nodes"].pop("done")
    data["nodes"]["start"]["transitions"] = [{"to": "finish"}]
    live.busy = True
    await manager.patch(live.id, Workflow.model_validate(data))
    live.session.node = "done"
    manager._apply_pending(live)
    assert live.patch_error and live.session.node == "done"
    assert "done" in live.session.workflow.nodes


@pytest.mark.parametrize("first", ["tool", "final"])
def test_message_sent_mid_turn_reaches_the_model_before_the_turn_ends(tmp_path, first):
    base = Provider("anthropic", calls=[("get_session", {})] if first == "tool" else [])
    state = {}

    def provider(request):
        data = json.loads(request.content) if request.method == "POST" else {}
        if data.get("tools") and "steered" not in state:
            # The user types while the model is still answering its first request.
            state["steered"] = True
            harness = state["client"].app.state.ai
            harness.steer(next(iter(harness.connections.values())), "Also check Bob Private's node")
        return base(request)

    with create_client(tmp_path, provider) as client:
        state["client"] = client
        headers = connect(client, "anthropic")
        events = turn(client, headers, values={"person": "Bob Private"})
        assert [e["text"] for e in events if e["kind"] == "steer"] == ["Also check [private]'s node"]
        assert events[-1]["kind"] == "done"
        second = base.payloads[-1]["messages"][-1]["content"]
        assert any("Also check [private]'s node" in block.get("text", "") for block in second)
        assert "Bob Private" not in json.dumps(base.payloads)
        if first == "tool":
            assert second[0]["type"] == "tool_result"
        history = client.app.state.ai.connections[headers["Authorization"][7:]].history["test"]
        assert "Also check [private]'s node" in history[-2]["content"]


def test_steering_without_a_running_turn_is_rejected(tmp_path):
    with create_client(tmp_path, Provider()) as client:
        headers = connect(client)
        response = client.post("/ai/steer", headers=headers, json={"message": "Stop and do X"})
        assert response.status_code == 409 and "No AI turn is running" in response.text


async def test_interrupted_turn_is_remembered_for_the_next_turn(tmp_path, monkeypatch):
    (tmp_path / "test.yaml").write_text(GRAPH, encoding="utf-8")
    app = create_app(graphs_dir=tmp_path, headed=False)
    harness = app.state.ai
    started = asyncio.Event()

    async def slow(*args, on_text=None, **kwargs):
        if not args[5]:
            return {"text": "ready"}
        await on_text("I will rewrite the start node ")
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("builder_api.ai.service.complete", slow)
    monkeypatch.setattr("builder_api.ai.service.model_output_limit", AsyncMock(return_value=4096))
    monkeypatch.setattr("builder_api.ai.service.list_models", AsyncMock(return_value=[{"id": "m", "label": "m"}]))
    token = await harness.connect("openai", "secret", "m")
    conn = harness.connection(token)

    async def emit(event):
        pass

    task = asyncio.create_task(harness.turn(conn, "test", None, "Rewrite start", {}, [], emit))
    await asyncio.wait_for(started.wait(), 2)
    harness.steer(SimpleNamespace(task=task, inbox=conn.inbox), "late note")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    user, assistant = conn.history["test"]
    assert user == {"role": "user", "content": "Rewrite start"}
    assert assistant["content"].startswith("I will rewrite the start node")
    assert "Interrupted by the user" in assistant["content"]
    assert conn.inbox == []
