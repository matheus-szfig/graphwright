"""Graph engine — workflows as data.

A workflow is a graph of recognizable page states (`schema.Node`) with a closed action
DSL and structured predicates. `compiler.compile_workflow` validates a `Workflow` and
turns it into a `CompiledGraph` whose predicates are closures over a `PageState`.
Everything here is pure Python and runs against stored observations, which is what
lets the repair loop replay graph changes without a browser.
"""

from graphwright.engine.compiler import CompiledGraph, GraphError, compile_workflow  # noqa: F401
from graphwright.engine.schema import Workflow  # noqa: F401
