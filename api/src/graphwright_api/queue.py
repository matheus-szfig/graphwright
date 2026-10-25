"""Celery *client*: enqueue and revoke runs by task name.

Never import ``graphwright.queue.app`` here — that module is the worker (it pulls
in the browser pool and worker signal handlers). The API only needs the names in
``graphwright.contract``. The Celery task id is the run id, so revoking needs
nothing but the run.
"""

from __future__ import annotations

import asyncio
from typing import Protocol

from celery import Celery
from graphwright.contract import QUEUE_RUNS, TASK_RUN_GRAPH


class Enqueuer(Protocol):
    async def enqueue(self, run_id: str) -> None: ...

    async def revoke(self, run_id: str) -> None: ...


class CeleryEnqueuer:
    def __init__(self, broker_url: str) -> None:
        self.app = Celery("graphwright-api", broker=broker_url)
        self.app.conf.update(
            task_serializer="json", accept_content=["json"],
            broker_connection_retry_on_startup=True,
            # Fail fast instead of blocking a request when Redis is down.
            broker_transport_options={"max_retries": 2, "interval_start": 0, "interval_step": 0.5},
        )

    async def enqueue(self, run_id: str) -> None:
        await asyncio.to_thread(
            self.app.send_task, TASK_RUN_GRAPH, args=[run_id], queue=QUEUE_RUNS, task_id=run_id, retry=False,
        )

    async def revoke(self, run_id: str) -> None:
        # The worker also checks the run's status between steps, so a lost revoke
        # still stops the run at the next step; terminate=True stops a long one now.
        await asyncio.to_thread(self.app.control.revoke, run_id, terminate=True, signal="SIGTERM")
