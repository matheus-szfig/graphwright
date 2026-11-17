"""Failure classes the runtime routes on (spec §4, "Failure routing")."""

from __future__ import annotations


class EngineError(Exception):
    """Base. `klass` is what failure routing switches on."""

    klass = "structural"

    def __init__(self, message: str, *, details: list[str] | None = None) -> None:
        super().__init__(message)
        self.details = details or []


class TransientError(EngineError):
    """Timeouts, 5xx, network errors — retry with backoff."""

    klass = "transient"


class BlockedError(EngineError):
    """Challenge / captcha / 403 / 429 — gate or quarantine, never the LLM."""

    klass = "blocked"


class StructuralError(EngineError):
    """Target missing, postcondition failed, no transition — the repair tier."""

    klass = "structural"


class TargetNotFound(StructuralError):
    pass


class NotActionable(StructuralError):
    """Found but occluded / disabled / zero-size."""


class NoTransition(StructuralError):
    pass


class PostconditionFailed(StructuralError):
    pass


class UnknownState(StructuralError):
    """Current page matches no node's `recognize`."""


class RecognitionFailed(StructuralError):
    """The expected node does not recognize the page; global recovery is disabled."""


class LoopGuard(StructuralError):
    pass


class JumpSignal(Exception):
    """Raised by the `jump` action; caught by the runtime, not a failure."""

    def __init__(self, to: str) -> None:
        super().__init__(to)
        self.to = to
