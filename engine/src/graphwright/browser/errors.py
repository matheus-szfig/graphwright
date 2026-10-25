"""Browser-layer exception hierarchy.

WorkflowRunner narrows its except to BrowserError so CancelledError and other
infra-level exceptions propagate. Steps should raise these from action wrappers.
"""

from __future__ import annotations


class BrowserError(Exception):
    """Base for all browser-layer failures."""


class StepFailedError(BrowserError):
    """A deterministic step failed in a way the LLM fallback may recover."""


class SelectorNotFoundError(StepFailedError):
    """A required selector was not present on the page."""


class NavigationTimeoutError(StepFailedError):
    """Page navigation did not complete within the deadline."""
