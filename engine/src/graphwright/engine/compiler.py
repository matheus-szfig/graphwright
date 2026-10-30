"""Workflow → CompiledGraph, with lint.

The compiler is the gate every graph version passes through — hand-written or
LLM-patched. It rejects dangling references, unreachable nodes, terminals with
outgoing edges, missing failure routes and unknown shapers/macros, then wraps each
node so the engine can ask `recognize(state)` and `next(state)` and get back a
`Match` trace explaining the decision.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from graphwright.engine import conditions, shapers
from graphwright.engine.conditions import EvalContext, Match
from graphwright.engine.requests import references as request_references
from graphwright.engine.requests import template_strings as _request_template_strings
from graphwright.engine.schema import Condition, Node, Target, Workflow
from graphwright.engine.targets import Resolution, resolve
from graphwright.observe.pagestate import PageState

_TEMPLATE_VAR = re.compile(r"\{([a-zA-Z_][\w.]*)\}")

# A fill into one of these targets must use an input template, never a literal: the
# graph file is reviewed, versioned and shipped — credentials and personal data are
# provided per run through `inputs` (which default to pii: true and are redacted).
_SENSITIVE_TARGET = re.compile(
    r"(password|passwd|pwd|secret|token|user|login|e-?mail|ssn|tax_?id|national_?id|document"
    r"|otp|totp|2fa|mfa|verification|pin\b|certificate|passphrase|card|cvv|iban)",
    re.IGNORECASE,
)


class GraphError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("graph failed to compile:\n  - " + "\n  - ".join(errors))


@dataclass
class Decision:
    """Outcome of evaluating a node's transitions against an observation."""

    to: str | None
    trace: list[tuple[str, Match]]  # (transition target, match) in evaluation order

    def explain(self) -> list[str]:
        lines: list[str] = []
        for to, m in self.trace:
            lines.append(f"→ {to}: {'MATCH' if m.ok else 'no'}")
            lines.extend("    " + r for r in m.reasons)
        if self.to is None:
            lines.append("no transition matched")
        return lines


@dataclass
class CompiledNode:
    name: str
    spec: Node
    graph: CompiledGraph = field(repr=False)

    def ctx(self, state: PageState, vars: dict[str, Any] | None = None, requests: dict[str, str] | None = None) -> EvalContext:
        return EvalContext(state=state, targets=self.graph.workflow.targets, vars=vars or {}, requests=requests or {})

    def recognize(self, state: PageState, vars: dict[str, Any] | None = None, requests: dict[str, str] | None = None) -> Match:
        if self.spec.recognize is None:
            return Match(False, ["node has no recognize (terminal or call)"])
        return conditions.evaluate(self.spec.recognize, self.ctx(state, vars, requests))

    def next(self, state: PageState, vars: dict[str, Any] | None = None, requests: dict[str, str] | None = None) -> Decision:
        ctx = self.ctx(state, vars, requests)
        trace: list[tuple[str, Match]] = []
        for t in self.spec.transitions:
            m = conditions.evaluate(t.when, ctx)
            trace.append((t.to, m))
            if m.ok:
                return Decision(to=t.to, trace=trace)
        return Decision(to=None, trace=trace)

    def check_postconditions(self, state: PageState, vars: dict[str, Any] | None = None, requests: dict[str, str] | None = None) -> list[Match]:
        ctx = self.ctx(state, vars, requests)
        return [conditions.evaluate(c, ctx) for c in self.spec.postconditions]

    def resolve_targets(self, state: PageState) -> dict[str, Resolution]:
        """Resolve every target this node's actions reference."""
        out: dict[str, Resolution] = {}
        for a in self.spec.actions:
            if a.target and a.target not in out:
                out[a.target] = resolve(a.target, self.graph.workflow.targets[a.target], state)
        return out


