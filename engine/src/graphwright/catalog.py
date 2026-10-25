"""The graph catalog — graphs on disk, as the worker, the runs API and the CLI see them.

One YAML file per graph in ``GRAPHS_DIR`` (default ``<engine>/graphs``); the file
stem is the graph's name. Loading always compiles (lints), so a graph that does
not lint can be listed — with its errors — but never run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from graphwright.config import settings
from graphwright.credentials.base import Credential
from graphwright.engine import GraphError, Workflow, compile_workflow
from graphwright.engine.compiler import CompiledGraph, lint
from graphwright.engine.secrets import Secret, SecretInputs

NAME = re.compile(r"^[a-z][a-z0-9_]*$")


class GraphNotFound(LookupError):
    pass


class GraphInvalid(ValueError):
    def __init__(self, name: str, errors: list[str]) -> None:
        super().__init__(f"graph {name!r} does not lint: {errors}")
        self.errors = errors


class InputsInvalid(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass
class GraphInfo:
    name: str
    workflow: str
    version: int
    portal: str
    entry_url: str | None
    inputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    requests: list[str] = field(default_factory=list)
    lint: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.lint


def graphs_dir() -> Path:
    return Path(settings.graphs_dir) if settings.graphs_dir else Path(__file__).resolve().parents[2] / "graphs"


def graph_path(name: str, directory: Path | None = None) -> Path:
    if not NAME.match(name):
        raise GraphNotFound(f"invalid graph name {name!r}")
    return (directory or graphs_dir()) / f"{name}.yaml"


def _info(name: str, path: Path) -> GraphInfo:
    try:
        wf = Workflow.from_yaml(path)
    except (ValidationError, yaml.YAMLError) as exc:
        return GraphInfo(name=name, workflow=name, version=0, portal="?", entry_url=None, lint=[str(exc).splitlines()[0]])
    return GraphInfo(
        name=name, workflow=wf.workflow, version=wf.version, portal=wf.portal, entry_url=wf.entry_url,
        inputs={k: v.model_dump(mode="json", exclude_none=True) for k, v in wf.inputs.items()},
        requests=list(wf.requests), lint=lint(wf),
    )


def list_graphs(directory: Path | None = None) -> list[GraphInfo]:
    d = directory or graphs_dir()
    return [_info(p.stem, p) for p in sorted(d.glob("*.yaml")) if NAME.match(p.stem)]


def get_graph(name: str, directory: Path | None = None) -> GraphInfo:
    path = graph_path(name, directory)
    if not path.is_file():
        raise GraphNotFound(f"no graph named {name!r}")
    return _info(name, path)


def load_graph(name: str, directory: Path | None = None) -> CompiledGraph:
    path = graph_path(name, directory)
    if not path.is_file():
        raise GraphNotFound(f"no graph named {name!r}")
    try:
        return compile_workflow(Workflow.from_yaml(path))
    except GraphError as exc:
        raise GraphInvalid(name, exc.errors) from exc
    except (ValidationError, yaml.YAMLError) as exc:
        raise GraphInvalid(name, [str(exc)]) from exc


def validate_inputs(wf: Workflow, provided: dict[str, Any], *, has_credential: bool) -> list[str]:
    """What the runs API checks before it queues a run (and the worker again before
    it starts the browser)."""
    errors: list[str] = []
    for name in provided:
        if name not in wf.inputs:
            errors.append(f"unknown input {name!r}")
        elif wf.inputs[name].credential:
            errors.append(f"input {name!r} comes from the credential, not from the run")
    for name, spec in wf.inputs.items():
        if spec.credential:
            if spec.required and not has_credential:
                errors.append(f"input {name!r} needs a credential (the run has none)")
            continue
        if spec.required and name not in provided and spec.default is None:
            errors.append(f"missing input {name!r}")
    return errors


def build_inputs(wf: Workflow, provided: dict[str, Any], credential: Credential | None) -> SecretInputs:
    """Run inputs: defaults, then what the run provided, then credential fields as
    Secrets. Raises InputsInvalid when a required one is missing."""
    out = SecretInputs()
    for name, spec in wf.inputs.items():
        if spec.credential:
            if credential is not None and credential.values.get(spec.credential):
                out[name] = Secret(credential.values[spec.credential])
            continue
        if name in provided:
            out[name] = provided[name]
        elif spec.default is not None:
            out[name] = spec.default
    missing = [n for n, s in wf.inputs.items() if s.required and n not in out]
    if missing:
        raise InputsInvalid([f"missing input {n!r}" + (" (credential field)" if wf.inputs[n].credential else "") for n in missing])
    return out
