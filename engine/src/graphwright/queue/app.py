"""Celery application — single source of truth.

Import as: ``from graphwright.queue.app import celery``
Run a worker: ``celery -A graphwright.queue.app worker -Q graphwright.runs,graphwright.maintenance``
"""

from __future__ import annotations

from celery import Celery

from graphwright.queue import config as queue_config
from graphwright.queue.routing import task_routes
from graphwright.worker import hooks as _worker_hooks  # noqa: F401  — registers signal handlers

celery = Celery("graphwright")
celery.config_from_object(queue_config)
celery.conf.task_routes = task_routes
celery.autodiscover_tasks(["graphwright"])
