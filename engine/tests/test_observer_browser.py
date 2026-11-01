"""Browser-backed tests for the Observer against the legacy-portal fixture. Opt-in:
they need Chromium (`uv run playwright install chromium`) and GW_BROWSER_TESTS=1.

    GW_BROWSER_TESTS=1 uv run pytest tests/test_observer_browser.py -v
"""

from __future__ import annotations

import pytest
from conftest import FIXTURES, browser_only

from graphwright.observe.observer import Observer

pytestmark = browser_only


@pytest.fixture
async def page():
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--disable-features=IsolateOrigins,site-per-process"],
        )
        context = await browser.new_context(viewport={"width": 1280, "height": 800})
        page = await context.new_page()

        async def serve(route, request):
            path = request.url.split("portal.test", 1)[1].split("?")[0]
            name = {"/child.html": "legacy_portal_child.html", "/cases/show.do": "legacy_show.html"}.get(
                path, "legacy_portal.html")
            await route.fulfill(
                status=200,
                content_type="text/html; charset=utf-8",
                body=(FIXTURES / name).read_text(encoding="utf-8"),
            )

        await page.route("https://portal.test/**", serve)
        await page.goto("https://portal.test/cases/open.do?conversationId=xyz")
        yield page
        await browser.close()


async def test_observe_reads_legacy_layout_frames_and_alerts(page) -> None:
    obs = Observer(page)
    state = await obs.observe()

    assert state.portal == "portal.test"
    assert state.route == "/cases/open.do ?conversationId"
    assert state.lang == "en-GB"
    assert state.alerts == ["No information is available for the given parameters."]
    assert [h.text for h in state.headings if h.frame == 0] == ["Case lookup — first instance"]

    by_name = {e.name: e for e in state.interactive() if e.name}
    # <label for> wins for the checkbox; the submit's value is its name.
    assert by_name["Unified"].role == "checkbox" and "checked" in by_name["Unified"].states
    assert by_name["Search"].role == "button"

    # Legacy table layout: the label sits in the previous <td>, so it lands in `nearby`.
    number = next(e for e in state.elements if e.tag == "input" and "required" in e.states)
    assert number.name == ""
    assert number.nearby[0] == "Case number"
    assert number.landmark == "table"  # closest landmark is the layout table
    assert number.label() == "Case number"

    # A <label> wrapping a whole radio group does not name each radio; <label for>,
    # trailing text ("<input> Loose text") and leading text ("Personal <input>") do.
    radios = [e for e in state.elements if e.role == "radio"]
    assert sorted(e.name for e in radios) == ["Company", "Free text", "Loose text", "Personal", "Standard number"]
    # The text field whose <label for> contains the radio group takes the label's
    # first text piece, not the whole group's text.
    assert by_name["Party document"].role == "textbox"
    # The "-" separator cell never shows up as nearby text.
    assert by_name["Search"].nearby == []

    select = next(e for e in state.elements if e.role == "combobox")
    assert select.value == "Party name"
    assert select.options[:3] == ["Case number", "Party name", "Party document"]
    assert select.nearby[0] == "Search by"

    # Hidden input and display:none link are not reported, but counted.
    assert "Hidden" not in by_name
    assert state.stats.hidden >= 2

    # Table shapes remain available; cells are now targetable too.
    table = next(t for t in state.tables if t.name == "Cases")
    assert table.headers == ["Number", "Class", "Subject"] and table.rows == 2
    assert any(e.role == "cell" and "CASE-2024-000123" in e.name for e in state.elements)

    # Frame awareness: child frame elements are prefixed and carry their frame index.
    assert len(state.frames) == 2 and state.frames[1].parent == 0
    child = [e for e in state.elements if e.frame == 1]
    assert child and all(e.id.startswith("f1e") for e in child)
    apply = next(e for e in child if e.name == "Apply")
    assert "Filter" in apply.nearby  # fieldset legend
    assert any(h.text == "Docket entries" and h.frame == 1 for h in state.headings)


async def test_ids_are_stable_and_signature_tracks_changes(page) -> None:
    obs = Observer(page)
    first = await obs.observe()
    second = await obs.observe()
    assert [e.id for e in first.elements] == [e.id for e in second.elements]
    assert first.signature == second.signature

    await page.evaluate("document.querySelector('.error').textContent = 'Case found'")
    assert await obs.wait_for_change(first.signature, timeout_s=2.0)
    third = await obs.observe()
    assert third.alerts == ["Case found"]
    assert third.by_id(first.elements[0].id) is not None  # same ids after a DOM change


async def test_box_translates_child_frame_coordinates_and_detects_occlusion(page) -> None:
    obs = Observer(page)
    state = await obs.observe()

    apply = next(e for e in state.elements if e.name == "Apply")
    box = await obs.box(apply.id)
    assert box is not None and box.frame == 1
    # iframe at (50, 500) + button at (20, 120) inside it
    assert abs(box.x - 70) < 2 and abs(box.y - 620) < 2
    assert box.occluded is False

    # The fixed overlay covers the header — the nav links are under it.
    home = next(e for e in state.elements if e.name == "Home")
    covered = await obs.box(home.id)
    assert covered is not None and covered.occluded is True


async def test_text_of_and_table_rows_read_full_values(page) -> None:
    obs = Observer(page)
    state = await obs.observe()

    table = next(t for t in state.tables if t.name == "Cases")
    rows = await obs.table_rows(table.id)
    assert rows == {
        "headers": ["Number", "Class", "Subject"],
        "rows": [
            {"Number": "CASE-2024-000123", "Class": "Ordinary procedure", "Subject": "Damages"},
            {"Number": "CASE-2024-000124", "Class": "Enforcement", "Subject": "Collection"},
        ],
    }
    assert (await obs.table_rows(table.id, max_rows=1))["rows"][0]["Number"] == "CASE-2024-000123"

    [h1] = await obs.query("main h1")
    assert await obs.text_of(h1) == "Case lookup — first instance"
    select = next(e for e in state.elements if e.role == "combobox")
    assert await obs.text_of(select.id) == "P"  # a form control reads as its value
    assert await obs.table_rows(select.id) is None  # not a table


async def test_nothing_leaks_into_the_main_world(page) -> None:
    obs = Observer(page)
    await obs.observe()
    assert await page.evaluate("typeof globalThis.__ps") == "undefined"
    assert await page.evaluate("document.querySelectorAll('[data-rpa], [data-ps], [data-gw]').length") == 0
    assert await page.frames[1].evaluate("typeof globalThis.__ps") == "undefined"
