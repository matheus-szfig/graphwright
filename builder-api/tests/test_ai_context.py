import asyncio
import json

import pytest

from builder_api.ai.context import (
    ELIDE_CHARS,
    Rejected,
    Resources,
    bound_messages,
    compact,
    recent_history,
    select_context,
)
from builder_api.ai.privacy import Projection
from builder_api.ai.providers import ProviderError, cache_tail, normalized_usage

SCRIPT = "\n".join(["const MAX = 10;", "function parseRow(row) {", "  return row.trim();", "}",
                    "const collect = async (rows) => rows.map(parseRow);"] + [f"// filler {i}" for i in range(80)])


def large_context():
    graph = {'start': 'node0', 'nodes': {f'node{i}': {
        'recognize': {'always': True}, 'actions': [{'click': {'target': 'button'}}] * 12,
        'transitions': [{'to': f'node{i + 1}'}] if i < 199 else [],
    } for i in range(200)}, 'targets': {'button': {'strategies': [{'role': 'button'}]}},
        'scripts': {'extract': '__private_1__'}}
    return {'graph': graph, 'revision': 'revision', 'session': {'attached': True, 'node': 'node1', 'status': 'paused'}}


def read_all_text(resources, rid, path=''):
    """Follow next cursors and rebuild the exact text from numbered lines."""
    lines, cursor = {}, ''
    while True:
        page = resources.read(rid, path, cursor)
        for row in page['content'].split('\n'):
            number, rest = int(row[:5]), row[7:]
            lines[number] = lines.get(number, '') + rest if row[5] == '…' else rest
        if page['next'] is None:
            return '\n'.join(lines[n] for n in sorted(lines))
        cursor = page['next']


@pytest.mark.parametrize('mode', ['valid', 'invalid', 'timeout'])
async def test_triage_is_bounded_validated_and_preserves_pinned_resources(monkeypatch, mode):
    resources = Resources(Projection(['credential-value']))
    base = large_context()
    events, requests = [], []

    async def emit(event):
        events.append(event)

    async def selector(provider, key, model, system, messages, tools, **kwargs):
        requests.append(messages)
        assert not tools
        assert len(messages[0]['content']) < 17000
        assert 'credential-value' not in compact(messages)
        if mode == 'timeout':
            await asyncio.sleep(1)
        return {'text': json.dumps({'ids': ['not-an-id'] if mode == 'invalid' else []}),
                'usage': {'input_tokens': 12, 'output_tokens': 3}}

    monkeypatch.setattr('builder_api.ai.context.complete', selector)
    packed, usage = await select_context(resources, base, 'Inspect node42', 'node42', provider='anthropic',
        key='credential-value', model='selector', transport=None, emit=emit, context_chars=16000, timeout_s=.01)
    assert len(requests) == 1
    assert packed['selection'] == ('model' if mode == 'valid' else 'fallback')
    assert len(compact(packed)) <= 16000
    assert len(compact(packed)) < len(compact(base)) / 3
    labels = [r['label'] for r in packed['resources']]
    assert 'nodes/node42' in labels and 'nodes/node1' in labels and 'nodes/node0' in labels
    assert 'targets/button' in labels
    assert resources.read('nodes/node199')['value']['transitions'] == []
    assert events[0]['phase'] == 'triage'
    assert usage['input_tokens'] == (0 if mode == 'timeout' else 12)


