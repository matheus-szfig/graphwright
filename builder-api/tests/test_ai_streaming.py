"""Incremental provider transport, privacy boundaries and long-lived connections."""
import asyncio
import json
import time

import httpx
import pytest
from test_ai import Provider, connect, create_client, turn

from builder_api.ai.privacy import Projection, StreamingText
from builder_api.ai.providers import (
    OutputTokenLimit,
    ProviderError,
    complete,
    model_output_limit,
)
from builder_api.config import Settings


def frame(event):
    return ("event: " + event['type'] + "\r\ndata: " + json.dumps(event, ensure_ascii=False) + "\r\n\r\n").encode()


class Chunks(httpx.AsyncByteStream):
    def __init__(self, events, gate=None):
        self.events, self.gate = events, gate
        self.closed = False

    async def __aiter__(self):
        for i, event in enumerate(self.events):
            if i == 1 and self.gate:
                await self.gate.wait()
            # Split frames, newlines, and multi-byte UTF-8 characters.
            for byte in frame(event):
                yield bytes([byte])

    async def aclose(self):
        self.closed = True


def events_for(provider):
    if provider == 'openai':
        return [
            {'type': 'response.output_text.delta', 'delta': 'Olá '},
            {'type': 'response.output_text.delta', 'delta': 'world'},
            {'type': 'response.completed', 'response': {'output': [
                {'type': 'reasoning', 'id': 'r1', 'encrypted_content': 'opaque', 'summary': []},
                {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'Olá world'}]},
                {'type': 'function_call', 'call_id': 'c1', 'name': 'get_graph', 'arguments': '{}'},
            ], 'usage': {'input_tokens': 10, 'output_tokens': 5}}},
        ]
    return [
        {'type': 'message_start', 'message': {'usage': {'input_tokens': 10, 'output_tokens': 0}}},
        {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}},
        {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': 'Olá '}},
        {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': 'world'}},
        {'type': 'content_block_stop', 'index': 0},
        {'type': 'content_block_start', 'index': 1, 'content_block': {'type': 'tool_use', 'id': 'c1', 'name': 'get_graph', 'input': {}}},
        {'type': 'content_block_delta', 'index': 1, 'delta': {'type': 'input_json_delta', 'partial_json': '{'}},
        {'type': 'content_block_delta', 'index': 1, 'delta': {'type': 'input_json_delta', 'partial_json': '}'}},
        {'type': 'content_block_stop', 'index': 1},
        {'type': 'content_block_start', 'index': 2, 'content_block': {'type': 'thinking', 'thinking': '', 'signature': ''}},
        {'type': 'content_block_delta', 'index': 2, 'delta': {'type': 'thinking_delta', 'thinking': 'internal'}},
        {'type': 'content_block_delta', 'index': 2, 'delta': {'type': 'signature_delta', 'signature': 'signed'}},
        {'type': 'content_block_stop', 'index': 2},
        {'type': 'message_delta', 'delta': {'stop_reason': 'tool_use'}, 'usage': {'output_tokens': 5}},
        {'type': 'message_stop'},
    ]


@pytest.mark.parametrize('provider', ['openai', 'anthropic'])
async def test_provider_stream_preserves_tools_continuation_usage_and_unicode(provider):
    stream = Chunks(events_for(provider))
    deltas = []

    async def receive(text):
        deltas.append(text)

    def transport(request):
        assert json.loads(request.content)['stream'] is True
        assert request.extensions['timeout']['read'] is None
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=stream)

    result = await complete(provider, 'test-key', 'model', 'system', [], [], max_tokens=100,
                            transport=httpx.MockTransport(transport), on_text=receive)
    assert deltas == ['Olá ', 'world']
    assert result['text'] == 'Olá world'
    assert result['calls'] == [{'id': 'c1', 'name': 'get_graph', 'args': {}}]
    assert result['usage'] == {'input_tokens': 10, 'output_tokens': 5, 'cached_input_tokens': 0}
    assert ('opaque' if provider == 'openai' else 'signed') in json.dumps(result['continuation'])
    assert 'internal' not in ''.join(deltas)
    assert stream.closed


