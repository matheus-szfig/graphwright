"""Opt-in, fully local AI panel test using the built frontend and fake providers."""
import functools
import os
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright
from test_ai import GRAPH, Provider

from builder_api.main import create_app

pytestmark = pytest.mark.skipif(os.environ.get("BUILDER_AI_UI_TESTS") != "1", reason="opt-in built AI panel browser test")


def test_offline_panel_proposal_save_collapse_and_private_key(tmp_path):
    dist = Path(__file__).resolve().parents[2] / "builder-web" / "dist"
    assert (dist / "index.html").exists(), "Build builder-web first"

    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self):
            self.path = self.path.removeprefix("/builder") or "/"
            super().do_GET()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(dist)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    (tmp_path / "test.yaml").write_text(GRAPH, encoding="utf-8")
    provider = Provider()
    try:
        with TestClient(create_app(graphs_dir=tmp_path, headed=False,
                                   ai_transport=httpx.MockTransport(provider))) as api, sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1600, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            model_failure = 404

            def route(r):
                nonlocal model_failure
                path = r.request.url.split("/builder/api", 1)[1]
                if path == "/ai/connect" and model_failure:
                    r.fulfill(status=model_failure, content_type="text/html", body="API unavailable")
                    return
                headers = {"Content-Type": "application/json"}
                if r.request.headers.get("authorization"):
                    headers["Authorization"] = r.request.headers["authorization"]
                response = api.request(r.request.method, path, content=r.request.post_data, headers=headers)
                r.fulfill(status=response.status_code, headers={"Content-Type": response.headers.get("content-type", "application/json")},
                          body=response.content)

            page.route("**/builder/api/**", route)
            page.goto(f"http://127.0.0.1:{server.server_port}/builder/")
            page.get_by_role("button", name="Show AI chat", exact=True).click()
            connect_button = page.get_by_role("button", name="Connect", exact=True)
            page.get_by_label("API key", exact=True).fill("provider-secret")
            connect_button.click()
            expect(page.get_by_text("Builder API needs restarting.", exact=False)).to_be_visible()
            model_failure = 502
            connect_button.click()
            expect(page.get_by_text("Builder API is unavailable.", exact=False)).to_be_visible()
            model_failure = 422
            connect_button.click()
            expect(page.get_by_role("alert")).to_contain_text("HTTP 422")
            expect(connect_button).to_be_enabled()
            expect(page.get_by_label("API key", exact=True)).to_be_editable()
            model_failure = 0
            provider_box = page.get_by_role("combobox", name="Provider", exact=True).bounding_box()
            key_box = page.get_by_label("API key", exact=True).bounding_box()
            connect_box = connect_button.bounding_box()
            assert abs(provider_box['y'] - key_box['y']) < 3
            assert abs(connect_box['y'] - key_box['y']) < 3
            page.get_by_role("combobox", name="Provider", exact=True).select_option("anthropic")
            expect(page.get_by_label("API key", exact=True)).to_have_value("")
            page.get_by_role("combobox", name="Provider", exact=True).select_option("openai")
            page.get_by_label("API key", exact=True).fill("provider-secret")
            connect_button.click()
            # No model was chosen up front: the server picks one, and the header badge switches it.
            badge = page.get_by_role("button", name="Model: test-model. Change model", exact=True)
            expect(badge).to_be_visible()
            badge.click()
            expect(page.get_by_role("listbox", name="Models").get_by_role("option")).to_have_count(2)
            page.get_by_label("Filter models", exact=True).fill("-2")
            page.get_by_label("Filter models", exact=True).press("Enter")
            expect(page.get_by_role("button", name="Model: test-model-2. Change model", exact=True)).to_be_visible()
            composer = page.get_by_label("Message the AI assistant")
            composer.fill("   ")
            composer.press("Enter")
            assert len(provider.payloads) == 1
            composer.fill("Add an OTP API request;")
            composer.dispatch_event("keydown", {"key": "Enter", "code": "Enter", "isComposing": True})
            expect(composer).to_have_value("Add an OTP API request;")
            assert len(provider.payloads) == 1
            composer.press("Shift+Enter")
            composer.press_sequentially("capture data.otp")
            expect(composer).to_have_value("Add an OTP API request;\ncapture data.otp")
            assert len(provider.payloads) == 1  # connection test only
            composer.press("Enter")
            save = page.get_by_role("button", name="Save graph", exact=True)
            expect(save).to_be_enabled()
            proposal_card = page.get_by_role("region", name="Proposed graph changes", exact=True)
            expect(proposal_card).to_be_visible()
            expect(proposal_card.get_by_text("Schema valid", exact=True)).to_be_visible()
            turn_models = {payload["model"] for payload in provider.payloads[1:] if payload.get("tools")}
            assert turn_models == {"test-model-2"}  # switched before the first message
            expect(proposal_card.get_by_role("button", name="Diff with graph", exact=True)).to_have_attribute("aria-pressed", "true")
            proposal_card.get_by_role("button", name="Proposed YAML", exact=True).click()
            expect(proposal_card.locator('pre')).to_contain_text('requests:')
            proposal_card.get_by_role("button", name="Diff with graph", exact=True).click()
            page.get_by_label("Execution activity", exact=True).first.click()
            tool = page.get_by_label("Prepare graph changes — Completed", exact=True)
            expect(tool).to_be_visible()
            tool.click()
            expect(page.get_by_text("Arguments", exact=True)).to_be_visible()
            expect(page.get_by_text("Result", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="Save & patch session")).to_have_count(0)
            page.get_by_role("button", name="Hide AI chat", exact=True).click()
            page.get_by_role("button", name="Show AI chat", exact=True).click()
            expect(save).to_be_enabled()
            assert "requests:" not in (tmp_path / "test.yaml").read_text()
            page.get_by_role('button', name='Expand AI panel', exact=True).click()
            proposal_card.scroll_into_view_if_needed()
            page.screenshot(path=str(Path(__file__).resolve().parents[3] / '.local' / 'ai-chat-proposal.png'))
            save.click()
            expect(page.get_by_text("Graph saved. The running session is unchanged.", exact=True)).to_be_visible()
            expect(proposal_card.get_by_text("Saved", exact=True)).to_be_visible()
            expect(proposal_card.get_by_role("button", name="Save graph", exact=True)).to_have_count(0)
            assert "requests:" in (tmp_path / "test.yaml").read_text()
            storage = page.evaluate("JSON.stringify({local: {...localStorage}, session: {...sessionStorage}})")
            assert "provider-secret" not in storage and "test-model" not in storage
            assert not errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture
