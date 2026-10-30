"""Action executors — the closed DSL, run against a live page.

Every verb resolves its target through the same path: PageState strategies first
(role/name/nearby/…), then css/xpath through the isolated world. Every click is
preceded by an actionability check (`Observer.box`) and goes through the Humanizer.
Nothing here knows about nodes or transitions; the runtime owns those.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Any

from graphwright.browser.humanize import Humanizer
from graphwright.engine import conditions, shapers
from graphwright.engine.conditions import EvalContext
from graphwright.engine.errors import JumpSignal, NotActionable, StructuralError, TargetNotFound, TransientError
from graphwright.engine.requests import RequestFailed, RequestResult, RequestRunner, dependencies
from graphwright.engine.requests import references as request_references
from graphwright.engine.schema import (
    Action,
    CaptureImage,
    Evaluate,
    Extract,
    Fetch,
    Fill,
    Pattern,
    Script,
    Target,
    Workflow,
)
from graphwright.engine.secrets import MASK, Secret
from graphwright.engine.targets import resolve
from graphwright.observe.observer import ElementBox, Observer
from graphwright.observe.pagestate import PageState

_TEMPLATE = re.compile(r"\{([a-zA-Z_][\w.\[\]]*)\}")


@dataclass
class ActionContext:
    page: Any
    observer: Observer
    human: Humanizer
    workflow: Workflow
    inputs: dict[str, Any]
    vars: dict[str, Any] = field(default_factory=dict)
    state: PageState | None = None  # latest observation; executors refresh it as needed
    log: Any = None  # callable(kind: str, **data)
    requests: RequestRunner | None = None
    # Element ids that received a PII/secret value, shared with the session
    # so caller-owned evidence capture can mask them before a screenshot.
    sensitive: set[str] = field(default_factory=set)

    def emit(self, kind: str, **data: Any) -> None:
        if self.log:
            self.log(kind, **data)

    async def fresh_state(self) -> PageState:
        self.state = await self.observer.observe()
        return self.state

    def eval_ctx(self, state: PageState) -> EvalContext:
        return EvalContext(state=state, targets=self.workflow.targets, vars=self.vars,
                           requests=self.requests.statuses if self.requests else {})

    def render_template(self, text: str, *, reveal: bool = True) -> str:
        """Substitute `{input}`, `{vars.x}`, `{page.url}` and `{requests.r.field}`.
        Request values must already be fetched — use `render()` from async code.
        `reveal=False` keeps Secrets masked (for traces and events)."""

        def sub(m: re.Match[str]) -> str:
            path = m.group(1)
            root, _, rest = path.partition(".")
            if root == "vars":
                value: Any = self.vars
                path = rest
            elif root == "page":
                value = {"url": self.state.url if self.state else "", "title": self.state.title if self.state else ""}
                path = rest
            elif root == "requests":
                name, _, path = rest.partition(".")
                value = self.requests.values(name) if self.requests else None
                if value is None:
                    raise RequestFailed(f"request {name!r} has no value for {{{m.group(1)}}}", name=name)
            else:
                value = self.inputs
            for part in [p for p in re.split(r"\.|\[(\d+)\]", path) if p]:
                if isinstance(value, list) and part.isdigit():
                    value = value[int(part)] if int(part) < len(value) else None
                else:
                    value = value.get(part) if isinstance(value, dict) else getattr(value, part, None)
                if value is None:
                    raise StructuralError(f"template {{{m.group(1)}}} is not set")
            # Credentials and sensitive request values arrive as Secrets; this is
            # the one place they open.
            if isinstance(value, Secret):
                return value.reveal() if reveal else MASK
            return str(value)

        return _TEMPLATE.sub(sub, text)

    async def render(self, text: str) -> str:
        """Fetch every request the template uses — now, at the moment of use — then
        render. Each request runs once per render even if referenced twice."""
        for name in dict.fromkeys(r for r, _ in request_references(text)):
            await fetch_request(name, self)
        return self.render_template(text)


# --- target resolution ---------------------------------------------------------------------


async def locate(name: str, ctx: ActionContext) -> tuple[str, list[str]]:
    """Return (element_id, explanation). PageState strategies first, then css/xpath."""
    target: Target = ctx.workflow.targets[name]
    state = ctx.state or await ctx.fresh_state()
    res = resolve(name, target, state)
    explanation = res.explain(target)
    if res.ok:
        return res.element.id, explanation
    for i, strategy in enumerate(target.strategies):
        if not strategy.needs_dom:
            continue
        selector = strategy.css or f"xpath={strategy.xpath}"
        frame = strategy.frame if isinstance(strategy.frame, int) else (target.frame if isinstance(target.frame, int) else None)
        ids = await ctx.observer.query(selector, frame=frame)
        explanation.append(f"#{i} (dom {selector!r}): {len(ids)} match(es)" + (f" → {ids[:5]}" if ids else ""))
        if ids:
            if strategy.index < len(ids):
                return ids[strategy.index], explanation
            explanation.append(f"#{i}: index {strategy.index} out of range")
    raise TargetNotFound(f"target {name!r} ({target.description}) not found", details=explanation)


async def actionable_box(element_id: str, ctx: ActionContext, *, allow_disabled: bool = False) -> ElementBox:
    box = await ctx.observer.box(element_id)
    if box is None:
        raise TargetNotFound(f"element {element_id} is gone")
    if not box.in_viewport:
        await ctx.observer.scroll_into_view(element_id)
        await ctx.human.pause(250, 80)
        box = await ctx.observer.box(element_id) or box
    problems = []
    if not box.visible:
        problems.append("not visible")
    if not box.in_viewport:
        problems.append("outside viewport after scroll")
    if box.occluded:
        problems.append("covered by another element")
    if box.disabled and not allow_disabled:
        problems.append("disabled")
    if problems:
        raise NotActionable(f"element {element_id} is not actionable: {', '.join(problems)}")
    return box


# --- verbs ---------------------------------------------------------------------------------------


async def _click(element_id: str, ctx: ActionContext) -> None:
    box = await actionable_box(element_id, ctx)
    await _click_box(box, ctx)


async def _click_box(box: ElementBox, ctx: ActionContext) -> None:
    cx, cy = box.center
    x, y = ctx.human.jitter(cx, cy, box.w, box.h)
    await ctx.human.click_at(x, y)


async def fetch_request(name: str, ctx: ActionContext, *, raise_on_error: bool = True,
                        _stack: tuple[str, ...] = ()) -> RequestResult:
    """Run one graph request (its dependencies first) and emit its trace."""
    if ctx.requests is None or name not in ctx.requests.specs:
        raise RequestFailed(f"unknown request {name!r}", name=name, code="unknown_request")
    runner = ctx.requests
    for dep in dependencies(runner.specs[name]):
        if dep in _stack or dep == name:
            raise RequestFailed(f"requests form a cycle: {' → '.join([*_stack, name, dep])}", name=name, code="request_cycle")
        try:
            await fetch_request(dep, ctx, raise_on_error=True, _stack=(*_stack, name))
        except RequestFailed as exc:
            if raise_on_error:
                raise
            failed = RequestResult(name=name, kind=runner.specs[name].kind, ok=False,
                                   error=f"dependency {dep!r} failed: {exc}", code="dependency_failed")
            runner.results[name] = failed
            ctx.emit("request", **failed.trace())
            return failed
    result = await runner.fetch(name, lambda t, reveal: ctx.render_template(t, reveal=reveal))
    ctx.emit("request", **result.trace())
    if not result.ok and raise_on_error:
        raise RequestFailed(
            f"request {name!r} failed after {len(result.attempts)} attempt(s): {result.error}",
            name=name, code=result.code or "request_failed",
            details=[f"#{a.n} {a.outcome}: {a.error or a.status}" for a in result.attempts],
        )
    return result


async def do_goto(url: str, ctx: ActionContext) -> None:
    url = await ctx.render(url)
    try:
        await ctx.page.goto(url, wait_until="domcontentloaded")
    except Exception as exc:  # playwright TimeoutError / net errors
        raise TransientError(f"goto {url} failed: {exc}") from exc
    ctx.state = None


async def _wait_clickable(target: str, ctx: ActionContext, timeout_s: float,
                          *, operation: str = "click") -> tuple[str, list[str], ElementBox]:
    """Re-resolve through the observer until uncovered, enabled and geometrically stable.

    Layout animations need a timer: the PageState signature may remain unchanged
    while the target moves. No mouse input happens during this wait.
    """
    started = time.perf_counter()
    deadline = started + timeout_s
    previous: ElementBox | None = None
    polls = 0
    while True:
        polls += 1
        ctx.state = None  # the element may have been replaced since the previous sample
        try:
            element_id, why = await locate(target, ctx)
            box = await actionable_box(element_id, ctx)
        except (TargetNotFound, NotActionable) as exc:
            last_error = exc
            previous = None
        else:
            stable = previous is not None and (box.id, box.frame) == (previous.id, previous.frame) and all(
                abs(getattr(box, key) - getattr(previous, key)) <= 0.5 for key in ("x", "y", "w", "h")
            )
            if timeout_s == 0 or stable:
                if polls > 2:
                    ctx.emit("wait_clickable", target=target, polls=polls,
                             elapsed_ms=int((time.perf_counter() - started) * 1000))
                return element_id, why, box
            previous = box
            last_error = NotActionable(f"target {target!r} is not actionable: position or size is not stable")
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            raise type(last_error)(f"{operation} {target!r} timed out after {timeout_s:g}s: {last_error}",
                                   details=last_error.details) from last_error
        await asyncio.sleep(min(0.1, remaining))


async def do_click(target: str, ctx: ActionContext, *, timeout_s: float = 10.0) -> None:
    element_id, why, box = await _wait_clickable(target, ctx, timeout_s)
    ctx.emit("locate", target=target, element=element_id, attempts=why)
    await _click_box(box, ctx)
    ctx.state = None


async def do_fill(spec: Fill, ctx: ActionContext) -> None:
    value = await ctx.render(spec.value)
    if spec.shape:
        value = shapers.shape(spec.shape, value)
    element_id, why = await locate(spec.target, ctx)
    ctx.emit("locate", target=spec.target, element=element_id, attempts=why)
    await _click(element_id, ctx)
    if spec.clear:
        await ctx.human.clear_field()
    await ctx.human.type_text(value)
    typed = await ctx.observer.value_of(element_id)
    pii = _is_pii(spec, ctx)
    if pii:
        ctx.sensitive.add(element_id)
    # Masked inputs reformat as you type; compare digits/letters only.
    if typed is not None and _alnum(typed) and _alnum(value) and _alnum(value) not in _alnum(typed):
        # Never quote a credential in an error: it would travel into events,
        # task_run.error_message and the progress key.
        shown = (f"<{len(typed)} chars>", f"<{len(value)} chars>") if pii else (repr(typed), repr(value))
        raise StructuralError(
            f"fill {spec.target!r}: field holds {shown[0]} after typing {shown[1]}",
            details=[f"element {element_id}"],
        )
    ctx.emit("fill", target=spec.target, element=element_id, value="***" if pii else value)
    ctx.state = None


async def do_select(target: str, option: str, ctx: ActionContext) -> None:
    option = await ctx.render(option)
    element_id, why = await locate(target, ctx)
    ctx.emit("locate", target=target, element=element_id, attempts=why)
    await actionable_box(element_id, ctx)
    result = await ctx.observer.select_option(element_id, option)
    if not result.get("ok"):
        raise StructuralError(
            f"select {target!r}: {result.get('reason')}",
            details=[f"options: {result.get('options')}"] if result.get("options") else [],
        )
    ctx.emit("select", target=target, element=element_id, option=result.get("text"))
    ctx.state = None


async def do_press(key: str, target: str | None, ctx: ActionContext) -> None:
    if target:
        element_id, _ = await locate(target, ctx)
        await _click(element_id, ctx)
    await ctx.human.press(key)
    ctx.state = None


async def do_scroll(target: str | None, to: str | None, by: int | None, ctx: ActionContext) -> None:
    if target:
        element_id, _ = await locate(target, ctx)
        await ctx.observer.scroll_into_view(element_id)
    elif to == "top":
        await ctx.human.wheel(-100000)
    elif to == "bottom":
        await ctx.human.wheel(100000)
    elif by is not None:
        await ctx.human.wheel(by)
    ctx.state = None


async def do_wait_for(condition, timeout_s: float, ctx: ActionContext) -> None:
    deadline = time.perf_counter() + timeout_s
    while True:
        state = await ctx.fresh_state()
        m = conditions.evaluate(condition, ctx.eval_ctx(state))
        if m.ok:
            return
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            raise StructuralError(f"wait_for timed out after {timeout_s}s", details=m.reasons)
        await ctx.observer.wait_for_change(state.signature or "", min(remaining, 2.0))


async def do_wait_for_target(target: str, state: str, timeout_s: float, ctx: ActionContext) -> None:
    if state == "clickable":
        await _wait_clickable(target, ctx, timeout_s, operation="wait_for")
        return
    deadline = time.perf_counter() + timeout_s
    while True:
        ctx.state = None
        try:
            element_id, why = await locate(target, ctx)
            box = await ctx.observer.box(element_id)
        except TargetNotFound as exc:
            box, why = None, exc.details
        matches = {
            "visible": box is not None and box.visible,
            "hidden": box is None or not box.visible,
            "enabled": box is not None and not box.disabled,
            "disabled": box is not None and box.disabled,
        }
        if matches[state]:
            return
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            raise StructuralError(f"wait_for {target!r} state {state!r} timed out after {timeout_s:g}s",
                                  details=why)
        await asyncio.sleep(min(0.1, remaining))


async def do_fetch(spec: Fetch, ctx: ActionContext) -> None:
    result = await fetch_request(spec.request, ctx, raise_on_error=spec.on_error == "fail")
    ctx.vars[spec.into or spec.request] = dict(result.values) if result.ok else None


async def do_extract(spec: Extract, ctx: ActionContext) -> None:
    """Fields: the text of a target (resolved like any action target, so it can be
    any element the observer sees or a css selector) narrowed by `pattern`.
    Tables: rows keyed by header, for a table matched by caption/id or a header."""
    state = await ctx.fresh_state()
    out: dict[str, Any] = {}
    missing: list[str] = []
    notes: list[str] = []

    for fname, fs in spec.fields.items():
        text: str | None = None
        try:
            if fs.target:
                element_id, _ = await locate(fs.target, ctx)
            else:
                ids = await ctx.observer.query(fs.css or "")
                element_id = ids[0] if ids else None
            if element_id:
                text = await ctx.observer.text_of(element_id)
        except TargetNotFound as exc:
            notes.append(f"{fname}: {exc}")
        if text is not None and fs.pattern:
            m = _field_pattern(fs.pattern).search(text)
            text = (m.group(1) if m and m.groups() else m.group(0)) if m else None
        out[fname] = text
        if text in (None, "") and fs.required:
            missing.append(fname)

    for tname, ts in spec.tables.items():
        table = next(
            (t for t in state.tables
             if (ts.match is not None and Pattern(ts.match).test(t.name or t.id))
             or (ts.header is not None and any(Pattern(ts.header).test(h) for h in t.headers))),
            None,
        )
        rows = await ctx.observer.table_rows(table.id, max_rows=ts.max_rows) if table else None
        out[tname] = rows["rows"] if rows else None
        if not rows and ts.required:
            missing.append(tname)
            notes.append(f"{tname}: no table matches (tables: {[t.name or t.id for t in state.tables]})")

    ctx.vars[spec.into] = out if spec.schema_name is None else {"schema": spec.schema_name, **out}
    summary = {k: (len(v) if isinstance(v, list) else (None if v is None else "set")) for k, v in out.items()}
    ctx.emit("extract", into=spec.into, values=summary)
    if missing:
        raise StructuralError(f"extract into {spec.into!r}: required value(s) missing: {missing}", details=notes)


def _field_pattern(source: str) -> re.Pattern[str]:
    """Same syntax as every other pattern (`re:`, `/…/flags`, substring), but kept as a
    regex so a capture group can select the value."""
    p = Pattern(source)
    return p._regex  # noqa: SLF001 — Pattern is ours


# --- code: sandboxed scripts and in-page JavaScript ------------------------------------

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _has_secret(value: Any) -> bool:
    if isinstance(value, Secret):
        return True
    if isinstance(value, dict):
        return any(_has_secret(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_has_secret(v) for v in value)
    return False


async def _render_args(args: Any, ctx: ActionContext) -> Any:
    """Templates in arg values → values. A string that is exactly one template keeps
    the value's type (a Secret stays a Secret, a dict stays a dict)."""
    if isinstance(args, str):
        m = _TEMPLATE.fullmatch(args.strip())
        if m:
            for name in dict.fromkeys(r for r, _ in request_references(args)):
                await fetch_request(name, ctx)
            return _lookup_template(m.group(1), ctx)
        return await ctx.render(args)
    if isinstance(args, dict):
        return {k: await _render_args(v, ctx) for k, v in args.items()}
    if isinstance(args, list):
        return [await _render_args(v, ctx) for v in args]
    return args


