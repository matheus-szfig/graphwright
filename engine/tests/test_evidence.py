"""Failure evidence: what is captured, how it is masked, and that it never raises."""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from graphwright import evidence
from graphwright.config import settings
from graphwright.engine.runtime import Event
from graphwright.engine.secrets import Redactor
from graphwright.observe.pagestate import BBox, Element, PageState

RUN = uuid.UUID(int=42)
SECRET = "hunter2-password"


def page_state(value: str) -> PageState:
    return PageState(
        url="http://demo/login", portal="demo", route="/login", title="Sign in",
        elements=[
            Element(id="e1", role="textbox", tag="input", name="Username", value="demo", bbox=BBox(x=0, y=0, w=10, h=10)),
            Element(id="e2", role="textbox", tag="input", name="Password", value=value, bbox=BBox(x=0, y=20, w=10, h=10)),
        ],
    )


class FakePage:
    url = "http://demo/login"

    def __init__(self, *, fail_screenshot: bool = False) -> None:
        self.log: list[str] = []
        self.fail_screenshot = fail_screenshot

    async def screenshot(self, **kw: Any) -> bytes:
        self.log.append(f"screenshot {kw}")
        if self.fail_screenshot:
            raise RuntimeError("target closed")
        return b"\xff\xd8\xff\xe0fake-jpeg"

    async def content(self) -> str:
        return f"<html><body><input name=password value='{SECRET}'><p>Hi</p></body></html>"


class FakeObserver:
    def __init__(self, page: FakePage, state: PageState) -> None:
        self.page, self.state = page, state

    async def observe(self) -> PageState:
        return self.state

    async def mask_value(self, element_id: str) -> bool:
        self.page.log.append(f"mask {element_id}")
        return True


def session(*, fail_screenshot: bool = False) -> SimpleNamespace:
    redactor = Redactor([SECRET])
    page = FakePage(fail_screenshot=fail_screenshot)
    return SimpleNamespace(
        page=page, observer=FakeObserver(page, page_state(SECRET)), redact=redactor,
        sensitive_elements={"e2"}, last_state=None, node="sign_in", status="needs_repair",
        failure={"type": "NoTransition", "message": "no transition matched"}, result=None,
        vars={"typed": SECRET}, request_statuses={"otp": "failed"},
        events=[Event(kind="observe", node="sign_in"), Event(kind="move", node="start", data={"to": "sign_in"}),
                Event(kind="failure", node="sign_in", data={"type": "NoTransition"})],
    )


async def test_collect_masks_fields_before_the_screenshot_and_redacts_the_rest() -> None:
    s = session()
    items = {i.kind: i for i in await evidence.collect(s, reason="failed")}

    assert set(items) == {"screenshot", "html", "pagestate", "context"}
    # the secret field was blanked on the page before the picture was taken
    assert s.page.log[0] == "mask e2" and s.page.log[1].startswith("screenshot")
    assert items["screenshot"].body.startswith(b"\xff\xd8") and items["screenshot"].metadata["masked_fields"] == 1
    for item in items.values():
        assert SECRET.encode() not in item.body, item.kind
    pagestate = json.loads(items["pagestate"].body)
    values = {e["id"]: e.get("value") for e in pagestate["elements"]}
    assert values == {"e1": "demo", "e2": "***"} and "***" in pagestate["render"]
    context = json.loads(items["context"].body)
    assert context["failure"]["type"] == "NoTransition" and context["requests"] == {"otp": "failed"}
    assert [e["kind"] for e in context["trail"]] == ["move", "failure"]  # observations are not the trail
    assert items["html"].metadata == {"truncated": False, "bytes": len(items["html"].body),
                                      "url": "http://demo/login", "route": "/login", "reason": "failed"}


async def test_a_failing_screenshot_does_not_stop_the_rest() -> None:
    items = await evidence.collect(session(fail_screenshot=True), reason="error")
    assert [i.kind for i in items] == ["html", "pagestate", "context"]


async def test_oversized_html_is_truncated_and_an_oversized_jpeg_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "evidence_max_bytes", 12)
    items = {i.kind: i for i in await evidence.collect(session(), reason="failed")}
    assert "screenshot" not in items
    assert len(items["html"].body) == 12 and items["html"].metadata["truncated"] is True


async def test_store_uploads_and_records_each_item(monkeypatch: pytest.MonkeyPatch) -> None:
    uploads: list[tuple[str, str, str]] = []
    rows: list[dict[str, Any]] = []

    async def put_object(bucket, key, body, *, content_type):
        if key.endswith("html.html"):
            raise ConnectionError("store down")  # one failed upload is skipped, not fatal
        uploads.append((bucket, key, content_type))

    async def insert_artifact(**kw):
        rows.append(kw)
        return uuid.UUID(int=len(rows))

    monkeypatch.setattr(evidence.blobs, "put_object", put_object)
    monkeypatch.setattr(evidence.db, "insert_artifact", insert_artifact)

    items = await evidence.collect(session(), reason="failed")
    stored = await evidence.store(RUN, 2, items, node="sign_in")

    assert [s["kind"] for s in stored] == ["screenshot", "pagestate", "context"]
    assert uploads[0] == (f"{settings.s3_bucket_prefix}-evidence", f"runs/{RUN}/2/screenshot.jpg", "image/jpeg")
    assert all(r["run_id"] == RUN and r["node"] == "sign_in" for r in rows)
    assert rows[0]["size_bytes"] == len(items[0].body)


async def test_capture_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    broken = SimpleNamespace(page=None)  # nothing usable at all
    assert await evidence.capture(broken, RUN, 1, reason="error") == []


@pytest.mark.parametrize(("mode", "outcome", "want"), [
    ("failure", "failed", True), ("failure", "failure", True), ("failure", "success", False),
    ("always", "success", True), ("never", "failed", False),
])
def test_wanted(monkeypatch: pytest.MonkeyPatch, mode: str, outcome: str, want: bool) -> None:
    monkeypatch.setattr(settings, "evidence_on", mode)
    assert evidence.wanted(outcome) is want
