"""Code nodes (`script`, sandboxed JS) and in-page JavaScript (`evaluate`)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from conftest import browser_only
from pydantic import ValidationError

from graphwright.engine.compiler import lint
from graphwright.engine.dsl import ActionContext, do_evaluate, do_script
from graphwright.engine.errors import StructuralError
from graphwright.engine.requests import RequestRunner, RequestServices
from graphwright.engine.sandbox import run_js, syntax_error
from graphwright.engine.schema import Evaluate, Script, Workflow
from graphwright.engine.secrets import Redactor, Secret


def workflow(actions: list[dict], inputs: dict | None = None) -> Workflow:
    return Workflow.model_validate({
        "workflow": "t", "portal": "x", "start": "a",
        "inputs": inputs if inputs is not None else {"date": {"pii": False}, "password": {"type": "password"}},
        "nodes": {
            "a": {"recognize": {"always": True}, "actions": actions, "transitions": [{"to": "done"}]},
            "done": {"terminal": "success"},
        },
    })


def ctx_for(wf: Workflow, inputs: dict | None = None, *, redactor: Redactor | None = None, observer=None) -> tuple[ActionContext, list]:
    events: list = []
    ctx = ActionContext(
        page=None, observer=observer, human=None, workflow=wf,  # type: ignore[arg-type]
        inputs=inputs if inputs is not None else {"date": "25/10/2026", "password": Secret("hunter2-pw")},
        vars={"count": 2, "token": Secret("tok-secret")},
        log=lambda kind, **data: events.append({"kind": kind, **data}),
        requests=RequestRunner({}, RequestServices(on_secret=redactor.add if redactor else None)),
    )
    return ctx, events


# --- sandbox ---------------------------------------------------------------------------


def test_sandbox_runs_modern_js_with_args_vars_inputs_and_console() -> None:
    r = run_js(
        "const [d, m, y] = args.date.split('/'); console.log('parsing', args.date, {n: vars.n});"
        "return { iso: `${y}-${m}-${d}`, next: vars.n + 1, who: inputs.user ?? 'nobody' };",
        args={"date": "25/10/2026"}, vars={"n": 1}, inputs={},
    )
    assert r.ok and r.value == {"iso": "2026-10-25", "next": 2, "who": "nobody"}
    assert r.logs == ['log parsing 25/10/2026 {"n":1}']


@pytest.mark.parametrize(("code", "needle"), [
    ("return fetch('http://example.com')", "fetch is not defined"),
    ("return require('fs')", "require is not defined"),
    ("return process.env", "process is not defined"),
    ("return new XMLHttpRequest()", "XMLHttpRequest is not defined"),
])
def test_sandbox_has_no_io(code: str, needle: str) -> None:
    r = run_js(code)
    assert not r.ok and needle in (r.error or "")


def test_sandbox_limits() -> None:
    assert "exceeded 150 ms" in (run_js("while (true) {}", timeout_ms=150).error or "")
    big = run_js("const a = []; for (;;) a.push(new Array(1e6).fill(1));", memory_mb=16, timeout_ms=5000)
    assert "exceeded 16 MB" in (big.error or "")
    assert "Promise" in (run_js("return Promise.resolve(1)").error or "")
    assert "boom" in (run_js("throw new Error('boom')").error or "")
    # each call is a fresh isolate: nothing leaks between scripts
    run_js("globalThis.leak = 1; return 1")
    assert run_js("return typeof globalThis.leak").value == "undefined"


def test_syntax_check() -> None:
    assert syntax_error("return {a:") and syntax_error("return 1") is None
    assert syntax_error("const x = await y; return x", is_async=True) is None
    assert syntax_error("const x = await y; return x")  # await is not allowed in sync code nodes


# --- script action --------------------------------------------------------------------


async def test_script_into_a_var() -> None:
    wf = workflow([])
    ctx, events = ctx_for(wf)
    await do_script(Script(code="return {iso: args.d.split('/').reverse().join('-'), n: vars.count * 10}",
                           args={"d": "{date}"}, into="parsed"), ctx)
    assert ctx.vars["parsed"] == {"iso": "2026-10-25", "n": 20}
    assert events[-1]["kind"] == "script" and events[-1]["ok"] and events[-1]["set"] == ["parsed"]


async def test_shared_scripts_resolve_for_sandbox_and_page_and_patch() -> None:
    wf = workflow([{'script': {'ref': 'sum', 'args': {'n': 3}, 'into': 'sum'}}])
    wf.scripts = {'sum': 'return args.n + vars.count;', 'extract': 'return document.title;'}
    assert lint(wf) == []
    observer = SimpleNamespace(evaluate=AsyncMock(return_value={'rows': [1, 2]}))
    ctx, _ = ctx_for(wf, observer=observer)
    await do_script(wf.nodes['a'].actions[0].script, ctx)
    assert ctx.vars['sum'] == 5
    await do_evaluate(Evaluate(ref='extract', args={'limit': 5}, into='page_data', timeout_ms=30000), ctx)
    observer.evaluate.assert_awaited_once_with('return document.title;', {'limit': 5}, world='isolated', frame=0)
    assert ctx.vars['page_data'] == {'rows': [1, 2]}
    ctx.workflow = wf.model_copy(deep=True)
    ctx.workflow.scripts['sum'] = 'return 9;'
    await do_script(wf.nodes['a'].actions[0].script, ctx)
    assert ctx.vars['sum'] == 9


def test_shared_script_validation_lint_and_yaml_roundtrip() -> None:
    with pytest.raises(ValidationError):
        Evaluate(script='return 1;', ref='shared')
    with pytest.raises(ValidationError):
        Script()
    wf = workflow([{'evaluate': {'ref': 'missing'}}])
    assert any('Unknown shared script' in error for error in lint(wf))
    wf.scripts['missing'] = 'const value = 1;\nreturn value;\n'
    assert lint(wf) == []
    text = wf.to_yaml()
    assert 'missing: |' in text
    assert Workflow.from_yaml_text(text).scripts == wf.scripts
    wf.scripts['missing'] = 'return {'
    assert any('does not compile' in error for error in lint(wf))


async def test_script_without_into_sets_each_returned_key() -> None:
    ctx, _ = ctx_for(workflow([]))
    await do_script(Script(code="return {year: 2026, month: 10}"), ctx)
    assert ctx.vars["year"] == 2026 and ctx.vars["month"] == 10
    with pytest.raises(StructuralError, match="must return an object"):
        await do_script(Script(code="return 5"), ctx)
    with pytest.raises(StructuralError, match="not valid var names"):
        await do_script(Script(code="return {'bad-key': 1}"), ctx)


async def test_script_never_sees_secrets_unless_allowed() -> None:
    redactor = Redactor()
    ctx, events = ctx_for(workflow([]), redactor=redactor)
    # vars/inputs copies drop secrets
    await do_script(Script(code="return {v: Object.keys(vars), i: Object.keys(inputs)}", into="seen"), ctx)
    assert ctx.vars["seen"] == {"v": ["count"], "i": ["date"]}
    # a secret arg is refused…
    with pytest.raises(StructuralError, match="allow_secrets"):
        await do_script(Script(code="return args.p.length", args={"p": "{password}"}, into="n"), ctx)
    # …unless allowed, and then every returned string is a secret too
    await do_script(Script(code="return {masked: args.p.slice(0, 3) + '***', len: args.p.length}",
                           args={"p": "{password}"}, allow_secrets=True), ctx)
    assert isinstance(ctx.vars["masked"], Secret) and ctx.vars["masked"].reveal() == "hun***"
    assert ctx.vars["len"] == 10
    assert events[-1]["value"] == "***" and "hunter2" not in str(events)
    assert redactor.text("x hun*** y") == "x *** y"


async def test_script_failure_is_structural_with_logs() -> None:
    ctx, events = ctx_for(workflow([]))
    with pytest.raises(StructuralError) as err:
        await do_script(Script(code="console.log('about to fail'); null.x"), ctx)
    assert "TypeError" in str(err.value) and any("about to fail" in d for d in err.value.details)
    assert events[-1]["ok"] is False


def test_lint_checks_code_and_args() -> None:
    wf = workflow([
        {"script": {"code": "return {a:", "into": "x"}},
        {"script": {"code": "return 1", "into": "not-a-name"}},
        {"script": {"code": "return 1", "args": {"v": "{nope}"}, "into": "y"}},
        {"evaluate": {"script": "return await fetchSomething()", "into": "z"}},
    ])
    errors = "\n".join(lint(wf))
    assert "action #0 (script) does not compile" in errors
    assert "`into` must be a var name" in errors
    assert "unknown input {nope}" in errors
    assert "action #3" not in errors  # async body is fine for evaluate


# --- evaluate (real browser) -----------------------------------------------------------

PAGE = """<html><head><title>Shop</title><script>window.appState = {user: 'ana', cart: [1, 2, 3]};</script></head>
<body><h1 id="h">Hello</h1><iframe srcdoc="<p id='inner'>framed</p>"></iframe></body></html>"""


@pytest.fixture
async def live():
    from playwright.async_api import async_playwright

    from graphwright.observe.observer import Observer

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(PAGE)
        await page.wait_for_timeout(200)
        observer = Observer(page)
        await observer.observe()
        yield page, observer
        await observer.close()
        await browser.close()


@browser_only
async def test_evaluate_isolated_sees_the_dom_not_page_globals(live) -> None:
    page, observer = live
    wf = workflow([])
    wf.scripts['inspect_heading'] = ("document.querySelector('#h').textContent = args.text;"
                                     "return {title: document.title, app: typeof window.appState}")
    ctx, events = ctx_for(wf, observer=observer)
    await do_evaluate(Evaluate(ref="inspect_heading",
                               args={"text": "{date}"}, into="probe"), ctx)
    assert ctx.vars["probe"] == {"title": "Shop", "app": "undefined"}  # page JS is invisible here
    assert await page.text_content("#h") == "25/10/2026"  # …but the DOM is shared
    assert events[-1] == {"kind": "evaluate", "ok": True, "world": "isolated", "set": ["probe"],
                          "elapsed_ms": events[-1]["elapsed_ms"], "value": {"title": "Shop", "app": "undefined"}}


@browser_only
async def test_evaluate_main_world_reaches_page_state_and_awaits(live) -> None:
    _, observer = live
    ctx, _ = ctx_for(workflow([]), observer=observer)
    await do_evaluate(Evaluate(world="main", into="cart",
                               script="await new Promise(r => setTimeout(r, 20)); return window.appState.cart.length"), ctx)
    assert ctx.vars["cart"] == 3


@browser_only
async def test_evaluate_in_a_child_frame_and_errors(live) -> None:
    _, observer = live
    ctx, _ = ctx_for(workflow([]), observer=observer)
    await do_evaluate(Evaluate(frame=1, into="inner", script="return document.querySelector('#inner').textContent"), ctx)
    assert ctx.vars["inner"] == "framed"
    with pytest.raises(StructuralError, match="evaluate failed"):
        await do_evaluate(Evaluate(script="throw new Error('nope')"), ctx)
    with pytest.raises(StructuralError, match="allow_secrets"):
        await do_evaluate(Evaluate(script="return args.p", args={"p": "{password}"}), ctx)


async def test_run_script_report() -> None:
    from graphwright.testing import run_script

    wf = workflow([])
    ok = await run_script(wf, "console.log('hi'); return {year: args.d.slice(-4)}", args={"d": "{date}"},
                          inputs={"date": "25/10/2026"})
    assert ok == {"ok": True, "value": {"year": "2026"}, "error": None, "details": [], "logs": ["log hi"],
                  "elapsed_ms": ok["elapsed_ms"], "sets": ["year"]}
    into = await run_script(wf, "return 1", into="one")
    assert into["ok"] and into["sets"] == ["one"]
    bad = await run_script(wf, "return 1")
    assert not bad["ok"] and "must return an object" in bad["error"]