async def test_text_arrives_before_provider_finishes_and_cancel_closes_stream():
    received, release = asyncio.Event(), asyncio.Event()
    stream = Chunks(events_for('openai'), gate=release)

    async def receive(text):
        assert text == 'Olá '
        received.set()

    task = asyncio.create_task(complete('openai', 'key', 'model', 'system', [], [], max_tokens=100,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=stream)),
        on_text=receive))
    await asyncio.wait_for(received.wait(), 2)
    assert not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stream.closed


@pytest.mark.parametrize('fragments', [[], [''], ['', ''], [' ', '\n'], ['', '{', '}']])
async def test_anthropic_empty_tool_input_deltas_keep_no_argument_tool_calls(fragments):
    events = [
        {'type': 'message_start', 'message': {'usage': {'input_tokens': 10}}},
        {'type': 'content_block_start', 'index': 0, 'content_block': {
            'type': 'tool_use', 'id': 'call1', 'name': 'get_schema', 'input': {}}},
        *[{'type': 'content_block_delta', 'index': 0, 'delta': {
            'type': 'input_json_delta', 'partial_json': fragment}} for fragment in fragments],
        {'type': 'content_block_stop', 'index': 0},
        {'type': 'message_delta', 'delta': {'stop_reason': 'tool_use'}, 'usage': {'output_tokens': 5}},
        {'type': 'message_stop'},
    ]
    result = await complete('anthropic', 'key', 'model', 'system', [], [], max_tokens=100,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Chunks(events))))
    assert result['calls'] == [{'id': 'call1', 'name': 'get_schema', 'args': {}}]
    assert result['continuation'][0]['content'][0]['input'] == {}


def test_empty_anthropic_deltas_execute_no_argument_tools(tmp_path):
    base = Provider('anthropic', calls=[('get_session', {}), ('get_events', {})])

    def provider(request):
        response = base(request)
        if request.method == 'GET':
            return response
        data = response.json()
        if data['content'][0]['type'] != 'tool_use':
            return response
        events = [
            {'type': 'message_start', 'message': {'usage': data['usage']}},
            {'type': 'content_block_start', 'index': 0, 'content_block': data['content'][0]},
            {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'input_json_delta', 'partial_json': ''}},
            {'type': 'content_block_stop', 'index': 0},
            {'type': 'message_delta', 'delta': {'stop_reason': 'tool_use'}},
            {'type': 'message_stop'},
        ]
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Chunks(events))

    with create_client(tmp_path, provider) as client:
        events = turn(client, connect(client, 'anthropic'))
    assert [e['name'] for e in events if e['kind'] == 'tool_start'] == ['get_session', 'get_events']
    assert all(e['ok'] for e in events if e['kind'] == 'tool_end')
    assert events[-1]['kind'] == 'done'


@pytest.mark.parametrize('payload,expected', [
    ('sensitive broken JSON', 'malformed event JSON'),
    ('["sensitive"]', 'invalid event shape'),
    (json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': 'sensitive'}}),
     'Claude response format was not recognized while reading streaming events (KeyError)'),
])
async def test_stream_format_errors_identify_stage_without_exposing_payload(payload, expected):
    with pytest.raises(ProviderError, match=expected.replace('(', r'\(').replace(')', r'\)')) as error:
        await complete('anthropic', 'key', 'model', 'system', [], [], max_tokens=100,
            transport=httpx.MockTransport(lambda _: httpx.Response(200,
                headers={'content-type': 'text/event-stream'}, content=f'data: {payload}\n\n')))
    assert 'sensitive' not in str(error.value)


@pytest.mark.parametrize('arguments', ['{"sensitive":', 'null', '[]'])
async def test_bad_tool_json_is_not_treated_as_empty_arguments(arguments):
    events = events_for('anthropic')
    for event in events:
        if event.get('delta', {}).get('type') == 'input_json_delta':
            event['delta']['partial_json'] = arguments
            arguments = ''
    with pytest.raises(ProviderError, match='tool arguments') as error:
        await complete('anthropic', 'key', 'model', 'system', [], [], max_tokens=100,
            transport=httpx.MockTransport(lambda _: httpx.Response(200,
                headers={'content-type': 'text/event-stream'}, stream=Chunks(events))))
    assert 'sensitive' not in str(error.value)


