"""GraphSession tests.

The first group runs without a browser: a scripted FakeObserver serves goldens and
a FakePage swallows input, so the loop (recognize → actions → transitions →
postconditions, re-sync, loop guards, failure routing, extraction, lazy requests)
is exercised in CI. The last test (opt-in, GW_BROWSER_TESTS=1) runs the real
Observer + Humanizer against the legacy-portal fixture end to end.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from conftest import FIXTURES, GOLDENS, GRAPHS, browser_only

from graphwright.browser.humanize import StealthBudget
from graphwright.catalog import build_inputs, load_graph
from graphwright.credentials import Credential
from graphwright.engine import Workflow, compile_workflow
from graphwright.engine.requests import RequestServices
from graphwright.engine.runtime import GraphSession
from graphwright.engine.schema import Transition
from graphwright.engine.secrets import Redactor, Secret, SecretInputs
from graphwright.observe.pagestate import PageState


def golden(name: str) -> PageState:
    return PageState.model_validate(json.loads((GOLDENS / f"{name}.json").read_text(encoding="utf-8")))


# --- fakes -----------------------------------------------------------------------------


class FakeObserver:
    """Serves PageStates from a script; `wait_for_change()` moves to the next one.
    `texts` (css → text) and `tables` (table id → rows) answer the live-DOM reads."""

    def __init__(self, states: list[PageState], *, texts: dict[str, str] | None = None,
                 tables: dict[str, list[dict]] | None = None) -> None:
        self.states = states
        self.i = 0
        self.boxes: dict[str, dict] = {}
        self.selected: list[tuple[str, str]] = []
        self.texts = texts or {}
        self.tables = tables or {}

    async def observe(self) -> PageState:
        s = self.states[min(self.i, len(self.states) - 1)]
        s.signature = f"sig{self.i}"
        return s

    async def signature(self) -> str:
        return f"sig{self.i}"

    async def wait_for_change(self, baseline: str, timeout_s: float, **_) -> bool:
        if self.i < len(self.states) - 1:
            self.i += 1
            return True
        return False

    async def box(self, element_id: str):
        from graphwright.observe.observer import ElementBox

        over = self.boxes.get(element_id, {})
        return ElementBox(id=element_id, frame=0, x=100, y=100, w=120, h=24, visible=True,
                          in_viewport=True, occluded=over.get("occluded", False), disabled=over.get("disabled", False))

    async def query(self, selector: str, **_) -> list[str]:
        return [f"css:{selector}"] if selector in self.texts else []

    async def text_of(self, element_id: str) -> str | None:
        return self.texts.get(element_id.removeprefix("css:"))

    async def table_rows(self, table_id: str, *, max_rows: int = 500) -> dict | None:
        rows = self.tables.get(table_id)
        return {"headers": list(rows[0]) if rows else [], "rows": rows[:max_rows]} if rows is not None else None

    async def select_option(self, element_id: str, pattern: str) -> dict:
        self.selected.append((element_id, pattern))
        return {"ok": True, "text": pattern}

    async def scroll_into_view(self, element_id: str) -> bool:
        return True

    async def value_of(self, element_id: str) -> str | None:
        return None  # skip the typed-value check

    async def evaluate(self, body: str, args=None, *, world: str = "isolated", frame=0):
        # The example graph's code node counts the table rows on the page.
        self.evaluated = getattr(self, "evaluated", []) + [(body, world, frame)]
        return sum(len(rows) for rows in self.tables.values())


def fake_page() -> MagicMock:
    page = MagicMock(name="page")
    page.goto = AsyncMock()
    page.mouse = MagicMock()
    page.mouse.move = AsyncMock()
    page.mouse.down = AsyncMock()
    page.mouse.up = AsyncMock()
    page.mouse.wheel = AsyncMock()
    page.keyboard = MagicMock()
    page.keyboard.type = AsyncMock()
    page.keyboard.press = AsyncMock()
    return page


def typed(page: MagicMock) -> str:
    return "".join(c.args[0] for c in page.keyboard.type.await_args_list)


FAST = StealthBudget(scale=0.01, min_delay_ms=0.1)
DETAIL_TEXTS = {
    "h1": "Record ACM-2024-000123",
    "#record-title": "Warehouse expansion permit",
    "#record-status": "Active",
    "#record-owner": "Jane Doe",
}
EVENTS = [
    {"Date": "2024-03-02", "Event": "Application received", "By": "Front desk"},
    {"Date": "2024-04-01", "Event": "Permit issued", "By": "Licensing office"},
]


@pytest.fixture
def search_graph():
    return load_graph("acme_record_search", GRAPHS)


def search_session(graph, states, **observer_kw) -> tuple[GraphSession, FakeObserver, MagicMock]:
    observer = FakeObserver(states, **observer_kw)
    page = fake_page()
    inputs = build_inputs(graph.workflow, {"reference": "acm-2024-000123"}, None)
    session = GraphSession(graph, page, inputs, observer=observer, budget=FAST)
    session._entered = True  # skip entry_url navigation
    return session, observer, page


# --- the loop ---------------------------------------------------------------------------


async def test_full_run_through_the_search_graph_with_extraction(search_graph) -> None:
    session, observer, page = search_session(
        search_graph, [golden("acme_search_form"), golden("acme_record_detail")],
        texts=DETAIL_TEXTS, tables={"e5": EVENTS},
    )

    result = await session.run()

    assert result.status == "done", session.failure
    assert session.result["terminal"] == "success"
    record = session.result["outputs"]["record"]
    assert record == {
        "schema": "acme_record_v1", "reference": "ACM-2024-000123", "title": "Warehouse expansion permit",
        "status": "Active", "owner": "Jane Doe", "events": EVENTS,
    }
    # the code node: evaluate (page) counted the rows, script (sandbox) summarized
    assert session.result["outputs"]["summary"] == {
        "event_count": len(EVENTS), "rows_match": True,
        "last_event": f"{EVENTS[-1]['Date']}: {EVENTS[-1]['Event']}", "status": "active",
    }
    assert observer.evaluated[0][1:] == ("isolated", 0)
    kinds = [e.kind for e in session.events]
    assert kinds.count("move") == 3 and kinds[-1] == "terminal"
    extract = next(e for e in session.events if e.kind == "extract")
    assert extract.data["values"] == {"reference": "set", "title": "set", "status": "set", "owner": "set", "events": 2}
    # The select went through the isolated world; the fill typed the shaped value.
    assert observer.selected == [("e5", "Any type")]
    assert typed(page) == "ACM-2024-000123"
    assert page.mouse.down.await_count == 2  # focus the field + Search


async def test_not_found_is_an_outcome_terminal(search_graph) -> None:
    session, _, _ = search_session(search_graph, [golden("acme_search_form"), golden("acme_not_found")])
    await session.run()
    assert session.status == "done"
    assert session.result["terminal"] == "outcome" and session.result["status"] == "not_found"


async def test_required_extract_value_missing_is_structural(search_graph) -> None:
    session, _, _ = search_session(
        search_graph, [golden("acme_search_form"), golden("acme_record_detail")],
        texts=DETAIL_TEXTS, tables={},  # the events table is required
    )
    await session.run()
    assert session.status == "needs_repair"
    assert "required value(s) missing: ['events']" in session.failure["message"]


async def test_resync_when_the_page_is_not_the_expected_node(search_graph) -> None:
    session, _, _ = search_session(search_graph, [golden("acme_record_detail")])

    first = await session.step()
    assert first.node == "search_form" and first.next_node == "record_detail"
    resync = next(e for e in session.events if e.kind == "resync")
    assert resync.data["winner"] == "record_detail"
    assert resync.data["matches"]["search_form"] is False


async def test_unknown_page_stops_in_needs_repair_with_evidence(search_graph) -> None:
    unknown = golden("acme_search_form").model_copy(update={
        "url": "http://demo-site:8090/error", "route": "/error", "elements": [], "alerts": ["Internal error"],
    })
    session, _, _ = search_session(search_graph, [unknown])

    result = await session.step()
    assert result.status == "needs_repair"
    assert session.failure["type"] == "UnknownState" and session.failure["class"] == "structural"
    assert any(line.startswith("search_form:") for line in session.failure["details"])


async def test_no_transition_times_out_with_the_trace(search_graph) -> None:
    search_graph.workflow.nodes["search_form"].timeout_s = 0.05
    session, _, _ = search_session(search_graph, [golden("acme_search_form")])

    result = await session.step()
    assert result.status == "needs_repair"
    assert session.failure["type"] == "NoTransition"
    assert any("→ not_found: no" in line for line in session.failure["details"])


async def test_occluded_target_is_not_actionable(search_graph) -> None:
    session, observer, _ = search_session(search_graph, [golden("acme_search_form")])
    observer.boxes["e6"] = {"occluded": True}

    result = await session.step()
    assert result.status == "needs_repair"
    assert session.failure["type"] == "NotActionable"
    assert "covered by another element" in session.failure["message"]


async def test_loop_guard_and_reset_to_and_patch(search_graph) -> None:
    # Force a loop: a self-transition that always fires, placed before `→ done` so the
    # graph still lints (done stays statically reachable) but never gets there.
    wf = search_graph.workflow.model_copy(deep=True)
    wf.nodes["record_detail"].transitions.insert(0, Transition(to="record_detail"))
    wf.nodes["record_detail"].max_visits = 2
    session, _, _ = search_session(search_graph, [golden("acme_record_detail")], texts=DETAIL_TEXTS,
                                   tables={"e5": EVENTS})
    session.patch(wf)
    session.reset_to("record_detail")

    statuses = [(await session.step()).status for _ in range(3)]
    assert statuses[-1] == "needs_repair"
    assert session.failure["type"] == "LoopGuard"


# --- failure routing --------------------------------------------------------------------


def _with_failure_route(graph, route: str):
    wf = graph.workflow.model_copy(deep=True)
    wf.nodes["search_form"].on_fail = route
    if route not in ("fail", "repair"):
        wf.nodes[route] = wf.nodes["not_found"].model_copy(update={"terminal": "failure", "result": {"status": route}})
    return compile_workflow(wf)


async def test_on_fail_node_routes_the_error_as_data(search_graph) -> None:
    graph = _with_failure_route(search_graph, "search_broken")
    session, observer, _ = search_session(graph, [golden("acme_search_form")])
    observer.boxes["e6"] = {"occluded": True}

    first = await session.step()
    assert first.status == "paused" and first.next_node == "search_broken"
    assert session.vars["_error"]["type"] == "NotActionable"
    assert session.vars["_error"]["node"] == "search_form"
    assert any(e.kind == "on_fail" and e.data["to"] == "search_broken" for e in session.events)
    await session.step()
    assert session.status == "done" and session.result["status"] == "search_broken"
    assert session.result["terminal"] == "failure"


async def test_on_fail_fail_forces_failed_instead_of_repair(search_graph) -> None:
    graph = _with_failure_route(search_graph, "fail")
    session, observer, _ = search_session(graph, [golden("acme_search_form")])
    observer.boxes["e6"] = {"occluded": True}
    assert (await session.step()).status == "failed"


# --- entry_url and requests -------------------------------------------------------------


async def test_entry_url_is_a_template_and_the_event_masks_secrets() -> None:
    wf = Workflow.model_validate({
        "workflow": "t", "portal": "p", "start": "a", "entry_url": "{base_url}/start?k={token}",
        "inputs": {"base_url": {"pii": False}, "token": {}},
        "nodes": {"a": {"recognize": {"always": True}, "transitions": [{"to": "done"}]}, "done": {"terminal": "success"}},
    })
    page = fake_page()
    session = GraphSession(compile_workflow(wf), page, SecretInputs({"base_url": "http://h:1", "token": Secret("tok-999")}),
                           observer=FakeObserver([golden("acme_search_form")]), budget=FAST)
    await session.start()
    page.goto.assert_awaited_once_with("http://h:1/start?k=tok-999", wait_until="domcontentloaded")
    goto = next(e for e in session.events if e.kind == "goto")
    assert goto.data["url"] == "http://h:1/start?k=***"


async def test_lazy_request_fills_a_code_fetched_at_the_moment_of_use() -> None:
    graph = load_graph("acme_account_login", GRAPHS)
    calls: list[float] = []

    def provider_totp() -> str:
        calls.append(1)
        return "654321"

    cred = Credential(ref="demo", values={"username": "demo", "password": "demo-password"}, totp=provider_totp)
    redactor = Redactor(cred.plaintexts)
    page = fake_page()
    session = GraphSession(
        graph, page, build_inputs(graph.workflow, {}, cred),
        observer=FakeObserver([golden("acme_login"), golden("acme_verify"), golden("acme_account")],
                              texts={"h1": "Welcome, Demo User", "#account-user": "demo"}, tables={"e5": []}),
        budget=FAST, redactor=redactor, services=RequestServices(totp=cred.totp),
    )
    session._entered = True

    await session.step()  # sign_in
    assert calls == []  # nothing fetched before the verify page shows up
    await session.step()  # verify: the fill fetches the code
    assert calls == [1]
    assert typed(page).endswith("654321")
    events = [e.as_dict() for e in session.events]
    order = [e["kind"] for e in events if e["node"] == "verify"]
    assert order.index("request") < order.index("fill")
    fill = [e for e in events if e["kind"] == "fill" and e["node"] == "verify"][0]
    assert fill["value"] == "***"
    blob = json.dumps(events)
    assert "654321" not in blob and "demo-password" not in blob
    await session.run()
    assert session.status == "done" and session.result["terminal"] == "success"


async def test_lazy_request_failure_routes_through_on_fail() -> None:
    graph = load_graph("acme_account_login", GRAPHS)
    cred = Credential(ref="demo", values={"username": "demo", "password": "demo-password"})  # no TOTP seed
    session = GraphSession(graph, fake_page(), build_inputs(graph.workflow, {}, cred),
                           observer=FakeObserver([golden("acme_verify")]), budget=FAST)
    session._entered = True
    session.reset_to("verify")

    result = await session.step()
    assert result.next_node == "code_rejected"  # verify's on_fail
    assert session.vars["_error"]["class"] == "request"
    assert "no TOTP source" in session.vars["_error"]["message"]


# --- browser-backed, end to end ----------------------------------------------------------

LEGACY_GRAPH = r"""
workflow: legacy_fixture
portal: portal.test
entry_url: https://portal.test/cases/open.do
inputs: {case_number: {type: string, pii: false}}
outputs: {case: v1}
start: lookup_form
nodes:
  lookup_form:
    recognize: {all: [{url: "re:/cases/(open|search)\\.do"}, {visible: number}]}
    actions:
      - {select: {target: search_by, option: "Case number"}}
      - {fill: {target: number, value: "{case_number}", shape: upper}}
      - {click: {target: search}}
    transitions:
      - {when: {text: "Case details", in: headings}, to: detail}
  detail:
    recognize: {all: [{url: "/cases/"}, {text: "Case details", in: headings}]}
    actions:
      - extract:
          into: case
          fields: {number: {css: "#case-number", required: true}}
          tables: {docket: {header: "re:^entry$"}}
    postconditions: [{var: {name: case.number, op: set}}]
    transitions: [{to: done}]
  done: {terminal: success}
