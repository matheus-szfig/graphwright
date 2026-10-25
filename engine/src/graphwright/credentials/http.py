"""HTTP lease provider — the worker asks a separate secrets service.

The worker never holds a key that decrypts anything. It presents its own
identity (``CREDENTIALS_HTTP_KEY_ID`` / ``CREDENTIALS_HTTP_KEY``) plus the
reference from the run (typically a short-lived lease id the caller obtained
when it created the run) and gets values back for this run only. TOTP codes
are fetched per call so the seed never leaves the service.

Contract (all ``POST``, JSON, under ``CREDENTIALS_HTTP_URL``)::

    /leases/{ref}/redeem   {"run_id": "..."}
      → {"credential": {"kind", "portal"?, "metadata"?, "values": {...}},
         "totp": true|false}
    /leases/{ref}/totp     {"run_id": "..."}
      → {"code": "123456"}

Errors: any 4xx/5xx; ``{"detail": {"code": "lease_expired"}}`` is recorded as
the run's ``error_type``. Headers: ``X-Key-Id``, ``X-Key``.

Synchronous on purpose: it runs from the Celery task body before the browser
starts and from ``Secret`` callbacks (which are sync). ``cert``/``verify`` are
plumbed for mTLS.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from graphwright.credentials.base import Credential, CredentialRejected

log = logging.getLogger(__name__)


class HttpLeaseProvider:
    name = "http"

    def __init__(
        self,
        base_url: str,
        *,
        key_id: str = "",
        key: str = "",
        client_cert: str = "",
        ca_bundle: str = "",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("CREDENTIALS_HTTP_URL is required for the http credential provider")
        cert: Any = tuple(client_cert.split(",", 1)) if "," in client_cert else (client_cert or None)
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            transport=transport,
            timeout=httpx.Timeout(5.0, connect=2.0),
            cert=cert,
            verify=ca_bundle or True,
            headers={"X-Key-Id": key_id, "X-Key": key, "User-Agent": "graphwright-worker"},
        )

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._client.post(path, json=body)
        except httpx.HTTPError as exc:
            log.warning("credential service unreachable: %s", type(exc).__name__)
            raise CredentialRejected("credentials_unavailable", "credential service unreachable") from exc
        if response.status_code >= 400:
            code = "credentials_error"
            try:
                detail = response.json().get("detail")
                if isinstance(detail, dict) and detail.get("code"):
                    code = str(detail["code"])
            except ValueError:
                pass
            raise CredentialRejected(code, f"credential service answered {response.status_code}")
        return response.json()

    def redeem(self, ref: str, *, run_id: str) -> Credential:
        data = self._post(f"/leases/{ref}/redeem", {"run_id": run_id})
        cred = data.get("credential") or {}
        has_totp = bool(data.get("totp"))
        return Credential(
            ref=ref,
            kind=str(cred.get("kind") or "password"),
            portal=cred.get("portal"),
            values={str(k): str(v) for k, v in (cred.get("values") or {}).items()},
            metadata=dict(cred.get("metadata") or {}),
            totp=(lambda: str(self._post(f"/leases/{ref}/totp", {"run_id": run_id})["code"])) if has_totp else None,
        )
