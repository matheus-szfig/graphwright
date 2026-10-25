"""API keys — ``X-API-Key: <secret>``; the matching key's id becomes ``requested_by``.

Single-tenant: every key sees every run. Keys come from ``API_KEYS``
(``id:secret,…``); comparison is constant-time and checks every key so timing
does not reveal which one was close.
"""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, Request


def check_key(keys: dict[str, str], presented: str | None) -> str | None:
    if not presented:
        return None
    match: str | None = None
    for key_id, secret in keys.items():
        if hmac.compare_digest(secret.encode(), presented.encode()):
            match = key_id
    return match


async def require_key(request: Request, x_api_key: str | None = Header(default=None)) -> str:
    keys: dict[str, str] = request.app.state.keys
    if not keys:
        raise HTTPException(status_code=503, detail={"code": "no_api_keys", "message": "API_KEYS is not configured"})
    key_id = check_key(keys, x_api_key)
    if key_id is None:
        raise HTTPException(status_code=401, detail={"code": "unauthorized", "message": "missing or invalid X-API-Key"},
                            headers={"WWW-Authenticate": "X-API-Key"})
    return key_id
