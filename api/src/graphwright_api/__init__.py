"""graphwright runs API — create runs, follow their progress, cancel them.

The API writes the ``run`` row, then enqueues a message that carries only the run
id (``graphwright.contract``). The worker claims the row and reports back through
Postgres (outcome, events) and Redis (live progress).
"""

__version__ = "0.1.0"
