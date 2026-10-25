"""Failure evidence — what the page looked like when a run went wrong.

When a run fails (an engine failure, an unexpected error, or a `terminal: failure`
outcome), the worker captures, from the still-open page:

    screenshot   JPEG (full page by default) — fields that received a typed secret are
                 replaced with bullets first, because pixels cannot be redacted later
    html         ``page.content()`` passed through the run's Redactor, size-capped
    pagestate    the last observation (JSON) — what the engine actually reasoned on
    context      node, url/route, the failure record, the tail of the decision trail
                 and the redacted vars (JSON)

Each item goes to ``<S3_BUCKET_PREFIX>-<EVIDENCE_BUCKET_SUFFIX>`` under
``runs/<run_id>/<attempt>/<kind>.<ext>`` with one ``artifact`` row; the runs API lists
and serves them. Capture is best effort: every step is guarded, a failure to capture
never changes the run's outcome, and nothing here raises.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from graphwright.config import settings
from graphwright.storage import blobs, db

log = logging.getLogger(__name__)

_TRAIL_KINDS = frozenset({"goto", "move", "jump", "resync", "request", "fill", "select", "extract",
                          "retry", "on_fail", "failure", "terminal", "recognize", "transitions"})
_TRAIL_TAIL = 40
_STEP_TIMEOUT_S = 15.0


@dataclass
class EvidenceItem:
    kind: str  # screenshot | html | pagestate | context
    content_type: str
    ext: str
    body: bytes
    metadata: dict[str, Any] = field(default_factory=dict)


def wanted(outcome: str) -> bool:
    """`outcome` is "failed" (the run failed), "failure" (a failure terminal) or
    "success"."""
    mode = settings.evidence_on
    if mode == "never":
        return False
    return mode == "always" or outcome in ("failed", "failure")


def _cap(body: bytes, limit: int) -> tuple[bytes, bool]:
    if len(body) <= limit:
        return body, False
    return body[:limit], True


async def _guard(label: str, coro: Any) -> Any:
    try:
        return await asyncio.wait_for(coro, _STEP_TIMEOUT_S)
    except Exception:  # noqa: BLE001 — evidence is best effort, by contract
        log.warning("evidence: %s failed", label, exc_info=True)
        return None


async def collect(session: Any, *, reason: str, failure: dict[str, Any] | None = None) -> list[EvidenceItem]:
    """Capture evidence from a GraphSession whose page is still open."""
    redact = session.redact
    page, observer = session.page, session.observer
    limit = settings.evidence_max_bytes
    items: list[EvidenceItem] = []
    url = getattr(page, "url", None)

    # Fresh observation if the page moved since the last one; else keep the last.
    state = await _guard("observe", observer.observe()) or session.last_state
    route = state.route if state is not None else None

    if settings.evidence_screenshot:
        masked = 0
        for element_id in sorted(session.sensitive_elements):
            if await _guard(f"mask {element_id}", observer.mask_value(element_id)):
                masked += 1
        shot = await _guard("screenshot", page.screenshot(type="jpeg", quality=75, full_page=settings.evidence_full_page))
        if shot:
            body, truncated = _cap(shot, limit)
            if not truncated:  # a cut JPEG is useless; skip rather than store garbage
                items.append(EvidenceItem("screenshot", "image/jpeg", "jpg", body,
                                          {"masked_fields": masked, "full_page": settings.evidence_full_page}))

    html = await _guard("html", page.content())
    if html is not None:
        body, truncated = _cap(redact(html).encode("utf-8"), limit)
        items.append(EvidenceItem("html", "text/html; charset=utf-8", "html", body,
                                  {"truncated": truncated, "bytes": len(body)}))

    if state is not None:
        payload = redact(state.model_dump(mode="json"))
        # A field that received a PII input holds it in `value` even when it is not
        # a credential the Redactor knows; mask those like the screenshot does.
        for element in payload.get("elements", []):
            if element.get("id") in session.sensitive_elements and element.get("value"):
                element["value"] = "***"
        payload["render"] = redact(type(state).model_validate(payload).render())
        items.append(EvidenceItem("pagestate", "application/json", "json",
                                  _cap(json.dumps(payload, ensure_ascii=False).encode("utf-8"), limit)[0]))

    trail = [e.as_dict() for e in session.events if e.kind in _TRAIL_KINDS][-_TRAIL_TAIL:]
    context = redact({
        "reason": reason,
        "node": session.node,
        "status": session.status,
        "url": url,
        "route": route,
        "failure": failure or session.failure,
        "result": session.result,
        "vars": session.vars,
        "requests": session.request_statuses,
        "trail": trail,
    })
    items.append(EvidenceItem("context", "application/json", "json",
                              json.dumps(context, ensure_ascii=False, default=str).encode("utf-8")))
    for item in items:
        item.metadata.setdefault("url", url)
        item.metadata.setdefault("route", route)
        item.metadata["reason"] = reason
    return items


async def store(run_id: UUID, attempt: int, items: list[EvidenceItem], *, node: str | None) -> list[dict[str, Any]]:
    """Upload each item and record an artifact row. Returns ``[{id, kind, …}]`` for
    the items that made it."""
    bucket = blobs.evidence_bucket()
    stored: list[dict[str, Any]] = []
    for item in items:
        key = f"runs/{run_id}/{attempt}/{item.kind}.{item.ext}"
        if not await _guard(f"upload {item.kind}", _upload(bucket, key, item)):
            continue
        artifact_id = await _guard(f"record {item.kind}", db.insert_artifact(
            run_id=run_id, kind=item.kind, bucket=bucket, object_key=key, content_type=item.content_type,
            size_bytes=len(item.body), node=node, metadata=item.metadata,
        ))
        if artifact_id is not None:
            stored.append({"id": str(artifact_id), "kind": item.kind, "content_type": item.content_type,
                           "size_bytes": len(item.body)})
    return stored


async def _upload(bucket: str, key: str, item: EvidenceItem) -> bool:
    await blobs.put_object(bucket, key, item.body, content_type=item.content_type)
    return True


async def capture(session: Any, run_id: UUID, attempt: int, *, reason: str,
                  failure: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """collect + store, never raising. What `tasks/runs.py` calls."""
    try:
        items = await collect(session, reason=reason, failure=failure)
        return await store(run_id, attempt, items, node=session.node)
    except Exception:  # noqa: BLE001
        log.warning("evidence: capture failed for run %s", run_id, exc_info=True)
        return []
