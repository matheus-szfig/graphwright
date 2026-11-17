"""Offline target editing, draft retention and deletion through the real API."""
import os

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright
from test_targets_api import GRAPH

from builder_api.main import create_app

URL = os.environ.get("BUILDER_WEB_TEST_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set BUILDER_WEB_TEST_URL to a builder preview")


def test_targets_edit_without_session(tmp_path):
    (tmp_path / "targets.yaml").write_text(GRAPH, encoding="utf-8")
    with TestClient(create_app(graphs_dir=tmp_path, headed=False)) as api, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        failures = []
        page.on("pageerror", lambda error: failures.append(str(error)))

        def route(request):
            path = request.request.url.split("/builder/api", 1)[1]
            response = api.request(request.request.method, path, content=request.request.post_data,
                                   headers={"Content-Type": "application/json"})
            request.fulfill(status=response.status_code, content_type="application/json", body=response.text)

        page.route("**/builder/api/**", route)
        page.goto(URL)
        page.get_by_role("tab", name="Targets", exact=True).click()
        card = page.locator('[data-target="otp"]')
        expect(card).to_contain_text("Not observed")
        card.locator("summary").first.click()
        editor = card.locator(".cm-content")
        editor.click()
        editor.press("Control+a")
        page.keyboard.insert_text("description: Edited offline\nstrategies:\n  - dom_id: 're:^otp$'\n")
        page.get_by_role("tab", name="Graph YAML", exact=True).click()
        page.get_by_role("tab", name="Targets WIP", exact=True).click()
        card.locator("summary").first.click()
        expect(card.locator(".cm-content")).to_contain_text("Edited offline")
        card.get_by_role("button", name="Save target", exact=True).click()
        expect(page.get_by_role("tab", name="Targets", exact=True)).to_be_visible()
        expect(card.locator("summary").first).to_contain_text("Edited offline")
        card.get_by_role("button", name="Delete target", exact=True).click()
        page.get_by_role("dialog").get_by_role("button", name="Delete target", exact=True).click()
        expect(card).to_have_count(0)
        assert api.get("/graphs/targets").json()["lint"]  # references weren't silently erased
        page.get_by_role("button", name="Add target", exact=True).click()
        page.get_by_role("dialog").get_by_role("textbox").fill("replacement")
        page.get_by_role("dialog").get_by_role("button", name="OK", exact=True).click()
        expect(page.locator('[data-target="replacement"]')).to_be_visible()
        assert not failures
        browser.close()
