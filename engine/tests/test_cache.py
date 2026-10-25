from __future__ import annotations

import json

import fakeredis

from graphwright.contract import progress_channel
from graphwright.storage import cache


def test_set_and_get_round_trip() -> None:
    cache.set_run_progress("abc", {"status": "running", "steps": 2})

    progress = cache.get_run_progress("abc")

    assert progress is not None
    assert progress["status"] == "running"
    assert progress["steps"] == 2
    assert progress["run_id"] == "abc"
    assert "updated_at" in progress


def test_get_unknown_run_returns_none() -> None:
    assert cache.get_run_progress("does-not-exist") is None


def test_list_recent_run_ids_strips_prefix() -> None:
    cache.set_run_progress("run-1", {"status": "running"})
    cache.set_run_progress("run-2", {"status": "completed"})

    assert set(cache.list_recent_run_ids()) == {"run-1", "run-2"}


def test_progress_is_published_on_the_run_channel(fake_redis: fakeredis.FakeRedis) -> None:
    pubsub = fake_redis.pubsub()
    pubsub.subscribe(progress_channel("run-9"))
    pubsub.get_message(timeout=1)  # subscribe confirmation

    cache.set_run_progress("run-9", {"status": "running", "node": "search"})

    msg = pubsub.get_message(timeout=1)
    assert msg is not None and msg["type"] == "message"
    assert json.loads(msg["data"])["node"] == "search"
