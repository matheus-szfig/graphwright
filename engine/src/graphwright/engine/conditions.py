"""Predicate evaluation with reasons.

`evaluate(condition, ctx)` returns a `Match` that says whether the condition held and
*why* (or why not), one line per leaf predicate. The builder's "why didn't this
transition fire" view is just `Match.reasons`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from graphwright.engine import targets as targets_mod
from graphwright.engine.schema import Condition, Pattern, Strategy, Target
from graphwright.observe.pagestate import PageState


@dataclass
class EvalContext:
    state: PageState
    targets: dict[str, Target] = field(default_factory=dict)
    vars: dict[str, Any] = field(default_factory=dict)
    requests: dict[str, str] = field(default_factory=dict)  # request name → ok | failed | pending


@dataclass
class Match:
    ok: bool
    reasons: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.ok


def _texts(ctx: EvalContext, where: str) -> list[str]:
    s = ctx.state
    if where == "alerts":
        return list(s.alerts)
    if where == "headings":
        return [h.text for h in s.headings]
    if where == "title":
        return [s.title]
    if where == "elements":
        out: list[str] = []
        for e in s.elements:
            out.append(e.name)
            if e.value:
                out.append(e.value)
            out.extend(e.nearby)
        return out
    return _texts(ctx, "alerts") + _texts(ctx, "headings") + _texts(ctx, "title") + _texts(ctx, "elements")


def _visible(spec: str | Strategy, ctx: EvalContext) -> tuple[bool, str]:
    if isinstance(spec, str):
        target = ctx.targets.get(spec)
        if target is None:
            return False, f"visible {spec!r}: unknown target"
        res = targets_mod.resolve(spec, target, ctx.state)
        if res.ok:
            return True, f"visible {spec!r}: {res.element.id} ({res.element.role} {res.element.name!r})"
        return False, f"visible {spec!r}: not found — " + "; ".join(res.explain(target))
    matches = targets_mod.resolve_inline(spec, ctx.state)
    crit = spec.model_dump(exclude_none=True, exclude_defaults=True)
    if matches:
        return True, f"visible {crit}: {len(matches)} match(es) → {[m.id for m in matches[:3]]}"
    return False, f"visible {crit}: no match"


def lookup_var(vars: dict[str, Any], path: str) -> Any:
    """`record.title` → vars["record"]["title"]; None when any hop is missing."""
    value: Any = vars
    for part in path.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        else:
            value = getattr(value, part, None)
        if value is None:
            return None
    return value


def _var(cond, ctx: EvalContext) -> tuple[bool, str]:
    val = lookup_var(ctx.vars, cond.name)
    present = val not in (None, "")
    label = f"var {cond.name}"
    if cond.op == "set":
        return present, f"{label} is {'set' if present else 'unset'}"
    if cond.op == "unset":
        return not present, f"{label} is {'set' if present else 'unset'}"
    if cond.op == "eq":
        return val == cond.value, f"{label}={val!r} eq {cond.value!r}"
    if cond.op == "ne":
        return val != cond.value, f"{label}={val!r} ne {cond.value!r}"
    if cond.op == "match":
        ok = Pattern(str(cond.value)).test(str(val) if val is not None else None)
        return ok, f"{label}={val!r} match {cond.value!r}"
    if cond.op == "gt":
        return (val is not None and val > cond.value), f"{label}={val!r} gt {cond.value!r}"
    if cond.op == "lt":
        return (val is not None and val < cond.value), f"{label}={val!r} lt {cond.value!r}"
    return False, f"{label}: unknown op {cond.op}"


def evaluate(cond: Condition, ctx: EvalContext) -> Match:
    s = ctx.state
    kind = cond.kind

    if kind == "always":
        return Match(bool(cond.always), ["always"])

    if kind == "url":
        p = Pattern(cond.url)
        ok = p.test(s.route) or p.test(s.url)
        return Match(ok, [f"url {cond.url!r} {'matches' if ok else 'does not match'} {s.route!r}"])

    if kind == "frame_url":
        p = Pattern(cond.frame_url)
        hits = [f"f{f.index}" for f in s.frames if p.test(f.route) or p.test(f.url)]
        ok = bool(hits)
        return Match(ok, [f"frame_url {cond.frame_url!r}: " + (f"matches {hits}" if ok else f"no frame matches ({len(s.frames)} frames)")])

    if kind == "page_type":
        ok = s.page_type == cond.page_type
        return Match(ok, [f"page_type {cond.page_type!r} vs observed {s.page_type!r}"])

    if kind == "visible":
        ok, why = _visible(cond.visible, ctx)
        return Match(ok, [why])

    if kind == "hidden":
        ok, why = _visible(cond.hidden, ctx)
        return Match(not ok, [why.replace("visible", "hidden", 1)])

    if kind == "text":
        p = Pattern(cond.text)
        hay = _texts(ctx, cond.in_)
        hit = next((t for t in hay if p.test(t)), None)
        ok = hit is not None
        return Match(ok, [f"text {cond.text!r} in {cond.in_}: " + (f"found in {hit[:60]!r}" if ok else f"not found ({len(hay)} strings)")])

    if kind == "var":
        ok, why = _var(cond.var, ctx)
        return Match(ok, [why])

    if kind == "request":
        rc = cond.request
        status = ctx.requests.get(rc.name, "pending")
        ok = status == rc.status
        return Match(ok, [f"request {rc.name!r} is {status} (want {rc.status})"])

    if kind == "frame_count":
        n = len(s.frames)
        fc = cond.frame_count
        ok = n >= fc.min and (fc.max is None or n <= fc.max)
        return Match(ok, [f"frame_count {n} within [{fc.min}, {fc.max}]" if ok else f"frame_count {n} outside [{fc.min}, {fc.max}]"])

    if kind == "all":
        reasons: list[str] = []
        ok = True
        for sub in cond.all:
            m = evaluate(sub, ctx)
            reasons.extend(m.reasons)
            if not m.ok:
                ok = False
        return Match(ok, [f"all({'ok' if ok else 'failed'}):", *("  " + r for r in reasons)])

    if kind == "any":
        reasons = []
        for sub in cond.any:
            m = evaluate(sub, ctx)
            reasons.extend(m.reasons)
            if m.ok:
                return Match(True, ["any(ok):", *("  " + r for r in reasons)])
        return Match(False, ["any(failed):", *("  " + r for r in reasons)])

    if kind == "not_":
        m = evaluate(cond.not_, ctx)
        return Match(not m.ok, [f"not({'ok' if not m.ok else 'failed'}):", *("  " + r for r in m.reasons)])

    return Match(False, [f"unknown condition kind {kind!r}"])
