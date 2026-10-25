"""Secrets inside a run — values that may be typed into a page but never shown.

A ``Secret`` prints as ``***`` wherever Python would print it (f-strings, ``repr``,
log records, tracebacks); the plaintext comes out only through ``reveal()``,
which ``ActionContext.render_template`` calls right before typing. A lazy
Secret (one built from a callable) is resolved on every reveal — that's how a
TOTP code is fetched at the moment of use.

``Redactor`` scrubs whatever still slips through: every event the runtime
emits, the failure record, the Redis progress payload and the ``task_run``
error columns pass through it, and each known plaintext becomes ``***``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

MASK = "***"
_MIN_LEN = 4  # don't scrub one-character "secrets" out of every string


class Secret:
    __slots__ = ("_get",)

    def __init__(self, value: str | Callable[[], str]) -> None:
        self._get: Callable[[], str] = value if callable(value) else (lambda: value)  # type: ignore[assignment]

    def reveal(self) -> str:
        return self._get()

    def __str__(self) -> str:
        return MASK

    def __repr__(self) -> str:
        return "Secret(***)"

    def __format__(self, _spec: str) -> str:
        return MASK


class SecretInputs(dict):
    """A plain dict (the engine checks ``isinstance(x, dict)``) whose repr hides
    Secret values. Pass it as ``GraphSession(..., inputs=...)``."""

    def __repr__(self) -> str:
        return "SecretInputs(" + repr({k: (MASK if isinstance(v, Secret) else v) for k, v in self.items()}) + ")"

    def plain(self) -> dict[str, Any]:
        """Non-secret entries only — safe to persist in ``task_run.config``."""
        return {k: v for k, v in self.items() if not isinstance(v, Secret)}


class Redactor:
    def __init__(self, values: Iterable[str] = ()) -> None:
        # Longest first so a value that contains another is replaced whole.
        self._values = sorted({v for v in values if v and len(v) >= _MIN_LEN}, key=len, reverse=True)

    def add(self, *values: str) -> None:
        self._values = sorted(set(self._values) | {v for v in values if v and len(v) >= _MIN_LEN}, key=len, reverse=True)

    def text(self, s: str) -> str:
        for v in self._values:
            if v in s:
                s = s.replace(v, MASK)
        return s

    def __call__(self, obj: Any) -> Any:
        if isinstance(obj, str):
            return self.text(obj)
        if isinstance(obj, Secret):
            return MASK
        if isinstance(obj, dict):
            return {k: self(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return type(obj)(self(v) for v in obj)
        return obj