@dataclass
class CompiledGraph:
    workflow: Workflow
    nodes: dict[str, CompiledNode] = field(default_factory=dict)

    def __getitem__(self, name: str) -> CompiledNode:
        return self.nodes[name]

    @property
    def start(self) -> CompiledNode:
        return self.nodes[self.workflow.start]

    def resync(self, state: PageState, vars: dict[str, Any] | None = None, *, exclude: Iterable[str] = (),
               requests: dict[str, str] | None = None) -> tuple[str | None, dict[str, Match]]:
        """Find nodes whose `recognize` matches the current page. Returns the first
        match in declaration order plus every node's Match for the builder."""
        skip = set(exclude)
        results: dict[str, Match] = {}
        winner: str | None = None
        for name, node in self.nodes.items():
            if node.spec.recognize is None or name in skip:
                continue
            m = node.recognize(state, vars, requests)
            results[name] = m
            if m.ok and winner is None:
                winner = name
        return winner, results

    def edges(self) -> list[tuple[str, str, str]]:
        """(from, to, label) for every transition / call / on_fail — for the canvas."""
        out: list[tuple[str, str, str]] = []
        for name, node in self.workflow.nodes.items():
            for t in node.transitions:
                out.append((name, t.to, _condition_label(t.when)))
            if node.call and node.then:
                out.append((name, node.then, f"call {node.call}"))
            if node.on_fail not in ("repair", "fail"):
                out.append((name, node.on_fail, "on_fail"))
        return out


def _condition_label(c: Condition) -> str:
    k = c.kind
    if k == "always":
        return "always"
    if k in ("all", "any"):
        return f"{k}({len(getattr(c, k))})"
    if k == "not_":
        return "not " + _condition_label(c.not_)
    if k == "visible":
        return f"visible {c.visible if isinstance(c.visible, str) else 'inline'}"
    if k == "text":
        return f"text {c.text!r}"
    if k == "request":
        return f"request {c.request.name} {c.request.status}"
    return f"{k} {getattr(c, k)!r}"[:40]


# --- lint -----------------------------------------------------------------------------


def _condition_requests(c: Condition) -> Iterable[str]:
    if c.request is not None:
        yield c.request.name
    for sub in (c.all or []) + (c.any or []) + ([c.not_] if c.not_ else []):
        yield from _condition_requests(sub)


def _condition_targets(c: Condition) -> Iterable[str]:
    if isinstance(c.visible, str):
        yield c.visible
    if isinstance(c.hidden, str):
        yield c.hidden
    for sub in (c.all or []) + (c.any or []) + ([c.not_] if c.not_ else []):
        yield from _condition_targets(sub)


def _check_template(text: str, where: str, wf: Workflow, errors: list[str], *, allow_page: bool = True) -> None:
    for m in _TEMPLATE_VAR.finditer(text):
        var = m.group(1)
        root = var.split(".", 1)[0]
        if root == "requests":
            continue  # checked by _check_request_refs
        if root == "vars" or (root == "page" and allow_page):
            continue
        if root not in wf.inputs:
            errors.append(f"{where} references unknown input {{{var}}}")
    _check_request_refs(text, where, wf, errors)


def _check_request_refs(text: str, where: str, wf: Workflow, errors: list[str]) -> None:
    for req, fld in request_references(text):
        spec = wf.requests.get(req)
        if spec is None:
            errors.append(f"{where} references unknown request {{requests.{req}}}")
        elif fld is None:
            errors.append(f"{where}: {{requests.{req}}} needs a field, e.g. {{requests.{req}.{(spec.fields or ['value'])[0]}}}")
        elif fld.split(".", 1)[0] not in spec.fields:
            errors.append(f"{where}: request {req!r} has no field {fld!r} (it extracts {spec.fields or 'nothing'})")


