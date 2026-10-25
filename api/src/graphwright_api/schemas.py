"""Request/response models. The credential reference is never echoed back: a
response says whether the run carries one (``has_credential``), not which."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class CreateRun(BaseModel):
    graph: str = Field(min_length=1, max_length=200)
    inputs: dict[str, Any] = Field(default_factory=dict)
    # A reference the worker's credential provider understands (a lease id, a
    # credentials-file key…). Never a value.
    credential_ref: str | None = Field(default=None, min_length=1, max_length=512)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)


class Run(BaseModel):
    id: UUID
    graph: str
    graph_version: int | None = None
    status: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    has_credential: bool = False
    requested_by: str | None = None
    idempotency_key: str | None = None
    attempt: int = 0
    current_node: str | None = None
    steps_completed: int = 0
    terminal: str | None = None
    result: dict[str, Any] | None = None
    failure: dict[str, Any] | None = None
    error_type: str | None = None
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    cancel_requested_at: datetime | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Run:
        return cls(**{k: v for k, v in row.items() if k in cls.model_fields}, has_credential=bool(row.get("credential_ref")))


class RunDetail(Run):
    worker_hostname: str | None = None
    browser_profile: str | None = None
    error_traceback: str | None = None
    progress: dict[str, Any] | None = None  # live snapshot from Redis while it exists

    @classmethod
    def from_row(cls, row: dict[str, Any], progress: dict[str, Any] | None = None) -> RunDetail:  # type: ignore[override]
        return cls(**{k: v for k, v in row.items() if k in cls.model_fields},
                   has_credential=bool(row.get("credential_ref")), progress=progress)


class RunList(BaseModel):
    runs: list[Run]
    next: UUID | None = None  # pass as ?before= for the next page


class RunEvent(BaseModel):
    id: int
    seq: int | None = None
    kind: str
    node: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class RunEvents(BaseModel):
    events: list[RunEvent]
    next: int | None = None  # pass as ?after= for the next page


class Artifact(BaseModel):
    """Failure evidence (screenshot, html, pagestate, context) — the bytes are at `url`."""

    id: UUID
    kind: str
    content_type: str | None = None
    size_bytes: int | None = None
    node: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    url: str  # relative to the API root: /runs/{run_id}/artifacts/{id}

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Artifact:
        return cls(
            id=row["id"], kind=row["kind"], content_type=row.get("content_type"), size_bytes=row.get("size_bytes"),
            node=row.get("node"), metadata=row.get("metadata") or {}, created_at=row["created_at"],
            url=f"/runs/{row['run_id']}/artifacts/{row['id']}",
        )


class Artifacts(BaseModel):
    artifacts: list[Artifact]


class GraphSummary(BaseModel):
    name: str
    workflow: str
    version: int
    portal: str
    entry_url: str | None = None
    inputs: dict[str, dict[str, Any]] = Field(default_factory=dict)
    requests: list[str] = Field(default_factory=list)
    lint: list[str] = Field(default_factory=list)
    ok: bool