def streaming_panel(tmp_path):
    """Controllable browser stream: inspect the UI while a tool is still running."""
    dist = Path(__file__).resolve().parents[2] / "builder-web" / "dist"

    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self):
            self.path = self.path.removeprefix("/builder") or "/"
            super().do_GET()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(dist)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    (tmp_path / "test.yaml").write_text(GRAPH, encoding="utf-8")
    try:
        with TestClient(create_app(graphs_dir=tmp_path, headed=False,
                                   ai_transport=httpx.MockTransport(Provider()))) as api, sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1600, "height": 1000})
            page.add_init_script('''
              const original = window.fetch.bind(window);
              window.aiStreams = [];
              window.aiCalls = [];
              window.aiSteers = [];
              window.pushAi = (event, index = window.aiStreams.length - 1) => {
                window.aiStreams[index].enqueue(new TextEncoder().encode('data: ' + JSON.stringify(event) + '\\n\\n'));
              };
              window.finishAi = () => window.aiStreams.at(-1).close();
              window.fetch = async (url, options) => {
                if (String(url).endsWith('/ai/turn')) {
                  window.aiCalls.push(JSON.parse(options.body));
                  return new Response(new ReadableStream({start(controller) {
                    window.aiStreams.push(controller);
                    options.signal.addEventListener('abort', () => controller.error(new DOMException('Aborted', 'AbortError')));
                  }}), {headers: {'Content-Type': 'text/event-stream'}});
                }
                if (String(url).endsWith('/ai/steer')) {
                  window.aiSteers.push(JSON.parse(options.body).message);
                  return new Response(JSON.stringify({accepted: true}), {status: 202, headers: {'Content-Type': 'application/json'}});
                }
                if (String(url).endsWith('/ai/cancel')) {
                  if (window.slowCancel) {
                    // Server still cancelling: the UI must not wait for it.
                    await new Promise(resolve => { window.releaseCancel = resolve; });
                    return new Response(null, {status: 204});
                  }
                  window.pushAi({kind: 'cancelled', text: 'Stopped by you.'});
                  window.finishAi();
                  return new Response(null, {status: 204});
                }
                return original(url, options);
              };
            ''')

            def route(r):
                path = r.request.url.split("/builder/api", 1)[1]
                headers = {"Content-Type": "application/json"}
                if r.request.headers.get("authorization"):
                    headers["Authorization"] = r.request.headers["authorization"]
                response = api.request(r.request.method, path, content=r.request.post_data, headers=headers)
                r.fulfill(status=response.status_code, content_type="application/json", body=response.content)

            page.route("**/builder/api/**", route)
            page.goto(f"http://127.0.0.1:{server.server_port}/builder/")
            page.get_by_role("button", name="Show AI chat", exact=True).click()
            page.get_by_label("API key", exact=True).fill("provider-secret")
            page.get_by_role("button", name="Connect", exact=True).click()
            expect(page.get_by_label("Message the AI assistant")).to_be_editable()
            yield page
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_running_turn_takes_messages_and_stops_at_once(streaming_panel):
    page = streaming_panel
    composer = page.get_by_label("Message the AI assistant")
    composer.fill("Inspect this graph")
    composer.press("Enter")
    page.wait_for_function("window.aiStreams.length === 1")
    page.evaluate('''() => {
      pushAi({kind: 'context', context: {graph: 'test'}, message: 'Inspect this graph'});
      pushAi({kind: 'activity', phase: 'model', round: 1, text: 'Waiting for the model…'});
      pushAi({kind: 'model_done', round: 1, duration_ms: 1200});
      pushAi({kind: 'tool_start', round: 1, tool_id: '1:0', name: 'explore_graph', arguments: '{}'});
    }''')
    tool = page.get_by_label("Explore graph — Running", exact=True)
    expect(tool).to_be_visible()
    tool.click()
    expect(page.get_by_text("Waiting for the tool result…", exact=True)).to_be_visible()
    # Typing mid-turn reaches the running turn instead of waiting for it to end.
    expect(composer).to_be_editable()
    composer.fill("Check the transitions too")
    composer.press("Enter")
    expect(page.get_by_label("Messages sent to the running turn")).to_contain_text("Check the transitions too")
    page.wait_for_function("window.aiSteers.length === 1")
    assert page.evaluate("window.aiSteers[0]") == "Check the transitions too"
    assert len(page.evaluate("window.aiCalls")) == 1
    page.get_by_role("button", name="Hide AI chat", exact=True).click()
    expect(page.get_by_role("button", name="Stop AI response")).to_be_visible()
    page.get_by_role("button", name="Show AI chat", exact=True).click()
    page.evaluate('''() => {
      pushAi({kind: 'tool_end', tool_id: '1:0', name: 'explore_graph', ok: true, result: '{"nodes": 2}', duration_ms: 2000});
      pushAi({kind: 'steer', text: 'Check the transitions too'});
    }''')
    expect(page.get_by_label("Message sent while working")).to_contain_text("Check the transitions too")
    expect(page.get_by_label("Messages sent to the running turn")).to_have_count(0)
    page.evaluate('''() => {
      pushAi({kind: 'text', text: 'Graph inspected, transitions included.'});
      pushAi({kind: 'done', reason: 'complete', usage: {input_tokens: 10, output_tokens: 20}});
      finishAi();
    }''')
    expect(page.get_by_role("button", name="Send", exact=True)).to_be_disabled()
    assert len(page.evaluate("window.aiCalls")) == 1

    # Esc stops at once, even while the server is still cancelling.
    composer.fill("Second task")
    composer.press("Enter")
    page.wait_for_function("window.aiCalls.length === 2")
    page.evaluate("pushAi({kind: 'tool_start', round: 1, tool_id: '1:0', name: 'list_context', arguments: '{}'})")
    expect(page.get_by_label("Find relevant context — Running", exact=True)).to_be_visible()
    page.evaluate("window.slowCancel = true")
    composer.fill("Keep this draft")
    composer.press("Escape")
    expect(page.get_by_label("Find relevant context — Interrupted", exact=True)).to_be_visible()
    expect(page.get_by_text("Stopped by you. Completed proposals are still available.")).to_be_visible()
    expect(composer).to_have_value("Keep this draft")
    expect(page.get_by_role("button", name="Send", exact=True)).to_be_enabled()
    expect(page.get_by_role("button", name="Stop AI response")).to_have_count(0)
    # A new message waits for the server to finish cancelling, then runs.
    composer.fill("Do this instead")
    composer.press("Enter")
    page.wait_for_timeout(300)
    assert len(page.evaluate("window.aiCalls")) == 2
    page.evaluate("window.releaseCancel()")
    page.wait_for_function("window.aiCalls.length === 3")
    assert page.evaluate("window.aiCalls[2].message") == "Do this instead"
    page.screenshot(path=str(Path(__file__).resolve().parents[3] / ".local" / "ai-panel-review.png"))


