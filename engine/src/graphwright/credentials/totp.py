"""Compatibility exports for credential-provider callers."""

from graphwright.engine.totp import seconds_left, totp

__all__ = ["seconds_left", "totp"]
