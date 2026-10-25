"""Blob session-state get/put against a patched aiobotocore client."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from botocore.exceptions import ClientError

from graphwright.storage import blobs


class _FakeStream:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    async def __aenter__(self) -> _FakeStream:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def read(self) -> bytes:
        return self._payload


class _FakeClient:
    def __init__(self) -> None:
        self.head_bucket = AsyncMock()
        self.create_bucket = AsyncMock()
        self.get_object = AsyncMock()
        self.put_object = AsyncMock()

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


@pytest.fixture
def fake_client(monkeypatch):
    client = _FakeClient()
    session = MagicMock()
    session.create_client = MagicMock(return_value=client)
    monkeypatch.setattr(blobs, "get_session", lambda: session)
    return client


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, "Op")


async def test_get_session_state_returns_payload(fake_client):
    payload = {"cookies": [{"name": "session", "value": "abc"}]}
    fake_client.get_object.return_value = {"Body": _FakeStream(json.dumps(payload).encode())}

    result = await blobs.get_session_state("example.com")

    assert result == payload
    args, kwargs = fake_client.get_object.call_args
    assert kwargs["Key"] == "example.com.json"


async def test_get_session_state_returns_none_on_missing(fake_client):
    fake_client.get_object.side_effect = _client_error("NoSuchKey")

    result = await blobs.get_session_state("example.com")

    assert result is None


async def test_get_session_state_returns_none_when_bucket_missing(fake_client):
    fake_client.get_object.side_effect = _client_error("NoSuchBucket")

    result = await blobs.get_session_state("example.com")

    assert result is None


async def test_get_session_state_reraises_unexpected_errors(fake_client):
    fake_client.get_object.side_effect = _client_error("AccessDenied")

    with pytest.raises(ClientError):
        await blobs.get_session_state("example.com")


async def test_put_session_state_creates_bucket_if_missing(fake_client):
    fake_client.head_bucket.side_effect = _client_error("NoSuchBucket")

    await blobs.put_session_state("example.com", {"cookies": []})

    fake_client.create_bucket.assert_awaited_once()
    fake_client.put_object.assert_awaited_once()
    put_kwargs = fake_client.put_object.call_args.kwargs
    assert put_kwargs["Key"] == "example.com.json"
    assert put_kwargs["ContentType"] == "application/json"
    assert json.loads(put_kwargs["Body"]) == {"cookies": []}


async def test_put_session_state_skips_create_when_bucket_exists(fake_client):
    await blobs.put_session_state("example.com", {"cookies": []})

    fake_client.head_bucket.assert_awaited_once()
    fake_client.create_bucket.assert_not_called()