def _lint_requests(wf: Workflow, errors: list[str]) -> None:
    graph: dict[str, set[str]] = {}
    for name, spec in wf.requests.items():
        if not _NAME.match(name):
            errors.append(f"request {name!r}: names are snake_case")
        where = f"request {name!r}"
        deps: set[str] = set()
        for text in _request_template_strings(spec):
            _check_template(text, where, wf, errors, allow_page=True)
            deps.update(r for r, _ in request_references(text))
        graph[name] = deps
        if spec.kind == "totp" and spec.extract:
            errors.append(f"{where}: a totp request yields `code`; it takes no `extract`")
        if spec.kind == "imap" and any(ex.regex is None for ex in spec.extract.values()):
            errors.append(f"{where}: imap extractors must be `regex`")
        if spec.kind == "http" and not spec.extract:
            errors.append(f"{where}: an http request needs `extract` (what to read from the response)")
        if spec.retry.until_extracted and spec.retry.attempts < 2:
            errors.append(f"{where}: `until_extracted` needs `attempts` >= 2 to poll")
        for k, ex in spec.extract.items():
            if ex.regex is not None:
                try:
                    re.compile(ex.regex)
                except re.error as exc:
                    errors.append(f"{where}: extract {k!r} has an invalid regex: {exc}")
    # requests may use other requests' values, but not in a cycle
    state: dict[str, int] = {}

    def visit(n: str, path: list[str]) -> None:
        if state.get(n) == 2:
            return
        if state.get(n) == 1:
            errors.append(f"requests form a cycle: {' → '.join([*path, n])}")
            return
        state[n] = 1
        for d in graph.get(n, ()):
            if d in graph:
                visit(d, [*path, n])
        state[n] = 2

    for n in graph:
        visit(n, [])


_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


def _input_name_for(target: str) -> str:
    """Suggest an input name for a sensitive target: `password_field` → `password`."""
    t = target.lower()
    for key in ("password", "user", "login", "email", "token", "otp", "pin"):
        if key in t:
            return key
    return re.sub(r"_(field|input|box)$", "", t) or "value"


