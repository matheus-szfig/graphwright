"""Graph store — YAML files in a directory, edited through typed operations.

Saving is an authoring operation, not an execution gate. Raw YAML may be invalid;
views carry diagnostics and a safe display projection. Only execution compiles.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import yaml
from graphwright.engine import Workflow
from graphwright.engine.compiler import CompiledGraph, lint
from graphwright.engine.schema import (
    Action,
    Condition,
    InputSpec,
    Node,
    PageFilter,
    RequestSpec,
    Script,
    Strategy,
    Target,
    Transition,
    dump_yaml,
)
from pydantic import BaseModel, ValidationError

_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


class GraphView(BaseModel):
    name: str
    path: str
    yaml: str
    workflow: dict[str, Any]
    edges: list[dict[str, str]]
    lint: list[str]


class GraphSummary(BaseModel):
    name: str
    workflow: str
    version: int
    portal: str
    nodes: int
    lint_ok: bool


class StoreError(ValueError):
    def __init__(self, message: str, errors: list[str] | None = None, status: int = 422) -> None:
        super().__init__(message)
        self.errors = errors or [message]
        self.status = status


def _edges(wf: Workflow) -> list[dict[str, str]]:
    # Drawing edges does not compile or approve a graph for execution.
    return [{"from": a, "to": b, "label": label}
            for a, b, label in CompiledGraph(wf, {}).edges()
            if a in wf.nodes and b in wf.nodes]


def _display_workflow(name: str, text: str) -> Workflow:
    """Best-effort canvas model. Never write or execute this repaired projection."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        data = {}
    data = data if isinstance(data, dict) else {}
    wf = Workflow(workflow=name, portal="?", start="", nodes={})
    for key in ("workflow", "portal", "start", "entry_url"):
        if isinstance(data.get(key), str):
            setattr(wf, key, data[key])
    if isinstance(data.get("scripts"), dict):
        wf.scripts = {k: v for k, v in data["scripts"].items() if isinstance(k, str) and isinstance(v, str)}
    for field, model in (("nodes", Node), ("targets", Target), ("inputs", InputSpec), ("requests", RequestSpec)):
        items = data.get(field)
        if not isinstance(items, dict):
            continue
        for key, value in items.items():
            if not isinstance(key, str):
                continue
            try:
                getattr(wf, field)[key] = model.model_validate(value)
            except ValidationError:
                if field == "nodes":
                    wf.nodes[key] = Node(intent="Invalid node — edit YAML", recognize=Condition(always=True),
                                         transitions=[Transition(to="")])
    return wf


_ITEM_HINTS = {
    "Action": "empty item — give it a verb: click, fill, select, press, scroll, wait_for, goto, fetch, extract, script, evaluate, assert, jump, call",
    "Condition": "empty item — give it a predicate: url, visible, text, frame_url, page_type, var, all, any, not",
    "Transition": "empty item — it needs `to:` (and optionally `when:`)",
    "Strategy": "empty item — it needs at least one of role, name, dom_id, nearby, landmark, css",
}


def _friendly(err: dict[str, Any]) -> str:
    """Pydantic's error → one line a graph author understands. An empty `- ` list item
    shows up as "Input should be a valid dictionary or instance of X"; say what X needs."""
    loc = ".".join(map(str, err.get("loc", ())))
    msg = str(err.get("msg", ""))
    for cls, hint in _ITEM_HINTS.items():
        if msg.endswith(f"instance of {cls}") and err.get("input") is None:
            return f"{loc}: {hint}"
    return f"{loc}: {msg}"


def _validation_errors(exc: ValidationError) -> list[str]:
    return [_friendly(e) for e in exc.errors()]


CODE_NODE_STUB = """// Sandboxed JavaScript (fresh V8 isolate, no network/files). In scope:
//   args (this action's args, rendered), vars, inputs (non-secret copies), console.
// Return an object: each key becomes a var (or set `into:` to store it whole).
return {};
"""
NODE_KINDS = ("step", "code", "outcome", "success", "failure")


def node_to_yaml(node: Node) -> str:
    data = node.model_dump(mode="json", by_alias=True, exclude_none=True, exclude_defaults=True)
    return dump_yaml(data)


