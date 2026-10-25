"""Extension points, loaded by name from ``PLUGINS`` (comma-separated modules).

A plugin is any importable module that registers things at import time:

    # acme_portals.py
    from graphwright.engine.shapers import register_shaper
    from graphwright.observe.pagestate import register_portal_alias

    @register_shaper("acme_ref_year")
    def acme_ref_year(value: str) -> str:
        return value.split("-")[1]

    register_portal_alias("records.acme.example", "acme-records")

The worker loads plugins at ``worker_process_init``; the runs API and the builder
load them at startup, so lint sees the same shapers the worker runs.
"""

from __future__ import annotations

import importlib
import logging

from graphwright.config import settings

log = logging.getLogger(__name__)
_loaded: set[str] = set()


def load(names: str | None = None) -> list[str]:
    """Import every configured plugin module once. Returns the modules imported."""
    raw = settings.plugins if names is None else names
    out: list[str] = []
    for name in (n.strip() for n in raw.split(",")):
        if not name or name in _loaded:
            continue
        importlib.import_module(name)
        _loaded.add(name)
        out.append(name)
        log.info("plugin loaded: %s", name)
    return out
