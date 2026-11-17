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


def test_run_keeps_entire_graph_visible_during_status_updates(browser_ui):
    page, _, session, _, _ = browser_ui
    nodes = page.locator('.react-flow__node')
    expect(page.locator('.react-flow__edge-path')).to_have_count(2)
    for node in nodes.all():
        expect(node).to_be_visible()
    positions = nodes.evaluate_all('(nodes) => nodes.map(n => [n.dataset.id, n.style.transform])')
    page.evaluate('''() => {
      window.hiddenGraphFrames = [];
      window.watchGraph = true;
      const sample = () => {
        if (!window.watchGraph) return;
        const hidden = [...document.querySelectorAll('.react-flow__node')]
          .filter(node => getComputedStyle(node).visibility !== 'visible')
          .map(node => node.dataset.id);
        const edges = document.querySelectorAll('.react-flow__edge-path').length;
        if (hidden.length || edges !== 2) window.hiddenGraphFrames.push({hidden, edges});
        requestAnimationFrame(sample);
      };
      requestAnimationFrame(sample);
    }''')
    page.get_by_role('button', name='Start', exact=True).click()
    expect(page.get_by_role('button', name='Run', exact=True)).to_be_enabled()
    session.update(status='running')
    page.get_by_role('button', name='Run', exact=True).click()
    for current in ['first', 'middle', 'done']:
        session.update(node=current, visits={current: 1}, status='done' if current == 'done' else 'running')
        page.evaluate('payload => window.testSocket.onmessage({data: JSON.stringify(payload)})',
                      {'kind': 'session_state', 'session': session})
        expect(page.locator(f'.react-flow__node[data-id="{current}"]')).to_contain_text(
            'here' if current == 'done' else 'running')
        page.evaluate('''() => new Promise(resolve => {
          requestAnimationFrame(() => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        })''')
        for node in nodes.all():
            expect(node).to_be_visible()
        expect(page.locator('.react-flow__edge-path')).to_have_count(2)
        assert nodes.evaluate_all('(nodes) => nodes.map(n => [n.dataset.id, n.style.transform])') == positions
    assert page.evaluate('window.watchGraph = false; window.hiddenGraphFrames') == []


def test_stop_unlocks_sidebar_inputs_for_the_next_run(browser_ui):
    page, api, session, _, _ = browser_ui
    response = api.put('/graphs/ui_demo/inputs/case_label', json={
        'type': 'string', 'required': False, 'pii': False,
    })
    assert response.status_code == 200
    page.reload()
    inputs = page.locator('section').filter(has=page.get_by_text('Inputs', exact=True))
    value = inputs.locator('.group input')
    credential = page.get_by_placeholder('optional — e.g. demo-user', exact=False)
    value.fill('first run')
    credential.fill('demo')
    session['inputs'] = {'case_label': 'first run'}
    page.get_by_role('button', name='Start', exact=True).click()
    expect(value).to_be_disabled()
    expect(credential).to_be_disabled()
    page.get_by_title('close the session and its browser context', exact=True).click()
    expect(page.get_by_role('button', name='Start', exact=True)).to_be_enabled()
    expect(value).to_be_editable()
    expect(value).to_have_value('first run')
    expect(credential).to_be_editable()
    expect(credential).to_have_value('demo')
    expect(page.get_by_text('Inputs are fixed while a session runs.', exact=True)).not_to_be_visible()
    value.fill('next run')
    credential.fill('other-account')
    session.update(id='next_run', inputs={'case_label': 'next run'})
    with page.expect_request(lambda request: request.method == 'POST' and request.url.endswith('/sessions')) as start:
        page.get_by_role('button', name='Start', exact=True).click()
    assert start.value.post_data_json['inputs']['case_label'] == 'next run'
    assert start.value.post_data_json['credential'] == 'other-account'
    expect(value).to_be_disabled()
    page.get_by_title('close the session and its browser context', exact=True).click()
    expect(credential).to_be_editable()


