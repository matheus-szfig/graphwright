"""Real SVG geometry regression for bypass edges crossing cards (opt-in Vite UI)."""
import os

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

from builder_api.main import create_app

URL = os.environ.get("BUILDER_WEB_TEST_URL")
pytestmark = pytest.mark.skipif(not URL, reason="opt-in builder UI test; set BUILDER_WEB_TEST_URL")

GRAPH = """workflow: bypass_demo
portal: test
start: start
nodes:
  start:
    intent: Initial page loaded; fill user and password
    recognize: {always: true}
    transitions:
      - {to: capture_image, when: {text: captcha}}
      - {to: fill_2fa, when: {text: codigo_2fa}}
  capture_image:
    recognize: {always: true}
    transitions: [{to: fill_2fa}]
  fill_2fa:
    intent: Request code, fill it and validate
    recognize: {always: true}
    transitions: [{to: done}]
  done: {terminal: success}
"""


@pytest.mark.parametrize("returns", [False, True])
def test_default_curves_clear_other_cards_by_node_placement(tmp_path, returns):
    graph = GRAPH
    if returns:
        graph = graph.replace("transitions: [{to: fill_2fa}]", "transitions: [{to: fill_2fa, when: {text: ready}}, {to: fill_2fa, when: {text: alternate}}, {to: capture_image}]")
        graph = graph.replace("transitions: [{to: done}]", "transitions: [{to: done, when: {text: success}}, {to: start}]")
    (tmp_path / 'bypass_demo.yaml').write_text(graph, encoding='utf-8')
    with TestClient(create_app(graphs_dir=tmp_path, headed=False)) as api, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1800, 'height': 1200})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))

        def route(r):
            path = r.request.url.split('/builder/api', 1)[1]
            response = api.request(r.request.method, path, content=r.request.post_data,
                                   headers={'Content-Type': 'application/json'})
            r.fulfill(status=response.status_code, content_type='application/json', body=response.text)

        page.route('**/builder/api/**', route)
        page.goto(URL)
        count = len(api.get('/graphs/bypass_demo').json()['edges'])
        expect(page.locator('.react-flow__edge-path')).to_have_count(count)
        page.evaluate('document.fonts.ready')

        def geometry():
            return page.evaluate('''() => {
              const cards = [...document.querySelectorAll('.react-flow__node')].map(n => ({id: n.dataset.id, box: n.getBoundingClientRect()}));
              const collisions = [];
              for (const path of document.querySelectorAll('.react-flow__edge-path')) {
                if (!/^M[^C]+ C/.test(path.getAttribute('d')) || /[LQ]/.test(path.getAttribute('d')))
                  collisions.push('edge is not the standard cubic Bezier');
                const id = path.closest('.react-flow__edge').dataset.id;
                const [source, end] = id.split('->'); const target = end.split('#')[0];
                const length = path.getTotalLength(), matrix = path.getScreenCTM();
                for (let d = 1; d < length; d += 3) {
                  const p = path.getPointAtLength(d).matrixTransform(matrix);
                  for (const card of cards) {
                    if (card.id === source || card.id === target) continue;
                    const b = card.box, margin = 8;
                    if (p.x > b.left - margin && p.x < b.right + margin && p.y > b.top - margin && p.y < b.bottom + margin) {
                      collisions.push(`${id} intersects ${card.id}`); break;
                    }
                  }
                }
              }
              for (const label of document.querySelectorAll('.react-flow__edge-textwrapper')) {
                const a = label.getBoundingClientRect();
                for (const {id, box: b} of cards) {
                  if (a.left < b.right + 8 && a.right > b.left - 8 && a.top < b.bottom + 8 && a.bottom > b.top - 8)
                    collisions.push(`label intersects ${id}`);
                }
              }
              return [...new Set(collisions)];
            }''')

        assert geometry() == []
        positions = page.locator('.react-flow__node').evaluate_all('(nodes) => nodes.map(n => [n.dataset.id, n.style.transform])')
        page.reload()
        expect(page.locator('.react-flow__edge-path')).to_have_count(count)
        assert page.locator('.react-flow__node').evaluate_all('(nodes) => nodes.map(n => [n.dataset.id, n.style.transform])') == positions
        assert geometry() == []
        page.screenshot(path=str(tmp_path / 'layout.png'))
        assert not errors
        browser.close()