class GraphStore:
    def __init__(self, directory: Path) -> None:
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)

    # -- files --

    def _path(self, name: str) -> Path:
        if not _NAME.match(name):
            raise StoreError(f"invalid graph name {name!r}", status=400)
        return self.dir / f"{name}.yaml"

    def list(self) -> list[GraphSummary]:
        out: list[GraphSummary] = []
        for p in sorted(self.dir.glob("*.yaml")):
            try:
                wf = Workflow.from_yaml(p)
                out.append(GraphSummary(name=p.stem, workflow=wf.workflow, version=wf.version,
                                        portal=wf.portal, nodes=len(wf.nodes), lint_ok=not lint(wf)))
            except (ValidationError, yaml.YAMLError) as exc:
                out.append(GraphSummary(name=p.stem, workflow=p.stem, version=0, portal="?", nodes=0, lint_ok=False))
                _ = exc
        return out

    def load(self, name: str) -> Workflow:
        p = self._path(name)
        if not p.exists():
            raise StoreError(f"graph {name!r} not found", status=404)
        try:
            return Workflow.from_yaml(p)
        except ValidationError as exc:
            raise StoreError("graph does not validate", _validation_errors(exc)) from exc
        except yaml.YAMLError as exc:
            raise StoreError(f"invalid YAML: {exc}") from exc

    def view(self, name: str, wf: Workflow | None = None) -> GraphView:
        text = self.read_text(name)
        errors = self.lint_yaml(text)
        if wf is None:
            try:
                wf = Workflow.from_yaml_text(text)
            except (ValidationError, yaml.YAMLError):
                wf = _display_workflow(name, text)
        return GraphView(
            name=name, path=str(self._path(name)), yaml=text,
            workflow=wf.model_dump(mode="json", by_alias=True, exclude_none=True),
            edges=_edges(wf), lint=errors,
        )

    def read_text(self, name: str) -> str:
        path = self._path(name)
        if not path.exists():
            raise StoreError(f"graph {name!r} not found", status=404)
        return path.read_text(encoding="utf-8")

    def _write_text(self, name: str, text: str) -> None:
        path = self._path(name)
        # Readers (including workers) see either saved version, never a truncated
        # file while the editor writes. The temporary file is on the same volume.
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.dir, suffix=".tmp", delete=False) as out:
            temp = Path(out.name)
            try:
                out.write(text)
            except BaseException:
                out.close()
                temp.unlink(missing_ok=True)
                raise
        try:
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)

    def _document(self, name: str) -> dict[str, Any]:
        try:
            data = yaml.safe_load(self.read_text(name))
        except yaml.YAMLError as exc:
            raise StoreError("Repair the graph YAML syntax before editing individual nodes") from exc
        if not isinstance(data, dict) or not isinstance(data.get("nodes"), dict):
            raise StoreError("Repair the graph's nodes mapping in Graph YAML first")
        return data

    def node_view(self, name: str, node: str) -> dict[str, Any]:
        data = self._document(name)
        if node not in data["nodes"]:
            raise StoreError(f"node {node!r} not found", status=404)
        value = data["nodes"][node]
        try:
            parsed = Node.model_validate(value)
            text = node_to_yaml(parsed)
            shown = parsed.model_dump(mode="json", by_alias=True, exclude_none=True)
        except ValidationError:
            text = value if isinstance(value, str) else dump_yaml(value)
            shown = {}
        return {"name": node, "yaml": text, "node": shown}

    def save(self, name: str, wf: Workflow, *, bump_version: bool = False) -> GraphView:
        if bump_version:
            wf.version += 1
        self._write_text(name, wf.to_yaml())
        return self.view(name, wf)

    # -- typed operations --

    def create(self, name: str, portal: str, entry_url: str | None) -> GraphView:
        p = self._path(name)
        if p.exists():
            raise StoreError(f"graph {name!r} already exists", status=409)
        wf = Workflow.model_validate({
            "workflow": name, "portal": portal, "entry_url": entry_url, "start": "start",
            "nodes": {
                "start": {"intent": "describe this page", "recognize": {"always": True}, "transitions": [{"to": "done"}]},
                "done": {"terminal": "success"},
            },
        })
        return self.save(name, wf)

    def delete(self, name: str) -> None:
        """Remove the graph's YAML file. Works on files that no longer validate, so a
        broken graph can always be thrown away; git keeps the history."""
        p = self._path(name)
        if not p.exists():
            raise StoreError(f"graph {name!r} not found", status=404)
        p.unlink()

    def put_yaml(self, name: str, text: str) -> GraphView:
        self._write_text(name, text)
        return self.view(name)

    def lint_yaml(self, text: str) -> list[str]:
        try:
            return lint(Workflow.from_yaml_text(text))
        except ValidationError as exc:
            return _validation_errors(exc)
        except yaml.YAMLError as exc:
            return [f"invalid YAML: {exc}"]

    def put_node(self, name: str, node_name: str, text: str) -> GraphView:
        data = self._document(name)
        try:
            value = yaml.safe_load(text)
        except yaml.YAMLError:
            # Preserve a malformed fragment as a scalar at its node key. It is
            # intentionally schema-invalid, but other nodes remain editable.
            value = text
        data["nodes"][node_name] = value
        return self.put_yaml(name, dump_yaml(data))

    def add_node(self, name: str, node_name: str, kind: str = "step", intent: str = "") -> GraphView:
        if not _NAME.match(node_name):
            raise StoreError(f"invalid node name {node_name!r}", status=400)
        wf = self.load(name)
        if node_name in wf.nodes:
            raise StoreError(f"node {node_name!r} already exists", status=409)
        if kind == "step":
            # Reachable-from-start is checked by lint, so hook the new node from the
            # start node with an `always` transition at the end (lowest priority).
            node = Node(intent=intent, recognize=Condition(always=True), transitions=[Transition(to=wf.start)])
            wf.nodes[node_name] = node
            wf.nodes[wf.start].transitions.append(Transition(to=node_name))
        elif kind == "code":
            # A code node: sandboxed JS between pages. Recognize `always` so it runs
            # wherever the graph is; hooked from start like a step so it lints.
            script = Script(code=CODE_NODE_STUB, args={}, into=None)
            node = Node(intent=intent or "code node — derive vars from vars", recognize=Condition(always=True),
                        actions=[Action(script=script)], transitions=[Transition(to=wf.start)])
            wf.nodes[node_name] = node
            wf.nodes[wf.start].transitions.append(Transition(to=node_name))
        elif kind in ("success", "outcome", "failure"):
            wf.nodes[node_name] = Node(terminal=kind, result={"status": node_name} if kind == "outcome" else None)
            wf.nodes[wf.start].transitions.append(Transition(to=node_name))
        else:
            raise StoreError(f"unknown node kind {kind!r}", status=400)
        return self.save(name, wf)

    def rename_node(self, name: str, old: str, new: str) -> GraphView:
        if not _NAME.match(new):
            raise StoreError(f"invalid node name {new!r}", status=400)
        wf = self.load(name)
        if old not in wf.nodes:
            raise StoreError(f"node {old!r} not found", status=404)
        if new in wf.nodes:
            raise StoreError(f"node {new!r} already exists", status=409)
        wf.nodes = {(new if k == old else k): v for k, v in wf.nodes.items()}
        if wf.start == old:
            wf.start = new
        for node in wf.nodes.values():
            for t in node.transitions:
                if t.to == old:
                    t.to = new
            if node.then == old:
                node.then = new
            if node.on_fail == old:
                node.on_fail = new
            for a in node.actions:
                if a.jump and a.jump.to == old:
                    a.jump.to = new
        return self.save(name, wf)

    def delete_node(self, name: str, node_name: str) -> GraphView:
        return self.delete_elements(name, [node_name], [])

    def delete_elements(self, name: str, nodes: list[str], edges: list[dict[str, str]]) -> GraphView:
        """Remove one selection and its references in a single saved draft.

        A deletion may leave unreachable nodes or no success terminal. Report lint
        in the returned view; authors must be able to repair incomplete graphs.
        """
        data = self._document(name)
        removed = set(nodes)
        missing = removed - data["nodes"].keys()
        if missing:
            raise StoreError(f"node {min(missing)!r} not found", status=404)
        if data.get("start") in removed:
            raise StoreError("Set another node as start before deleting the start node", status=400)
        for node_name in removed:
            del data["nodes"][node_name]
        for node in data["nodes"].values():
            if not isinstance(node, dict):
                continue  # preserve unrelated invalid drafts verbatim as data
            transitions = node.get("transitions")
            if isinstance(transitions, list):
                node["transitions"] = [t for t in transitions
                                       if not isinstance(t, dict) or t.get("to") not in removed]
            if node.get("then") in removed:
                node.pop("then")
            if node.get("on_fail") in removed:
                node.pop("on_fail")  # default repair behavior
            actions = node.get("actions")
            if isinstance(actions, list):
                node["actions"] = [a for a in actions if not (
                    isinstance(a, dict) and isinstance(a.get("jump"), dict)
                    and a["jump"].get("to") in removed)]
        for edge in edges:
            source, target = edge["source"], edge["target"]
            if source in removed or target in removed:
                continue  # incident edges were already removed with their node
            node = data["nodes"].get(source)
            if not isinstance(node, dict):
                raise StoreError(f"node {source!r} not found", status=404)
            transitions = node.get("transitions", [])
            matches = [i for i, t in enumerate(transitions)
                       if isinstance(t, dict) and t.get("to") == target]
            if matches:
                del transitions[matches[-1]]
            elif node.get("on_fail") == target:
                node.pop("on_fail")
            elif node.get("then") == target:
                node.pop("then")
            else:
                raise StoreError(f"no edge {source!r} to {target!r}", status=404)
        self._write_text(name, dump_yaml(data))
        return self.view(name)

    def add_edge(self, name: str, src: str, dst: str, condition: dict[str, Any] | None = None) -> GraphView:
        wf = self.load(name)
        for n in (src, dst):
            if n not in wf.nodes:
                raise StoreError(f"node {n!r} not found", status=404)
        when = Condition.model_validate(condition) if condition else Condition(always=True)
        node = wf.nodes[src]
        if node.is_terminal:
            raise StoreError("terminal nodes have no outgoing transitions", status=400)
        # An `always` edge must stay last or it shadows everything after it.
        if when.kind == "always":
            node.transitions = [t for t in node.transitions if t.when.kind != "always"]
            node.transitions.append(Transition(to=dst, when=when))
        else:
            idx = next((i for i, t in enumerate(node.transitions) if t.when.kind == "always"), len(node.transitions))
            node.transitions.insert(idx, Transition(to=dst, when=when))
        return self.save(name, wf)

    def delete_edge(self, name: str, src: str, dst: str, index: int | None = None) -> GraphView:
        wf = self.load(name)
        if src not in wf.nodes:
            raise StoreError(f"node {src!r} not found", status=404)
        node = wf.nodes[src]
        matching = [i for i, t in enumerate(node.transitions) if t.to == dst]
        if not matching:
            raise StoreError(f"no transition {src} → {dst}", status=404)
        del node.transitions[matching[index] if index is not None and index < len(matching) else matching[-1]]
        return self.save(name, wf)

    def set_start(self, name: str, node_name: str) -> GraphView:
        wf = self.load(name)
        if node_name not in wf.nodes:
            raise StoreError(f"node {node_name!r} not found", status=404)
        wf.start = node_name
        return self.save(name, wf)

    def lint_node_yaml(self, name: str, node_name: str, text: str) -> list[str]:
        """Lint a node edit without writing: substitute it into the graph and lint."""
        try:
            node = Node.model_validate(yaml.safe_load(text) or {})
        except ValidationError as exc:
            return _validation_errors(exc)
        except yaml.YAMLError as exc:
            return [f"invalid YAML: {exc}"]
        data = self._document(name)
        data["nodes"][node_name] = node.model_dump(mode="json", by_alias=True, exclude_none=True)
        return self.lint_yaml(dump_yaml(data))

    def completions(self, name: str) -> dict[str, Any]:
        """Graph-aware vocabulary for the editor's autocomplete."""
        from graphwright.engine.shapers import SHAPERS

        wf = Workflow.model_validate(self.view(name).workflow)
        return {
            "nodes": list(wf.nodes),
            "targets": list(wf.targets),
            "inputs": list(wf.inputs),
            # request name → the fields a template can read: {requests.<name>.<field>}
            "requests": {k: r.fields for k, r in wf.requests.items()},
            "scripts": list(wf.scripts),
            "shapers": sorted(SHAPERS),
            "macros": [],
        }

    def put_page_filter(self, name: str, value: PageFilter, expected: PageFilter) -> GraphView:
        data = self._document(name)
        try:
            current = PageFilter.model_validate(data.get("page_filter", {}))
        except ValidationError:
            raise StoreError("Repair page_filter in Graph YAML first") from None
        if current != expected:
            raise StoreError("Page filters changed. Load graph defaults before saving again.", status=409)
        data["page_filter"] = value.model_dump(mode="json")
        return self.put_yaml(name, dump_yaml(data))

    def add_target(self, name: str, target_name: str, target: Target) -> GraphView:
        if not _NAME.match(target_name):
            raise StoreError(f"invalid target name {target_name!r}", status=400)
        wf = self.load(name)
        if target_name in wf.targets:
            raise StoreError(f"target {target_name!r} already exists", status=409)
        wf.targets[target_name] = target
        return self.save(name, wf)

    def target_views(self, name: str) -> dict[str, Any]:
        data = self._document(name)
        targets = data.get("targets", {})
        if not isinstance(targets, dict):
            raise StoreError("Repair the targets mapping in Graph YAML first")
        def references(value, target, path=""):
            found = []
            if isinstance(value, dict):
                for key, child in value.items():
                    location = f"{path}.{key}" if path else str(key)
                    if key in ("target", "visible", "hidden") and child == target:
                        found.append(location)
                    else:
                        found.extend(references(child, target, location))
            elif isinstance(value, list):
                for i, child in enumerate(value):
                    found.extend(references(child, target, f"{path}[{i}]"))
            return found
        return {key: {"yaml": dump_yaml(value),
                      "description": value["description"] if isinstance(value, dict) and isinstance(value.get("description"), str) else "",
                      "references": references(data.get("nodes", {}), key, "nodes")}
                for key, value in targets.items()}

    def put_target(self, name: str, target: str, text: str, expected: str | None = None) -> GraphView:
        if not _NAME.fullmatch(target):
            raise StoreError("invalid target name (use snake_case)", status=400)
        data = self._document(name)
        targets = data.setdefault("targets", {})
        if not isinstance(targets, dict):
            raise StoreError("Repair the targets mapping in Graph YAML first")
        current = dump_yaml(targets[target]) if target in targets else ""
        if expected is not None and current != expected:
            raise StoreError("Target changed since this edit started. Reload it before saving.", status=409)
        try:
            value = yaml.safe_load(text)
        except yaml.YAMLError:
            value = text  # retain malformed drafts, as node editing does
        targets[target] = value
        return self.put_yaml(name, dump_yaml(data))

    def delete_target(self, name: str, target: str) -> GraphView:
        data = self._document(name)
        targets = data.get("targets", {})
        if not isinstance(targets, dict) or target not in targets:
            raise StoreError("target not found", status=404)
        del targets[target]
        # Keep referencing actions visible; lint blocks execution until repaired.
        return self.put_yaml(name, dump_yaml(data))

    def put_input(self, name: str, input_name: str, spec: InputSpec) -> GraphView:
        if not _NAME.match(input_name):
            raise StoreError(f"invalid input name {input_name!r} (snake_case)", status=400)
        wf = self.load(name)
        wf.inputs[input_name] = spec
        return self.save(name, wf)

    def delete_input(self, name: str, input_name: str) -> GraphView:
        wf = self.load(name)
        if input_name not in wf.inputs:
            raise StoreError(f"input {input_name!r} not found", status=404)
        del wf.inputs[input_name]
        return self.save(name, wf)  # lint catches actions still referencing it

    def set_recognize(self, name: str, node_name: str, condition: Condition) -> GraphView:
        wf = self.load(name)
        if node_name not in wf.nodes:
            raise StoreError(f"node {node_name!r} not found", status=404)
        if wf.nodes[node_name].is_terminal:
            raise StoreError("terminal nodes have no recognize", status=400)
        wf.nodes[node_name].recognize = condition
        return self.save(name, wf)


