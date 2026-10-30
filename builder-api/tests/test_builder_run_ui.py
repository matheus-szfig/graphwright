"""Real browser regression for deletion, streamed status and retained/copyable logs."""
import json
import os

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

from builder_api.main import create_app

URL = os.environ.get('BUILDER_WEB_TEST_URL')
pytestmark = pytest.mark.skipif(not URL, reason='set BUILDER_WEB_TEST_URL for UI tests')
GRAPH = '''workflow: ui_demo
portal: test
start: first
nodes:
  first:
    recognize: {always: true}
    transitions: [{to: middle}]
  middle:
    recognize: {always: true}
    transitions: [{to: done}]
  done: {terminal: success}
'''


@pytest.fixture
def browser_ui(tmp_path):
    (tmp_path / 'ui_demo.yaml').write_text(GRAPH, encoding='utf-8')
    with TestClient(create_app(graphs_dir=tmp_path, headed=False)) as api, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1900, 'height': 1100})
        page.add_init_script('''
          window.WebSocket = class {
            constructor(url) { window.testSocket = this; }
            close() { this.onclose?.(); }
          };
          Object.defineProperty(navigator, 'clipboard', {value: {
            writeText: async text => { window.copiedText = text; }
          }});
        ''')
        session = {'id': 'test_run', 'graph': 'ui_demo', 'inputs': {}, 'status': 'paused',
                   'node': 'first', 'visits': {}, 'events': 0, 'failure': None, 'result': None,
                   'vars': {}, 'url': 'about:blank', 'busy': False}
        events = []
        calls = []

        def route(r):
            path = r.request.url.split('/builder/api', 1)[1].split('?', 1)[0]
            calls.append((r.request.method, path))
            if path.startswith('/sessions'):
                if r.request.method == 'DELETE':
                    r.fulfill(status=204)
                    return
                value = session
                if path.endswith('/events'):
                    value = events
                elif path.endswith('/recognize'):
                    value = {}
                elif path.endswith('/state'):
                    value = {'render': 'Last observed page', 'state': {
                        'url': 'about:blank', 'portal': 'test', 'route': 'about:blank',
                        'title': 'Last page', 'elements': [], 'headings': [], 'alerts': [], 'frames': [],
                    }}
                elif path.endswith('/targets'):
                    value = {}
                elif path.endswith('/screenshot'):
                    value = {'jpeg_base64': ''}
                r.fulfill(status=200, content_type='application/json', body=json.dumps(value))
                return
            response = api.request(r.request.method, path, content=r.request.post_data,
                                   headers={'Content-Type': 'application/json'})
            r.fulfill(status=response.status_code, content_type='application/json', body=response.text)

        page.route('**/builder/api/**', route)
        page.goto(URL)
        expect(page.locator('.react-flow__node')).to_have_count(3)
        yield page, api, session, events, calls
        browser.close()


def test_delete_connected_node_uses_one_request_and_rejected_start_stays(browser_ui):
    page, api, _, _, calls = browser_ui
    page.locator('.react-flow__node[data-id="middle"]').click()
    page.keyboard.press('Delete')
    expect(page.locator('.react-flow__node')).to_have_count(2)
    assert 'middle' not in api.get('/graphs/ui_demo').json()['workflow']['nodes']
    mutations = [(method, path) for method, path in calls if method in ('POST', 'DELETE') and 'delete' in path]
    assert mutations == [('POST', '/graphs/ui_demo/delete-elements')]
    page.locator('.react-flow__node[data-id="first"]').click()
    page.keyboard.press('Delete')
    expect(page.get_by_text('Set another node as start before deleting the start node', exact=True)).to_be_visible()
    expect(page.locator('.react-flow__node')).to_have_count(2)


def test_live_node_stop_copy_export_and_new_run_reset(browser_ui, tmp_path):
    page, _, session, events, _ = browser_ui
    page.get_by_role('button', name='Start', exact=True).click()
    expect(page.locator('.react-flow__node[data-id="first"]')).to_contain_text('here')
    session.update(node='middle', status='running', visits={'middle': 1})
    event = {'kind': 'action', 'node': 'middle', 'ts': '2026-10-29T12:00:00Z',
             'verb': 'click', 'spec': {'target': 'button'}, 'render': 'Full untruncated event details'}
    events.append(event)
    page.evaluate('payload => window.testSocket.onmessage({data: JSON.stringify(payload)})', event)
    page.evaluate('payload => window.testSocket.onmessage({data: JSON.stringify(payload)})',
                  {'kind': 'session_state', 'session': session})
    expect(page.locator('.react-flow__node[data-id="middle"] .node-running')).to_have_count(1)
    page.get_by_text('Events (1)', exact=True).click()
    page.get_by_role('button', name='Copy event', exact=True).click()
    assert json.loads(page.evaluate('window.copiedText')) == event
    page.get_by_role('button', name='Copy whole log', exact=True).click()
    assert json.loads(page.evaluate('window.copiedText')) == events
    with page.expect_download() as download:
        page.get_by_role('button', name='Export log', exact=True).click()
    path = tmp_path / 'events.json'
    download.value.save_as(path)
    assert json.loads(path.read_text(encoding='utf-8')) == events
    page.get_by_title('close the session and its browser context', exact=True).click()
    expect(page.get_by_role('button', name='Start', exact=True)).to_be_visible()
    expect(page.get_by_text('Events (1)', exact=True)).to_be_visible()
    expect(page.locator('.react-flow__node[data-id="middle"]')).to_contain_text('here')
    page.get_by_role('button', name='Copy whole log', exact=True).click()
    assert json.loads(page.evaluate('window.copiedText')) == events
    events.clear()
    session.update(id='next_run', node='first', status='paused', visits={})
    page.get_by_role('button', name='Start', exact=True).click()
    expect(page.get_by_text('No events yet.', exact=True)).to_be_visible()