@pytest.mark.parametrize("failure", ["server", "disconnect"])
def test_failed_turn_unlocks_composer_and_keeps_queued_message(streaming_panel, failure):
    page = streaming_panel
    composer = page.get_by_label("Message the AI assistant")
    composer.fill("Inspect graph")
    composer.press("Enter")
    page.wait_for_function("window.aiStreams.length === 1")
    composer.fill("Follow up")
    composer.press("Enter")
    if failure == "server":
        page.evaluate("pushAi({kind: 'error', text: 'Provider request failed (HTTP 429). Check quota.'}); finishAi()")
        expected = "Provider request failed (HTTP 429). Check quota."
    elif failure == "disconnect":
        page.evaluate("finishAi()")
        expected = "Connection interrupted before the assistant finished."
    expect(page.get_by_text(expected, exact=False)).to_be_visible()
    expect(composer).to_be_editable()
    expect(page.get_by_role("button", name="Send now", exact=True)).to_be_enabled()
    assert len(page.evaluate("window.aiCalls")) == 1
    page.get_by_role("button", name="Reuse message", exact=True).click()
    expect(composer).to_have_value("Inspect graph")
    composer.press("Enter")
    page.wait_for_function("window.aiCalls.length === 2")
    page.evaluate("pushAi({kind: 'done', reason: 'complete', usage: {input_tokens: 1, output_tokens: 1}}); finishAi()")
    expect(page.get_by_role("button", name="Send now", exact=True)).to_be_enabled()
    # A new successful request must not silently resume a queue paused by an error.
    assert len(page.evaluate("window.aiCalls")) == 2