# --- generators: YAML from what the page shows ------------------------------------------

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_JSF_PREFIX = re.compile(r"^.*:")  # searchForm:j_id161:referenceInput → referenceInput


def _slug(text: str, fallback: str) -> str:
    import unicodedata

    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    s = _SLUG_STRIP.sub("_", s).strip("_")
    s = re.sub(r"_+", "_", s)[:40].strip("_")
    if not s or not _NAME.match(s):
        s = fallback
    return s


def _anchored(text: str) -> str:
    """Exact, case-insensitive match of a literal: `re:^…$` with the literal escaped."""
    return "re:^" + re.escape(text) + "$"


def target_from_element(element: dict[str, Any], name: str | None = None) -> tuple[str, Target]:
    """Derive a target (name + ordered strategies) from one observed element.

    Order mirrors the house convention: accessible name first (survives id renames),
    then the HTML id (stable on legacy portals; suffix-only for JSF ids whose middle
    segment is auto-generated), then layout hints (nearby text, landmark).
    """
    role = element.get("role") or "generic"
    acc_name = (element.get("name") or "").strip()
    dom_id = element.get("dom_id") or ""
    nearby = [n for n in element.get("nearby") or [] if n]
    landmark = element.get("landmark") or ""

    strategies: list[Strategy] = []
    if acc_name:
        strategies.append(Strategy(role=role, name=_anchored(acc_name)))
    if dom_id:
        if ":" in dom_id:  # JSF: keep only the stable last segment, anchored at the end
            suffix = _JSF_PREFIX.sub("", dom_id)
            strategies.append(Strategy(role=role, dom_id="re::" + re.escape(suffix) + "$"))
        else:
            strategies.append(Strategy(role=role, dom_id=_anchored(dom_id)))
    if nearby:
        extra = {"landmark": "re:" + re.escape(landmark.split("#", 1)[-1])} if "#" in landmark else {}
        strategies.append(Strategy(role=role, nearby=_anchored(nearby[0]), **extra))
    if element.get("css_path"):
        strategies.append(Strategy(css=element["css_path"]))
    if not strategies:
        strategies.append(Strategy(role=role, landmark=landmark or None, in_viewport=True) if landmark else Strategy(role=role))

    label = acc_name or (nearby[0] if nearby else "") or dom_id or role
    target_name = name or _slug(label, f"{role}_{element.get('id', 'x')}")
    description = f"{role} “{label}”" + (f" in {landmark}" if landmark else "")
    return target_name, Target(description=description, strategies=strategies, frame=element.get("frame", "any"))