async def test_small_context_avoids_selector_and_stays_retrievable(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError('Small contexts must not make a triage request')

    monkeypatch.setattr('builder_api.ai.context.complete', forbidden)
    base = {'graph': {'start': 'a', 'nodes': {'a': {'terminal': 'success'}}}, 'revision': 'r', 'session': {'attached': False}}
    resources = Resources(Projection())
    packed, _ = await select_context(resources, base, 'Explain', 'a', provider='openai', key='key', model='small',
                                    transport=None, emit=forbidden)
    assert packed['selection'] == 'local'
    assert packed['included'][0] == {'resource': resources.keys['graph/nodes/a'], 'item': 'nodes/a',
                                     'data': {'terminal': 'success'}}


async def test_packed_context_stubs_long_text_but_keeps_small_fields(monkeypatch):
    base = {'graph': {'start': 'a', 'scripts': {'big': SCRIPT}, 'nodes': {'a': {
        'actions': [{'script': {'code': SCRIPT, 'into': 'rows'}}], 'transitions': [{'to': 'b'}]}, 'b': {'terminal': 'success'}}},
        'revision': 'r', 'session': {'attached': False}}
    resources = Resources(Projection())
    packed, _ = await select_context(resources, base, 'Fix a', 'a', provider='openai', key='k', model='off',
                                    transport=None, emit=None)
    node = next(item['data'] for item in packed['included'] if item['item'] == 'nodes/a')
    stub = node['actions'][0]['script']['code']['$elided']
    assert node['actions'][0]['script']['into'] == 'rows' and node['transitions'] == [{'to': 'b'}]
    assert stub['lines'] == SCRIPT.count('\n') + 1 and stub['chars'] == len(SCRIPT)
    assert stub['head'] == 'const MAX = 10;' and '2: parseRow' in stub['outline'] and '5: collect' in stub['outline']
    assert SCRIPT not in compact(packed)
    assert read_all_text(resources, stub['resource'], json.dumps(stub['path'])) == SCRIPT


def test_text_reads_are_numbered_ranged_and_lossless_for_long_lines():
    resources = Resources(Projection(), page_chars=1000)
    text = 'short\n' + 'y' * 2500 + '\nlast'
    rid = resources.put('document/x.md', text, category='document', label='x.md')
    first = resources.read('x.md', '', '1-1')
    assert first['content'] == '    1│ short' and first['next'] is None and first['total_lines'] == 3
    assert read_all_text(resources, rid) == text
    assert resources.read(rid, '', 'abc')['error'] and resources.read(rid, '', '9')['error']
    assert resources.read(rid, '["missing"]')['error']


def test_large_tool_results_are_masked_summarized_and_losslessly_retrievable():
    resources = Resources(Projection(['credential-value']), page_chars=1000)
    original = {'rows': [{'n': i, 'secret': 'credential-value', 'unicode': 'naïve'} for i in range(200)]}
    bounded = resources.bound(original, 'tool/1', 'test result')
    assert len(compact(bounded)) <= 1000
    rows, value = [], bounded['value']['rows']
    while True:
        more = value[-1].get('$more') if value and isinstance(value[-1], dict) else None
        rows += value[:-1] if more else value
        if not more:
            break
        page = resources.read(bounded['resource'], json.dumps(more['read']))
        assert len(compact(page)) <= 1000
        value = page['value']
    assert rows == [{'n': i, 'secret': '[private]', 'unicode': 'naïve'} for i in range(200)]
    assert 'credential-value' not in compact(resources.values)
    assert resources.read('../../secret')['error']


def test_graph_refresh_keeps_script_refs_and_removes_deleted_resources():
    resources = Resources(Projection())
    graph = {'scripts': {'extract': '__private_1__'}, 'nodes': {'a': {'actions': [{'evaluate': {'ref': 'extract'}}]}}}
    resources.graph(graph)
    rid = resources.keys['graph/scripts/extract']
    assert resources.read(rid)['content'].endswith('__private_1__')
    graph['scripts']['extract'] = '__private_2__'
    resources.graph(graph)
    assert resources.read(rid)['content'].endswith('__private_2__')
    del graph['scripts']['extract']
    resources.graph(graph)
    assert resources.read(rid)['error']


def test_reference_index_drives_explore_and_avoids_substring_matches():
    graph = {'start': 'login', 'inputs': {'user': {}, 'id': {}}, 'targets': {'id': {}, 'submit': {}},
             'requests': {'otp': {}}, 'scripts': {'parse': 'x'},
             'nodes': {'login': {'actions': [{'fill': {'target': 'submit', 'value': '{user}'}},
                                             {'fetch': {'request': 'otp'}}],
                                 'transitions': [{'to': 'done', 'when': {'request': {'name': 'otp', 'status': 'ok'}}}]},
                       'done': {'actions': [{'evaluate': {'ref': 'parse'}}], 'intent': 'validated id-like text',
                                'terminal': 'success'}}}
    resources = Resources(Projection())
    resources.graph(graph)
    assert resources.references['nodes/login'] == {'targets/submit', 'inputs/user', 'requests/otp', 'nodes/done'}
    assert resources.references['nodes/done'] == {'scripts/parse'}
    explored = resources.explore('login', '2')
    assert explored['uses'] == ['inputs/user', 'nodes/done', 'requests/otp', 'targets/submit']
    assert {n['item']: n['distance'] for n in explored['neighbors']}['scripts/parse'] == 2
    assert resources.explore('scripts/parse', '')['used_by'] == ['nodes/done']
    assert 'nodes/login' in resources.explore('logn', '')['similar']
    assert resources.explore('', '')['graph']['nodes'] == {'count': 2}
    assert resources.explore('login', '9')['error']


def test_search_spans_resources_and_reports_paths_and_lines():
    resources = Resources(Projection())
    resources.put('document/manual.md', 'irrelevant\n' * 3000 + 'target section: timeout_ms controls execution',
                  category='document', label='manual.md')
    resources.put('schema/Script', {'properties': {'timeout_ms': {'type': 'integer'}}}, category='schema', label='Script')
    found = resources.search('TIMEOUT_MS')
    assert found['total'] == 2
    by_label = {m['label']: m for m in found['matches']}
    assert by_label['Script']['path'] == ['properties', 'timeout_ms']
    assert by_label['manual.md']['line'] == 3001
    page = resources.read('manual.md', '', '3001')
    assert 'timeout_ms' in page['content'] and len(compact(found)) < 1500
    assert resources.search('x', 'nope')['error'] and resources.search('  ')['error']


def test_long_text_edits_require_a_current_read_and_a_unique_match():
    projection = Projection()
    resources = Resources(projection)
    handle = projection.hide('private code', ('scripts', 'old'))
    resources.graph({'scripts': {'big': SCRIPT, 'old': handle}})
    path = ['scripts', 'big']
    with pytest.raises(Rejected, match='Read the current text'):
        resources.replace_text(path, SCRIPT, 'MAX = 10', 'MAX = 20')
    with pytest.raises(Rejected, match='Read the current text'):
        resources.check_fresh(path, SCRIPT)
    resources.read('scripts/big', '', '1-3')
    with pytest.raises(Rejected, match='occurs 80 times'):
        resources.replace_text(path, SCRIPT, '// filler', '//')
    with pytest.raises(Rejected, match='not found'):
        resources.replace_text(path, SCRIPT, 'absent', '')
    edited = resources.replace_text(path, SCRIPT, 'MAX = 10', 'MAX = 20')
    assert edited == SCRIPT.replace('MAX = 10', 'MAX = 20')
    with pytest.raises(Rejected, match='changed since'):
        resources.check_fresh(path, SCRIPT)
    resources.check_fresh(path, edited)
    with pytest.raises(Rejected, match='private handle'):
        resources.replace_text(['scripts', 'old'], handle, 'private', 'public')
    short = 'x' * ELIDE_CHARS
    resources.check_fresh(['scripts', 'small'], short)


def test_anthropic_cache_breakpoints_do_not_mutate_history_and_usage_counts_cache():
    messages = [{'role': 'user', 'content': 'first'},
                {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'c1', 'content': 'x'}]}]
    marked = cache_tail(messages)
    assert marked[-1]['content'][-1]['cache_control'] == {'type': 'ephemeral'}
    assert 'cache_control' not in messages[-1]['content'][-1] and marked[0] is messages[0]
    assert cache_tail([{'role': 'user', 'content': 'task'}])[0]['content'] == [
        {'type': 'text', 'text': 'task', 'cache_control': {'type': 'ephemeral'}}]
    assert normalized_usage({'input_tokens': 10, 'cache_read_input_tokens': 900, 'cache_creation_input_tokens': 5,
                             'output_tokens': 7}) == {'input_tokens': 915, 'output_tokens': 7, 'cached_input_tokens': 900}
    assert normalized_usage({'input_tokens': 100, 'input_tokens_details': {'cached_tokens': 64}, 'output_tokens': 1}) == {
        'input_tokens': 100, 'output_tokens': 1, 'cached_input_tokens': 64}


