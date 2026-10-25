"""Maintenance tasks."""

from __future__ import annotations

from graphwright.queue.app import celery


@celery.task(name="graphwright.tasks.maintenance.health_check")
def health_check() -> dict[str, str]:
    return {"status": "ok"}