targets:
  search_by: {description: combo, strategies: [{role: combobox, nearby: "/search by/i"}]}
  number: {description: case number, strategies: [{role: textbox, nearby: "/case number/i"}, {css: "#caseNumberField"}]}
  search: {description: button, strategies: [{role: button, name: "/^search$/i"}]}
"""


@browser_only
async def test_end_to_end_against_the_legacy_fixture() -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--disable-features=IsolateOrigins,site-per-process"])
        context = await browser.new_context(viewport={"width": 1280, "height": 800})
        page = await context.new_page()
        posted: list[str] = []

        async def serve(route, request):
            path = request.url.split("portal.test", 1)[1].split("?")[0]
            if path == "/cases/search.do":
                # Chromium does not follow a 3xx coming from an intercepted response, so
                # the POST answers with the detail page directly (same URL, new content).
                posted.append(request.post_data or "")
                name = "legacy_show.html"
            else:
                name = {"/child.html": "legacy_portal_child.html"}.get(path, "legacy_portal.html")
            await route.fulfill(status=200, content_type="text/html; charset=utf-8",
                                body=(FIXTURES / name).read_text(encoding="utf-8"))

        await page.route("https://portal.test/**", serve)
        graph = compile_workflow(Workflow.from_yaml_text(LEGACY_GRAPH))
        session = GraphSession(graph, page, {"case_number": "case-2024-000123"}, budget=FAST)

        result = await session.run()

        assert result.status == "done", (session.failure, [e.as_dict() for e in session.events][-6:])
        case = session.result["outputs"]["case"]
        assert case["number"] == "CASE-2024-000123"
        assert [r["Entry"] for r in case["docket"]] == ["Submitted for decision", "Motion filed"]
        assert posted and "number=CASE-2024-000123" in posted[0]
        assert "searchBy=N" in posted[0]  # the select landed on "Case number"
        # select applied through the isolated world; nothing leaked into the page world
        assert await page.evaluate("typeof globalThis.__ps") == "undefined"
        await browser.close()
