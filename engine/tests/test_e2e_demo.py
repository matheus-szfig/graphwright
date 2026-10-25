"""End to end: the example graphs against the Acme Records demo site in a real,
headless Chromium. Opt-in (GW_BROWSER_TESTS=1). This is the reproducible version of
"does the engine actually work": login with password + TOTP typed at the moment of
use, a code relayed over HTTP with polling, extraction of fields and tables, and a
business outcome — with no secret in the event stream.

    GW_BROWSER_TESTS=1 uv run pytest tests/test_e2e_demo.py -v
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import GRAPHS, browser_only

from graphwright.browser.humanize import StealthBudget
from graphwright.catalog import build_inputs, load_graph
from graphwright.credentials.local import FileProvider
from graphwright.engine.requests import RequestServices
from graphwright.engine.runtime import GraphSession
from graphwright.engine.secrets import Redactor

pytestmark = browser_only

FAST = StealthBudget(scale=0.05, min_delay_ms=1)


@pytest.fixture
def credentials_file(tmp_path: Path, demo_site) -> Path:
    site, _ = demo_site
    path = tmp_path / "credentials.yaml"
    path.write_text(
        "demo-user:\n"
        "  kind: password\n"
        "  portal: acme-records\n"
        f"  totp_secret: {site.DEMO_TOTP_SEED}\n"
        "  values:\n"
        f"    username: {site.DEMO_USER}\n"
        f"    password: {site.DEMO_PASSWORD}\n"
        f"    relay_token: {site.DEMO_RELAY_TOKEN}\n"
        "wrong-password:\n"
        "  values: {username: demo, password: not-the-password, relay_token: nope}\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
async def page():
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 900}, locale="en-US")
        yield await context.new_page()
        await browser.close()


async def run(graph_name: str, page, provided: dict, credential=None) -> tuple[GraphSession, list[dict]]:
    graph = load_graph(graph_name, GRAPHS)
    inputs = build_inputs(graph.workflow, provided, credential)
    redactor = Redactor(credential.plaintexts if credential else ())
    events: list[dict] = []
    session = GraphSession(
        graph, page, inputs, budget=FAST, redactor=redactor,
        services=RequestServices(totp=credential.totp if credential else None),
        on_event=lambda ev: events.append(ev.as_dict()),
    )
    await session.run()
    return session, events


async def test_record_search_extracts_fields_and_events(page, demo_site) -> None:
    _, base = demo_site
    session, events = await run("acme_record_search", page, {"base_url": base, "reference": "acm-2024-000123"})

    assert session.status == "done", session.failure
    record = session.result["outputs"]["record"]
    assert session.result["terminal"] == "success"
    assert record["reference"] == "ACM-2024-000123"  # typed upper-cased by the shaper
    assert record["title"] == "Warehouse expansion permit"
    assert record["status"] == "Active" and record["owner"] == "Jane Doe"
    assert [r["Event"] for r in record["events"]] == ["Application received", "Site inspection passed", "Permit issued"]
    assert record["schema"] == "acme_record_v1"
    assert any(e["kind"] == "extract" for e in events)


async def test_record_search_not_found_is_an_outcome(page, demo_site) -> None:
    _, base = demo_site
    session, _ = await run("acme_record_search", page,
                           {"base_url": base, "reference": "ACM-2024-000123", "record_type": "License"})
    assert session.status == "done"
    assert session.result["terminal"] == "outcome" and session.result["status"] == "not_found"


async def test_login_types_a_totp_fetched_at_the_moment_of_use(page, demo_site, credentials_file) -> None:
    site, base = demo_site
    credential = FileProvider(credentials_file).redeem("demo-user", run_id="e2e")
    session, events = await run("acme_account_login", page, {"base_url": base}, credential)

    assert session.status == "done", session.failure
    assert session.result["terminal"] == "success"
    account = session.result["outputs"]["account"]
    assert account["welcome"] == "Welcome, Demo User" and account["user"] == "demo"
    assert len(account["activity"]) == 2
    # The code was produced by the request right before the fill, after sign-in.
    kinds = [(e["kind"], e.get("node")) for e in events]
    assert kinds.index(("request", "verify")) > kinds.index(("move", "sign_in"))
    request = next(e for e in events if e["kind"] == "request")
    assert request["ok"] and request["values"] == {"code": "***"}
    blob = json.dumps(events)
    assert site.DEMO_PASSWORD not in blob and site.DEMO_TOTP_SEED not in blob


async def test_login_with_a_wrong_password_ends_in_a_failure_terminal(page, demo_site, credentials_file) -> None:
    _, base = demo_site
    credential = FileProvider(credentials_file).redeem("wrong-password", run_id="e2e")
    session, events = await run("acme_account_login", page, {"base_url": base}, credential)
    assert session.status == "done"
    assert session.result["terminal"] == "failure" and session.result["status"] == "invalid_credentials"
    assert "not-the-password" not in json.dumps(events)


async def test_relay_code_is_polled_until_it_arrives(page, demo_site, credentials_file) -> None:
    site, base = demo_site
    credential = FileProvider(credentials_file).redeem("demo-user", run_id="e2e")
    session, events = await run("acme_login_relay_otp", page, {"base_url": base}, credential)

    assert session.status == "done", session.failure
    assert session.result["terminal"] == "success"
    otp = next(e for e in events if e["kind"] == "request" and e["name"] == "otp")
    assert [a["outcome"] for a in otp["attempts"]] == ["retry", "retry", "ok"]  # two empty polls, then the code
    assert otp["attempts"][0]["missing"] == ["code"]
    assert otp["attempts"][0]["request"]["headers"]["Authorization"] == "***"
    blob = json.dumps(events)
    assert site.DEMO_RELAY_TOKEN not in blob and site.DEMO_PASSWORD not in blob


async def test_relay_failure_routes_to_a_failure_terminal(page, demo_site, credentials_file, tmp_path) -> None:
    _, base = demo_site
    good = FileProvider(credentials_file).redeem("demo-user", run_id="e2e")
    # Valid login, but a relay token the API rejects: otp_request fails, so does otp.
    from dataclasses import replace

    credential = replace(good, values={**good.values, "relay_token": "revoked-token"})
    graph = load_graph("acme_login_relay_otp", GRAPHS)
    inputs = build_inputs(graph.workflow, {"base_url": base}, credential)
    events: list[dict] = []
    session = GraphSession(graph, page, inputs, budget=FAST, redactor=Redactor(credential.plaintexts),
                           services=RequestServices(), on_event=lambda ev: events.append(ev.as_dict()))
    await session.run()

    assert session.status == "done", session.failure
    assert session.result["status"] == "otp_unavailable"
    assert "revoked-token" not in json.dumps(events)


STUCK_AFTER_PASSWORD = """
workflow: stuck_login
portal: acme-records
entry_url: "{base_url}/login"
inputs:
  base_url: {type: url, pii: false}
  password: {type: password, credential: password}
