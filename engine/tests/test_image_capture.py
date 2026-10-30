"""Image CAPTCHA plumbing: observer pixels -> HTTP -> sandbox -> browser fill.

External inference is mocked; this tests transport and execution, not model accuracy.
"""
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from graphwright.browser.humanize import StealthBudget
from graphwright.engine import compile_workflow
from graphwright.engine.dsl import ActionContext, do_capture_image
from graphwright.engine.errors import StructuralError
from graphwright.engine.requests import RequestServices
from graphwright.engine.runtime import GraphSession
from graphwright.engine.schema import CaptureImage, Workflow
from graphwright.engine.secrets import Redactor, Secret
from graphwright.observe.observer import Observer

EXAMPLE = Path(__file__).resolve().parents[2] / "docs/engine-yaml/examples/image_captcha.yaml"
BROWSER = pytest.mark.skipif(os.environ.get("GW_BROWSER_TESTS") != "1", reason="opt-in browser test")


def test_capture_schema_lints_targets_and_templates():
    wf = Workflow.from_yaml(EXAMPLE)
    compile_workflow(wf)
    wf.nodes["challenge"].actions[0].capture_image.target = "missing"
    with pytest.raises(Exception, match="unknown target"):
        compile_workflow(wf)


async def test_failed_capture_discards_previous_value():
    wf = Workflow.from_yaml(EXAMPLE)
    ctx = ActionContext(page=None, observer=AsyncMock(), human=None, workflow=wf, inputs={})
    ctx.vars["captcha"] = {"data_url": Secret("stale")}
    ctx.observer.observe.side_effect = StructuralError("page unavailable")
    with pytest.raises(StructuralError):
        await do_capture_image(CaptureImage(target="captcha_image", into="captcha"), ctx)
    assert "captcha" not in ctx.vars


@pytest.fixture
async def page():
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--disable-features=IsolateOrigins,site-per-process"])
        page = await browser.new_page(device_scale_factor=2)
        await page.set_content('''<form id="loginForm">
          <img alt="Logo" aria-hidden="true" width="30" height="30">
          <label id="challengeLabel" for="challengeAnswer">
            <img title="Verification image">
          </label>
          <input id="challengeAnswer" maxlength="4">
          <input id="challengeState" type="hidden" value="0">
          <button type="button" onclick="submitAnswer()">Submit</button>
          <p role="alert"></p>
        </form><script>
          function changeImage(color = 'red') {
            const c = document.createElement('canvas'); c.width = 137; c.height = 45;
            const x = c.getContext('2d'); x.fillStyle = color; x.fillRect(0, 0, 137, 45);
            x.fillStyle = 'black'; x.font = '30px sans-serif'; x.fillText('A1b2', 10, 35);
            document.querySelector('#challengeLabel img').src = c.toDataURL('image/png');
          }
          function submitAnswer() {
            if (document.querySelector('input').value === 'A1b2') {
              document.body.innerHTML = '<main><button id="authenticatedHome">Home</button></main>';
            } else document.querySelector('[role=alert]').textContent = 'Invalid verification code';
          }
          changeImage();
        </script>''')
        await page.locator('#challengeLabel img').evaluate('(img) => img.decode()')
        yield page
        await browser.close()


@BROWSER
@pytest.mark.parametrize("answer,changed,expected", [
    ("A1b2", False, "done"),
    ("too long", False, "needs_repair"),
    ("A1b2", True, "needs_repair"),
    ("ZZZZ", False, "needs_repair"),
])
async def test_image_to_vision_request_and_submit(page, answer, changed, expected):
    obs = Observer(page)
    state = await obs.observe()
    image = next(e for e in state.elements if e.role == "img")
    assert image.name == "Verification image"
    assert not any(e.name == "Logo" for e in state.elements)
    assert next(e for e in state.elements if e.dom_id == "challengeAnswer").name == image.name
    assert "data:image" not in state.model_dump_json()
    captured = await obs.capture_image(image.id)
    assert (captured["width"], captured["height"]) == (137, 45)
    assert captured["data_url"].startswith("data:image/png;base64,")
    assert (await obs.capture_image(image.id))["sha256"] == captured["sha256"]
    calls = []

    async def infer(request):
        payload = json.loads(request.content)
        assert payload["image"] == captured["data_url"]
        assert request.headers["Authorization"] == "Bearer fake-vision-key"
        calls.append(payload)
        if changed:
            await page.evaluate("changeImage('blue')")
            await page.locator('#challengeLabel img').evaluate('(img) => img.decode()')
        return httpx.Response(200, json={"code": answer})

    session = GraphSession(
        compile_workflow(Workflow.from_yaml(EXAMPLE)), page,
        {"vision_url": "https://vision.test/read", "vision_key": Secret("fake-vision-key")},
        observer=obs, redactor=Redactor(["fake-vision-key"]),
        budget=StealthBudget(scale=0),
        services=RequestServices(transport=httpx.MockTransport(infer), allowed_hosts="vision.test"),
    )
    await session.run()
    assert session.status == expected, session.failure
    assert len(calls) == (3 if answer == "ZZZZ" else 1)
    if changed or answer == "too long":
        assert await page.locator('#challengeAnswer').input_value() == ""
    trace = repr(session.events)
    assert captured["data_url"] not in trace
    assert "fake-vision-key" not in trace
    assert answer not in trace


