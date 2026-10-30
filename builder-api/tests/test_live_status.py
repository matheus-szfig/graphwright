"""Builder status frames follow the engine after mutations, while a run is active."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from graphwright.engine.runtime import Event
from graphwright.engine.secrets import Redactor, Secret

from builder_api.sessions import LiveSession, SessionManager


def live_session():
    session = SimpleNamespace(node='first', status='paused', visits={}, failure=None,
                              result=None, request_statuses={}, redact=Redactor())
    return LiveSession(id='run', graph_name='test', graph_path=Path('test.yaml'),
                       inputs={}, context=None, page=None, session=session)


async def test_live_state_uses_post_event_node_and_redacts_results():
    live = live_session()
    q = asyncio.Queue()
    live.subscribers.append(q)
    live.publish(Event(kind='move', node='first', data={'to': 'second'}))
    live.session.node = 'second'  # runtime mutates after emitting move
    live.session.status = 'running'
    live.session.visits['second'] = 1
    live.session.result = {'value': Secret('private')}
    live.publish(Event(kind='action', node='second', data={}))
    await asyncio.sleep(0)
    messages = [q.get_nowait() for _ in range(q.qsize())]
    assert [message['kind'] for message in messages] == ['move', 'action', 'session_state']
    state = messages[-1]['session']
    assert state['node'] == 'second' and state['status'] == 'running'
    assert state['visits'] == {'second': 1}
    assert state['result'] == {'value': '***'}


async def test_state_is_sent_before_operation_finishes_and_busy_clears(tmp_path):
    live = live_session()
    live.subscribers.append(asyncio.Queue())
    q = live.subscribers[0]
    manager = SessionManager(graphs_dir=tmp_path, headed=False)
    entered, release = asyncio.Event(), asyncio.Event()

    async def operation():
        live.session.status = 'running'
        live.publish(Event(kind='action', node='first', data={}))
        entered.set()
        await release.wait()
        live.session.status = 'done'

    task = asyncio.create_task(manager._guard(live, operation()))
    await entered.wait()
    await asyncio.sleep(0)
    messages = [q.get_nowait() for _ in range(q.qsize())]
    assert any(m['kind'] == 'session_state' and m['session']['status'] == 'running'
               and m['session']['busy'] for m in messages)
    assert not task.done()
    release.set()
    await task
    state = q.get_nowait()['session']
    assert state['status'] == 'done' and not state['busy']


async def test_stop_cancels_operation_before_closing_browser(tmp_path):
    live = live_session()
    live.session.observer = SimpleNamespace(close=AsyncMock())
    live.context = SimpleNamespace(close=AsyncMock())
    manager = SessionManager(graphs_dir=tmp_path, headed=False)
    manager.sessions[live.id] = live
    entered = asyncio.Event()

    async def operation():
        entered.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(manager._guard(live, operation()))
    await entered.wait()
    await manager.close(live.id)
    assert task.cancelled()
    live.context.close.assert_awaited_once()
    assert live.id not in manager.sessions
