"""Live progress from Redis — the snapshot key and the per-run pub/sub channel the
worker writes (``graphwright.storage.cache``; names in ``graphwright.contract``)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, Protocol

import redis.asyncio as aioredis
from graphwright.contract import progress_channel, progress_key


class Progress(Protocol):
    async def get(self, run_id: str) -> dict[str, Any] | None: ...

    def subscribe(self, run_id: str, *, poll_s: float) -> AsyncIterator[dict[str, Any] | None]:
        """Yield each published payload; ``None`` every ``poll_s`` without one (heartbeat tick)."""
        ...

    async def ping(self) -> bool: ...


class RedisProgress:
    def __init__(self, client: aioredis.Redis) -> None:
        self.client = client

    @classmethod
    def from_url(cls, url: str) -> RedisProgress:
        return cls(aioredis.from_url(url, decode_responses=True))

    async def close(self) -> None:
        await self.client.aclose()

    async def get(self, run_id: str) -> dict[str, Any] | None:
        raw = await self.client.get(progress_key(run_id))
        return json.loads(raw) if raw else None

    async def subscribe(self, run_id: str, *, poll_s: float = 15.0) -> AsyncIterator[dict[str, Any] | None]:
        pubsub = self.client.pubsub()
        await pubsub.subscribe(progress_channel(run_id))
        try:
            while True:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=poll_s)
                if msg is None:
                    yield None
                    continue
                try:
                    yield json.loads(msg["data"])
                except (TypeError, ValueError):
                    continue
        finally:
            await pubsub.unsubscribe()
            await pubsub.aclose()

    async def ping(self) -> bool:
        return bool(await self.client.ping())
