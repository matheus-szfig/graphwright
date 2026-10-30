"""Caller-side configuration for graph request execution.

Worker, CLI and builder adapters call this before handing services to the engine.
Embedding GraphSession directly needs no environment or worker configuration.
"""
from typing import Any

from graphwright.config import settings
from graphwright.engine.requests import RequestServices


def request_services(**overrides: Any) -> RequestServices:
    options = {
        "allowed_hosts": settings.requests_allowed_hosts,
        "default_timeout_s": settings.requests_default_timeout_s,
    }
    options.update(overrides)
    return RequestServices(**options)
