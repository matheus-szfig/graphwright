"""Builder API — a dev-only FastAPI service around graphwright's GraphSession.

Not part of the production stack. It edits the YAML graphs in `engine/graphs/` (the
repo is the source of truth, so every change is a reviewable diff) and runs sessions
in a browser on the developer's machine.
"""
