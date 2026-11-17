"""Page filters, saved defaults and target creation through the real builder API."""
import json
import os

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

from builder_api.main import create_app

URL = os.environ.get("BUILDER_WEB_TEST_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set BUILDER_WEB_TEST_URL to a builder preview")


def test_page_filters_and_left_plus(tmp_path):
    (tmp_path / "inventory.yaml").write_text("""workflow: inventory
portal: test
start: first
nodes:
  first:
    recognize: {always: true}
    transitions: [{to: done}]
  done: {terminal: success}
""", encoding="utf-8")

    def element(id, category, role, name, frame=0, visible=True):
        return {"id": id, "category": category, "role": role, "tag": "button" if role == "button" else "span",
                "name": name, "dom_id": id, "frame": frame, "visible": visible, "in_viewport": visible,
                "value": None, "href": None, "options": [], "states": [] if visible else ["hidden"],
                "nearby": [], "landmark": None, "bbox": {"x": 0, "y": 0, "w": 100, "h": 20}}

    state = {"url": "https://test/", "portal": "test", "route": "/", "title": "Inventory",
             "frames": [{"index": 0, "route": "/"}, {"index": 1, "route": "/child"}],
             "elements": [element("e1", "controls", "button", "Submit"), element("e2", "text", "heading", "Case details"),
                          element("f1e1", "tables", "cell", "Case 123", frame=1)],
             "hidden_elements": [element("e3", "controls", "button", "Later", visible=False)],
             "headings": [], "alerts": [], "tables": [],
             "stats": {"kept": 3, "hidden": 1, "candidates": 4, "frames": 2, "total_ms": 2}}
    session = {"id": "test", "graph": "inventory", "inputs": {}, "status": "paused", "node": "first",
               "visits": {}, "events": 0, "failure": None, "result": None, "vars": {}, "url": "https://test/", "busy": False}

    with TestClient(create_app(graphs_dir=tmp_path, headed=False)) as api, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1900, "height": 1100})
        failures = []
        page.on("pageerror", lambda error: failures.append(str(error)))
        page.add_init_script("window.WebSocket = class { close() {} };")

        def route(r):
            path = r.request.url.split("/builder/api", 1)[1].split("?", 1)[0]
            if path.startswith("/sessions"):
                value = session
                if path.endswith(("/state", "/observe")):
                    value = {"render": "Inventory", "state": state}
                elif path.endswith("/events"):
                    value = []
                elif path.endswith(("/targets", "/recognize")):
                    value = {}
                r.fulfill(status=200, content_type="application/json", body=json.dumps(value))
                return
            response = api.request(r.request.method, path, content=r.request.post_data, headers={"Content-Type": "application/json"})
            r.fulfill(status=response.status_code, content_type="application/json", body=response.text)

        page.route("**/builder/api/**", route)
        page.goto(URL)
        page.get_by_role("button", name="Start", exact=True).click()
        page.get_by_role("tab", name="Page", exact=True).click()
        rows = page.locator("tbody tr")
        expect(rows).to_have_count(1)
        plus = page.get_by_role("button", name="Add element e1 as target")
        assert plus.bounding_box()["width"] >= 32
        expect(rows.first.locator("td").first).to_contain_text("add")
        page.get_by_role("button", name="Show everything", exact=True).click()
        expect(rows).to_have_count(4)
        page.get_by_label("Search page elements").fill("Case")
        expect(rows).to_have_count(2)
        page.get_by_label("Element frame").select_option("1")
        expect(rows).to_have_count(1)
        page.get_by_role("tab", name="Graph YAML", exact=True).click()
        page.get_by_role("tab", name="Page WIP", exact=True).click()
        expect(page.get_by_label("Search page elements")).to_have_value("Case")
        page.get_by_role("button", name="Save filters to graph", exact=True).click()
        expect(page.get_by_role("tab", name="Page", exact=True)).to_be_visible()
        saved = api.get("/graphs/inventory").json()["workflow"]["page_filter"]
        assert saved["frame"] == 1 and saved["query"] == "Case" and saved["categories"] == []
        page.get_by_role("button", name="Add element f1e1 as target").click()
        page.get_by_role("dialog").get_by_role("button", name="Add target", exact=True).click()
        assert api.get("/graphs/inventory").json()["workflow"]["targets"]["case_123"]["frame"] == 1
        page.get_by_role("button", name="Show everything", exact=True).click()
        page.get_by_label("Element visibility").select_option("hidden")
        expect(rows).to_have_count(1)
        expect(rows.first).to_contain_text("Later")
        page.get_by_role("button", name="Load graph defaults", exact=True).click()
        expect(page.get_by_label("Search page elements")).to_have_value("Case")
        expect(rows).to_have_count(1)
        page.screenshot(path=str(tmp_path / "page-inventory.png"))
        print(f"Screenshot: {tmp_path / 'page-inventory.png'}")
        assert not failures
        browser.close()