def _lookup_template(path: str, ctx: ActionContext) -> Any:
    root, _, rest = path.partition(".")
    if root == "vars":
        value: Any = ctx.vars
    elif root == "page":
        value = {"url": ctx.state.url if ctx.state else "", "title": ctx.state.title if ctx.state else ""}
    elif root == "requests":
        name, _, rest = rest.partition(".")
        value = ctx.requests.values(name) if ctx.requests else None
        if value is None:
            raise RequestFailed(f"request {name!r} has no value for {{{path}}}", name=name)
    else:
        value, rest = ctx.inputs, path
    for part in [p for p in re.split(r"\.|\[(\d+)\]", rest) if p]:
        if isinstance(value, list) and part.isdigit():
            value = value[int(part)] if int(part) < len(value) else None
        else:
            value = value.get(part) if isinstance(value, dict) else getattr(value, part, None)
        if value is None:
            raise StructuralError(f"template {{{path}}} is not set")
    return value


def _reveal(value: Any) -> Any:
    if isinstance(value, Secret):
        return value.reveal()
    if isinstance(value, dict):
        return {k: _reveal(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_reveal(v) for v in value]
    return value


def _plain(value: Any) -> Any:
    """A JSON-safe copy without Secrets (what a script may read of vars/inputs)."""
    if isinstance(value, Secret):
        return None
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items() if not isinstance(v, Secret)}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _as_secrets(value: Any, ctx: ActionContext) -> Any:
    """Outputs of a script that saw secrets: every string becomes a Secret and the
    run's redactor learns it."""
    if isinstance(value, str):
        if ctx.requests is not None and ctx.requests.services.on_secret:
            ctx.requests.services.on_secret(value)
        return Secret(value)
    if isinstance(value, dict):
        return {k: _as_secrets(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [_as_secrets(v, ctx) for v in value]
    return value


def _store_result(into: str | None, value: Any, ctx: ActionContext, verb: str) -> list[str]:
    if into:
        ctx.vars[into] = value
        return [into]
    if not isinstance(value, dict):
        raise StructuralError(f"{verb}: without `into` the code must return an object (got {type(value).__name__})")
    bad = [k for k in value if not _IDENT.match(k) or k.startswith("_")]
    if bad:
        raise StructuralError(f"{verb}: returned keys are not valid var names: {bad}")
    ctx.vars.update(value)
    return list(value)


async def do_capture_image(spec: CaptureImage, ctx: ActionContext) -> None:
    # Resolve expected hash before overwriting `into`, including when it refers
    # to a previous capture stored under the same variable name.
    try:
        expected = await ctx.render(spec.expected_sha256) if spec.expected_sha256 else None
    finally:
        ctx.vars.pop(spec.into, None)  # a failed capture cannot leave stale pixels
    await ctx.fresh_state()
    element_id, _ = await locate(spec.target, ctx)
    try:
        captured = await ctx.observer.capture_image(element_id)
    except ValueError as exc:
        raise StructuralError(f"capture_image: {exc}") from None
    if expected is not None and expected != captured["sha256"]:
        raise StructuralError("capture_image: image changed since the expected capture")
    ctx.vars[spec.into] = {**captured, "data_url": _as_secrets(captured["data_url"], ctx)}
    ctx.emit("capture_image", target=spec.target, into=spec.into,
             width=captured["width"], height=captured["height"], sha256=captured["sha256"])


async def do_script(spec: Script, ctx: ActionContext) -> None:
    import asyncio

    from graphwright.engine.sandbox import run_js

    args = await _render_args(spec.args, ctx)
    secret = _has_secret(args)
    if secret and not spec.allow_secrets:
        raise StructuralError("script: an arg is a secret (credential or sensitive request value); "
                              "set `allow_secrets: true` if the code really needs it")
    result = await asyncio.to_thread(
        run_js, spec.code, args=_reveal(args), vars=_plain(ctx.vars), inputs=_plain(dict(ctx.inputs)),
        timeout_ms=spec.timeout_ms, memory_mb=spec.memory_mb,
    )
    if not result.ok:
        ctx.emit("script", ok=False, error=result.error, logs=result.logs, elapsed_ms=result.elapsed_ms)
        raise StructuralError(f"script failed: {(result.error or '').splitlines()[0] if result.error else 'error'}",
                              details=[*(result.error or "").splitlines()[:8], *result.logs[-10:]])
    value = _as_secrets(result.value, ctx) if secret else result.value
    names = _store_result(spec.into, value, ctx, "script")
    ctx.emit("script", ok=True, set=names, logs=result.logs, elapsed_ms=result.elapsed_ms,
             value="***" if secret else _summary(result.value))


async def do_evaluate(spec: Evaluate, ctx: ActionContext) -> None:
    import asyncio
    import json as _json
    import time as _time

    args = await _render_args(spec.args, ctx)
    secret = _has_secret(args)
    if secret and not spec.allow_secrets:
        raise StructuralError("evaluate: an arg is a secret; set `allow_secrets: true` to hand it to the page")
    t0 = _time.perf_counter()
    try:
        value = await asyncio.wait_for(
            ctx.observer.evaluate(spec.script, _reveal(args), world=spec.world, frame=spec.frame),
            spec.timeout_ms / 1000,
        )
    except TimeoutError as exc:
        raise TransientError(f"evaluate did not finish in {spec.timeout_ms} ms") from exc
    except Exception as exc:  # the page threw, or the frame is gone
        ctx.emit("evaluate", ok=False, world=spec.world, error=str(exc))
        raise StructuralError(f"evaluate failed: {exc}") from exc
    elapsed = int((_time.perf_counter() - t0) * 1000)
    try:
        _json.dumps(value)
    except (TypeError, ValueError) as exc:
        raise StructuralError(f"evaluate returned a non-JSON value: {exc}") from exc
    stored = _as_secrets(value, ctx) if secret else value
    names = _store_result(spec.into, stored, ctx, "evaluate") if (spec.into or value is not None) else []
    ctx.emit("evaluate", ok=True, world=spec.world, set=names, elapsed_ms=elapsed,
             value="***" if secret else _summary(value))
    ctx.state = None  # the script may have changed the page


def _summary(value: Any, limit: int = 300) -> Any:
    import json as _json

    text = _json.dumps(value, ensure_ascii=False, default=str)
    return value if len(text) <= limit else text[:limit] + "…"


async def do_assert(condition, message: str, ctx: ActionContext) -> None:
    state = ctx.state or await ctx.fresh_state()
    m = conditions.evaluate(condition, ctx.eval_ctx(state))
    if not m.ok:
        raise StructuralError(message or "assertion failed", details=m.reasons)


async def run_action(action: Action, ctx: ActionContext) -> None:
    verb = action.verb
    body = action.body
    ctx.emit("action", verb=verb, spec=body.model_dump(mode="json", exclude_none=True))
    if verb in ("click", "fill") and body.delay_s:
        # Delay before resolving or rendering: lazy OTP requests must still be
        # fetched at the moment of use, not before an intentional pause.
        await asyncio.sleep(body.delay_s)
        ctx.state = None
    if verb == "goto":
        await do_goto(body.url, ctx)
    elif verb == "click":
        await do_click(body.target, ctx, timeout_s=body.timeout_s)
    elif verb == "fill":
        await do_fill(body, ctx)
    elif verb == "select":
        await do_select(body.target, body.option, ctx)
    elif verb == "press":
        await do_press(body.key, body.target, ctx)
    elif verb == "scroll":
        await do_scroll(body.target, body.to, body.by, ctx)
    elif verb == "wait_for":
        if body.target is not None:
            await do_wait_for_target(body.target, body.state, body.timeout_s, ctx)
        else:
            await do_wait_for(body.condition, body.timeout_s, ctx)
    elif verb == "fetch":
        await do_fetch(body, ctx)
    elif verb == "extract":
        await do_extract(body, ctx)
    elif verb == "capture_image":
        await do_capture_image(body, ctx)
    elif verb == "script":
        await do_script(body, ctx)
    elif verb == "evaluate":
        await do_evaluate(body, ctx)
    elif verb == "assert":
        await do_assert(body.condition, body.message, ctx)
    elif verb == "jump":
        raise JumpSignal(body.to)
    elif verb == "call":
        raise StructuralError(f"macro {body.macro!r}: macros are not implemented yet")
    else:
        raise StructuralError(f"unknown verb {verb!r}")


def _alnum(s: str) -> str:
    return re.sub(r"[^0-9a-zA-Z]", "", s)


def _is_pii(spec: Fill, ctx: ActionContext) -> bool:
    for m in _TEMPLATE.finditer(spec.value):
        root, _, rest = m.group(1).partition(".")
        if root == "requests":
            req = ctx.workflow.requests.get(rest.split(".", 1)[0])
            if req is None or req.sensitive:
                return True
            continue
        if root == "vars":
            # a fetched sensitive value is stored as a Secret
            if isinstance(conditions.lookup_var(ctx.vars, rest), Secret):
                return True
            continue
        inp = ctx.workflow.inputs.get(root)
        if inp is not None and inp.pii:
            return True
    return False