@pytest.mark.parametrize('provider', ['openai', 'anthropic'])
def test_old_tool_results_evict_without_breaking_call_pairs(provider):
    resources = Resources(Projection())
    result = 'x' * 20000
    if provider == 'openai':
        messages = [{'type': 'function_call', 'call_id': 'c1', 'name': 'explore_graph', 'arguments': '{}'},
                    {'type': 'function_call_output', 'call_id': 'c1', 'output': result}]
    else:
        messages = [{'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 'c1', 'name': 'explore_graph', 'input': {}}]},
                    {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'c1', 'content': result}]}]
    compacted = bound_messages(messages, resources, 3000)
    assert compacted[0] == messages[0]
    assert 'c1' in compact(compacted[1])
    assert len(compact(compacted)) < 3000
    assert result in resources.values.values()
    assert messages[-1] != compacted[-1]


def test_history_is_kept_as_complete_pairs():
    history = [{'role': role, 'content': str(i) * 20} for i in range(5) for role in ('user', 'assistant')]
    recent = recent_history(history, 250)
    assert recent and recent[0]['role'] == 'user' and recent[-1]['role'] == 'assistant'
    assert len(compact(recent)) <= 250


def test_oversized_instructions_fail_without_silent_truncation():
    messages = [{'role': 'user', 'content': 'important instructions ' * 1000}]
    with pytest.raises(ProviderError, match='context budget'):
        bound_messages(messages, Resources(Projection()), 3000)


async def test_cancel_during_triage_propagates(monkeypatch):
    async def selector(*args, **kwargs):
        raise asyncio.CancelledError

    async def emit(event):
        pass

    monkeypatch.setattr('builder_api.ai.context.complete', selector)
    with pytest.raises(asyncio.CancelledError):
        await select_context(Resources(Projection()), large_context(), 'Inspect', None,
                             provider='anthropic', key='key', model='selector', transport=None, emit=emit)
