"""Clicks wait for menu animations without repeatedly sending mouse input."""

import os
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from test_runtime import FakeObserver, golden

from graphwright.engine.compiler import lint
from graphwright.engine.dsl import ActionContext, run_action
from graphwright.engine.errors import NotActionable, StructuralError, TargetNotFound, TransientError
from graphwright.engine.schema import Action, Click, Fill, WaitFor, Workflow
from graphwright.observe.observer import ElementBox, Observer


def context():
    workflow = Workflow.model_validate({
        "workflow": "click_wait", "portal": "test", "start": "done",
        "nodes": {"done": {"terminal": "success"}},
        "targets": {"go": {"description": "Search", "strategies": [
            {"role": "button", "name": "re:^Search$"},
        ]}},
    })
    observer = FakeObserver([golden("acme_search_form")])
    human = MagicMock()
    human.click_at = AsyncMock()
    human.jitter.side_effect = lambda x, y, w, h: (x, y)
    return ActionContext(page=None, observer=observer, human=human, workflow=workflow, inputs={})


def click(timeout=1):
    return Action(click=Click(target="go", timeout_s=timeout))


def box(**over):
    return replace(ElementBox(id="e6", frame=0, x=100, y=100, w=120, h=24,
                             visible=True, in_viewport=True, occluded=False, disabled=False), **over)


@pytest.mark.parametrize("blocked", [{"occluded": True}, {"disabled": True}, {"visible": False}])
async def test_click_waits_for_unblocked_and_stable_geometry(blocked):
    ctx = context()
    samples = iter([box(**blocked), box(y=150), box(y=200), box(y=200)])

    async def sample(_):
        ctx.human.click_at.assert_not_awaited()
        return next(samples)

    ctx.observer.box = AsyncMock(side_effect=sample)
    await run_action(click(), ctx)
    assert ctx.observer.box.await_count == 4
    ctx.human.click_at.assert_awaited_once_with(160, 212)


async def test_click_refreshes_missing_and_replaced_targets():
    ctx = context()
    state = await ctx.observer.observe()
    replacement = state.model_copy(update={"elements": [
        e.model_copy(update={"id": "replacement"}) if e.id == "e6" else e for e in state.elements
    ]})
    ctx.state = state  # cached observation must not hide a replacement
    ctx.observer.observe = AsyncMock(side_effect=[state.model_copy(update={"elements": []}),
                                                 state, replacement, replacement])
    ctx.observer.box = AsyncMock(side_effect=[box(), box(id="replacement"), box(id="replacement")])
    await run_action(click(), ctx)
    assert [call.args[0] for call in ctx.observer.box.await_args_list] == ["e6", "replacement", "replacement"]
    ctx.human.click_at.assert_awaited_once()


@pytest.mark.parametrize("missing", [False, True])
async def test_click_timeout_keeps_structural_failure_without_mouse_input(missing):
    ctx = context()
    if missing:
        state = (await ctx.observer.observe()).model_copy(update={"elements": []})
        ctx.observer.observe = AsyncMock(return_value=state)
    else:
        ctx.observer.box = AsyncMock(return_value=box(occluded=True))
    with pytest.raises(TargetNotFound if missing else NotActionable, match="timed out after") as failure:
        await run_action(click(0.03), ctx)
    assert failure.value.klass == "structural"
    if not missing:
        assert "covered by another element" in str(failure.value)
    ctx.human.click_at.assert_not_awaited()


async def test_zero_timeout_checks_once_and_does_not_retry_mouse_errors():
    ctx = context()
    ctx.observer.box = AsyncMock(return_value=box())
    ctx.human.click_at.side_effect = TransientError("mouse failed")
    with pytest.raises(TransientError, match="mouse failed"):
        await run_action(click(0), ctx)
    ctx.observer.box.assert_awaited_once()
    ctx.human.click_at.assert_awaited_once()


