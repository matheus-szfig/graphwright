"""Input shapers — named, pure functions that turn a run value into what a specific
page input expects (a masked field, a value split across two inputs…). Referenced
from ``fill.shape`` in a graph; the compiler checks the name is registered, so the
graph stays data and the code stays reviewable.

Built-ins are generic. Site-specific shapers are registered by plugins
(``graphwright/plugins.py``) with ``@register_shaper("name")``.
"""

from __future__ import annotations

import re
from collections.abc import Callable

Shaper = Callable[[str], str]


class ShaperError(ValueError):
    """The value cannot be shaped (wrong length/format)."""


SHAPERS: dict[str, Shaper] = {}


def register_shaper(name: str) -> Callable[[Shaper], Shaper]:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        raise ValueError(f"shaper names are snake_case, got {name!r}")

    def deco(fn: Shaper) -> Shaper:
        if name in SHAPERS and SHAPERS[name] is not fn:
            raise ValueError(f"shaper {name!r} is already registered")
        SHAPERS[name] = fn
        return fn

    return deco


@register_shaper("digits_only")
def digits_only(value: str) -> str:
    return re.sub(r"\D", "", value)


@register_shaper("alnum_only")
def alnum_only(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", value)


@register_shaper("upper")
def upper(value: str) -> str:
    return value.upper()


@register_shaper("lower")
def lower(value: str) -> str:
    return value.lower()


@register_shaper("strip")
def strip(value: str) -> str:
    return value.strip()


@register_shaper("collapse_spaces")
def collapse_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def shape(name: str, value: str) -> str:
    try:
        fn = SHAPERS[name]
    except KeyError as exc:
        raise ShaperError(f"unknown shaper {name!r}; known: {sorted(SHAPERS)}") from exc
    return fn(value)
