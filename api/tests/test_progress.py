"""RedisProgress against fakeredis: the snapshot key and the channel the worker's
``graphwright.storage.cache`` writes."""

from __future__ import annotations

import asyncio
import json

from fakeredis import FakeAsyncRedis
from graphwright.contract import progress_channel, progress_key

from graphwright_api.progress import RedisProgress


async def test_get_reads_the_worker_snapshot() -> None:
    client = FakeAsyncRedis(decode_responses=True)
    await client.set(progress_key("r1"), json.dumps({"status": "running", "node": "search"}))
    p = RedisProgress(client)
    assert (await p.get("r1"))["node"] == "search"
    assert await p.get("missing") is None
    assert await p.ping()


async def test_subscribe_yields_published_payloads_and_ticks() -> None:
    client = FakeAsyncRedis(decode_responses=True)
    p = RedisProgress(client)
    stream = p.subscribe("r2", poll_s=0.05)

    first = await anext(stream)  # subscribes, then ticks: nothing published yet
    assert first is None
    await client.publish(progress_channel("r2"), json.dumps({"status": "completed"}))
    got = None
    for _ in range(20):
        got = await anext(stream)
        if got is not None:
            break
        await asyncio.sleep(0)
    assert got == {"status": "completed"}
    await stream.aclose()
