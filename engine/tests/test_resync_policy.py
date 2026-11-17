"""Routing policy regressions without site files or a live browser."""

import pytest
from test_runtime import FAST, FakeObserver, fake_page

from graphwright.engine import Workflow, compile_workflow
from graphwright.engine.runtime import GraphSession
from graphwright.observe.pagestate import PageState

GRAPH = """
workflow: routes
portal: test
start: first
nodes:
  first:
    recognize: {url: /first}
    transitions: [{to: expected}]
  expected:
    recognize: {url: /expected}
    actions: [{press: {key: Enter}}]
    transitions: [{to: unrelated}]
  unrelated:
    recognize: {always: true}
    transitions: [{to: done}]
  done: {terminal: success}
"""


def session_for(wf, route):
    state = PageState(url=f"https://test{route}", portal="test", route=route)
    return GraphSession(compile_workflow(wf), fake_page(), {}, observer=FakeObserver([state]), budget=FAST)


def test_policy_is_validated_and_round_trips():
    wf = Workflow.from_yaml_text(GRAPH)
    assert wf.resync == "disabled"
    wf.resync = "global"
    assert Workflow.from_yaml_text(wf.to_yaml()).resync == "global"
    with pytest.raises(ValueError, match="resync"):
        Workflow.from_yaml_text(GRAPH + "\nresync: unknown\n")


async def test_transition_destination_must_recognize_before_acting():
    session = session_for(Workflow.from_yaml_text(GRAPH), "/first")
    assert (await session.step()).next_node == "expected"
    result = await session.step()
    assert result.status == "needs_repair" and session.node == "expected"
    assert session.failure["type"] == "RecognitionFailed"
    assert "expected" in session.failure["message"]
    session.page.keyboard.press.assert_not_awaited()
    assert not any(e.kind == "resync" for e in session.events)


async def test_global_recovery_can_choose_a_node_without_a_connecting_transition():
    wf = Workflow.from_yaml_text(GRAPH + "\nresync: global\n")
    session = session_for(wf, "/unexpected")
    result = await session.step()
    assert result.next_node == "unrelated"
    assert any(e.kind == "resync" and e.data["winner"] == "unrelated" for e in session.events)


async def test_global_recovery_no_match_explains_failure():
    wf = Workflow.from_yaml_text(GRAPH.replace("{always: true}", "{url: /unrelated}") + "\nresync: global\n")
    session = session_for(wf, "/unexpected")
    assert (await session.step()).status == "needs_repair"
    assert session.failure["type"] == "UnknownState"
    assert "global resync found no matching node" in session.failure["message"]


async def test_patch_can_explicitly_enable_recovery_after_a_stop():
    wf = Workflow.from_yaml_text(GRAPH)
    session = session_for(wf, "/unexpected")
    assert (await session.step()).status == "needs_repair"
    enabled = Workflow.from_yaml_text(GRAPH + "\nresync: global\n")
    session.patch(enabled)
    assert (await session.step()).next_node == "unrelated"


@pytest.mark.parametrize("mode", ["jump", "manual"])
async def test_explicit_routes_remain_available_without_global_recovery(mode):
    from graphwright.engine.schema import Action

    wf = Workflow.from_yaml_text(GRAPH)
    if mode == "jump":
        wf.nodes["first"].actions = [Action(jump={"to": "unrelated"})]
    session = session_for(wf, "/first")
    if mode == "manual":
        session.reset_to("unrelated")
        assert (await session.step()).next_node == "done"
    else:
        assert (await session.step()).next_node == "unrelated"
    assert not any(e.kind == "resync" for e in session.events)


async def test_timeout_reports_actions_completed_and_only_outgoing_transitions():
    wf = Workflow.from_yaml_text(GRAPH)
    wf.nodes["first"].transitions[0].when = wf.nodes["expected"].recognize
    wf.nodes["first"].timeout_s = 0.001
    session = session_for(wf, "/first")
    assert (await session.step()).status == "needs_repair"
    assert session.failure["type"] == "NoTransition"
    assert "actions completed" in session.failure["message"]
    assert "outgoing transitions" in session.failure["message"]
    assert not any(e.kind == "resync" for e in session.events)