def lint(wf: Workflow, *, macros: Iterable[str] = ()) -> list[str]:
    errors: list[str] = []
    nodes = wf.nodes
    known_macros = set(macros)

    if wf.start not in nodes:
        errors.append(f"start node {wf.start!r} does not exist")
    if wf.entry_url:
        _check_template(wf.entry_url, "entry_url", wf, errors, allow_page=False)
    for iname, spec in wf.inputs.items():
        if spec.credential and spec.default is not None:
            errors.append(f"input {iname!r}: a credential input cannot have a default")
    _lint_requests(wf, errors)

    for name, node in nodes.items():
        for t in node.transitions:
            if t.to not in nodes:
                errors.append(f"{name}: transition to unknown node {t.to!r}")
        if node.then and node.then not in nodes:
            errors.append(f"{name}: `then` points to unknown node {node.then!r}")
        if node.on_fail not in ("repair", "fail") and node.on_fail not in nodes:
            errors.append(f"{name}: on_fail points to unknown node {node.on_fail!r}")
        if node.call and known_macros and node.call not in known_macros:
            errors.append(f"{name}: unknown subgraph/macro {node.call!r}")

        for c in [node.recognize, *node.postconditions, *(t.when for t in node.transitions)]:
            if c is None:
                continue
            for tgt in _condition_targets(c):
                if tgt not in wf.targets:
                    errors.append(f"{name}: condition references unknown target {tgt!r}")
            for req in _condition_requests(c):
                if req not in wf.requests:
                    errors.append(f"{name}: condition references unknown request {req!r}")

        for i, a in enumerate(node.actions):
            if a.target and a.target not in wf.targets:
                errors.append(f"{name}: action #{i} ({a.verb}) references unknown target {a.target!r}")
            if a.fill and a.fill.shape and a.fill.shape not in shapers.SHAPERS:
                errors.append(f"{name}: action #{i} uses unknown shaper {a.fill.shape!r}")
            if a.fill and not a.fill.literal and a.fill.value.strip() and not _TEMPLATE_VAR.search(a.fill.value):
                target = wf.targets.get(a.fill.target)
                haystack = a.fill.target + " " + (target.description if target else "")
                if _SENSITIVE_TARGET.search(haystack):
                    errors.append(
                        f"{name}: action #{i} fills {a.fill.target!r} with a literal value — credentials and "
                        f"personal data must come from inputs, e.g. value: \"{{{_input_name_for(a.fill.target)}}}\""
                    )
            if a.jump and a.jump.to not in nodes:
                errors.append(f"{name}: action #{i} jumps to unknown node {a.jump.to!r}")
            if a.call and known_macros and a.call.macro not in known_macros:
                errors.append(f"{name}: action #{i} calls unknown macro {a.call.macro!r}")
            if a.fetch and a.fetch.request not in wf.requests:
                errors.append(f"{name}: action #{i} fetches unknown request {a.fetch.request!r}")
            if a.script or a.evaluate:
                from graphwright.engine.sandbox import syntax_error

                body = a.script.code if a.script else a.evaluate.script
                err = syntax_error(body, is_async=bool(a.evaluate))
                if err:
                    errors.append(f"{name}: action #{i} ({a.verb}) does not compile: {err}")
                code_spec = a.script or a.evaluate
                if code_spec.into is not None and not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", code_spec.into):
                    errors.append(f"{name}: action #{i} ({a.verb}) `into` must be a var name, got {code_spec.into!r}")

                def _arg_strings(v):
                    if isinstance(v, str):
                        yield v
                    elif isinstance(v, dict):
                        for x in v.values():
                            yield from _arg_strings(x)
                    elif isinstance(v, list):
                        for x in v:
                            yield from _arg_strings(x)

                for text in _arg_strings(code_spec.args):
                    _check_template(text, f"{name}: action #{i} ({a.verb}) args", wf, errors)
            if a.extract:
                for fname, fs in a.extract.fields.items():
                    if fs.target and fs.target not in wf.targets:
                        errors.append(f"{name}: action #{i} extract field {fname!r} references unknown target {fs.target!r}")
                if not a.extract.fields and not a.extract.tables:
                    errors.append(f"{name}: action #{i} extract needs `fields` or `tables`")
            for attr in ("value", "url", "option", "expected_sha256"):
                v = getattr(a.body, attr, None)
                if isinstance(v, str):
                    _check_template(v, f"{name}: action #{i}", wf, errors)

        if not node.is_terminal and node.max_visits < 1:
            errors.append(f"{name}: max_visits must be >= 1")

    # reachability from start (transitions, then, on_fail, jumps)
    if wf.start in nodes:
        seen: set[str] = set()
        stack = [wf.start]
        while stack:
            n = stack.pop()
            if n in seen or n not in nodes:
                continue
            seen.add(n)
            node = nodes[n]
            stack.extend(t.to for t in node.transitions)
            if node.then:
                stack.append(node.then)
            if node.on_fail not in ("repair", "fail"):
                stack.append(node.on_fail)
            stack.extend(a.jump.to for a in node.actions if a.jump)
        for n in nodes:
            if n not in seen:
                errors.append(f"{n}: unreachable from start")
        if not any(nodes[n].terminal == "success" for n in seen):
            errors.append("no reachable `terminal: success` node")

    return errors


def compile_workflow(wf: Workflow, *, macros: Iterable[str] = ()) -> CompiledGraph:
    errors = lint(wf, macros=macros)
    if errors:
        raise GraphError(errors)
    graph = CompiledGraph(workflow=wf)
    for name, node in wf.nodes.items():
        graph.nodes[name] = CompiledNode(name=name, spec=node, graph=graph)
    return graph


def targets_report(targets: dict[str, Target], state: PageState) -> dict[str, Resolution]:
    """Resolve every target of a workflow against one observation (builder inspector)."""
    return {name: resolve(name, t, state) for name, t in targets.items()}
