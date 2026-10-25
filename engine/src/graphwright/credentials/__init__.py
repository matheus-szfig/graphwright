"""Credential providers — where a run's credential reference becomes values.

``CREDENTIAL_PROVIDER`` picks one: ``none`` (default; runs with a credential are
refused), ``file``, ``env`` or ``http``. Add your own by implementing
``CredentialProvider.redeem`` and returning it from a plugin via
``register_provider``.
"""

from __future__ import annotations

from collections.abc import Callable

from graphwright.config import settings
from graphwright.credentials.base import Credential, CredentialProvider, CredentialRejected, NoProvider
from graphwright.credentials.http import HttpLeaseProvider
from graphwright.credentials.local import EnvProvider, FileProvider

__all__ = [
    "Credential", "CredentialProvider", "CredentialRejected",
    "get_provider", "register_provider", "reset_provider",
]

_FACTORIES: dict[str, Callable[[], CredentialProvider]] = {
    "none": NoProvider,
    "file": lambda: FileProvider(settings.credentials_file),
    "env": lambda: EnvProvider(settings.credentials_env_prefix),
    "http": lambda: HttpLeaseProvider(
        settings.credentials_http_url,
        key_id=settings.credentials_http_key_id,
        key=settings.credentials_http_key,
        client_cert=settings.credentials_http_client_cert,
        ca_bundle=settings.credentials_http_ca_bundle,
    ),
}
_provider: CredentialProvider | None = None


def register_provider(name: str, factory: Callable[[], CredentialProvider]) -> None:
    """Plugins: make ``CREDENTIAL_PROVIDER=<name>`` build your provider."""
    _FACTORIES[name] = factory


def get_provider() -> CredentialProvider:
    global _provider
    if _provider is None:
        try:
            _provider = _FACTORIES[settings.credential_provider]()
        except KeyError as exc:
            raise CredentialRejected("unknown_credential_provider", f"no provider {settings.credential_provider!r}") from exc
    return _provider


def reset_provider() -> None:
    global _provider
    _provider = None
