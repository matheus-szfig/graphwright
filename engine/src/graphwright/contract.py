"""The contract between the runs API and the worker — task names, queues and the
message. Both sides import this module; neither imports the other's code.

A run message carries **one id**. Everything else (graph, inputs, credential
reference) lives in the ``run`` row the API wrote before enqueueing, so nothing a
caller sent travels through Redis, and a redelivered message finds the row it
needs to decide whether there is anything left to do.
"""

from __future__ import annotations

QUEUE_RUNS = "graphwright.runs"
QUEUE_MAINTENANCE = "graphwright.maintenance"

TASK_RUN_GRAPH = "graphwright.tasks.runs.run_graph"

RUN_STATUSES = ("queued", "running", "completed", "failed", "cancelled")
FINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})

# Redis keys (storage/cache.py writes them, the API reads them).
PROGRESS_KEY_PREFIX = "graphwright:run:progress:"
PROGRESS_CHANNEL_PREFIX = "graphwright:run:events:"


def progress_key(run_id: str) -> str:
    return PROGRESS_KEY_PREFIX + run_id


def progress_channel(run_id: str) -> str:
    return PROGRESS_CHANNEL_PREFIX + run_id