start: sign_in
nodes:
  sign_in:
    recognize: {visible: password_field}
    actions:
      - fill: {target: password_field, value: "{password}"}
    timeout_s: 1
    transitions:
      - {to: done, when: {url: "re:^/never$"}}
  done: {terminal: success}
targets:
  password_field:
    description: the password field
    strategies: [{role: textbox, name: "re:^password$"}]
"""


async def test_failure_evidence_masks_the_typed_secret(page, demo_site, credentials_file, monkeypatch) -> None:
    """A run stuck on the page right after typing a password: the evidence (real
    screenshot, HTML, PageState) must not carry the password, and the field on the
    page shows bullets by the time the screenshot is taken."""
    import uuid

    from graphwright import evidence
    from graphwright.engine import Workflow, compile_workflow

    _, base = demo_site
    credential = FileProvider(credentials_file).redeem("demo-user", run_id="e2e")
    graph = compile_workflow(Workflow.from_yaml_text(STUCK_AFTER_PASSWORD))
    redactor = Redactor(credential.plaintexts)
    session = GraphSession(graph, page, build_inputs(graph.workflow, {"base_url": base}, credential),
                           budget=FAST, redactor=redactor)
    await session.run()
    assert session.status == "needs_repair" and session.failure["type"] == "NoTransition"

    stored: dict[str, bytes] = {}

    async def put_object(bucket, key, body, *, content_type):
        stored[key.rsplit("/", 1)[-1]] = body

    async def insert_artifact(**kw):
        return uuid.uuid4()

    monkeypatch.setattr(evidence.blobs, "put_object", put_object)
    monkeypatch.setattr(evidence.db, "insert_artifact", insert_artifact)
    artifacts = await evidence.capture(session, uuid.UUID(int=1), 1, reason="failed", failure=session.failure)

    assert {a["kind"] for a in artifacts} == {"screenshot", "html", "pagestate", "context"}
    assert stored["screenshot.jpg"][:3] == b"\xff\xd8\xff"  # a real JPEG
    password = credential.values["password"]
    for name, body in stored.items():
        assert password.encode() not in body, name
    field_value = await page.evaluate("document.querySelector('#password').value")
    assert field_value and set(field_value) == {"•"}  # masked before the picture
    state = json.loads(stored["pagestate.json"])
    pw = next(e for e in state["elements"] if e.get("dom_id") == "password")
    assert pw["value"] == "***"
    context = json.loads(stored["context.json"])
    assert context["failure"]["type"] == "NoTransition" and context["node"] == "sign_in"
