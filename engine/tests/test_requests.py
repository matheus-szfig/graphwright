"""Graph requests: lazy values with retry, extraction, redaction and routing."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import format_datetime

import httpx
import pytest

from graphwright.credentials.totp import totp
from graphwright.engine.compiler import lint
from graphwright.engine.conditions import EvalContext, evaluate
from graphwright.engine.dsl import ActionContext, do_fetch, fetch_request
from graphwright.engine.errors import StructuralError
from graphwright.engine.requests import RequestFailed, RequestRunner, RequestServices, json_path
from graphwright.engine.schema import Condition, Fetch, Workflow
from graphwright.engine.secrets import MASK, Redactor, Secret
from graphwright.observe.pagestate import PageState
from graphwright.testing import run_request


def workflow(requests: dict, inputs: dict | None = None, nodes: dict | None = None) -> Workflow:
    return Workflow.model_validate({
        "workflow": "t", "portal": "example", "start": "a",
        "inputs": inputs or {"api": {"type": "url", "pii": False}, "account": {"pii": False}},
        "requests": requests,
        "nodes": nodes or {
            "a": {"recognize": {"always": True}, "transitions": [{"to": "done"}]},
            "done": {"terminal": "success"},
        },
    })


def ctx_for(wf: Workflow, services: RequestServices, inputs: dict | None = None) -> tuple[ActionContext, list[dict]]:
    events: list[dict] = []
    ctx = ActionContext(
        page=None, observer=None, human=None, workflow=wf,  # type: ignore[arg-type]
        inputs=inputs or {"api": "https://otp.example", "account": "jane"},
        log=lambda kind, **data: events.append({"kind": kind, **data}),
        requests=RequestRunner(wf.requests, services),
    )
    return ctx, events


OTP_REQUEST = {
    "url": "{api}/codes/latest",
    "query": {"account": "{account}"},
    "headers": {"Authorization": "Bearer t0ken-abc"},
    "extract": {"code": {"json": "data.code"}},
    "retry": {"attempts": 3, "backoff_s": [0]},
}


def transport(*responses: httpx.Response | Exception) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    seen: list[httpx.Request] = []
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item

    return httpx.MockTransport(handler), seen


async def test_http_retries_on_5xx_then_extracts_and_masks() -> None:
    wf = workflow({"otp": OTP_REQUEST})
    mock, seen = transport(httpx.Response(503, text="busy"), httpx.Response(200, json={"data": {"code": "482913"}}))
    redactor = Redactor()
    ctx, events = ctx_for(wf, RequestServices(transport=mock, on_secret=redactor.add))

    result = await fetch_request("otp", ctx)

    assert result.ok and isinstance(result.values["code"], Secret)
    assert result.values["code"].reveal() == "482913"
    assert [a.outcome for a in result.attempts] == ["retry", "ok"]
    assert result.attempts[0].status == 503
    # the request went out rendered, with the real header
    assert str(seen[-1].url) == "https://otp.example/codes/latest?account=jane"
    assert seen[-1].headers["Authorization"] == "Bearer t0ken-abc"
    # …but the trace masks the sensitive header, the value and the body that held it
    trace = events[-1]
    assert trace["kind"] == "request" and trace["values"] == {"code": MASK}
    assert trace["attempts"][1]["request"]["headers"]["Authorization"] == MASK
    assert "482913" not in json.dumps(trace)
    # and the run's redactor now knows the code
    assert redactor.text("code is 482913") == f"code is {MASK}"


async def test_until_extracted_polls_until_the_code_arrives() -> None:
    spec = {**OTP_REQUEST, "retry": {"attempts": 4, "backoff_s": [0], "until_extracted": True}}
    wf = workflow({"otp": spec})
    mock, seen = transport(
        httpx.Response(200, json={"data": {}}),
        httpx.Response(200, json={"data": {"code": None}}),
        httpx.Response(200, json={"data": {"code": "111222"}}),
    )
    ctx, _ = ctx_for(wf, RequestServices(transport=mock))

    result = await fetch_request("otp", ctx)

    assert result.ok and len(seen) == 3
    assert result.attempts[0].missing == ["code"]


async def test_failure_after_attempts_raises_with_every_attempt_in_details() -> None:
    wf = workflow({"otp": {**OTP_REQUEST, "retry": {"attempts": 2, "backoff_s": [0]}}})
    mock, _ = transport(httpx.ConnectError("refused"))
    ctx, _ = ctx_for(wf, RequestServices(transport=mock))

    with pytest.raises(RequestFailed) as err:
        await fetch_request("otp", ctx)
    assert "2 attempt(s)" in str(err.value)
    assert len(err.value.details) == 2


async def test_4xx_is_not_retried_by_default() -> None:
    wf = workflow({"otp": OTP_REQUEST})
    mock, seen = transport(httpx.Response(404, text="nope"))
    ctx, _ = ctx_for(wf, RequestServices(transport=mock))

    result = await fetch_request("otp", ctx, raise_on_error=False)

    assert not result.ok and len(seen) == 1
    assert "HTTP 404" in (result.error or "")


async def test_fetch_on_error_continue_routes_through_a_request_condition() -> None:
    wf = workflow({"otp": OTP_REQUEST})
    mock, _ = transport(httpx.Response(500))
    ctx, _ = ctx_for(wf, RequestServices(transport=mock))
    state = PageState(url="https://x/", portal="x", route="/", title="")

    assert ctx.requests is not None and ctx.requests.statuses == {"otp": "pending"}
    await do_fetch(Fetch(request="otp", on_error="continue"), ctx)

    assert ctx.vars["otp"] is None
    failed = Condition.model_validate({"request": {"name": "otp", "status": "failed"}})
    unset = Condition.model_validate({"var": {"name": "otp.code", "op": "unset"}})
    ectx = EvalContext(state=state, vars=ctx.vars, requests=ctx.requests.statuses)
    assert evaluate(failed, ectx).ok and evaluate(unset, ectx).ok


async def test_cache_none_refetches_on_every_use_and_cache_run_reuses() -> None:
    mock, seen = transport(httpx.Response(200, json={"data": {"code": "999000"}}))
    fresh = workflow({"otp": OTP_REQUEST})
    ctx, _ = ctx_for(fresh, RequestServices(transport=mock))
    await ctx.render("{requests.otp.code}")
    await ctx.render("{requests.otp.code}")
    assert len(seen) == 2

    seen.clear()
    cached = workflow({"otp": {**OTP_REQUEST, "cache": "run"}})
    ctx, _ = ctx_for(cached, RequestServices(transport=mock))
    assert await ctx.render("code={requests.otp.code} again={requests.otp.code}") == "code=999000 again=999000"
    await ctx.render("{requests.otp.code}")
    assert len(seen) == 1


async def test_a_request_can_use_another_requests_value() -> None:
    wf = workflow({
        "token": {"method": "POST", "url": "{api}/auth", "json": {"user": "{account}"},
                  "extract": {"value": {"json": "token"}}},
        "otp": {"url": "{api}/codes", "headers": {"Authorization": "Bearer {requests.token.value}"},
                "extract": {"code": {"regex": r"code=(\d{6})"}}},
    })

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth":
            assert json.loads(request.content) == {"user": "jane"}
            return httpx.Response(200, json={"token": "tk-42-secret"})
        assert request.headers["Authorization"] == "Bearer tk-42-secret"
        return httpx.Response(200, text="your code=314159 expires soon")

    ctx, events = ctx_for(wf, RequestServices(transport=httpx.MockTransport(handler)))
    assert await ctx.render("{requests.otp.code}") == "314159"
    assert [e["name"] for e in events] == ["token", "otp"]


async def test_header_extractor_and_non_sensitive_values_stay_plain() -> None:
    wf = workflow({"link": {"url": "{api}/magic", "sensitive": False,
                            "extract": {"location": {"header": "Location"}}}})
    mock, _ = transport(httpx.Response(200, headers={"Location": "https://x/login?t=1"}))
    ctx, events = ctx_for(wf, RequestServices(transport=mock))

    result = await fetch_request("link", ctx)

    assert result.values == {"location": "https://x/login?t=1"}
    assert events[-1]["values"] == {"location": "https://x/login?t=1"}


async def test_host_allowlist_blocks_other_hosts() -> None:
    wf = workflow({"otp": OTP_REQUEST})
    mock, seen = transport(httpx.Response(200, json={"data": {"code": "1"}}))
    ctx, _ = ctx_for(wf, RequestServices(transport=mock, allowed_hosts=".internal.example,otp.other"))

    result = await fetch_request("otp", ctx, raise_on_error=False)

    assert not result.ok and result.code == "host_not_allowed" and not seen


async def test_totp_from_secret_template_and_from_credential() -> None:
    seed = "JBSWY3DPEHPK3PXP"
    wf = workflow({"otp": {"kind": "totp", "secret": "{seed}"}}, inputs={"seed": {}})
    ctx, _ = ctx_for(wf, RequestServices(), inputs={"seed": Secret(seed)})
    code = (await fetch_request("otp", ctx)).values["code"].reveal()
    assert code in {totp(seed, at=time.time() + d) for d in (-30, 0, 30)}

    wf = workflow({"otp": {"kind": "totp"}}, inputs={})
    ctx, _ = ctx_for(wf, RequestServices(totp=lambda: "246810"), inputs={})
    assert (await fetch_request("otp", ctx)).values["code"].reveal() == "246810"

    ctx, _ = ctx_for(wf, RequestServices(), inputs={})
    result = await fetch_request("otp", ctx, raise_on_error=False)
    assert not result.ok and "no TOTP source" in (result.error or "")


def test_totp_matches_rfc6238_vector() -> None:
    # RFC 6238 appendix B, SHA1, T=59 → 94287082 (8 digits)
    assert totp("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", at=59, digits=8) == "94287082"


class FakeImap:
    def __init__(self, messages: list[bytes]) -> None:
        self.messages = messages
        self.logged_in = False

    def login(self, user: str, password: str) -> None:
        assert (user, password) == ("bot@example.com", "app-pass")
        self.logged_in = True

    def select(self, folder: str, readonly: bool = False) -> None:
        assert readonly

    def search(self, charset, criteria):  # noqa: ANN001
        return "OK", [b" ".join(str(i + 1).encode() for i in range(len(self.messages)))]

    def fetch(self, msg_id: bytes, spec: str):  # noqa: ANN001
        return "OK", [(b"1 (RFC822)", self.messages[int(msg_id) - 1])]

    def logout(self) -> None:
        pass


def mail(subject: str, body: str, *, sender: str = "no-reply@site.example", age_s: float = 0) -> bytes:
    m = EmailMessage()
    m["From"], m["Subject"] = sender, subject
    m["Date"] = format_datetime(datetime.fromtimestamp(time.time() - age_s, UTC))
    m.set_content(body)
    return m.as_bytes()


async def test_imap_takes_the_newest_matching_message_after_the_run_started() -> None:
    wf = workflow({"mail_code": {
        "kind": "imap",
        "imap": {"host": "imap.example", "username": "bot@example.com", "password": "{pw}", "from": "site.example"},
        "extract": {"code": {"regex": r"\b(\d{6})\b"}},
    }}, inputs={"pw": {}})
    fake = FakeImap([
        mail("Your code", "Code: 000001", age_s=3600),  # before the run — ignored
        mail("Newsletter", "nothing here 123456", sender="news@other.example"),  # wrong sender
        mail("Your code", "Code: 775533"),
    ])
    services = RequestServices(imap_factory=lambda h, p: fake, run_started=time.time() - 5)
    ctx, events = ctx_for(wf, services, inputs={"pw": Secret("app-pass")})

    result = await fetch_request("mail_code", ctx)

    assert fake.logged_in and result.values["code"].reveal() == "775533"
    assert "app-pass" not in json.dumps(events)


def test_json_path() -> None:
    data = {"data": {"items": [{"code": "a"}, {"code": "b"}]}}
    assert json_path(data, "$.data.items[1].code") == "b"
    assert json_path(data, "data.items[5].code") is None
    assert json_path(data, "data.missing") is None


def test_lint_catches_request_mistakes() -> None:
    wf = workflow(
        {
            "a": {"url": "{api}/a?x={requests.b.value}", "extract": {"value": {"json": "v"}}},
            "b": {"url": "{api}/b?x={requests.a.value}", "extract": {"value": {"json": "v"}}},
            "t": {"kind": "totp", "extract": {"code": {"json": "c"}}},
            "poll": {"url": "{nope}", "extract": {"v": {"json": "v"}}, "retry": {"until_extracted": True}},
        },
        nodes={
            "a": {"recognize": {"request": {"name": "ghost"}},
                  "actions": [{"fill": {"target": "x", "value": "{requests.a.missing}"}},
                              {"fetch": {"request": "ghost2"}}],
                  "transitions": [{"to": "done"}]},
            "done": {"terminal": "success"},
        },
    )
    errors = "\n".join(lint(wf))
    assert "cycle" in errors
    assert "a totp request yields `code`" in errors
    assert "until_extracted` needs `attempts` >= 2" in errors
    assert "unknown input {nope}" in errors
    assert "unknown request 'ghost'" in errors
    assert "fetches unknown request 'ghost2'" in errors
    assert "has no field 'missing'" in errors


async def test_run_request_report_masks_unless_revealed() -> None:
    wf = workflow({"otp": OTP_REQUEST})
    mock, _ = transport(httpx.Response(200, json={"data": {"code": "123987"}}))

    report = await run_request(wf, "otp", {"api": "https://otp.example", "account": "jane"},
                               services=RequestServices(transport=mock))
    assert report["ok"] and report["values"] == {"code": MASK}
    assert report["calls"][0]["attempts"][0]["status"] == 200

    report = await run_request(wf, "otp", {"api": "https://otp.example", "account": "jane"},
                               services=RequestServices(transport=mock), reveal=True)
    assert report["values"] == {"code": "123987"}


async def test_run_request_reports_unset_template() -> None:
    wf = workflow({"otp": OTP_REQUEST})
    report = await run_request(wf, "otp", {"account": "jane"})
    assert not report["ok"] and "{api}" in report["error"]


def test_render_template_requires_a_fetched_request() -> None:
    wf = workflow({"otp": OTP_REQUEST})
    ctx, _ = ctx_for(wf, RequestServices())
    with pytest.raises(RequestFailed):
        ctx.render_template("{requests.otp.code}")
    with pytest.raises(StructuralError):
        ctx.render_template("{vars.nothing}")
