"""Celery worker process lifecycle hooks.

One BrowserPool + one asyncpg pool per worker process, both anchored to the
same long-lived event loop. Celery tasks reuse this loop via
``loop.run_until_complete(coroutine)`` — never ``asyncio.run(...)``, which
would create a fresh loop and detach Playwright + asyncpg objects from their
owner loop.

Assumes the Celery ``prefork`` pool (default). ``gevent``/``solo`` pools have
different lifecycle semantics and would need a different bridge.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import asyncpg
from celery.signals import worker_process_init, worker_process_shutdown

from graphwright import plugins
from graphwright.browser.pool import BrowserPool
from graphwright.config import settings
from graphwright.storage import db

log = logging.getLogger(__name__)

_loop: asyncio.AbstractEventLoop | None = None
_pool: BrowserPool | None = None
_db_pool: asyncpg.Pool | None = None


def get_loop_and_pool() -> tuple[asyncio.AbstractEventLoop, BrowserPool]:
    if _loop is None or _pool is None:
        raise RuntimeError(
            "BrowserPool not initialized — worker_process_init did not run "
            "(are you calling browser tasks outside a Celery worker?)"
        )
    return _loop, _pool


def get_db_pool() -> asyncpg.Pool:
    if _db_pool is None:
        raise RuntimeError(
            "asyncpg pool not initialized — worker_process_init did not run"
        )
    return _db_pool


@worker_process_init.connect
def _on_worker_process_init(**_: Any) -> None:
    global _loop, _pool, _db_pool
    plugins.load()
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)

    _db_pool = _loop.run_until_complete(db.init_pool(min_size=1, max_size=4))

    _pool = BrowserPool(
        profile_name=settings.browser_default_profile,
        headless=settings.browser_headless,
    )
    _loop.run_until_complete(_pool.start())
    log.info(
        "worker_process_init: BrowserPool ready (profile=%s) + asyncpg pool ready",
        settings.browser_default_profile,
    )


@worker_process_shutdown.connect
def _on_worker_process_shutdown(**_: Any) -> None:
    global _loop, _pool, _db_pool
    if _loop is None:
        return
    try:
        if _pool is not None:
            _loop.run_until_complete(_pool.shutdown())
        if _db_pool is not None:
            _loop.run_until_complete(db.close_pool())
    finally:
        _loop.close()
    _pool = None
    _db_pool = None
    _loop = None
    log.info("worker_process_shutdown: BrowserPool + asyncpg pool stopped")
