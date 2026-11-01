"""Broader observation must stay targetable without changing visible predicates."""
import pytest
from conftest import browser_only
from pydantic import ValidationError

from graphwright.engine.schema import ObservationOptions, PageFilter, Strategy
from graphwright.engine.targets import match_strategy
from graphwright.observe.observer import Observer
from graphwright.observe.pagestate import PageState


def test_inventory_is_separate_from_visible_matching():
    state = PageState.from_frames([(0, None, {
        "url": "https://test/", "elements": [],
        "hidden_elements": [{"id": "e1", "dom_id": "later", "role": "button", "tag": "button",
                             "visible": False, "bbox": {"x": 0, "y": 0, "w": 0, "h": 0}}],
        "stats": {"hidden": 1, "truncated": 3},
    })])
    assert state.hidden_elements[0].dom_id == "later"
    assert state.stats.truncated == 3
    assert not match_strategy(Strategy(dom_id="later"), state)


def test_page_filter_schema_rejects_typos_and_invalid_limits():
    assert PageFilter().visibility == "visible"
    assert PageFilter(categories=[]).categories == []
    for data in ({"categories": ["buttons"]}, {"frame": -1}, {"visiblity": "hidden"}):
        with pytest.raises(ValidationError):
            PageFilter.model_validate(data)
    for limit in (0, 20001):
        with pytest.raises(ValidationError):
            ObservationOptions(max_elements=limit)


@browser_only
async def test_inventory_shadow_targets_hidden_and_budget():
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content('''
          <h6 id="heading">Record details</h6><p id="text">Updated yesterday</p>
          <div id="custom" role="treeitem">Document</div>
          <div id="plain" style="width:30px;height:30px"></div>
          <progress id="progress" value="2" max="3"></progress>
          <table id="table"><tr><th>Record</th></tr><tr><td id="cell">123</td></tr></table>
          <input type="hidden" id="csrf" value="must-not-appear">
          <button id="later" style="display:none">Later</button>
          <button id="offscreen" style="position:absolute;top:3000px">Below</button>
          <div id="script-container"><script>/* private-script-metadata */</script></div>
          <div id="shadow"></div>
          <script>document.querySelector('#shadow').attachShadow({mode:'open'}).innerHTML =
            '<label for="code">Code</label><input id="code"><button>Shadow button</button><button>Another shadow button</button>';</script>
        ''')
        obs = Observer(page)
        state = await obs.observe()
        by_id = {e.dom_id: e for e in state.elements if e.dom_id}
        assert {"heading", "text", "custom", "plain", "progress", "table", "cell", "code", "offscreen"} <= by_id.keys()
        assert by_id["custom"].category == "controls"
        assert by_id["cell"].category == "tables"
        assert by_id["heading"].role == "heading"
        assert by_id["code"].name == "Code"
        assert not by_id["offscreen"].in_viewport
        assert "csrf" not in by_id and "later" not in by_id
        hidden = {e.dom_id: e for e in state.hidden_elements}
        assert hidden["csrf"].value is None and not hidden["later"].visible
        assert "must-not-appear" not in state.model_dump_json()
        assert "private-script-metadata" not in state.model_dump_json()
        shadow = next(e for e in state.elements if e.role == "button" and e.name == "Shadow button")
        assert await obs.query(shadow.css_path, frame=0) == [shadow.id]
        box = await obs.box(shadow.id)
        assert box.visible and box.occluded is False
        await page.evaluate("document.querySelector('#shadow').shadowRoot.querySelector('button').textContent = 'Changed'")
        assert await obs.signature() != state.signature
        await page.evaluate("document.querySelector('#later').style.display = 'block'")
        updated = await obs.observe()
        assert match_strategy(Strategy(dom_id="re:^later$"), updated)
        assert next(e for e in updated.elements if e.dom_id == "later").id == hidden["later"].id
        limited = await Observer(page, max_elements=2).observe()
        assert len(limited.elements) == 2 and limited.stats.truncated > 0
        assert all(e.category in ("controls", "links", "media") for e in limited.elements)
        await browser.close()