def test_incremental_markdown_survives_hour_long_silence_and_legacy_activity(streaming_panel):
    page = streaming_panel
    page.clock.install()
    composer = page.get_by_label("Message the AI assistant")
    composer.fill("Explain the graph")
    composer.press("Enter")
    page.wait_for_function("window.aiStreams.length === 1")
    page.evaluate("pushAi({kind: 'activity', phase: 'model', round: 1, text: 'Waiting for the model…'})")
    page.evaluate("pushAi({kind: 'text_delta', round: 1, text: '**Hello'})")
    expect(page.get_by_text('**Hello', exact=True)).to_be_visible()
    page.evaluate("pushAi({kind: 'text_delta', round: 1, text: ' world**'})")
    expect(page.locator('.ai-markdown strong')).to_have_text('Hello world')
    # Fast-forward beyond both the old watchdog and a one-hour response.
    page.clock.fast_forward(3_700_000)
    expect(page.get_by_role('button', name='Stop AI response', exact=True)).to_be_enabled()
    expect(composer).to_be_editable()
    expect(page.get_by_text('No recent server update', exact=False)).to_be_visible()
    page.evaluate('''() => {
      pushAi({kind: 'text', round: 1, text: '**Hello world**'});
      pushAi({kind: 'model_done', round: 1});
      pushAi({kind: 'activity', text: 'Read documentation'});
      pushAi({kind: 'done', reason: 'complete'});
    }''')
    # Terminal SSE must unlock the composer even if the HTTP body stays open.
    expect(page.get_by_role('article', name='Assistant turn')).to_contain_text('Completed')
    expect(page.locator('.ai-markdown strong')).to_have_count(1)
    for group in page.get_by_label('Execution activity', exact=True).all():
        group.click()
    expect(page.get_by_text('Read documentation', exact=True)).to_be_visible()
    expect(page.get_by_text('Model request interrupted', exact=False)).to_have_count(0)
    composer.fill('Next question')
    expect(page.get_by_role('button', name='Send', exact=True)).to_be_enabled()


def test_output_recovery_labels_partial_text_and_keeps_chat_running(streaming_panel):
    page = streaming_panel
    composer = page.get_by_label('Message the AI assistant')
    composer.fill('Prepare a larger graph change')
    composer.press('Enter')
    page.wait_for_function('window.aiStreams.length === 1')
    page.evaluate('''() => {
      pushAi({kind: 'activity', phase: 'model', round: 1, text: 'Working'});
      pushAi({kind: 'text_delta', round: 1, text: 'Partial explanation.'});
      pushAi({kind: 'model_done', round: 1, reason: 'output_limit'});
      pushAi({kind: 'activity', phase: 'recovery', text: 'Retrying with 64,000 output tokens.'});
      pushAi({kind: 'activity', phase: 'model', round: 2, text: 'Working'});
    }''')
    expect(page.get_by_text('Partial response · output limit reached', exact=True)).to_be_visible()
    expect(page.get_by_text('Retrying with 64,000 output tokens.', exact=True)).to_be_visible()
    expect(page.get_by_role('button', name='Stop AI response', exact=True)).to_be_enabled()
    expect(composer).to_be_editable()
    page.evaluate('''() => {
      pushAi({kind: 'text_delta', round: 2, text: 'Completed explanation.'});
      pushAi({kind: 'model_done', round: 2});
      pushAi({kind: 'done', reason: 'complete'});
      finishAi();
    }''')
    expect(page.get_by_text('Completed explanation.', exact=True)).to_be_visible()
    expect(page.get_by_role('article', name='Assistant turn')).to_contain_text('Completed')
    expect(page.get_by_text('Model request interrupted', exact=False)).to_have_count(0)
