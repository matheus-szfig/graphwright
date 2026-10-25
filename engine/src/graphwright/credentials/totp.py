"""RFC 6238 time-based one-time passwords (stdlib only).

Used by providers that hold the seed locally (file/env) and by `totp` graph
requests with an explicit `secret`. A provider that keeps the seed elsewhere
(http) returns codes from its own endpoint instead.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import time

_ALGORITHMS = {"sha1": hashlib.sha1, "sha256": hashlib.sha256, "sha512": hashlib.sha512}


def _decode_secret(secret: str) -> bytes:
    cleaned = secret.replace(" ", "").replace("-", "").upper()
    cleaned += "=" * (-len(cleaned) % 8)
    try:
        return base64.b32decode(cleaned, casefold=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("TOTP secret is not valid base32") from exc


def totp(secret: str, *, at: float | None = None, digits: int = 6, period: int = 30, algorithm: str = "sha1") -> str:
    key = _decode_secret(secret)
    counter = int((time.time() if at is None else at) // period)
    mac = hmac.new(key, struct.pack(">Q", counter), _ALGORITHMS[algorithm]).digest()
    offset = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF) % (10**digits)
    return str(code).zfill(digits)


def seconds_left(period: int = 30, *, at: float | None = None) -> float:
    now = time.time() if at is None else at
    return period - (now % period)
