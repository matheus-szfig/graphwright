"""Object storage client (any S3-compatible store; compose runs RustFS).

Two uses: failure evidence (``evidence_bucket`` — screenshots, HTML, PageStates,
written by the worker and served by the runs API) and per-domain browser session
jars for the BrowserPool (``get/put_session_state``; graph runs open contexts with
``persist_state=False``, so jars are only used by callers that opt in).

Buckets are created on first write. Thin wrapper — no business logic.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from aiobotocore.session import get_session
from botocore.exceptions import ClientError

from graphwright.config import settings

_MISSING = {"NoSuchKey", "404", "NoSuchBucket", "NotFound"}


class ObjectNotFound(LookupError):
    pass


def evidence_bucket() -> str:
    return f"{settings.s3_bucket_prefix}-{settings.evidence_bucket_suffix}"


def _session_bucket() -> str:
    return f"{settings.s3_bucket_prefix}-{settings.browser_session_bucket_suffix}"


def _session_key(domain: str) -> str:
    return f"{domain}.json"


def _client_kwargs() -> dict[str, Any]:
    return {
        "service_name": "s3",
        "endpoint_url": settings.s3_endpoint,
        "aws_access_key_id": settings.s3_access_key,
        "aws_secret_access_key": settings.s3_secret_key,
        "region_name": settings.s3_region,
    }


def _code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))


async def _ensure_bucket(client: Any, bucket: str) -> None:
    try:
        await client.head_bucket(Bucket=bucket)
    except ClientError as exc:
        if _code(exc) in _MISSING:
            await client.create_bucket(Bucket=bucket)
        else:
            raise


# --- generic objects ----------------------------------------------------------------


async def put_object(bucket: str, key: str, body: bytes, *, content_type: str) -> None:
    async with get_session().create_client(**_client_kwargs()) as client:
        await _ensure_bucket(client, bucket)
        await client.put_object(Bucket=bucket, Key=key, Body=body, ContentType=content_type)


async def stream_object(bucket: str, key: str, *, chunk_size: int = 64 * 1024) -> AsyncIterator[bytes]:
    """Yield an object's bytes. Raises ObjectNotFound before the first chunk."""
    async with get_session().create_client(**_client_kwargs()) as client:
        try:
            response = await client.get_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            if _code(exc) in _MISSING:
                raise ObjectNotFound(f"{bucket}/{key}") from exc
            raise
        async with response["Body"] as stream:
            while chunk := await stream.read(chunk_size):
                yield chunk


# --- browser session jars -------------------------------------------------------------


async def get_session_state(domain: str) -> dict[str, Any] | None:
    bucket, key = _session_bucket(), _session_key(domain)
    async with get_session().create_client(**_client_kwargs()) as client:
        try:
            response = await client.get_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            if _code(exc) in _MISSING:
                return None
            raise
        async with response["Body"] as stream:
            payload = await stream.read()
    return json.loads(payload)


async def put_session_state(domain: str, state: dict[str, Any]) -> None:
    await put_object(_session_bucket(), _session_key(domain), json.dumps(state).encode("utf-8"),
                     content_type="application/json")
