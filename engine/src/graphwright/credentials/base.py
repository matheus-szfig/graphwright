"""What a credential provider hands the worker, and the interface it implements.

A run names its credential by **reference** (``run.credential_ref``) — never by
value. The worker asks the configured provider to redeem that reference once,
at the start of the run, and gets a ``Credential``: the values the graph's
``credential:`` inputs read, plus a ``totp()`` callable that produces a code at
the moment it is called (codes expire; nothing is fetched ahead of time).

Plaintext lives here only until ``tasks/runs.py`` wraps each value in a
``Secret`` and registers it with the run's ``Redactor``.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


class CredentialRejected(Exception):
    """The provider refused or could not be reached. ``code`` is recorded on the
    run (``error_type``); the message never carries a value."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


@dataclass(frozen=True)
class Credential:
    ref: str
    kind: str = "password"  # password | token | certificate | … (free label)
    # When set, the run is refused unless it equals the graph's `portal` — a
    # credential for one site is never typed into another.
    portal: str | None = None
    values: dict[str, str] = field(default_factory=dict, repr=False)
    metadata: dict[str, Any] = field(default_factory=dict)
    totp: Callable[[], str] | None = field(default=None, repr=False)

    @property
    def plaintexts(self) -> list[str]:
        """Everything that must never appear in a log — feeds the Redactor."""
        return [v for k, v in self.values.items() if k != "pfx" and v]

    def client_certificates(self) -> list[dict[str, Any]] | None:
        """Playwright ``client_certificates`` for a TLS client-certificate login:
        ``values.pfx`` (base64 PKCS#12), ``values.passphrase``, ``metadata.origins``."""
        raw = self.values.get("pfx")
        if not raw:
            return None
        pfx = base64.b64decode(raw)
        return [
            {"origin": origin, "pfx": pfx, "passphrase": self.values.get("passphrase", "")}
            for origin in self.metadata.get("origins", [])
        ]


class CredentialProvider(Protocol):
    name: str

    def redeem(self, ref: str, *, run_id: str) -> Credential: ...


class NoProvider:
    """``CREDENTIAL_PROVIDER=none``: runs that name a credential are refused."""

    name = "none"

    def redeem(self, ref: str, *, run_id: str) -> Credential:
        raise CredentialRejected("no_credential_provider", "this worker has no credential provider configured")
