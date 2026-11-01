"""Opt-in UI regression: BUILDER_WEB_TEST_URL=http://127.0.0.1:5176/builder/.

Uses real graph-store endpoints on temporary files.
No credentials, real graph mutations, or site browser sessions.
"""

import os

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

from builder_api.main import create_app

URL = os.environ.get("BUILDER_WEB_TEST_URL")
pytestmark = pytest.mark.skipif(not URL, reason="opt-in builder UI test; start Vite and set BUILDER_WEB_TEST_URL")

GRAPH = """workflow: {name}
version: 1
portal: test
start: first
nodes:
  first:
    recognize: {{always: true}}
    actions:
      - script: {{code: 'return {{count: 1}};'}}
    transitions: [{{to: second}}]
  second:
    recognize: {{always: true}}
    transitions: [{{to: done}}]
  done: {{terminal: success}}
"""


def test_drafts_survive_unmount_navigation_updates_and_failed_saves(tmp_path):
    for name in ("alpha", "beta"):
        (tmp_path / f"{name}.yaml").write_text(GRAPH.format(name=name), encoding="utf-8")
    with TestClient(create_app(graphs_dir=tmp_path, headed=False)) as api, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1100})
        page.set_default_timeout(8000)
        failures = []
        page.on("pageerror", lambda error: failures.append(str(error)))
        reject_save = True

        def route(request):
            path = request.request.url.split("/builder/api", 1)[1]
            if reject_save and path == "/graphs/alpha/nodes/first" and request.request.method == "PUT":
                request.fulfill(status=503, json={"detail": "Simulated save failure"})
                return
            else:
                response = api.request(request.request.method, path, content=request.request.post_data,
                                       headers={"Content-Type": "application/json"})
                request.fulfill(status=response.status_code, content_type="application/json", body=response.text)

        page.route("**/builder/api/**", route)
        page.goto(URL)
        credential = page.get_by_placeholder("optional — e.g. demo-user", exact=False)
        credential.fill("temporary-test-credential")
        page.get_by_title("collapse Inputs", exact=True).click()
        expect(credential).to_have_count(0)
        page.get_by_title("expand Inputs", exact=True).click()
        expect(credential).to_have_value("temporary-test-credential")

        def tab(name):
            return page.get_by_role("tab", name=name, exact=True)

        def edit(content, index=0):
            editor = page.locator(".cm-content").nth(index)
            editor.click()
            editor.press("Control+a")
            page.keyboard.insert_text(content)

        page.locator('.react-flow__node[data-id="first"]').click()
        expect(page.locator(".cm-content")).to_contain_text("recognize")
        original = api.get("/graphs/alpha/nodes/first").json()["yaml"]
        draft = original + "\n# retained node draft\nmax_visits: 47\n"
        edit(draft)
        expect(tab("Node WIP")).to_be_visible()
        page.locator('.react-flow__node[data-id="second"]').click()
        expect(page.locator(".cm-content")).not_to_contain_text("retained node draft")
        page.locator('.react-flow__node[data-id="first"]').click()
        expect(page.locator(".cm-content")).to_contain_text("retained node draft")
        tab("Graph YAML").click()
        edit(api.get("/graphs/alpha").json()["yaml"] + "\n# retained graph draft\n")
        expect(tab("Graph YAML WIP")).to_be_visible()
        tab("Node WIP").click()
        expect(page.locator(".cm-content")).to_contain_text("retained node draft")
        # Undo history survives unmount too. Undo and redo without losing WIP.
        page.locator(".cm-content").click()
        page.locator(".cm-content").press("Control+z")
        expect(page.locator(".cm-content")).not_to_contain_text("retained node draft")
        page.locator(".cm-content").press("Control+y")
        expect(page.locator(".cm-content")).to_contain_text("retained node draft")
        expect(page.get_by_role("button", name="Save node", exact=True)).to_be_enabled()
        page.get_by_role("button", name="Save node", exact=True).click()
        expect(page.get_by_text("Simulated save failure", exact=True)).to_be_visible()
        expect(tab("Node WIP")).to_be_visible()
        tab("Graph YAML WIP").click()
        expect(page.locator(".cm-content")).to_contain_text("retained graph draft")
        tab("Node WIP").click()
        reject_save = False
        page.get_by_role("button", name="Save node", exact=True).click()
        expect(tab("Node")).to_be_visible()
        expect(tab("Graph YAML WIP")).to_be_visible()
        tab("Graph YAML WIP").click()
        expect(page.locator(".cm-content")).to_contain_text("retained graph draft")
        expect(page.get_by_text("The saved graph changed", exact=False)).to_be_visible()
        tab("Code (1)").click()
        edit("return {count: 42};")
        page.locator("textarea").first.fill('{"sample": 42}')
        expect(tab("Code (1) WIP")).to_be_visible()
        tab("Page").click()
        expect(page.locator(".cm-content")).to_have_count(0)
        tab("Code (1) WIP").click()
        expect(page.locator(".cm-content").first).to_contain_text("42")
        expect(page.locator("textarea").first).to_have_value('{"sample": 42}')
        # Graph switching also isolates and retains both YAML and code drafts.
        graph_picker = page.locator("select").first
        graph_picker.select_option("beta")
        expect(page.get_by_role("tab", name="Graph YAML", exact=True)).to_be_visible()
        graph_picker.select_option("alpha")
        expect(tab("Graph YAML WIP")).to_be_visible()
        tab("Code (1) WIP").click()
        expect(page.locator(".cm-content").first).to_contain_text("42")
        page.get_by_title("discard the scratch edit").click()
        expect(tab("Code (1)")).to_be_visible()
        tab("Graph YAML WIP").click()
        page.get_by_role("button", name="Discard draft", exact=True).click()
        expect(tab("Graph YAML")).to_be_visible()

        # Invalid work can be persisted, reopened after a page reload, and repaired.
        # It must never enable execution, and a bad node must not prevent a graph save.
        credential.fill("")
        page.locator('.react-flow__node[data-id="first"]').click()
        node_before = api.get("/graphs/alpha/nodes/first").json()["yaml"]
        edit(node_before.replace("to: second", "to: missing"))
        expect(page.get_by_text("unknown node 'missing'", exact=False)).to_be_visible()
        expect(page.get_by_role("button", name="Save node", exact=True)).to_be_enabled()
        page.get_by_role("button", name="Save node", exact=True).click()
        expect(tab("Node")).to_be_visible()
        expect(page.get_by_text("Saved graph has", exact=False)).to_be_visible()
        expect(page.get_by_role("button", name="Start", exact=True)).to_be_disabled()
        page.reload()
        page.locator('.react-flow__node[data-id="first"]').click()
        expect(page.locator(".cm-content")).to_contain_text("missing")
        edit(node_before)
        page.get_by_role("button", name="Save node", exact=True).click()
        expect(page.get_by_role("button", name="Start", exact=True)).to_be_enabled()
        graph_before = api.get("/graphs/alpha").json()["yaml"]
        tab("Graph YAML").click()
        edit("workflow: [unfinished")
        page.locator(".cm-content").press("Control+s")
        expect(tab("Graph YAML")).to_be_visible()
        expect(page.get_by_role("button", name="Start", exact=True)).to_be_disabled()
        page.reload()
        tab("Graph YAML").click()
        expect(page.locator(".cm-content")).to_contain_text("workflow: [unfinished")
        edit(graph_before)
        page.get_by_role("button", name="Save graph", exact=True).click()
        expect(page.get_by_role("button", name="Start", exact=True)).to_be_enabled()
        stored = page.evaluate("JSON.stringify({...localStorage, ...sessionStorage})")
        assert "temporary-test-credential" not in stored and "retained graph draft" not in stored
        assert not failures, failures
        browser.close()