@BROWSER
async def test_capture_accepts_elements_and_rejects_unloaded_images(page):
    obs = Observer(page)
    state = await obs.observe()
    field = next(e for e in state.elements if e.dom_id == "challengeAnswer")
    assert (await obs.capture_image(field.id))["data_url"].startswith("data:image/png;base64,")
    await page.locator('#challengeLabel img').evaluate("img => img.removeAttribute('src')")
    image = next(e for e in state.elements if e.role == "img")
    with pytest.raises(ValueError, match="not loaded"):
        await obs.capture_image(image.id)


@BROWSER
async def test_canvas_capture_in_child_frame_and_size_limit(page):
    await page.set_content('<iframe srcdoc="<canvas id=challenge width=100 height=40></canvas>"></iframe>')
    obs = Observer(page)
    state = await obs.observe()
    canvas = next(e for e in state.elements if e.tag == "canvas")
    assert canvas.frame == 1 and canvas.role == "img"
    captured = await obs.capture_image(canvas.id)
    assert (captured["width"], captured["height"]) == (100, 40)
    await page.frames[1].evaluate("document.querySelector('canvas').width = 100000")
    with pytest.raises(ValueError, match="clipped|pixel limit"):
        await obs.capture_image(canvas.id)


@BROWSER
async def test_rendered_capture_ignores_dom_image_obfuscation(page):
    import base64

    obs = Observer(page)
    await obs.observe()
    original = await page.locator('#challengeLabel img').get_attribute('src')
    png = base64.b64decode(original.split(',', 1)[1])
    await page.route('https://images.test/**', lambda route: route.fulfill(content_type='image/png', body=png))
    await page.set_content('''<div style="height:1500px"></div>
      <div id="composite" role="img" aria-label="Challenge" style="position:relative;width:137px;height:45px;background:url(https://images.test/background)">
        <span style="position:absolute;left:5px;top:4px;background:white">Overlay</span>
      </div>
      <canvas id="tainted" width="137" height="45"></canvas>
      <script>
        window.ready = new Promise(resolve => {
          const img = new Image(); img.onload = () => {
            document.querySelector('canvas').getContext('2d').drawImage(img, 0, 0);
            resolve();
          }; img.src = 'https://images.test/captcha';
        });
      </script>''')
    await page.evaluate('window.ready')
    assert await page.evaluate("(() => {try {document.querySelector('canvas').toDataURL(); return false;} catch {return true;}})()")
    state = await obs.observe()
    for dom_id in ['composite', 'tainted']:
        element = next(e for e in state.elements if e.dom_id == dom_id)
        captured = await obs.capture_image(element.id)
        expected = await page.locator('#' + dom_id).screenshot(type='png', scale='css')
        assert base64.b64decode(captured['data_url'].split(',', 1)[1]) == expected
        assert (captured['width'], captured['height']) == (137, 45)


@BROWSER
async def test_rendered_capture_rejects_overlay(page):
    obs = Observer(page)
    state = await obs.observe()
    image = next(e for e in state.elements if e.role == "img")
    await page.evaluate("document.body.insertAdjacentHTML('beforeend', '<div style=\"position:fixed;inset:0;background:white;z-index:999\"></div>')")
    with pytest.raises(ValueError, match="covered"):
        await obs.capture_image(image.id)


@BROWSER
async def test_rendered_capture_in_scrolled_cross_origin_frame(page):
    import base64

    await page.route('https://portal.test/**', lambda route: route.fulfill(
        content_type='text/html', body='<div style="height:1000px"></div><iframe style="margin:30px;border:8px solid black" src="https://child.test/"></iframe>'))
    await page.route('https://child.test/**', lambda route: route.fulfill(
        content_type='text/html', body='<div style="height:400px"></div><div role="img" id="captcha" style="background:orange;width:120px;height:45px">A1b2</div>'))
    await page.goto('https://portal.test/')
    obs = Observer(page)
    state = await obs.observe()
    target = next(e for e in state.elements if e.dom_id == 'captcha')
    assert target.frame == 1
    captured = await obs.capture_image(target.id)
    expected = await page.frame_locator('iframe').locator('#captcha').screenshot(type='png', scale='css')
    assert base64.b64decode(captured['data_url'].split(',', 1)[1]) == expected
