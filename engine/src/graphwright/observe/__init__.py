"""Observation layer — turns a live page into a compact, frame-aware PageState.

`pagestate.py` is the data contract shared by the graph engine (predicates read it),
decision models and LLM prompts (they consume its rendering) and the recorder (goldens
are PageStates). `observer.py` fills it by running `pagestate.js` inside a CDP isolated
world per frame, so nothing is written to the page's DOM or main JS world.
"""

from graphwright.observe.pagestate import PageState, route_template  # noqa: F401
