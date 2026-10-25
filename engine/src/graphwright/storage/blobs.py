"""Blob storage client.

Scope-limited to session-state save/load for the BrowserPool. Screenshot and
HTML upload helpers ship with the recorder layer.
"""

from __future__ import annotations

import json
from typing import Any

from aiobotocore.session import get_session
from botocore.exceptions import ClientError

from graphwright.config import settings


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
        "region_name": "us-east-1",
    }


async def _ensure_bucket(client: Any, bucket: str) -> None:
    try:
        await client.head_bucket(Bucket=bucket)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in {"404", "NoSuchBucket", "NotFound"}:
            await client.create_bucket(Bucket=bucket)
        else:
            raise


async def get_session_state(domain: str) -> dict[str, Any] | None:
    bucket, key = _session_bucket(), _session_key(domain)
    async with get_session().create_client(**_client_kwargs()) as client:
        try:
            response = await client.get_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in {"NoSuchKey", "404", "NoSuchBucket"}:
                return None
            raise
        async with response["Body"] as stream:
            payload = await stream.read()
    return json.loads(payload)


async def put_session_state(domain: str, state: dict[str, Any]) -> None:
    bucket, key = _session_bucket(), _session_key(domain)
    body = json.dumps(state).encode("utf-8")
    async with get_session().create_client(**_client_kwargs()) as client:
        await _ensure_bucket(client, bucket)
        await client.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json")
