"""Reading failure evidence back out of object storage.

The worker writes evidence through ``graphwright.storage.blobs`` (S3 settings come
from the same ``S3_*`` variables, read by graphwright's settings); the API only
streams objects out. ``BlobReader`` is the seam unit tests replace.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from graphwright.storage import blobs as gw_blobs

ObjectNotFound = gw_blobs.ObjectNotFound


class BlobReader(Protocol):
    def stream(self, bucket: str, key: str) -> AsyncIterator[bytes]:
        """Yield the object's bytes; raise ObjectNotFound before the first chunk."""
        ...


class S3BlobReader:
    def stream(self, bucket: str, key: str) -> AsyncIterator[bytes]:
        return gw_blobs.stream_object(bucket, key)
