"""The runtime executes supplied inputs without loading its hosting application."""
from __future__ import annotations

import subprocess
import sys
import textwrap

import httpx

from graphwright.engine.requests import RequestServices
from graphwright.engine.schema import Workflow
from graphwright.services import request_services


def test_engine_runs_with_host_imports_blocked():
    # A fresh interpreter catches indirect imports that pytest's conftest masks.
    code = textwrap.dedent('''
        import asyncio
        import importlib.abc
        import sys
        from unittest.mock import AsyncMock

        class NoHostImports(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                forbidden = (
                    'graphwright.config', 'graphwright.credentials', 'graphwright.catalog',
                    'graphwright.services', 'graphwright.tasks', 'graphwright.worker',
                    'graphwright.queue', 'graphwright.storage', 'graphwright.evidence',
                    'celery', 'redis', 'asyncpg', 'aiobotocore', 'fastapi', 'builder_api',
                )
                if any(fullname == name or fullname.startswith(name + '.') for name in forbidden):
                    raise AssertionError('engine imported host module: ' + fullname)

        sys.meta_path.insert(0, NoHostImports())
        from graphwright.engine import Workflow, compile_workflow
        from graphwright.engine.runtime import GraphSession
        from graphwright.engine.secrets import Secret, Redactor
        from graphwright.observe.pagestate import PageState

        async def main():
            workflow = Workflow.model_validate({
                'workflow': 'inputs_only', 'portal': 'arbitrary', 'start': 'compute',
                'inputs': {'opaque': {'credential': 'irrelevant_adapter_metadata'}},
                'outputs': {'result': 'opaque_value'},
                'nodes': {
                    'compute': {
                        'recognize': {'always': True},
                        'actions': [{'script': {
                            'args': {'value': '{opaque}'}, 'allow_secrets': True,
                            'code': 'return {value: args.value};', 'into': 'result',
                        }}],
                        'transitions': [{'to': 'done'}],
                    },
                    'done': {'terminal': 'success'},
                },
            })
            observer = AsyncMock()
            observer.observe.return_value = PageState(url='about:blank', portal='arbitrary', route='about:blank')
            observer.signature.return_value = 'stable'
            events = []
            session = GraphSession(compile_workflow(workflow), None,
                {'opaque': Secret('provided-by-caller')}, observer=observer,
                redactor=Redactor(['provided-by-caller']), on_event=events.append)
            await session.run()
            assert session.status == 'done', session.failure
            assert session.vars['result']['value'].reveal() == 'provided-by-caller'
            assert 'provided-by-caller' not in repr(events)

        asyncio.run(main())
    ''')
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_host_settings_are_injected_not_read_by_engine(monkeypatch):
    from graphwright.config import settings

    monkeypatch.setattr(settings, 'requests_allowed_hosts', 'relay.example')
    monkeypatch.setattr(settings, 'requests_default_timeout_s', 3.5)
    isolated = RequestServices()
    configured = request_services()
    assert isolated.allowed_hosts == '' and isolated.default_timeout_s == 10.0
    assert configured.allowed_hosts == 'relay.example' and configured.default_timeout_s == 3.5
    assert request_services(allowed_hosts='override.example').allowed_hosts == 'override.example'


async def test_injected_policy_applies_to_http_transport():
    from graphwright.engine.dsl import ActionContext, fetch_request
    from graphwright.engine.requests import RequestRunner

    seen = []

    def respond(request):
        seen.append(request)
        assert request.extensions['timeout']['read'] == 1.25
        return httpx.Response(200, json={'value': 'opaque'})

    workflow = Workflow.model_validate({
        'workflow': 'policy', 'portal': 'arbitrary', 'start': 'done',
        'nodes': {'done': {'terminal': 'success'}},
        'requests': {'read': {'kind': 'http', 'url': '{endpoint}',
                              'extract': {'value': {'json': 'value'}}}},
    })
    services = RequestServices(allowed_hosts='relay.example', default_timeout_s=1.25,
                               transport=httpx.MockTransport(respond))
    ctx = ActionContext(page=None, observer=None, human=None, workflow=workflow,
                        inputs={'endpoint': 'https://other.example'},
                        requests=RequestRunner(workflow.requests, services))
    denied = await fetch_request('read', ctx, raise_on_error=False)
    assert not denied.ok and denied.code == 'host_not_allowed'
    assert not seen
    ctx.inputs['endpoint'] = 'https://relay.example'
    assert (await fetch_request('read', ctx, raise_on_error=True)).ok
    assert len(seen) == 1


def test_literal_fill_is_explicit_and_capture_hash_references_are_linted():
    from graphwright.engine.compiler import lint

    wf = Workflow.model_validate({
        'workflow': 'lint_features', 'portal': 'arbitrary', 'start': 'fill',
        'targets': {'user_filter': {'description': 'User category filter',
                                  'strategies': [{'role': 'textbox'}]}},
        'nodes': {
            'fill': {'recognize': {'always': True},
                     'actions': [{'fill': {'target': 'user_filter', 'value': 'active'}}],
                     'transitions': [{'to': 'done'}]},
            'done': {'terminal': 'success'},
        },
    })
    assert any('literal value' in error for error in lint(wf))
    wf.nodes['fill'].actions[0].fill.literal = True
    assert not lint(wf)
    from graphwright.engine.schema import Action

    wf.nodes['fill'].actions = [Action.model_validate({'capture_image': {
        'target': 'user_filter', 'into': 'pixels', 'expected_sha256': '{missing_hash}',
    }})]
    assert any('missing_hash' in error for error in lint(wf))
