"""Providers that hold values locally: a mounted file, or environment variables.

Both are meant for development and for deployments where the secret store
injects a file or env vars into the worker container (Docker/Kubernetes
secrets). Neither persists anything.

File (``CREDENTIALS_FILE``, YAML or JSON)::

    demo-user:
      kind: password
      portal: demo-records          # optional: refuse other graphs
      values: {username: jane, password: s3cret}
      totp_secret: JBSWY3DPEHPK3PXP # optional: enables totp()

Env (``CREDENTIALS_ENV_PREFIX``, default ``GW_CRED_``)::

    GW_CRED_DEMO_USER_USERNAME=jane
    GW_CRED_DEMO_USER_PASSWORD=s3cret
    GW_CRED_DEMO_USER_TOTP_SECRET=JBSWY3DPEHPK3PXP
    GW_CRED_DEMO_USER_PORTAL=demo-records

The ref ``demo-user`` maps to ``DEMO_USER`` (upper-case, ``-`` → ``_``).
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from graphwright.credentials.base import Credential, CredentialRejected
from graphwright.credentials.totp import totp


def _with_totp(ref: str, entry: Mapping[str, Any]) -> Credential:
    values = {str(k): str(v) for k, v in (entry.get("values") or {}).items()}
    seed = entry.get("totp_secret")
    return Credential(
        ref=ref,
        kind=str(entry.get("kind") or "password"),
        portal=entry.get("portal"),
        values=values,
        metadata=dict(entry.get("metadata") or {}),
        totp=(lambda s=str(seed): totp(s)) if seed else None,
    )


class FileProvider:
    name = "file"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _load(self) -> dict[str, Any]:
        # Read per redeem: a rotated secret file is picked up without a restart.
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise CredentialRejected("credentials_file_unreadable", f"cannot read {self.path.name}") from exc
        data = json.loads(text) if self.path.suffix == ".json" else yaml.safe_load(text)
        if not isinstance(data, dict):
            raise CredentialRejected("credentials_file_invalid", "credentials file must be a mapping of ref → entry")
        return data

    def redeem(self, ref: str, *, run_id: str) -> Credential:
        entry = self._load().get(ref)
        if not isinstance(entry, dict):
            raise CredentialRejected("credential_not_found", f"no credential {ref!r}")
        return _with_totp(ref, entry)


def _dotenv() -> dict[str, str]:
    from dotenv import dotenv_values

    return {k: v for k, v in dotenv_values(".env").items() if v is not None}


class EnvProvider:
    name = "env"

    def __init__(self, prefix: str = "GW_CRED_", environ: Mapping[str, str] | None = None) -> None:
        self.prefix = prefix
        self._environ = environ

    def redeem(self, ref: str, *, run_id: str) -> Credential:
        # Host runs keep GW_CRED_* in .env (which pydantic-settings reads but does
        # not export); real environment variables win over the file.
        environ = self._environ if self._environ is not None else {**_dotenv(), **os.environ}
        stem = self.prefix + ref.upper().replace("-", "_") + "_"
        found = {k[len(stem):].lower(): v for k, v in environ.items() if k.startswith(stem)}
        if not found:
            raise CredentialRejected("credential_not_found", f"no credential {ref!r}")
        entry: dict[str, Any] = {
            "kind": found.pop("kind", "password"),
            "portal": found.pop("portal", None),
            "totp_secret": found.pop("totp_secret", None),
            "values": found,
        }
        return _with_totp(ref, entry)