def recognize_from_state(state: dict[str, Any], *, max_elements: int = 3) -> Condition:
    """A starting `recognize` for the page an observation shows: its route plus up to
    `max_elements` distinctive visible controls (prefer ones with an id or a name,
    skip site chrome). The author trims it afterwards."""
    parts: list[Condition] = []
    route = state.get("route") or ""
    path = route.split(" ?", 1)[0]
    if path and path != "/":
        parts.append(Condition(url="re:" + re.escape(path)))

    chrome = {"nav", "navigation", "header", "banner", "footer", "contentinfo", "aside", "complementary"}
    picked = 0
    for e in state.get("elements") or []:
        if picked >= max_elements:
            break
        if e.get("category", "controls") != "controls":
            continue
        if not e.get("in_viewport") or e.get("role") in ("link", "generic", "img", "clickable"):
            continue
        lm = (e.get("landmark") or "").split("#", 1)[0]
        if lm in chrome:
            continue
        if e.get("dom_id") and ":" not in e["dom_id"]:
            parts.append(Condition(visible=Strategy(role=e["role"], dom_id=_anchored(e["dom_id"]))))
        elif e.get("name"):
            parts.append(Condition(visible=Strategy(role=e["role"], name=_anchored(e["name"]))))
        else:
            continue
        picked += 1

    if not parts:
        parts.append(Condition(always=True))
    return parts[0] if len(parts) == 1 else Condition(all=parts)
