"""Outbound AI projection. Raw page text and runtime values never enter it.

Graph identifiers are author-facing metadata. Literal graph values are opaque,
path-bound handles: the model can preserve them, but cannot relocate them to a
different field (for example moving an authorization header into a public URL).
JavaScript sources are readable so the assistant can edit them (D-44); private
values and PII-looking literals inside them become inline handles bound to that field.
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any
from urllib.parse import quote

from graphwright.engine.schema import Workflow
from graphwright.engine.secrets import Secret

IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
TEMPLATE = re.compile(r"^\{[A-Za-z_][A-Za-z0-9_.\[\]]*\}$")
PUBLIC_FIELDS = {"workflow", "portal", "start", "to", "target", "visible", "type", "credential",
                 "kind", "method", "cache", "on_error", "terminal", "on_fail", "into", "shape",
                 "world", "role", "request", "status", "op", "page_type", "key", "ref"}
# These subtrees contain arbitrary payloads, not language vocabulary.
PRIVATE_TREES = {"default", "headers", "query", "body", "args", "result"}
PUBLIC_NUMBERS = {"version", "timeout_s", "timeout_ms", "delay_s", "max_visits", "max_steps", "frame",
                  "attempts", "backoff_s", "memory_mb", "by", "max_elements", "max_total_steps"}
PII = re.compile(
    r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|"
    r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b|"
    r"\b\d{7}-?\d{2}\.?\d{4}\.?\d\.?\d{2}\.?\d{4}\b|"
    r"\b(?:sk-|sk-ant-)[A-Za-z0-9_-]+|\b\d{6,}\b"
)
HANDLE = re.compile(r"__private_\d+__")
MASK = "[private]"


def is_code(path: tuple[str, ...]) -> bool:
    """workflow.scripts bodies, script.code and evaluate.script."""
    return (len(path) == 2 and path[0] == "scripts") or path[-2:] in {("script", "code"), ("evaluate", "script")}


class Projection:
    def __init__(self, private_values: list[str] = ()) -> None:
        self.hidden: dict[str, tuple[tuple[str, ...], Any]] = {}
        self.inline: set[str] = set()
        self.sources: dict[tuple[str, ...], str] = {}
        self.private_values = {v for v in private_values if v}

    def hide(self, value: Any, path: tuple[str, ...]) -> str:
        token = f"__private_{len(self.hidden) + 1}__"
        self.hidden[token] = (path, copy.deepcopy(value))
        # Echo masking for hidden literals; very short ones ("0", "id") would garble all text.
        if isinstance(value, (str, int, float)) and len(str(value)) >= 4:
            self.private_values.add(str(value))
        return token

    def graph(self, value: Any, path: tuple[str, ...] = (), private: bool = False) -> Any:
        if isinstance(value, str) and is_code(path):
            return self.code(value, path)
        deferred: list[tuple[dict, str, tuple[str, ...], str]] = []
        result = self.project(value, path, deferred)
        # Code last: every literal hidden elsewhere is then known and tokenized inside it.
        for container, key, code_path, source in deferred:
            container[key] = self.code(source, code_path)
        return result

    def code(self, source: str, path: tuple[str, ...]) -> str:
        """Readable source with path-bound inline handles; whole-field handle if that is unsafe."""
        if HANDLE.search(source):
            return self.hide(source, path)
        tokens: dict[str, str] = {}

        def token(match: re.Match[str]) -> str:
            if match[0] not in tokens:
                tokens[match[0]] = f"__private_{len(self.hidden) + 1}__"
                self.hidden[tokens[match[0]]] = (path, match[0])
                self.inline.add(tokens[match[0]])
            return tokens[match[0]]

        values = sorted(self.private_values, key=len, reverse=True)
        visible = re.sub("|".join([*map(re.escape, values), PII.pattern]), token, source)
        if self.text(visible) != visible:
            # An escaped variant survived; never show a partially masked program.
            return self.hide(source, path)
        self.sources[path] = source
        return visible

    def project(self, value: Any, path: tuple[str, ...], deferred: list) -> Any:
        field = path[-1] if path else ""
        if field in PRIVATE_TREES or (field == "json" and "extract" not in path):
            return self.hide(value, path)
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for key, child in value.items():
                child_path = (*path, str(key))
                if isinstance(child, str) and is_code(child_path):
                    deferred.append((result, str(key), child_path, child))
                    result[str(key)] = None
                else:
                    result[str(key)] = self.project(child, child_path, deferred)
            return result
        if isinstance(value, list):
            return [self.project(v, (*path, str(i)), deferred) for i, v in enumerate(value)]
        if isinstance(value, str):
            if any(secret in value for secret in self.private_values):
                return self.hide(value, path)
            if TEMPLATE.fullmatch(value):
                return value
            if field in PUBLIC_FIELDS and IDENT.fullmatch(value) and not PII.search(value):
                return value
            # Extractor paths describe the API contract, not response values.
            if field == "json" and "extract" in path and re.fullmatch(r"[\w.$\[\]]+", value):
                return value
            return self.hide(value, path)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and field not in PUBLIC_NUMBERS:
            return self.hide(value, path)
        return value

    def restore(self, value: Any, path: tuple[str, ...] = ()) -> Any:
        if isinstance(value, str) and value in self.hidden and value not in self.inline:
            original_path, original = self.hidden[value]
            if path != original_path:
                raise ValueError("Private handles may only be preserved at their original path.")
            return copy.deepcopy(original)
        if isinstance(value, str) and is_code(path):
            def put_back(match: re.Match[str]) -> str:
                if match[0] not in self.inline or self.hidden[match[0]][0] != path:
                    raise ValueError("Inline private handles may only stay in the code field where they appeared.")
                return self.hidden[match[0]][1]

            restored = HANDLE.sub(put_back, value)
            if MASK in restored and MASK not in self.sources.get(path, ""):
                raise ValueError("Masked text cannot be written into code; edit the original source instead.")
            return restored
        if isinstance(value, str) and "__private_" in value:
            raise ValueError("Do not embed private handles in code or strings; preserve the whole field.")
        if isinstance(value, dict):
            return {k: self.restore(v, (*path, str(k))) for k, v in value.items()}
        if isinstance(value, list):
            return [self.restore(v, (*path, str(i))) for i, v in enumerate(value)]
        return value

    def text(self, text: str) -> str:
        variants = set(self.private_values)
        for value in self.private_values:
            variants.update((quote(value, safe=""), json.dumps(value, ensure_ascii=False)[1:-1]))
        for value in sorted(variants, key=len, reverse=True):
            if value:
                text = text.replace(value, "[private]")
        return PII.sub("[private]", text)


class StreamingText:
    """Release safe text prefixes; retain unfinished identifiers and private values.

    Masking individual tokens leaks secrets split across tokens. Keep the current
    word (email/key/number characters) and any partial known-secret suffix until
    its boundary is known. Multi-word secrets and escaped variants stay atomic.
    """

    def __init__(self, projection: Projection) -> None:
        self.projection = projection
        self.pending = ""
        self.variants = set(projection.private_values)
        for value in projection.private_values:
            self.variants.update((quote(value, safe=""), json.dumps(value, ensure_ascii=False)[1:-1]))

    def feed(self, text: str, *, final: bool = False) -> str:
        self.pending += text
        cut = len(self.pending)
        if not final:
            while True:
                previous = cut
                boundaries = list(re.finditer(r"[^\w.+@-]", self.pending[:cut]))
                cut = boundaries[-1].end() if boundaries else 0
                for value in self.variants:
                    # A complete secret may straddle the current release boundary.
                    start = self.pending.find(value)
                    while start >= 0 and start < cut:
                        if start + len(value) > cut:
                            cut = start
                        start = self.pending.find(value, start + 1)
                    # An incomplete secret may end at this chunk boundary.
                    for size in range(min(len(value) - 1, len(self.pending)), 0, -1):
                        if self.pending.endswith(value[:size]):
                            cut = min(cut, len(self.pending) - size)
                            break
                if cut == previous or cut == 0:
                    break
        ready, self.pending = self.pending[:cut], self.pending[cut:]
        return self.projection.text(ready)


def page_context(live: Any) -> dict[str, Any]:
    """Allowlist structural properties; never invoke PageState.render()."""
    state = live.session.last_state
    return {
        "node": live.session.node, "status": live.session.status, "busy": live.busy,
        "elements": [{"handle": e.id, "frame": e.frame, "role": e.role, "tag": e.tag,
                      "states": e.states, "in_viewport": e.in_viewport}
                     for e in state.elements[:150]] if state else [],
        "observation": state.observed_at.isoformat() if state else None,
        "private": "All page text, values, URLs, images, variables and request results are withheld.",
    }


def private_inputs(wf: Workflow, values: dict[str, Any]) -> list[str]:
    from graphwright.engine.secrets import is_private_input

    return [str(value) for name, value in values.items()
            if not isinstance(value, Secret) and value is not None
            and (name not in wf.inputs or is_private_input(wf.inputs[name]))]
