"""Task name → queue mapping. Task names are the contract with the runs API
(``graphwright.contract``); keep them stable."""

from __future__ import annotations

from graphwright.contract import QUEUE_MAINTENANCE, QUEUE_RUNS

task_routes = {
    "graphwright.tasks.runs.*": {"queue": QUEUE_RUNS},
    "graphwright.tasks.maintenance.*": {"queue": QUEUE_MAINTENANCE},
}