@pytest.mark.parametrize('viewport_height', [650, 900])
def test_sidebars_scroll_with_ai_chat_open(browser_ui, viewport_height):
    page, api, _, _, _ = browser_ui
    for i in range(15):
        response = api.put(f'/graphs/ui_demo/inputs/field_{i}', json={
            'type': 'string', 'required': False, 'pii': False,
        })
        assert response.status_code == 200
    page.set_viewport_size({'width': 1400, 'height': viewport_height})
    page.reload()
    page.get_by_role('button', name='Show AI chat', exact=True).click()
    left, right = page.locator('aside').all()
    rail = left.locator(':scope > div')
    # Cards must keep their content height instead of shrinking and clipping it.
    assert left.locator('section').evaluate_all(
        '(cards) => cards.every(card => card.scrollHeight <= card.clientHeight + 1)')
    rail.hover()
    page.mouse.wheel(0, 5000)
    credential = page.get_by_placeholder('optional — e.g. demo-user', exact=False)
    expect(credential).to_be_in_viewport()
    assert rail.evaluate('(el) => el.scrollTop') > 0

    page.get_by_role('tab', name='Graph YAML', exact=True).click()
    editor = right.locator('.cm-scroller')
    editor.hover()
    page.mouse.wheel(0, 5000)
    page.wait_for_function("document.querySelector('aside .cm-scroller').scrollTop > 0")
    save = page.get_by_role('button', name='Save graph', exact=True)
    save.scroll_into_view_if_needed()
    expect(save).to_be_in_viewport()

    # Expanding chat must not collapse the editor or make the rail's end unreachable.
    page.get_by_role('button', name='Expand AI panel', exact=True).click()
    assert editor.evaluate('(el) => el.clientHeight') >= 100
    credential.scroll_into_view_if_needed()
    expect(credential).to_be_in_viewport()
    save.scroll_into_view_if_needed()
    expect(save).to_be_in_viewport()


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
    page.get_by_role('button', name='Copy ALL', exact=True).click()
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
    page.get_by_role('button', name='Copy ALL', exact=True).click()
    assert json.loads(page.evaluate('window.copiedText')) == events
    events.clear()
    session.update(id='next_run', node='first', status='paused', visits={})
    page.get_by_role('button', name='Start', exact=True).click()
    expect(page.get_by_text('No events yet.', exact=True)).to_be_visible()


def test_large_run_result_is_collapsed_bounded_scrollable_and_copyable(browser_ui):
    page, _, session, _, _ = browser_ui
    page.get_by_role('button', name='Start', exact=True).click()
    result = {'rows': [{'id': i, 'text': 'Synthetic output ' * 80} for i in range(200)], 'long_value': 'x' * 50000}
    session.update(status='done', node='done', result=result)
    page.evaluate('payload => window.testSocket.onmessage({data: JSON.stringify(payload)})',
                  {'kind': 'session_state', 'session': session})
    panel = page.get_by_label('Run result', exact=True)
    expect(panel.get_by_role('button', name='Run completed View result')).to_be_visible()
    assert panel.bounding_box()['height'] < 80
    expect(panel.locator('pre')).to_have_count(0)
    panel.get_by_role('button', name='Run completed View result').click()
    canvas = page.locator('.react-flow').bounding_box()
    box = panel.bounding_box()
    assert box['height'] <= canvas['height'] * .46
    assert box['x'] >= canvas['x'] and box['x'] + box['width'] <= canvas['x'] + canvas['width']
    assert panel.locator('pre').evaluate('(el) => el.scrollHeight > el.clientHeight')
    panel.get_by_role('button', name='Copy Run result', exact=True).click()
    assert json.loads(page.evaluate('window.copiedText')) == result
    panel.get_by_role('button', name='Run completed Hide result').click()
    assert panel.bounding_box()['height'] < 80
