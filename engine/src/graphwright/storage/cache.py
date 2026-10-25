"""Redis: live run progress.

The worker overwrites one key per run (the latest snapshot, TTL'd) and publishes
the same payload on a per-run channel. The runs API serves the key to pollers
and relays the channel as Server-Sent Events. Key and channel names live in
``graphwright.contract`` so both sides agree.

Sync client — called from Celery task bodies and CLI scripts.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import redis

from graphwright.config import settings
from graphwright.contract import PROGRESS_KEY_PREFIX, progress_channel, progress_key

PROGRESS_TTL_SECONDS = 24 * 3600

_client: redis.Redis | None = None


def get_client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(settings.redis_url, decode_responses=True)
    return _client


def reset_client() -> None:
    global _client
    _client = None


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def set_run_progress(run_id: str, progress: dict[str, Any]) -> None:
    payload = json.dumps({**progress, "run_id": run_id, "updated_at": _now_iso()})
    client = get_client()
    client.set(progress_key(run_id), payload, ex=PROGRESS_TTL_SECONDS)
    client.publish(progress_channel(run_id), payload)


def get_run_progress(run_id: str) -> dict[str, Any] | None:
    raw = get_client().get(progress_key(run_id))
    return json.loads(raw) if raw else None


def list_recent_run_ids(limit: int = 50) -> list[str]:
    keys = list(get_client().scan_iter(match=PROGRESS_KEY_PREFIX + "*", count=limit))
    return [k.removeprefix(PROGRESS_KEY_PREFIX) for k in keys[:limit]]