@pytest.mark.parametrize('provider,events,reason', [
    ('openai', [{'type': 'response.output_text.delta', 'delta': 'Partial '}], 'closed its stream'),
    ('openai', [{'type': 'response.incomplete', 'response': {'incomplete_details': {'reason': 'max_output_tokens'}}}], 'token limit'),
    ('openai', [{'type': 'response.failed', 'response': {'error': {'message': 'sensitive'}}}], 'streaming error'),
    ('anthropic', [{'type': 'error', 'error': {'message': 'sensitive'}}], 'streaming error'),
    ('anthropic', events_for('anthropic')[:-1], 'closed its stream'),
])
async def test_provider_failure_is_not_success_and_never_exposes_raw_errors(provider, events, reason):
    async def receive(_text):
        pass

    with pytest.raises(ProviderError, match=reason) as error:
        await complete(provider, 'key', 'model', 'system', [], [], max_tokens=100,
            transport=httpx.MockTransport(lambda _: httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Chunks(events))),
            on_text=receive)
    assert 'sensitive' not in str(error.value)


@pytest.mark.parametrize('text', [
    'Before Alice Private after.', 'Before person@example.com after.',
    'Before 123.456.789-01 after.', 'Before sk-ant-abc123456 after.',
    'Before Alice%20Private after.', 'Before a secret with spaces after.',
    'Before 1234567-89.2026.1.23.4567 after.',
])
def test_stream_masking_at_every_split(text):
    projection = Projection(['Alice Private', 'a secret with spaces'])
    for split in range(len(text) + 1):
        streaming = StreamingText(projection)
        first = streaming.feed(text[:split])
        second = streaming.feed(text[split:])
        assert first + second + streaming.feed('', final=True) == projection.text(text)
        # Every released prefix is already redacted, not retroactively corrected.
        assert projection.text(text).startswith(first)
    streaming = StreamingText(projection)
    assert ''.join(streaming.feed(char) for char in text) + streaming.feed('', final=True) == projection.text(text)


def test_harness_streams_masked_deltas_and_retains_final_text(tmp_path):
    setup = Provider(calls=[])
    raw_text = 'Safe prefix. Alice Private person@example.com safe suffix.'

    def provider(request):
        if not json.loads(request.content).get('tools'):
            return setup(request)
        events = [{'type': 'response.output_text.delta', 'delta': char} for char in raw_text]
        events.append({'type': 'response.completed', 'response': {'output': [
            {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': raw_text}]}
        ], 'usage': {'input_tokens': 10, 'output_tokens': 5}}})
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Chunks(events))

    with create_client(tmp_path, provider) as client:
        events = turn(client, connect(client))
    deltas = [e['text'] for e in events if e['kind'] == 'text_delta']
    assert ''.join(deltas) == next(e['text'] for e in events if e['kind'] == 'text')
    assert len(deltas) > 2
    assert 'Alice Private' not in json.dumps(events)
    assert 'person@example.com' not in json.dumps(events)
    assert '[private]' in ''.join(deltas)
    assert events[-1]['kind'] == 'done'


async def test_active_connection_is_not_pruned_after_an_hour(tmp_path):
    with create_client(tmp_path, Provider()) as client:
        headers = connect(client)
        harness = client.app.state.ai
        token = headers['Authorization'][7:]
        conn = harness.connections[token]
        task = asyncio.create_task(asyncio.Event().wait())
        conn.task = task
        conn.expires = time.monotonic() - 3700
        assert harness.connection(token) is conn
        assert not task.cancelled()
        assert conn.expires > time.monotonic()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        conn.expires = time.monotonic() - 1
        harness.prune()
        assert token not in harness.connections
        assert conn.key == ''
    assert Settings().ai_timeout_s == 0
    assert Settings(ai_timeout_s=7200).ai_timeout_s == 7200


@pytest.mark.parametrize('stop_reason,error', [
    ('max_tokens', OutputTokenLimit), ('model_context_window_exceeded', ProviderError),
])
async def test_truncated_tool_json_reports_stop_reason_before_json_error(stop_reason, error):
    events = [
        {'type': 'message_start', 'message': {'usage': {'input_tokens': 10}}},
        {'type': 'content_block_start', 'index': 0, 'content_block': {
            'type': 'tool_use', 'id': 'c1', 'name': 'propose_edits', 'input': {}}},
        {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'input_json_delta', 'partial_json': '{"edits_json": "unfinished'}},
        {'type': 'content_block_stop', 'index': 0},
        {'type': 'message_delta', 'delta': {'stop_reason': stop_reason}, 'usage': {'output_tokens': 32000}},
        {'type': 'message_stop'},
    ]
    with pytest.raises(error) as caught:
        await complete('anthropic', 'key', 'model', 'system', [], [], max_tokens=32000,
            transport=httpx.MockTransport(lambda _: httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Chunks(events))))
    if stop_reason == 'max_tokens':
        assert caught.value.usage['output_tokens'] == 32000
    else:
        assert 'context window' in str(caught.value)
        assert not isinstance(caught.value, OutputTokenLimit)


