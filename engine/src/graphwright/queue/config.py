"""Celery broker/backend/timeout settings."""

from __future__ import annotations

from graphwright.config import settings

broker_url = settings.redis_url
result_backend = settings.redis_url

task_serializer = "json"
result_serializer = "json"
accept_content = ["json"]

timezone = "UTC"
enable_utc = True

broker_connection_retry_on_startup = True
# Off: with it on, Celery copies every task's args into the result backend.
# Run messages carry a run id only, but nothing should get a second copy for free.
result_extended = False
result_expires = 24 * 3600

task_soft_time_limit = 600
task_time_limit = 720
# A run that dies mid-flight (worker crash, recycle) is redelivered; the task is
# idempotent on the run row (it skips runs that already finished).
task_acks_late = True
task_reject_on_worker_lost = True
worker_prefetch_multiplier = 1

task_track_started = True