@pytest.mark.parametrize("verb", ["click", "fill"])
async def test_delay_precedes_target_resolution_and_value_rendering(monkeypatch, verb):
    from graphwright.engine import dsl

    ctx = context()
    order = []

    async def delay(seconds):
        assert seconds == 1.25
        ctx.human.click_at.assert_not_awaited()
        order.append("delay")

    async def sample(_):
        assert order[0] == "delay"
        return box()

    async def render(_):
        assert order == ["delay"]  # no short-lived code fetched before the delay
        order.append("render")
        return "1234"

    ctx.state = await ctx.observer.observe()
    ctx.observer.observe = AsyncMock(wraps=ctx.observer.observe)
    ctx.observer.box = AsyncMock(side_effect=sample)
    ctx.render = render
    ctx.human.clear_field = AsyncMock()
    ctx.human.type_text = AsyncMock()
    monkeypatch.setattr(dsl, "asyncio", SimpleNamespace(sleep=delay))
    spec = {"target": "go", "delay_s": 1.25}
    spec.update({"timeout_s": 0} if verb == "click" else {"value": "{pin}"})
    await run_action(Action.model_validate({verb: spec}), ctx)
    assert ctx.observer.observe.await_count == 1
    ctx.human.click_at.assert_awaited_once()
    if verb == "fill":
        ctx.human.type_text.assert_awaited_once_with("1234")


@pytest.mark.parametrize("state,samples", [
    ("visible", [None, box(visible=False), box()]),
    ("hidden", [box(), None]),
    ("enabled", [box(disabled=True), box()]),
    ("disabled", [box(), box(disabled=True)]),
    ("clickable", [box(occluded=True), box(), box()]),
])
async def test_wait_for_target_states_does_not_send_mouse_input(state, samples):
    ctx = context()
    ctx.observer.box = AsyncMock(side_effect=samples)
    await run_action(Action(wait_for=WaitFor(target="go", state=state, timeout_s=1)), ctx)
    assert ctx.observer.box.await_count == len(samples)
    ctx.human.click_at.assert_not_awaited()


async def test_wait_for_state_timeout_and_existing_condition_syntax():
    ctx = context()
    ctx.observer.box = AsyncMock(return_value=box(disabled=True))
    with pytest.raises(StructuralError, match="state 'enabled' timed out"):
        await run_action(Action(wait_for=WaitFor(target="go", state="enabled", timeout_s=0)), ctx)
    await run_action(Action(wait_for=WaitFor(condition={"visible": "go"})), ctx)
    ctx.human.click_at.assert_not_awaited()


def test_wait_for_validates_modes_and_target_references():
    for args in ({}, {"target": "go"}, {"state": "clickable"},
                 {"condition": {"always": True}, "target": "go", "state": "visible"}):
        with pytest.raises(ValueError):
            WaitFor(**args)
    wf = context().workflow
    wf.start = "wait"
    from graphwright.engine.schema import Node
    wf.nodes["wait"] = Node(recognize={"always": True}, transitions=[{"to": "done"}],
                            actions=[Action(wait_for=WaitFor(target="missing", state="clickable"))])
    assert any("unknown target 'missing'" in error for error in lint(wf))


@pytest.mark.parametrize("timeout", [-1, float("inf"), float("nan")])
def test_click_timeout_must_be_finite_and_nonnegative(timeout):
    with pytest.raises(ValueError):
        Click(target="go", timeout_s=timeout)
    for model in (Click, Fill):
        with pytest.raises(ValueError):
            model(target="go", **({"value": "x"} if model is Fill else {}), delay_s=timeout)


@pytest.mark.skipif(os.environ.get("GW_BROWSER_TESTS") != "1", reason="opt-in browser test")
@pytest.mark.parametrize("explicit_wait", [False, True])
async def test_click_waits_for_vertical_expansion_in_real_browser(explicit_wait):
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content('''
                <style>
                  #menu { height: 1px; overflow: hidden; }
                  #go { display: block; margin-top: 60px; }
                </style>
                <div id="menu"><button id="go">Search</button></div>
                <script>
                  window.clicks = [];
                  document.querySelector('#go').onclick = () => clicks.push(menu.getBoundingClientRect().height);
                </script>
            ''')
            ctx = context()
            ctx.observer = Observer(page)
            # Real pointer click, with the other input behavior mocked for this test.
            ctx.human.click_at = AsyncMock(side_effect=page.mouse.click)
            await page.evaluate('''() => {
                menu.animate([{height: '1px'}, {height: '140px'}],
                             {duration: 700, delay: 200, fill: 'forwards'});
            }''')
            if explicit_wait:
                await run_action(Action(wait_for=WaitFor(target="go", state="clickable", timeout_s=3)), ctx)
                ctx.human.click_at.assert_not_awaited()
            await run_action(click(0 if explicit_wait else 3), ctx)
            ctx.human.click_at.assert_awaited_once()
            heights = await page.evaluate('clicks')
            assert len(heights) == 1 and heights[0] >= 80
        finally:
            await browser.close()