@pytest.mark.parametrize('max_tokens,expected', [(128000, 128000), (8192, 8192), (None, 128000)])
async def test_model_budget_discovery_and_verified_fallback(max_tokens, expected):
    cap = await model_output_limit('anthropic', 'key', 'claude-sonnet-5-5',
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={'max_tokens': max_tokens})))
    assert cap == expected
    unknown = await model_output_limit('anthropic', 'key', 'unknown-model',
        transport=httpx.MockTransport(lambda _: httpx.Response(404)))
    assert unknown == 4096


@pytest.mark.parametrize('mode,cap,expected_budgets', [
    ('recover', 128000, [32000, 64000, 64000]),
    ('exhaust', 128000, [32000, 64000, 128000]),
    ('recover', 48000, [32000, 48000, 48000]),
    ('exhaust', 8192, [8192]),
    ('context', 128000, [32000]),
])
def test_token_recovery_is_bounded_clamped_and_does_not_execute_partial_tools(tmp_path, monkeypatch, mode, cap, expected_budgets):
    from builder_api.config import settings

    monkeypatch.setattr(settings, 'ai_max_output_tokens', 0)
    monkeypatch.setattr(settings, 'ai_output_token_ceiling', 128000)
    base = Provider('anthropic', calls=[])
    payloads = []

    def provider(request):
        if request.method == 'GET':
            return httpx.Response(200, json={'max_tokens': cap})
        body = json.loads(request.content)
        if not body.get('tools'):
            return base(request)
        payloads.append(body)
        if mode != 'recover' or len(payloads) == 1:
            events = [
                {'type': 'message_start', 'message': {'usage': {'input_tokens': 10}}},
                {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}},
                {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': 'Safe text. provider-sec'}},
                {'type': 'content_block_stop', 'index': 0},
                {'type': 'content_block_start', 'index': 1, 'content_block': {'type': 'tool_use', 'id': 'partial', 'name': 'propose_edits', 'input': {}}},
                {'type': 'content_block_delta', 'index': 1, 'delta': {'type': 'input_json_delta', 'partial_json': '{"edits_json":'}},
                {'type': 'content_block_stop', 'index': 1},
                {'type': 'message_delta', 'delta': {'stop_reason': 'model_context_window_exceeded' if mode == 'context' else 'max_tokens'},
                 'usage': {'output_tokens': body['max_tokens']}},
                {'type': 'message_stop'},
            ]
            return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Chunks(events))
        if len(payloads) == 2:
            return httpx.Response(200, json={'content': [{'type': 'tool_use', 'id': 'ok', 'name': 'explore_graph',
                                                        'input': {'name': '', 'depth': ''}}],
                                           'usage': {'input_tokens': 10, 'output_tokens': 5}})
        return httpx.Response(200, json={'content': [{'type': 'text', 'text': 'Finished.'}],
                                       'usage': {'input_tokens': 10, 'output_tokens': 5}})

    with create_client(tmp_path, provider) as client:
        events = turn(client, connect(client, 'anthropic'))
    assert [p['max_tokens'] for p in payloads] == expected_budgets
    assert 'provider-sec' not in json.dumps(events)
    tools = [e['name'] for e in events if e['kind'] == 'tool_start']
    if mode == 'recover':
        assert tools == ['explore_graph']
        assert payloads[0]['messages'] == payloads[1]['messages']
        assert events[-1]['kind'] == 'done'
        assert events[-1]['usage']['output_tokens'] == 32010
        assert any(e.get('phase') == 'recovery' for e in events)
    else:
        assert tools == []
        assert events[-1]['kind'] == 'error'
        assert ('context window' if mode == 'context' else 'output budget') in events[-1]['text']
